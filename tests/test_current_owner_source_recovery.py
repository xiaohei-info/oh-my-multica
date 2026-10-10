"""Already reviewed current-source recovery preserves every original guard."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ui_preserved_owner_assessment import captured, ALLOWED, ui
from omac.core.manifest import load_manifest
from omac.core.stage_recovery import recovery_control_snapshot
from omac.core import owner_amendment as owner
from omac.errors import ValidationError
from omac.pipeline.owner_amendment import (
    prepare_current_owner_source, resolve_current_owner_source,
    prepare_owner_amendment, resolve_owner_amendment,
)


pytestmark = pytest.mark.integration


def test_current_root_qualified_pending_recovery_rechecks_full_history_and_opaque_source(captured, monkeypatch):
    c = captured
    source_file = str(Path(c.output).with_name("current-source.json"))
    source = prepare_current_owner_source(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=source_file,
        source_witness_file=c.witness,
    )
    resolve_current_owner_source(
        c.engine, c.path, source_file, source_sha256=source["source_sha256"],
        authority="Offline exact Root current source fixture", reason="Assessment only",
    )
    result = prepare_owner_amendment(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=c.output,
        source_witness_file=c.witness, current_source_file=source_file,
    )
    resolve_owner_amendment(
        c.engine, c.path, c.output, request_sha256=result["request_sha256"],
        authority="Offline exact assessment resolution fixture", reason="Independent Review",
    )
    manifest = load_manifest(c.path)
    entry = manifest.meta[owner.JOURNAL][result["request_sha256"]]
    entry.update(state="reviewed", issue_id="offline-review",
                 planner_source={"offline": "planner"}, review_source={"offline": "review"},
                 review_dispatches={"subject": {"item_id": "offline-review", "reviewer": "offline-reviewer"}})
    real_get = c.engine.store.get_work_item
    c.engine.store.get_work_item = lambda key: (
        SimpleNamespace(id=key, review_subject_digest="subject", review_verdict="pass")
        if key == "offline-review" else real_get(key))
    monkeypatch.setattr(owner, "planner_source", lambda *args: {"offline": "planner"})
    monkeypatch.setattr(owner, "fresh_review_source", lambda *args: {"offline": "review"})
    manifest.meta["amendment_apply"] = {
        "schema": "omac.amendment-apply/v1", "nodes": {"ui-foundation": {
            "stage": "authoring", "state": "pending", "work_item_id": ui(c).id,
            "baseline": recovery_control_snapshot(ui(c)),
            "expected_contract_sha256": owner.digest(ui(c).contract),
            "expected_review_generation": ui(c).review_generation,
            "bounce_baseline": copy.deepcopy(ui(c).bounce_baseline),
        }},
    }
    amendment = {"owner_resolution": result["request_sha256"],
                 "owner_resolution_approval": entry["approval_sha256"]}
    owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)
    manifest.meta[owner.JOURNAL]["foreign-history"] = {"state": "approved"}
    with pytest.raises(ValidationError):
        owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)
    del manifest.meta[owner.JOURNAL]["foreign-history"]
    ui(c).unknown_persisted_fields["metadata.pr_number"] = 21.0
    with pytest.raises(ValidationError):
        owner.guard_apply_resume(manifest, amendment, c.engine.store, c.engine.runtime)



def test_current_recovery_rejects_foreign_runtime_shape_before_selected_source(monkeypatch):
    from omac.core.manifest import Manifest, Node
    from omac.core import owner_amendment as owner
    from omac.core.amendment import _manifest_payload
    from types import SimpleNamespace
    original = Manifest(meta={}, nodes={"foreign": Node(id="foreign", worker="worker", blocked_by=[], status="todo")})
    request = {"manifest": _manifest_payload(original, include_runtime=True), "sources": {}, "allowed_nodes": [],
               "required_inputs": {"manifest_path": "/offline", "files": {}},
               "retry_policy": {"config_path": "/offline", "limits": {}}, "current_source_qualification": {}}
    approval = {"request_sha256": owner.digest(request), "authority": "fixture", "reason": "fixture"}
    manifest = copy.deepcopy(original)
    key = owner.digest(request)
    manifest.meta[owner.JOURNAL] = {key: {"request": request, "approval": approval, "approval_sha256": owner.digest(approval), "state": "reviewed"}}
    manifest.nodes["foreign"].status = "in_progress"
    import omac.pipeline.owner_amendment as pipeline
    import omac.core.config as config
    import omac.core.current_owner_source as current
    monkeypatch.setattr(pipeline, "required_inputs", lambda *a: request["required_inputs"])
    monkeypatch.setattr(config, "load_config", lambda *a: {})
    monkeypatch.setattr(config, "resolve_retry", lambda *a: {})
    monkeypatch.setattr(current, "approved_current_source", lambda *a, **kw: {})
    with pytest.raises(ValidationError, match="Foreign current Source runtime"):
        owner.guard_apply_resume(manifest, {"owner_resolution": key, "owner_resolution_approval": owner.digest(approval)}, None, None)
