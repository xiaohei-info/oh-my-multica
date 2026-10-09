"""An approved capture is exact authority, never a stale-source bypass."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ui_preserved_owner_assessment import captured, ALLOWED
from omac.core.manifest import load_manifest, save_manifest
from omac.core.owner_amendment import JOURNAL, digest
from omac.core.current_owner_source import CURRENT_JOURNAL
from omac.engines.models import AgentRunObservation
from omac.errors import ValidationError
from omac.pipeline.owner_amendment import (
    prepare_current_owner_source, resolve_current_owner_source,
    prepare_owner_amendment,
)


@pytest.fixture(scope="module")
def approved(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    c = captured.__wrapped__(tmp_path_factory.mktemp("current-root-authority"), patch)
    c.source_file = str(Path(c.output).with_name("current-source.json"))
    draft = prepare_current_owner_source(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=c.source_file,
        source_witness_file=c.witness,
    )
    c.source_sha = draft["source_sha256"]
    resolve_current_owner_source(
        c.engine, c.path, c.source_file, source_sha256=c.source_sha,
        authority="Explicit offline Root current source approval",
        reason="Assessment only, all original historical provenance retained",
    )
    c.manifest_bytes = Path(c.path).read_bytes()
    c.source_bytes = Path(c.source_file).read_bytes()
    c.doc_bytes = Path(c.docs[0]).read_bytes()
    c.original_items = copy.deepcopy(c.items)
    c.original_runs = copy.deepcopy(c.runs)
    yield c
    patch.undo()


@pytest.mark.parametrize("drift", [
    "unapproved", "forged-approval-file", "changed-source-file", "foreign-history",
    "foreign-source-grant", "DONE", "other-node-progress", "UI-field-type",
    "UI-active-hold", "UI-whole-tuple", "target-contract", "target-generation",
    "target-budget", "active-Run", "extra-terminal-Run", "required-bytes",
    "allowed-omission", "prospective-candidate", "approval-authority",
])
def test_current_source_drift_rejects_before_request_or_manifest_effect(approved, drift):
    c = approved
    Path(c.path).write_bytes(c.manifest_bytes)
    Path(c.source_file).write_bytes(c.source_bytes)
    Path(c.docs[0]).write_bytes(c.doc_bytes)
    c.items.clear(); c.items.update(copy.deepcopy(c.original_items))
    c.runs.clear(); c.runs.update(copy.deepcopy(c.original_runs))
    Path(c.output).unlink(missing_ok=True)
    m = load_manifest(c.path)
    identity = c.items[m.nodes["identity-local"].work_item_id]
    ui = c.items[m.nodes["ui-foundation"].work_item_id]
    allowed = list(ALLOWED)
    if drift in {"unapproved", "forged-approval-file"}:
        m.meta.pop(CURRENT_JOURNAL)
        if drift == "forged-approval-file":
            source = json.loads(c.source_bytes)
            source["approval"] = {"authority": "Root", "decision": "APPROVED"}
            Path(c.source_file).write_text(json.dumps(source))
    elif drift == "changed-source-file":
        Path(c.source_file).write_bytes(c.source_bytes + b" ")
    elif drift == "foreign-history":
        m.meta[JOURNAL]["foreign"] = {"state": "approved"}
    elif drift == "foreign-source-grant":
        m.meta[CURRENT_JOURNAL]["foreign"] = {"state": "approved"}
    elif drift == "DONE":
        next(n for n in m.nodes.values() if n.status == "done").worker = "foreign-done-worker"
    elif drift == "other-node-progress":
        node = m.nodes["system-upgrade"]
        node.status = "blocked" if node.status != "blocked" else "todo"
    elif drift == "UI-field-type":
        key = next(iter(ui.unknown_persisted_fields))
        ui.unknown_persisted_fields[key] = {"different-type": True}
    elif drift == "UI-active-hold":
        ui.decision_required = {"foreign_hold": True}
    elif drift == "UI-whole-tuple":
        ui.title += " changed"
    elif drift == "target-contract":
        identity.contract = {"changed": "contract"}
    elif drift == "target-generation":
        identity.review_generation = "foreign-generation"
    elif drift == "target-budget":
        identity.bounces.worker += 1
    elif drift in {"active-Run", "extra-terminal-Run"}:
        c.runs[identity.id].append(AgentRunObservation(
            id="foreign-run", kind="direct", status="running" if drift == "active-Run" else "completed",
        ))
    elif drift == "required-bytes":
        Path(c.docs[0]).write_bytes(c.doc_bytes + b"foreign input")
    elif drift == "allowed-omission":
        allowed.remove("ui-foundation")
    elif drift == "prospective-candidate":
        node = copy.deepcopy(m.nodes["identity-local"])
        node.id = "agent-catalog-production-commands"
        m.nodes[node.id] = node
    elif drift == "approval-authority":
        m.meta[CURRENT_JOURNAL][c.source_sha]["approval"]["authority"] = "forged Root"
    save_manifest(m, c.path)
    before = Path(c.path).read_bytes()
    with pytest.raises(ValidationError):
        prepare_owner_amendment(
            c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=allowed,
            report_file=c.report, docs=c.docs, output_file=c.output,
            source_witness_file=c.witness, current_source_file=c.source_file,
        )
    assert not Path(c.output).exists()
    assert Path(c.path).read_bytes() == before
