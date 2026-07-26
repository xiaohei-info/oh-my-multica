"""PlanReturn 人工计划门 —— 严格解析 + 不可变 SHA-256 快照 + exit 20 决策。

批准的三种形式(有且仅有;其余一律拒绝,报错即教学):
    PlanReturn path=/absolute/path/to/plan.md
    PlanReturn artifact=https://artifactd.example/...
    PlanReturn host=artemis path=/absolute/path/to/plan.md sha256=<digest>

分层:
  - parse_plan_return:纯语法。评论小说、相对路径、未支持 scheme、未知/重复键、
    歧义多返回、非法键组合 → ValidationError(exit 5,附可复制的正确形式)。
  - resolve_plan_return:把已解析的输入落成不可变快照。缺失/不可读/读取期间
    变动的文件、hash 不匹配、未配置安全 fetch、host 不在 allowlist
    → NeedsDecision(exit 20,结构化报告含 repair 可复制修复行)。
    快照按内容寻址写入 plan store(<sha256>.md),幂等;artifact/host 形式
    只走注入的窄 fetch(source dict) -> bytes 接口,绝不内嵌任意 shell 执行。
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import NeedsDecision, ValidationError
from ..i18n import ui

_KINDS = ("path", "artifact", "host")
_ALLOWED_KEYS = {"path", "artifact", "host", "sha256"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_TEACHING = ui(
    "Expected exactly one line in one of the approved forms:\n"
    "  PlanReturn path=/absolute/path/to/plan.md\n"
    "  PlanReturn artifact=https://artifactd.example/...\n"
    "  PlanReturn host=artemis path=/absolute/path/to/plan.md sha256=<digest>",
    "只接受恰好一行、且为以下批准形式之一:\n"
    "  PlanReturn path=/absolute/path/to/plan.md\n"
    "  PlanReturn artifact=https://artifactd.example/...\n"
    "  PlanReturn host=artemis path=/absolute/path/to/plan.md sha256=<digest>")

_REPAIR_LINE = "PlanReturn path=/absolute/path/to/plan.md"


@dataclass(frozen=True)
class PlanReturn:
    """解析后的 PlanReturn。kind ∈ {"path", "artifact", "host"}。"""
    kind: str
    path: str | None = None
    url: str | None = None
    host: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class PlanSnapshot:
    """不可变计划快照:内容寻址落盘 + 来源记录。"""
    sha256: str
    size: int
    snapshot_path: str
    source: dict


def _reject(reason_en: str, reason_zh: str) -> ValidationError:
    return ValidationError(ui(
        f"Invalid PlanReturn: {reason_en}\n{_TEACHING}",
        f"非法 PlanReturn:{reason_zh}\n{_TEACHING}"))


def parse_plan_return(text: str) -> PlanReturn:
    """严格解析 PlanReturn 文本(必须恰好一行、恰好一个返回)。

    拒绝:评论小说(附加散文/多行)、相对路径、非 https scheme、未知键、
    重复键、空值、裸 token、歧义组合、非法 sha256。
    """
    if not isinstance(text, str):
        raise _reject(f"expected text, got {type(text).__name__}",
                      f"应为文本,got {type(text).__name__}")
    stripped = text.strip()
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if len(lines) != 1 or not lines[0].strip().startswith("PlanReturn"):
        raise _reject(
            "expected exactly one PlanReturn line and no other prose",
            "必须恰好一行 PlanReturn,不允许附加散文(拒绝评论小说)")
    tokens = lines[0].strip().split()
    if tokens[0] != "PlanReturn" or len(tokens) == 1:
        raise _reject("missing key=value payload", "缺少 key=value 载荷")

    fields: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise _reject(f"bare token {token!r} (expected key=value)",
                          f"裸 token {token!r}(应为 key=value)")
        key, _, value = token.partition("=")
        if key not in _ALLOWED_KEYS:
            raise _reject(f"unknown key {key!r}", f"未知键 {key!r}")
        if key in fields:
            raise _reject(f"duplicate key {key!r}", f"重复键 {key!r}")
        if not value:
            raise _reject(f"empty value for key {key!r}", f"键 {key!r} 的值为空")
        fields[key] = value

    if "path" in fields and not fields["path"].startswith("/"):
        raise _reject(f"path must be absolute: {fields['path']!r}",
                      f"path 必须为绝对路径: {fields['path']!r}")
    if "artifact" in fields and not fields["artifact"].startswith("https://"):
        raise _reject(
            f"artifact must be an https:// URL: {fields['artifact']!r}",
            f"artifact 必须为 https:// URL: {fields['artifact']!r}")
    if "sha256" in fields and not _SHA256_RE.match(fields["sha256"]):
        raise _reject(
            f"sha256 must be 64 lowercase hex chars: {fields['sha256']!r}",
            f"sha256 必须为 64 位小写 hex: {fields['sha256']!r}")

    keys = set(fields)
    if keys == {"path"}:
        return PlanReturn(kind="path", path=fields["path"])
    if keys == {"artifact"}:
        return PlanReturn(kind="artifact", url=fields["artifact"])
    if keys == {"host", "path", "sha256"}:
        return PlanReturn(kind="host", host=fields["host"],
                          path=fields["path"], sha256=fields["sha256"])
    raise _reject(
        f"ambiguous/unsupported key combination: {sorted(keys)} "
        "(approved: path | artifact | host+path+sha256)",
        f"歧义/未支持的键组合: {sorted(keys)}"
        "(批准形式: path | artifact | host+path+sha256)")


def _decision(reason_en: str, reason_zh: str, source: dict) -> NeedsDecision:
    report = {
        "kind": "plan_return",
        "reason": reason_en,
        "source": source,
        "repair": _REPAIR_LINE,
    }
    return NeedsDecision(ui(
        f"PlanReturn cannot be safely resolved: {reason_en}. "
        f"Repair with a readable local snapshot line: {_REPAIR_LINE}",
        f"PlanReturn 无法安全解析:{reason_zh}。"
        f"请用可读的本地快照行修复:{_REPAIR_LINE}"), report=report)


def _snapshot(body: bytes, plan_store_dir: str, source: dict) -> PlanSnapshot:
    """内容寻址快照:<store>/<sha256>.md;同 hash 已存在则幂等复用。"""
    digest = hashlib.sha256(body).hexdigest()
    os.makedirs(plan_store_dir, exist_ok=True)
    target = os.path.join(plan_store_dir, f"{digest}.md")
    if not os.path.exists(target):
        tmp = f"{target}.tmp"
        with open(tmp, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    return PlanSnapshot(sha256=digest, size=len(body),
                        snapshot_path=target, source=source)


def _resolve_local_path(ret: PlanReturn, plan_store_dir: str) -> PlanSnapshot:
    """本地 path 形式:读两遍比对 hash,检测读取期间变动。"""
    source = {"kind": "path", "path": ret.path}
    path = Path(ret.path)
    try:
        first = path.read_bytes()
    except OSError as exc:
        raise _decision(
            f"plan file is missing or unreadable: {ret.path} ({exc.strerror or exc})",
            f"计划文件缺失或不可读: {ret.path} ({exc.strerror or exc})",
            source) from exc
    try:
        second = path.read_bytes()
    except OSError as exc:
        raise _decision(
            f"plan file changed while reading: {ret.path}",
            f"计划文件在读取期间发生变动: {ret.path}",
            source) from exc
    if hashlib.sha256(first).digest() != hashlib.sha256(second).digest():
        raise _decision(
            f"plan file changed while reading: {ret.path}",
            f"计划文件在读取期间发生变动: {ret.path}",
            source)
    return _snapshot(first, plan_store_dir, source)


def _resolve_fetched(ret: PlanReturn, plan_store_dir: str, fetch,
                     allowed_hosts) -> PlanSnapshot:
    """artifact/host 形式:仅经注入的窄 fetch 接口取字节,绝不 shell。"""
    if ret.kind == "artifact":
        source = {"kind": "artifact", "url": ret.url}
        missing = f"no fetch adapter configured for artifact URL: {ret.url}"
        missing_zh = f"未配置 artifact URL 的安全 fetch 适配器: {ret.url}"
    else:
        source = {"kind": "host", "host": ret.host, "path": ret.path}
        if ret.host not in set(allowed_hosts or ()):
            raise _decision(
                f"host {ret.host!r} is not in the configured allowlist "
                "(plan_gate.allowed_hosts)",
                f"host {ret.host!r} 不在配置的 allowlist(plan_gate.allowed_hosts)中",
                source)
        missing = (f"no fetch adapter configured for host {ret.host!r}; "
                   "copy the plan locally and resubmit")
        missing_zh = (f"未配置 host {ret.host!r} 的安全 fetch 适配器;"
                      "请把计划复制到本地后重新提交")
    if fetch is None:
        raise _decision(missing, missing_zh, source)
    body = fetch(source)
    if not isinstance(body, (bytes, bytearray)):
        raise _decision(
            f"fetch adapter returned {type(body).__name__}, expected bytes",
            f"fetch 适配器返回 {type(body).__name__},应为 bytes",
            source)
    body = bytes(body)
    if ret.sha256 is not None:
        actual = hashlib.sha256(body).hexdigest()
        if actual != ret.sha256:
            raise _decision(
                f"sha256 mismatch for {ret.host}:{ret.path} "
                f"(declared {ret.sha256}, actual {actual})",
                f"{ret.host}:{ret.path} 的 sha256 不匹配"
                f"(声明 {ret.sha256},实际 {actual})",
                source)
    return _snapshot(body, plan_store_dir, source)


def resolve_plan_return(ret: PlanReturn, *, plan_store_dir: str, fetch=None,
                        allowed_hosts=()) -> PlanSnapshot:
    """把解析后的 PlanReturn 落成不可变快照。

    fetch:注入的窄接口 fetch(source: dict) -> bytes(artifact/host 形式);
    allowed_hosts:host 形式的主机 allowlist(缺省为空 —— fail closed)。
    无法安全解析 → NeedsDecision(exit 20,report 含 repair 可复制修复行)。
    """
    if ret.kind == "path":
        return _resolve_local_path(ret, plan_store_dir)
    if ret.kind in ("artifact", "host"):
        return _resolve_fetched(ret, plan_store_dir, fetch, allowed_hosts)
    raise ValidationError(ui(
        f"Unknown PlanReturn kind: {ret.kind!r} (expected one of {_KINDS})",
        f"未知 PlanReturn kind: {ret.kind!r}(应为 {_KINDS} 之一)"))
