"""Stateless helpers: canonical digests and verified reads of published attachments."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from enum import Enum

import yaml

from ..errors import ValidationError


def digest(value) -> str:
    """sha256 of the canonical JSON form of ``value`` (key order and whitespace do not matter)."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plain(value):
    """JSON-round-trip ``value`` so enums and dataclass fields compare as plain data."""
    return json.loads(json.dumps(
        value, ensure_ascii=False, default=lambda v: v.value if isinstance(v, Enum) else v))


def read_attachment(store, item_id: str, ref: dict) -> dict:
    """Re-read a published YAML attachment and fail closed unless bytes and platform identity match ``ref``."""
    if (not isinstance(ref, dict)
            or any(not ref.get(k) for k in ("attachment_id", "comment_id", "sha256"))
            or type(ref.get("bytes")) is not int or ref["bytes"] <= 0):
        raise ValidationError(
            "Attachment reference is incomplete (needs attachment_id, comment_id, sha256, bytes); "
            f"inspect the work item with `omac work show {item_id}`")
    observed = store.observe_verification_attachment(item_id, ref)
    content = observed.content
    if (observed.attachment_id != ref["attachment_id"] or observed.comment_id != ref["comment_id"]
            or observed.sha256 != ref["sha256"] or hashlib.sha256(content).hexdigest() != ref["sha256"]
            or len(content) != ref["bytes"]):
        raise ValidationError(
            "Attachment bytes or platform identity changed since it was recorded; "
            f"re-read it with `omac work show {item_id}` before retrying")
    try:
        text = content.decode("utf-8")
        body = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValidationError("Attachment cannot be decoded as UTF-8 YAML") from exc
    if not isinstance(body, dict):
        raise ValidationError("Attachment is not a YAML mapping")
    return {"ref": copy.deepcopy(ref), "native": asdict(observed) | {"content": text}, "body": body}
