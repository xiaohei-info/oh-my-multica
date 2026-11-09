"""Facts about a review submission the platform rejected, kept where the rejected metadata budget cannot block them.

`omac work submit` runs inside the Reviewer's Run. When the platform refuses the write (issue metadata over its
aggregate limit), the Loop would otherwise see "Run ended, no verdict" and spend the no-submit budget. The failed
submit leaves one marker comment (comments do not count against metadata); the Loop reads it back (`parse`) and
blocks with its own reason instead (§5.4 failure closes, never silently).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

SUBMIT_REJECTED_SCHEMA = "omac.submit-rejected/v1"
_MARKER = "OMAC-SUBMIT-REJECTED "

FAILURE_CLASS_METADATA_BUDGET = "platform-metadata-budget"
REASON_REVIEWER_SUBMIT_REJECTED = "reviewer-submit-rejected-by-platform"


@dataclass(frozen=True)
class SubmitRejection:
    failure_class: str
    phase: str
    subject_digest: str
    verdict: str = ""
    detail: str = ""
    created_at: Optional[str] = None   # platform comment time, filled by the adapter
    comment_id: Optional[str] = None


def format_submit_rejection(rejection: SubmitRejection) -> str:
    payload = {
        "schema": SUBMIT_REJECTED_SCHEMA,
        "failure_class": rejection.failure_class,
        "phase": rejection.phase,
        "subject_digest": rejection.subject_digest,
        "verdict": rejection.verdict,
        "detail": rejection.detail[:500],
    }
    return (
        "## omac submit rejected\n"
        "The platform rejected this submission; the OMAC loop will stop the stage and report it.\n\n"
        + _MARKER + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def parse_submit_rejection(text: object) -> Optional[SubmitRejection]:
    """The rejection recorded in a comment body, or None when the text carries none (or a malformed one)."""
    if not isinstance(text, str) or _MARKER not in text:
        return None
    for line in text.splitlines():
        if not line.startswith(_MARKER):
            continue
        try:
            raw = json.loads(line[len(_MARKER):])
        except ValueError:
            return None
        if not isinstance(raw, dict) or raw.get("schema") != SUBMIT_REJECTED_SCHEMA:
            return None
        fields = {k: raw.get(k) for k in ("failure_class", "phase", "subject_digest", "verdict", "detail")}
        if not all(isinstance(v, str) for v in fields.values()) or not fields["failure_class"]:
            return None
        return SubmitRejection(**fields)
    return None
