"""Metadata write policy for platform-backed stores."""
from __future__ import annotations

import json
from typing import Any, Optional

import yaml

from ..core.taskmeta import DECISION_REQUIRED_KEY


_LEGACY_INLINE_PAYLOAD_KEYS = {"contract", "review_report", "verification"}
_FORBIDDEN_DECISION_KEYS = {
    "review_report",
    "blockers",
    "nits",
    "issue",
    "fix",
    "evidence",
    "summary",
}
MULTICA_METADATA_VALUE_MAX_BYTES = 8 * 1024


def encode_metadata_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def assert_metadata_write_allowed(key: str, value: Any) -> None:
    """Reject metadata writes that would store unbounded prose payloads."""
    if key in _LEGACY_INLINE_PAYLOAD_KEYS:
        raise ValueError(f"{key} is legacy read-only metadata; write {key}_ref instead")
    if key == DECISION_REQUIRED_KEY:
        _assert_decision_required_allowed(value)
    encoded = encode_metadata_value(value)
    size = len(encoded.encode("utf-8"))
    if size > MULTICA_METADATA_VALUE_MAX_BYTES:
        raise ValueError(
            f"{key} metadata is {size} bytes; Multica allows at most "
            f"{MULTICA_METADATA_VALUE_MAX_BYTES} bytes. Store the complete value "
            "as an attachment and write only its bounded reference in metadata."
        )


def _assert_decision_required_allowed(value: Any) -> None:
    if not isinstance(value, dict):
        return
    forbidden = sorted(_FORBIDDEN_DECISION_KEYS.intersection(value))
    if forbidden:
        joined = ", ".join(forbidden)
        raise ValueError(f"decision_required contains forbidden prose fields: {joined}")


def parse_payload_text(text: Optional[str], *, legacy_raw_fallback: bool = False) -> Optional[dict]:
    """Parse YAML/JSON attachment payloads; optionally preserve legacy raw values."""
    if not text:
        return None
    for loader in (json.loads, yaml.safe_load):
        try:
            parsed = loader(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"raw": text} if legacy_raw_fallback else None
