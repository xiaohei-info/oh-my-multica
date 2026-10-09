"""Qualify retained historical identity without rewriting the affected handoff."""
import copy
import hashlib
import json
from pathlib import Path

from .owner_amendment import JOURNAL, digest, manifest_source, owner_request
from ..errors import ValidationError

SCHEMA = "omac.historical-review-source/v1"
JOURNAL_KEY = "historical_review_source_qualifications"
DOCUMENTS = {
    "original-report.yaml": (16538, "ad632e6c2d92486ff50c635e815fb209def077df710d9a2403758450deb720e5"),
    "original-ledger.yaml": (13529, "09ded9238195407899585f9cb7e043e48ac5d9908d139e0c313ae391424f56e0"),
    "report.yaml": (30573, "03b60b266b1d29a19d72981321108c5b69b282e109130aa15f123abb395872e3"),
    "ledger.yaml": (50534, "df834e42602fb86576ad4929e61327257d9201f1df01fa5a1d271842500dc002"),
    "original-download-proof.json": (3097, "d7a5e842dffba0126690e91a0b4785b2c5e010044130978dae84020dff9317dd"),
    "before.json": (75116, "5cdfc30be37adb22c3be7a9fc14c18d76e7e794958bafcca79c9333ab46aed51"),
    "after.json": (73600, "40d3f126b0f969fd99f5a16fe5974ece44a7dcdd4c1671822c6bbacea8027e69"),
    "retry-intent.json": (808, "3f385a47cf75ea82c621c9595821d8098c6d2031a4831441ef4f985b3843d92f"),
    "retry-stdout.json": (172, "04a2dab900c7a63384a1578c286c8301d890a34e4ad32c78a7af0436cc968d96"),
    "retry-stderr.log": (157, "40d79ea2dfe4a732fb7acef2cd674905c70b85e64caea4eb5c00fe6c084f3c7d"),
    "retry-authority.json": (1295, "3660240944e62ee7fd6f07654450811cad80ab0cbc8c659c1252966fc2915e05"),
    "native-attribution.json": (1903, "389cde6f94095365dcac7860b89a1563b3293ad0d4492551459d685dde95304e"),
}


def _invalid(message):
    raise ValidationError(message + "; inspect omac dag amend prepare-review-source --help")


def read_witness(file):
    path = Path(file).resolve()
    if not path.is_file() or path.stat().st_size > 65536:
        _invalid("Historical source witness must be a bounded regular file")
    raw = path.read_bytes()
    if len(raw) > 65536:
        _invalid("Historical source witness exceeds its bound")
    try:
        value = json.loads(raw)
        if (set(value) != {"schema", "references"}
                or value["schema"] != "omac.historical-review-source-witness/v1"
                or set(value["references"]) != set(DOCUMENTS)):
            _invalid("Complete original causal witness is required")
        refs, bodies = {}, {}
        for key, (size, sha) in DOCUMENTS.items():
            ref = value["references"][key]
            if set(ref) != {"file", "bytes", "sha256"} or ref["bytes"] != size or ref["sha256"] != sha:
                _invalid("Original full before/after retry or native provenance changed")
            source = (path.parent / ref["file"]).resolve()
            data = source.read_bytes()
            if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                _invalid("Complete original historical source bytes changed")
            refs[key] = {**ref, "file": str(source)}
            bodies[key] = data
        return {"input": {"file": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                "references": refs}, bodies
    except (KeyError, TypeError, ValueError) as exc:
        _invalid("Malformed historical source witness: " + str(exc))


class _HistoricalCapture:
    def __init__(self, witness):
        self.witness = witness


def original_subject(item, result, witness_file):
    witness, bodies = read_witness(witness_file)
    before, after = (json.loads(bodies[k]) for k in ("before.json", "after.json"))
    prior, retry = before["worker_handoff"], after["worker_handoff"]
    intent = item.worker_handoff
    feedback = prior["source_review_feedback"]
    if (item.id != before["id"] or item.id != after["id"] or item.dag_key != "system-upgrade"
            or intent.source_review_feedback != feedback or feedback != retry["source_review_feedback"]
            or intent.source_review_subject_digest != retry["source_review_subject_digest"]
            or prior["source_review_subject_digest"] == retry["source_review_subject_digest"]
            or intent.source_review_round != prior["source_review_round"]
            or intent.source_review_round != retry["source_review_round"]
            or intent.source_review_verdict != "reject"
            or intent.review_context_binding["contract_sha256"] != prior["review_context_binding"]["contract_sha256"]
            or retry["review_context_binding"]["contract_sha256"] != prior["review_context_binding"]["contract_sha256"]):
        _invalid("Current retained rejection does not match the complete original retry chain")
    native = json.loads(bodies["native-attribution.json"])["rows"]
    for row in native:
        observed = result["failed"][row["kind"]]
        if row["ref"] != observed["ref"] or any(observed["native"].get(k) != v
                                                  for k, v in row["native_primary_metadata"].items()):
            _invalid("Original native uploader/task/time or exact attachment identity changed")
        matches = [r for r in result["runs"] if r["id"] == observed["native"]["task_id"]]
        if (len(matches) != 1 or matches[0]["agent_id"] != observed["native"]["uploader_id"]
                or matches[0]["kind"] != "direct" or matches[0]["status"] != "completed"
                or matches[0]["trigger_kind"] not in {"issue_assignment", "rerun"}
                or not matches[0].get("created_at") or not matches[0].get("updated_at")
                or not (matches[0]["created_at"] <= observed["native"]["created_at"] <= matches[0]["updated_at"])):
            _invalid("Historical native attachment is outside its independent Reviewer Run")
    return prior["source_review_subject_digest"], witness


def qualify_subject(manifest, node_id, item, result, *, request_sha=None, capture=None):
    if isinstance(capture, _HistoricalCapture):
        subject, _ = original_subject(item, result, capture.witness)
        return subject, None
    record = manifest.meta.get(JOURNAL_KEY, {}).get(node_id)
    if not isinstance(record, dict) or record.get("state") != "approved":
        _invalid("Historical review source mismatch has no exact current Root qualification")
    approval = record.get("approval", {})
    if (not isinstance(approval, dict) or set(approval) != {"source_sha256", "source_input", "scope", "authority", "reason"}
            or approval.get("scope") != "historical-source-qualification-only"
            or record.get("approval_sha256") != digest(approval) or not approval.get("authority") or not approval.get("reason")):
        _invalid("Historical source approval is malformed")
    ref = approval["source_input"]
    raw = Path(ref["file"]).read_bytes()
    if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        _invalid("Approved historical source file bytes changed")
    source = json.loads(raw)
    if source.get("schema") != SCHEMA or digest(source) != approval["source_sha256"]:
        _invalid("Historical source logical identity changed")
    subject, witness = original_subject(item, result, source["witness"]["input"]["file"])
    expected = copy.deepcopy(result)
    expected["subject_digest"] = subject
    if digest(expected) != digest(source["source"]) or digest(witness) != digest(source["witness"]):
        _invalid("Complete current historical source control/Run/budget/evidence changed")
    normalized = copy.deepcopy(manifest)
    del normalized.meta[JOURNAL_KEY][node_id]
    if not normalized.meta[JOURNAL_KEY] and JOURNAL_KEY not in source["manifest"]["meta"]:
        normalized.meta.pop(JOURNAL_KEY)
    history = normalized.meta.get(JOURNAL, {})
    qualification = {"source_sha256": approval["source_sha256"], "approval_sha256": record["approval_sha256"], "input": ref}
    if request_sha in history:
        own = history[request_sha]
        if (owner_request(own).get("sources", {}).get(node_id, {}).get("historical_identity_qualification") != qualification
                or digest(owner_request(own)) != request_sha
                or own.get("approval", {}).get("request_sha256") != request_sha
                or digest(own.get("approval")) != own.get("approval_sha256")):
            _invalid("Foreign owner request cannot be removed from historical source CAS")
        history.pop(request_sha)
    materialized = {key: owner_request(entry) for key, entry in history.items() if "request_ref" in entry}
    if digest(materialized) != digest(source.get("owner_history_inputs", {})):
        _invalid("Complete referenced historical owner request bytes changed")
    if digest(manifest_source(normalized)) != digest(source["manifest"]) or digest(history) != digest(source["owner_history"]):
        _invalid("Complete historical source manifest/DONE/owner history changed")
    from ..pipeline.owner_amendment import required_inputs
    from .owner_amendment import retry_policy
    if (required_inputs(normalized, source["manifest_path"]) != source["required_inputs"]
            or retry_policy(source["manifest_path"]) != source["retry_policy"]):
        _invalid("Historical source required inputs or configured budgets changed")
    return subject, qualification
