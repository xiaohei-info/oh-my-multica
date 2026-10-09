"""Exact Credential compensating publication drafting, without allocation."""
import hashlib
import json
from pathlib import Path

from .owner_amendment import digest, capture_source, terminal_runs
from .prospective_owner import _invalid, _HISTORY
from .state_transport import DECODED_MAX

CANDIDATE = "credential-rotation-wire-publication"
ISSUE = "01a11f7b-d5ac-73a8-972c-7939d1458ccf"
RUN = "01a11f7d-a4b4-77b1-99ae-4212cf3c1e8a"
DOCUMENTS = {
    "decision.json": (2504, "0d87154a6071ecb6e3b9e4366d73aa0e2b83bb7219b3fdafafed88f227b25d0a"),
    "original-request.json": (62110663, "08c14296cc47a83cffc2779b7b53ee0ae8a633954b48ec1261d730277f97e9dc"),
    "scope-gap.md": (7582, "5b6c0ab94eb908245bc830dc3429713c1e8073ceff6cbe4b1ff06abb995465ee"),
    "workshow.json": (15558, "9d880d58a6378baacf88db3658110fa2621649ded01b4c4da2882c2fa2aa0e61"),
    "planner-runs.json": (3687, "19b2a13ea37a6999b521059402366d9af6bb052b6593817dbb26475af147c5dd"),
    "scope-gap-provenance.json": (550, "9b4b3b1f09a6501a7763fc387dbc1db5cd8a8a7c72dd4328579fa7766f46fd99"),
}


def credential_input(path, witness, raw):
    if set(witness.get("references", {})) != set(DOCUMENTS) | {"planner-messages.json"}:
        _invalid("Require complete Credential original Source including all147 Planner native messages")
    refs, bodies = {}, {}
    for name, ref in witness["references"].items():
        if (not isinstance(ref, dict) or set(ref) != {"file", "bytes", "sha256"}
                or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= DECODED_MAX):
            _invalid("Full Credential source reference is malformed")
        if name in DOCUMENTS and (ref["bytes"], ref["sha256"]) != DOCUMENTS[name]:
            _invalid("Exact historical Credential source bytes differ")
        source = (path.parent / ref["file"]).resolve()
        data = source.read_bytes()
        if len(data) != ref["bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            _invalid("Complete Credential original source changed")
        refs[name] = {**ref, "file": str(source)}
        bodies[name] = data
    if (refs["planner-messages.json"]["bytes"], refs["planner-messages.json"]["sha256"]) != (129259, "9de51127bdf1406a316de54b4672a4db3ac53e72655a9f0933991a986aadff1c"):
        _invalid("Complete original Credential147 native bytes differ from Root-qualified historical provenance")
    messages = json.loads(bodies["planner-messages.json"])
    if (not isinstance(messages, list) or len(messages) != 147
            or any(not isinstance(m, dict) or m.get("task_id") != RUN or m.get("issue_id") != ISSUE
                   or type(m.get("seq")) is not int for m in messages)
            or [m["seq"] for m in messages] != list(range(1, 148))):
        _invalid("Complete Credential147 original Planner messages must bind the exact original Run/issue")
    decision = json.loads(bodies["decision.json"])
    publication_sha = digest(decision["draft_declaration"])
    return {"schema": witness["schema"], "group": "credential", "references": refs,
            "declarations": {CANDIDATE: {"state": "assessment-only", "native_workitem": "not-created",
                "allocation": "not-authorized", "history": "not-observed",
                "publication_scope_sha256": publication_sha}},
            "publication_scope_sha256": publication_sha,
            "input": {"file": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}}, bodies


def qualify_credential(manifest, qualification, store, runtime, *, current_source, request_sha=None):
    from .prospective_owner import prospective_input, CANDIDATES
    from .current_owner_source import historical_current_binding
    from .amendment import _node_dict
    if current_source is None:
        _invalid("Credential prospective drafting requires separate exact current Root Source qualification")
    observed, bodies = prospective_input(qualification["input"]["file"])
    if observed != {k: v for k, v in qualification.items() if k not in {"global_held_sources", "done_producer"}}:
        _invalid("Credential prospective declarations changed")
    historical_current_binding(manifest, current_source, _HISTORY, request_sha=request_sha)
    if CANDIDATES & set(manifest.nodes):
        _invalid("A prospective candidate already exists")
    for candidate in CANDIDATES:
        if store.find_work_item_by_dag_key(store.config.workspace_id, candidate) is not None:
            _invalid("A prospective candidate already has a native identity")
    producer = manifest.nodes.get("contracts-credential")
    if producer is None or producer.status != "done" or not producer.merged:
        _invalid("Credential DONE producer context must remain immutable")
    held = {"credential-store": capture_source(manifest, "credential-store", store, runtime, held=True)}
    old = json.loads(bodies["original-request.json"])
    if held["credential-store"] != old["sources"]["credential-store"]:
        _invalid("Original held Credential source/report/ledger/Run/budget changed")
    raw_runs = json.loads(bodies["planner-runs.json"])
    from dataclasses import asdict
    from ..engines.models import AgentRunObservation
    expected = sorted([asdict(AgentRunObservation(
        id=r["id"], kind=r["kind"], status=r["status"], agent_id=r["agent_id"],
        created_at=r.get("created_at"), updated_at=r.get("updated_at") or r.get("completed_at"),
        error=r.get("error"), retry_of_run_id=r.get("retry_of_task_id") or r.get("parent_task_id"),
        trigger_kind=(r.get("attribution") or {}).get("evidence", {}).get("kind"),
    )) for r in raw_runs], key=lambda r: r["id"])
    actual = terminal_runs(runtime, ISSUE)
    if not expected or actual != expected:
        _invalid("Original complete unsubmitted Planner native Run history changed")
    messages = runtime.read_run_messages(ISSUE, RUN)
    if messages != json.loads(bodies["planner-messages.json"]):
        _invalid("Original complete unsubmitted Planner messages differ from native authority")
    result = {**observed, "global_held_sources": held,
              "done_producer": _node_dict(producer, include_runtime=True)}
    if qualification.get("global_held_sources", held) != held or qualification.get("done_producer", result["done_producer"]) != result["done_producer"]:
        _invalid("Credential held or DONE context changed")
    return result
