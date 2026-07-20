"""Safe delivery-command rendering and explicit GitHub CLI classification."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shlex
from typing import Mapping, Optional, Sequence, Tuple

from ..errors import ValidationError
from ..i18n import ui


_SAFE_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_PR_URL = re.compile(
    r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~:@%+-]*)*/?\Z"
)
_TIMEOUT_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?\Z", re.IGNORECASE)


@dataclass(frozen=True)
class PreparedDeliveryCommand:
    argv: Tuple[str, ...]
    environment: Optional[dict[str, str]]
    is_github_cli: bool


def validate_delivery_revision(value: object) -> str:
    """Validate an opaque Git revision token before it reaches a command."""
    if not isinstance(value, str) or not _SAFE_REVISION.fullmatch(value):
        raise ValidationError(ui(
            "delivered revision must be a non-empty Git revision token containing "
            "only letters, digits, '.', '_', or '-' (maximum 128 characters).",
            "delivered revision 必须是非空 Git revision token，且只能包含字母、"
            "数字、'.'、'_' 或 '-'（最长 128 个字符）。",
        ))
    return value


def validate_delivery_pr_url(value: object) -> str:
    """Reject shell-significant or whitespace-bearing PR URL placeholders."""
    if not isinstance(value, str) or not _SAFE_PR_URL.fullmatch(value):
        raise ValidationError(ui(
            "PR URL must be an https URL containing no whitespace or shell control characters.",
            "PR URL 必须是 https URL，且不得包含空白或 shell 控制字符。",
        ))
    return value


def _is_assignment(token: str) -> bool:
    name, separator, _ = token.partition("=")
    return bool(
        separator
        and name
        and (name[0].isalpha() or name[0] == "_")
        and all(char.isalnum() or char == "_" for char in name[1:])
    )


def _is_gh_executable(token: str) -> bool:
    return token == "gh" or os.path.basename(token) == "gh"


def _consume_env_wrapper(tokens: Sequence[str], start: int) -> Optional[int]:
    """Return the command index for the supported POSIX/GNU env grammar."""
    index = start + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"}:
            index += 1
            continue
        if token in {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if token.startswith(("--unset=", "--chdir=", "--argv0=")):
            index += 1
            continue
        if token.startswith("-"):
            return None
        break
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    return index


def _unwrap_github_command(tokens: Sequence[str]) -> Optional[Sequence[str]]:
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1

    while index < len(tokens):
        token = tokens[index]
        if token == "env":
            next_index = _consume_env_wrapper(tokens, index)
            if next_index is None:
                return None
            index = next_index
            continue
        if token == "command":
            index += 1
            if index < len(tokens) and tokens[index] == "--":
                index += 1
            continue
        if token == "timeout":
            index += 1
            while index < len(tokens):
                option = tokens[index]
                if option in {"--foreground", "--preserve-status", "--verbose"}:
                    index += 1
                    continue
                if option in {"-k", "--kill-after", "-s", "--signal"}:
                    if index + 1 >= len(tokens):
                        return None
                    index += 2
                    continue
                if option.startswith(("--kill-after=", "--signal=")):
                    index += 1
                    continue
                break
            if index >= len(tokens) or not _TIMEOUT_DURATION.fullmatch(tokens[index]):
                return None
            index += 1
            continue
        break
    return tokens[index:]


def _classify_github_command(tokens: Sequence[str], operation_kind: str) -> bool:
    core = _unwrap_github_command(tokens)
    if not core or not _is_gh_executable(core[0]):
        return False
    expected = "checks" if operation_kind == "ci" else "merge"
    if len(core) < 3 or list(core[1:3]) != ["pr", expected]:
        raise ValidationError(ui(
            f"GitHub delivery command must use `gh pr {expected}` for "
            f"the {operation_kind} operation.",
            f"GitHub delivery command 在 {operation_kind} 操作中必须使用 "
            f"`gh pr {expected}`。",
        ))
    return True


def _prepare_execution(tokens: Sequence[str]) -> tuple[Tuple[str, ...], Optional[dict[str, str]]]:
    """Apply assignment/env/command wrappers without invoking a shell."""
    remaining = list(tokens)
    environment: Optional[dict[str, str]] = None

    def mutable_environment() -> dict[str, str]:
        nonlocal environment
        if environment is None:
            environment = dict(os.environ)
        return environment

    while remaining and _is_assignment(remaining[0]):
        name, _, value = remaining.pop(0).partition("=")
        mutable_environment()[name] = value

    while remaining:
        if remaining[0] == "command":
            remaining.pop(0)
            if remaining[:1] == ["--"]:
                remaining.pop(0)
            continue
        if remaining[0] != "env":
            break

        fallback = tuple(remaining)
        command_index = _consume_env_wrapper(remaining, 0)
        if command_index is None:
            return fallback, environment
        prefix = remaining[1:command_index]
        env = mutable_environment()
        index = 0
        while index < len(prefix):
            token = prefix[index]
            if token == "--":
                index += 1
                continue
            if token in {"-i", "--ignore-environment"}:
                env.clear()
                index += 1
                continue
            if token in {"-u", "--unset"}:
                env.pop(prefix[index + 1], None)
                index += 2
                continue
            if token.startswith("--unset="):
                env.pop(token.split("=", 1)[1], None)
                index += 1
                continue
            if _is_assignment(token):
                name, _, value = token.partition("=")
                env[name] = value
                index += 1
                continue
            # Other valid env options affect env's own process behavior. Keep the
            # wrapper executable rather than reimplementing those semantics.
            return fallback, environment
        remaining = remaining[command_index:]

    if not remaining:
        raise ValidationError(ui(
            "delivery command has no executable after wrappers.",
            "delivery command 在 wrapper 之后没有可执行程序。",
        ))
    return tuple(remaining), environment


def prepare_delivery_command(
    command: object,
    values: Mapping[str, str],
    operation_kind: str,
) -> PreparedDeliveryCommand:
    """Parse a command template once, substitute values as argv data, and classify it."""
    if not isinstance(command, str) or not command.strip():
        raise ValidationError(ui(
            "delivery command must be a non-empty string.",
            "delivery command 必须是非空字符串。",
        ))
    try:
        template_tokens = shlex.split(command)
    except ValueError as exc:
        raise ValidationError(ui(
            f"Invalid delivery command syntax: {exc}",
            f"delivery command 语法非法: {exc}",
        )) from exc
    if not template_tokens:
        raise ValidationError(ui(
            "delivery command must contain an executable.",
            "delivery command 必须包含可执行程序。",
        ))

    rendered_tokens = tuple(
        _replace_template_values(token, values) for token in template_tokens
    )
    argv, environment = _prepare_execution(rendered_tokens)
    return PreparedDeliveryCommand(
        argv=argv,
        environment=environment,
        is_github_cli=_classify_github_command(template_tokens, operation_kind),
    )


def _replace_template_values(token: str, values: Mapping[str, str]) -> str:
    rendered = token
    for name, value in values.items():
        rendered = rendered.replace("{" + name + "}", value)
    return rendered
