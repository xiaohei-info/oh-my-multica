"""Acceptance v3 requirements: what the design demands, and the id-based chain requirement -> action -> node.

The model extracts the requirements from the design and writes a disposition for each; code only does set operations on
ids: ids are unique, ``action.requirement_ids`` name known in-scope requirements, every in-scope ``action`` requirement is
referenced by at least one action, and every ``technical`` requirement (no acceptance action) is claimed by a node's
``contract.requirement_refs``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

IN_SCOPE = "in-scope"
DISPOSITIONS = (IN_SCOPE, "out-of-scope", "deferred")
VERIFICATIONS = ("action", "technical")
SUMMARY_MAX = 200
_ID = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Requirement:
    id: str
    source: str
    summary: str
    disposition: str
    verification: str
    reason: str = ""

    @property
    def in_scope(self) -> bool:
        return self.disposition == IN_SCOPE


def _text(raw: dict, name: str, where: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} {name} is required (a non-empty string)")
    return value.strip()


def load_requirements(raw: Any) -> list[Requirement]:
    """Parse the ``requirements`` list of an acceptance v3 document; any malformed entry is a ValueError."""
    if not isinstance(raw, list):
        raise ValueError("acceptance requirements must be a list")
    found: list[Requirement] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"requirements[{index}] must be an object")
        requirement_id = entry.get("id")
        if not isinstance(requirement_id, str) or not _ID.fullmatch(requirement_id):
            raise ValueError(f"requirements[{index}] id must match [A-Za-z0-9._-]+, e.g. REQ-012")
        where = f"requirement {requirement_id}"
        if any(item.id == requirement_id for item in found):
            raise ValueError(f"duplicate requirement id: {requirement_id}")
        summary = _text(entry, "summary", where)
        if len(summary) > SUMMARY_MAX:
            raise ValueError(f"{where} summary must be at most {SUMMARY_MAX} characters")
        disposition, verification = entry.get("disposition"), entry.get("verification")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"{where} disposition must be one of: {', '.join(DISPOSITIONS)}")
        if verification not in VERIFICATIONS:
            raise ValueError(f"{where} verification must be one of: {', '.join(VERIFICATIONS)}")
        reason = entry.get("reason")
        if disposition != IN_SCOPE and not (isinstance(reason, str) and reason.strip()):
            raise ValueError(f"{where} reason is required when disposition is {disposition}")
        found.append(Requirement(
            id=requirement_id, source=_text(entry, "source", where), summary=summary, disposition=disposition,
            verification=verification, reason=reason.strip() if isinstance(reason, str) else ""))
    return found


def technical_ids(doc: Any) -> set[str]:
    """In-scope requirements with no acceptance action: nodes claim them with ``requirement_refs``."""
    return {r.id for r in getattr(doc, "requirements", ()) if r.in_scope and r.verification == "technical"}


def requirement_errors(doc: Any) -> list[str]:
    """Coverage of the requirements by the document's own actions (the acceptance machine gate)."""
    requirements = {r.id: r for r in getattr(doc, "requirements", ())}
    referenced: set[str] = set()
    errors: list[str] = []
    for flow in doc.flows:
        for action in flow.actions:
            for requirement_id in action.requirement_ids:
                referenced.add(requirement_id)
                requirement = requirements.get(requirement_id)
                where = f"flow {flow.id} action {action.id}: requirement_ids {requirement_id!r}"
                if requirement is None:
                    errors.append(f"{where} is not a requirement in the document's `requirements` list")
                elif not requirement.in_scope:
                    errors.append(f"{where} is {requirement.disposition}; only in-scope requirements can be referenced")
                elif requirement.verification == "technical":
                    errors.append(
                        f"{where} is verification: technical; technical requirements have no acceptance action, "
                        "nodes claim them with contract.requirement_refs")
    for requirement in requirements.values():
        if requirement.in_scope and requirement.verification == "action" and requirement.id not in referenced:
            errors.append(
                f"requirement {requirement.id} is in-scope with verification: action but no action references it; "
                f"add `requirement_ids: [{requirement.id}]` to the action that proves it, or mark it "
                "`verification: technical`")
    return errors


def technical_claim_errors(manifest: Any, doc: Any, *, require_claimed: bool) -> list[str]:
    """``requirement_refs`` ⊆ technical requirements; with ``require_claimed`` every technical requirement has a node."""
    technical = technical_ids(doc)
    claimed: set[str] = set()
    errors: list[str] = []
    for node in manifest.nodes.values():
        for ref in getattr(node.contract, "requirement_refs", None) or []:
            if not isinstance(ref, str):
                continue
            claimed.add(ref)
            if ref not in technical:
                errors.append(
                    f"node {node.id}: contract.requirement_refs {ref!r} is not an in-scope technical requirement of the "
                    "acceptance document (requirements with acceptance actions are covered through the node's "
                    "acceptance_contributions)")
    if require_claimed:
        for requirement_id in sorted(technical - claimed):
            errors.append(
                f"technical requirement {requirement_id} is claimed by no node; add it to the owning node: "
                f"`contract: {{requirement_refs: [{requirement_id}]}}`")
    return errors
