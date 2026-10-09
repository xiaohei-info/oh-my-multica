"""Explicit operator approval of a complete current assessment source.

Like owner resolutions, the canonical journal is the operator trust boundary.
A caller's capture file is never an approval and grants no native effects.
"""
import copy
import hashlib
import json
from pathlib import Path

from .owner_amendment import (
    JOURNAL, _invalid, digest, manifest_source, capture_source,
    preserved_source_input, owner_request,
)

SCHEMA = "omac.current-owner-source/v1"
CURRENT_JOURNAL = "owner_source_qualifications"


class _Capture:
    """Internal, read-only capture context; never accepted from request JSON."""

    def __init__(self, manifest):
        self.manifest = digest(manifest_source(manifest))
        self.history = copy.deepcopy(manifest.meta.get(JOURNAL, {}))
        self.history_sha256 = digest(self.history)


def historical_current_binding(manifest, context, history, *, request_sha=None,
                               recovered=False):
    """Keep the immutable historical prefix even during a new exact capture."""
    if isinstance(context, _Capture):
        if (digest(manifest_source(manifest)) != context.manifest
                or digest(manifest.meta.get(JOURNAL, {})) != context.history_sha256):
            _invalid("Current capture changed; run omac dag amend prepare-owner-source --help")
        actual = context.history
    else:
        source = approved_current_source(manifest, context, request_sha=request_sha,
                                         check_manifest=not recovered)
        actual = source["owner_history"]
    if any(key not in actual or digest(actual[key]) != value
           for key, value in history.items()):
        _invalid("Original historical owner entries changed; inspect the complete current source")


def _without_own_steps(manifest, source, source_sha, request_sha=None):
    result = copy.deepcopy(manifest)
    grants = result.meta.get(CURRENT_JOURNAL, {})
    if source_sha in grants:
        del grants[source_sha]
        if not grants and CURRENT_JOURNAL not in source["manifest"]["meta"]:
            result.meta.pop(CURRENT_JOURNAL)
    if request_sha and request_sha in result.meta.get(JOURNAL, {}):
        entry = result.meta[JOURNAL][request_sha]
        request = owner_request(entry)
        approval = entry.get("approval", {})
        if (digest(request) != request_sha
                or request.get("current_source_qualification", {}).get("source_sha256") != source_sha
                or approval.get("request_sha256") != request_sha
                or digest(approval) != entry.get("approval_sha256")):
            _invalid("Foreign current-source owner journal entry; inspect exact resolution")
        del result.meta[JOURNAL][request_sha]
    return result


def load_current_source(file):
    try:
        path = Path(file).resolve(strict=True)
        from .state_transport import DECODED_MAX
        if not path.is_file() or path.stat().st_size > DECODED_MAX:
            raise ValueError("not a regular current source file")
        source = json.loads(path.read_bytes())
        if not isinstance(source, dict) or source.get("schema") != SCHEMA:
            raise ValueError("wrong current source schema")
        return source
    except (OSError, ValueError, TypeError) as exc:
        _invalid("Cannot read complete current source: " + str(exc)
                 + "; run omac dag amend prepare-owner-source --help")


def capture_current_source(engine, manifest, manifest_path, *, blocked_nodes,
                           allowed_nodes, report_file, docs,
                           source_witness_file=None, prospective_source_file=None):
    from ..pipeline.owner_amendment import request_inputs, required_inputs
    from ..pipeline.amendment import _resolve_docs_input, _manifest_project_root
    from .owner_amendment import retry_policy, remaining_budget
    from .prospective_owner import prospective_input, qualify_prospective, GROUPS

    if bool(source_witness_file) == bool(prospective_source_file):
        _invalid("Select exactly one historical witness; run omac dag amend prepare-owner-source --help")
    blocked, allowed = sorted(set(blocked_nodes)), sorted(set(allowed_nodes))
    context = _Capture(manifest)
    historical = None
    prospective = None
    if source_witness_file:
        historical = preserved_source_input(source_witness_file)[0]
        if blocked != ["identity-local"] or allowed != ["authentication-methods", "identity-local", "ui-foundation"]:
            _invalid("Current UI qualification requires complete Identity/UI/AuthMethod selectors")
    else:
        prospective = prospective_input(prospective_source_file)[0]
        held, existing, _ = GROUPS[prospective["group"]]
        if blocked != [held] or set(allowed) != existing:
            _invalid("Current prospective qualification requires exact existing/derived selectors")
    sources = {
        key: capture_source(manifest, key, engine.store, engine.runtime,
                            held=key in blocked,
                            qualification=historical if key == "ui-foundation" else None,
                            current_source=context)
        for key in allowed
    }
    if prospective is not None:
        prospective = qualify_prospective(manifest, prospective, engine.store,
                                           engine.runtime, current_source=context)
    policy = retry_policy(manifest_path)
    raw_report = Path(report_file).read_bytes()
    return {
        "schema": SCHEMA, "scope": "assessment-only-current-source",
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest": manifest_source(manifest),
        "owner_history": copy.deepcopy(manifest.meta.get(JOURNAL, {})),
        "owner_history_inputs": {key: owner_request(entry) for key, entry in manifest.meta.get(JOURNAL, {}).items()
                                 if "request_ref" in entry},
        "blocked_nodes": blocked, "allowed_nodes": allowed,
        "historical_source": historical, "prospective_assessment": prospective,
        "sources": sources,
        "inputs": request_inputs(manifest_path, report_file, docs, blocked),
        "input_bytes": {"report": {"bytes": len(raw_report),
                                   "sha256": hashlib.sha256(raw_report).hexdigest()}},
        "input_paths": {"report": str(Path(report_file).resolve()),
                        "docs": [str(_resolve_docs_input(path, _manifest_project_root(manifest_path)))
                                 for path in docs]},
        "required_inputs": required_inputs(manifest, manifest_path),
        "retry_policy": policy,
        "remaining_budgets": {key: remaining_budget(value, policy["limits"])
                              for key, value in sources.items()},
    }


def recapture_current_source(engine, manifest, source, *, request_sha=None):
    from .manifest import load_manifest
    source_sha = digest(source)
    original = _without_own_steps(manifest, source, source_sha, request_sha)
    historical = source.get("historical_source")
    prospective = source.get("prospective_assessment")
    inputs = source["input_paths"]
    report = inputs["report"]
    docs = inputs["docs"]
    actual = capture_current_source(
        engine, original, source["manifest_path"],
        blocked_nodes=source["blocked_nodes"], allowed_nodes=source["allowed_nodes"],
        report_file=report, docs=docs,
        source_witness_file=historical["input"]["file"] if historical else None,
        prospective_source_file=prospective["input"]["file"] if prospective else None,
    )
    if digest(actual) != digest(source):
        _invalid("Complete current source/CAS changed; capture and explicitly resolve a new current source")
    # Include the persisted canonical state in the native observation sandwich.
    persisted = load_manifest(source["manifest_path"])
    if (digest(manifest_source(persisted)) != digest(manifest_source(manifest))
            or digest(persisted.meta.get(JOURNAL, {})) != digest(manifest.meta.get(JOURNAL, {}))):
        _invalid("Canonical current source changed during observation; inspect before any effect")
    return actual


def approved_current_source(manifest, qualification, *, request_sha=None,
                            check_manifest=True):
    if not isinstance(qualification, dict) or set(qualification) != {"input", "source_sha256", "approval_sha256"}:
        _invalid("Current source has no explicit operator approval; run omac dag amend resolve-owner-source --help")
    ref = qualification["input"]
    from .state_transport import DECODED_MAX
    if (not isinstance(ref, dict) or set(ref) != {"file", "bytes", "sha256"}
            or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= DECODED_MAX):
        _invalid("Complete immutable current source reference is required")
    try:
        raw = Path(ref["file"]).read_bytes()
    except (OSError, TypeError) as exc:
        _invalid("Cannot read complete current source file: " + str(exc))
    import hashlib
    if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        _invalid("Complete approved current source file bytes changed")
    try:
        source = json.loads(raw)
    except (TypeError, ValueError) as exc:
        _invalid("Complete current source JSON is malformed: " + str(exc))
    if not isinstance(source, dict) or source.get("schema") != SCHEMA:
        _invalid("Current source qualification schema is invalid")
    source_sha = digest(source)
    if source_sha != qualification["source_sha256"]:
        _invalid("Immutable current source content digest changed")
    entry = manifest.meta.get(CURRENT_JOURNAL, {}).get(source_sha)
    if (not isinstance(entry, dict) or set(entry) != {"approval", "approval_sha256", "state"}
            or entry.get("state") != "approved"
            or entry.get("approval_sha256") != qualification["approval_sha256"]
            or digest(entry.get("approval")) != entry["approval_sha256"]):
        _invalid("Current source is unapproved or its exact canonical approval changed")
    approval = entry["approval"]
    if (set(approval) != {"source_sha256", "source_input", "manifest_path", "scope", "authority", "reason"}
            or approval["source_sha256"] != source_sha
            or approval["source_input"] != ref
            or approval["manifest_path"] != source.get("manifest_path")
            or approval["scope"] != "assessment-only-current-source"
            or not isinstance(approval["authority"], str) or not approval["authority"].strip()
            or not isinstance(approval["reason"], str) or not approval["reason"].strip()):
        _invalid("Exact current source operator authority is missing or malformed")
    original = _without_own_steps(manifest, source, source_sha, request_sha)
    materialized = {key: owner_request(value) for key, value in original.meta.get(JOURNAL, {}).items()
                    if "request_ref" in value}
    if digest(materialized) != digest(source.get("owner_history_inputs", {})):
        _invalid("Complete referenced historical owner request bytes changed or were omitted")
    if digest(original.meta.get(JOURNAL, {})) != digest(source["owner_history"]):
        _invalid("Whole current owner history changed or was omitted")
    if check_manifest and digest(manifest_source(original)) != digest(source["manifest"]):
        _invalid("Whole current manifest/DONE/history source changed")
    return source
