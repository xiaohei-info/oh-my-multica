"""Exact captured stale authorization; no product or live OAC verdict."""
import json
import copy
import shutil
from contextlib import nullcontext
from dataclasses import fields
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omac.core.manifest import load_manifest, save_manifest
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, parse_worker_handoff, parse_reviewer_run_baseline, parse_delivery_identity
from omac.engines.models import WorkItem, WorkItemStatus, AgentRunObservation, VerificationAttachmentObservation, PullRequestReadiness
from omac.errors import ValidationError
from omac.pipeline import evidence_handoff as handoff

FIXTURES = Path(__file__).parent / "fixtures/unconsumed_evidence_retirement"
TOKEN = "33d055c04a9f98cd61460ad4ffeba449513381cfbc63340827c5fc70338e9576"
OLD = Path(__file__).parent / "fixtures/owner_amendment_admission"


@pytest.fixture
def captured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(OLD / "required-contracts", tmp_path, dirs_exist_ok=True)
    (tmp_path / ".omac").mkdir()
    shutil.copyfile(OLD / "open-agent-cluster.acceptance.yaml", tmp_path / ".omac/open-agent-cluster.acceptance.yaml")
    request = json.loads((FIXTURES / "old-request.json").read_bytes())["request"]
    canonical = request["tuple"]["manifest_path"]
    disk = tmp_path / ".omac/dag.yaml"
    shutil.copyfile(FIXTURES / "current-manifest.yaml", disk)
    manifest = load_manifest(str(disk))
    items, runs = {}, {}
    for name in ["api-model.json", "fixture.json"]:
        cap = json.loads((FIXTURES / name).read_bytes())
        d = cap["control"]
        values = {f.name: copy.deepcopy(d[f.name]) for f in fields(WorkItem) if f.name in d}
        values.update(status=WorkItemStatus(d["status"]), kind=TaskKind(d["kind"]), phase=TaskPhase(d["phase"]), bounces=Bounces(**d["bounces"]), worker_handoff=parse_worker_handoff(d.get("worker_handoff")), reviewer_run_baseline=parse_reviewer_run_baseline(d.get("reviewer_run_baseline")), delivery_identity=parse_delivery_identity(d.get("delivery_identity")))
        item = WorkItem(**values)
        items[item.id] = item
        runs[item.id] = [AgentRunObservation(**r) for r in cap["runs"]]
    value = request["tuple"]
    native = {value[k]["attachment_id"]: value[k] for k in ["original_native", "current_native", "report_native", "ledger_native"]}

    def attachment(item_id, ref):
        d = native[ref["attachment_id"]]
        return VerificationAttachmentObservation(**{**d, "content": d["content"].encode()})

    artifacts = {value[k]["url"]: value[k]["content"].encode() for k in ["original_publication", "current_publication"]}
    for row in json.loads((FIXTURES / "payload-map.json").read_bytes())["rows"]:
        url = "https://github.com/xiaohei-info/open-agent-cluster/blob/" + row["commit"] + "/" + row["path"]
        artifacts[url] = (FIXTURES / "payloads" / row["commit"] / row["path"]).read_bytes()
    worker_run = next(r for r in runs[value["issue_id"]] if r.id == native[value["current_native"]["attachment_id"]]["task_id"])
    reviewer_run = next(r for r in runs[value["issue_id"]] if r.id == value["report_native"]["task_id"])
    node = manifest.nodes[value["node_id"]]
    agent_ids = {node.worker: worker_run.agent_id, node.reviewer: reviewer_run.agent_id}
    store = SimpleNamespace(config=SimpleNamespace(workspace_id="410ade5e-8ae0-4402-b975-813dea2ff3e1"), get_work_item=lambda key: items[key], resolve_agent_id=lambda name: agent_ids[name], observe_verification_attachment=attachment, read_immutable_artifact=lambda url: artifacts[url], read_pull_request_readiness=lambda url: PullRequestReadiness(False, "OPEN", value["head_sha"]))
    runtime = SimpleNamespace(list_runs=lambda key: copy.deepcopy(runs[key]))
    actual_load, actual_save = load_manifest, save_manifest
    monkeypatch.setattr(handoff, "load_manifest", lambda path: actual_load(str(disk)) if str(path) == canonical else actual_load(path))
    monkeypatch.setattr(handoff, "save_manifest", lambda m, path: actual_save(m, str(disk)) if str(path) == canonical else actual_save(m, path))
    file_inputs = {x["path"]: x for x in [value["source_file"], *value["history_files"]]}
    actual_file = handoff._file
    monkeypatch.setattr(handoff, "_file", lambda path: copy.deepcopy(file_inputs[path]) if path in file_inputs else actual_file(path))
    import omac.pipeline.owner_amendment as owner
    actual_required = owner.required_inputs

    def required(m, path):
        result = actual_required(m, str(disk))
        result["manifest_path"] = canonical
        return result

    monkeypatch.setattr(owner, "required_inputs", required)
    config = yaml.safe_load((FIXTURES / "config.yaml").read_text())
    assert handoff._digest(config) == value["config_sha256"]
    return SimpleNamespace(store=store, runtime=runtime, manifest=manifest, path=canonical, disk=disk, key=value["node_id"], old=request, config=config, items=items, runs=runs, artifacts=artifacts)


def test_existing_full_source_guard_still_rejects_actual_projection_change():
    request = json.loads((FIXTURES / "old-request.json").read_bytes())["request"]
    current = load_manifest(str(FIXTURES / "current-manifest.yaml"))
    with pytest.raises(ValidationError, match="Full manifest/DONE/history"):
        handoff._current_source(current, TOKEN, request)
    assert not current.meta.get(handoff.JOURNAL, {}).get(TOKEN)
    assert current.meta[handoff.RESOLUTIONS][TOKEN]["request"] == request


def test_public_retirement_preparation_is_required_for_authentic_stale_case(captured):
    c = captured
    before = copy.deepcopy(c.manifest)
    result = handoff.prepare_evidence_retirement(
        c.store, c.runtime, c.manifest, c.path, c.key,
        old_request_sha256=TOKEN, config=c.config,
    )
    assert result["schema"] == "omac.unconsumed-evidence-resolution-retirement/v1"
    assert result["old_request_sha256"] == TOKEN
    assert c.manifest == before == load_manifest(str(c.disk))


def prepare(c):
    return handoff.prepare_evidence_retirement(c.store, c.runtime, handoff.load_manifest(c.path), c.path, c.key, old_request_sha256=TOKEN, config=c.config)


def resolve(c, request, **kwargs):
    return handoff.resolve_evidence_retirement(c.store, c.runtime, c.path, c.key, request, request_sha256=handoff._digest(request), authority="offline Root exact retirement", reason="Preserve unused authorization; Source changed by canonical reconcile", config=c.config, **kwargs)


def test_append_only_retirement_then_fresh_public_continuation(captured, monkeypatch):
    c = captured
    original = copy.deepcopy(c.manifest)
    request = prepare(c)
    result = resolve(c, request)
    assert result["state"] == "retired-unconsumed"
    current = handoff.load_manifest(c.path)
    assert current.nodes == original.nodes
    assert {k: current.meta[k] for k in original.meta} == original.meta
    assert len(current.meta) == 22
    assert not current.meta.get(handoff.JOURNAL, {}).get(TOKEN)
    assert handoff.retired_evidence_resolutions(current) == {TOKEN}
    with pytest.raises(ValidationError, match="Full manifest/DONE/history"):
        handoff._current_source(current, TOKEN, c.old)
    assert handoff.consume_evidence_handoff(c.store, c.runtime, current, c.path, c.key, c.config) is False
    before = c.disk.read_bytes()
    assert resolve(c, request)["observed_existing"] is True
    assert c.disk.read_bytes() == before
    fresh = handoff.prepare_evidence_handoff(c.store, c.runtime, current, c.path, c.key, rejected_source=c.old["tuple"]["source_file"]["path"], history_files=[v["path"] for v in c.old["tuple"]["history_files"]], config=c.config)
    token = handoff._digest(fresh)
    assert token != TOKEN
    approved = handoff.resolve_evidence_handoff(c.store, c.runtime, c.path, c.key, fresh, request_sha256=token, authority="offline Root fresh continuation", reason="Fresh canonical Source after explicit retirement", config=c.config)
    assert approved["state"] == "approved-for-exact-evidence-continuation"
    selected = {}

    class NoControlEffect(Exception):
        pass

    def stop_before_apply(store, runtime, path, key, request, request_token, **kw):
        selected["token"] = request_token
        selected["request"] = request
        kw["verifier"](handoff.load_manifest(path))
        raise NoControlEffect

    monkeypatch.setattr(handoff, "_apply_review_request", stop_before_apply)
    with pytest.raises(NoControlEffect):
        handoff.consume_evidence_handoff(c.store, c.runtime, handoff.load_manifest(c.path), c.path, c.key, c.config)
    assert selected["token"] == token
    assert selected["request"] == fresh
    assert handoff.load_manifest(c.path).meta[handoff.RESOLUTIONS][TOKEN] == original.meta[handoff.RESOLUTIONS][TOKEN]


def test_unknown_append_is_observed_without_second_write(captured, monkeypatch):
    c = captured
    request = prepare(c)
    real = handoff.save_manifest
    calls = []

    def write_then_lose_result(manifest, path):
        calls.append(path)
        real(manifest, path)
        raise TimeoutError("offline result lost after atomic append")

    monkeypatch.setattr(handoff, "save_manifest", write_then_lose_result)
    with pytest.raises(TimeoutError):
        resolve(c, request)
    assert resolve(c, request)["observed_existing"] is True
    assert calls == [c.path]


@pytest.mark.parametrize("change", ["done", "metadata", "marker", "old_approval", "old_request", "old_journal", "other_candidate", "model_hold", "model_assignee", "model_run", "fixture_budget", "fixture_baseline", "fixture_generation", "fixture_run", "fixture_unknown", "publication_payload", "config"])
def test_unrelated_source_or_native_drift_is_not_retirement_authority(captured, change):
    c = captured
    m = handoff.load_manifest(c.path)
    model = c.items[m.nodes["api-model"].work_item_id]
    fixture = c.items[m.nodes[c.key].work_item_id]
    if change == "done":
        next(n for k, n in m.nodes.items() if k != "api-model" and n.status == "done").description = "changed"
    elif change == "metadata":
        m.meta["operator_review_recovery"] = {}
    elif change == "marker":
        m.nodes["api-model"].recovery_marker = True
    elif change == "old_approval":
        m.meta[handoff.RESOLUTIONS][TOKEN]["approval"]["reason"] = "changed"
    elif change == "old_request":
        m.meta[handoff.RESOLUTIONS][TOKEN]["request"]["expected_control"]["bounce_baseline"] = {}
    elif change == "old_journal":
        m.meta.setdefault(handoff.JOURNAL, {})[TOKEN] = {"state": "applying", "step": 0}
    elif change == "other_candidate":
        m.meta[handoff.RESOLUTIONS]["other"] = copy.deepcopy(m.meta[handoff.RESOLUTIONS][TOKEN])
    elif change == "model_hold":
        model.decision_required = {"reason_code": "genuine-hold"}
    elif change == "model_assignee":
        model.platform_assignee_id = "assigned"
    elif change == "model_run":
        c.runs[model.id][0] = replace(c.runs[model.id][0], status="queued")
    elif change == "fixture_budget":
        fixture.bounces.worker = 0
    elif change == "fixture_baseline":
        fixture.bounce_baseline = {"worker": 12, "review": 2, "merge": 0}
    elif change == "fixture_generation":
        fixture.review_generation = "changed"
    elif change == "fixture_run":
        c.runs[fixture.id][0] = replace(c.runs[fixture.id][0], status="unknown")
    elif change == "fixture_unknown":
        fixture.unknown_persisted_fields["metadata.foreign_hold"] = True
    elif change == "publication_payload":
        url = next(k for k in c.artifacts if not k.endswith("evidence-index.json"))
        c.artifacts[url] += b"changed"
    else:
        c.config["language"] = "changed"
    handoff.save_manifest(m, c.path)
    with pytest.raises(ValidationError):
        prepare(c)
    assert handoff.RETIREMENTS not in handoff.load_manifest(c.path).meta


def test_public_cli_explicit_retirement_and_certificate_integrity(captured, monkeypatch, capsys, tmp_path):
    from omac.cli.main import main
    import omac.cli.commands.node as node
    import omac.core.manifest as manifest_module

    c = captured
    monkeypatch.setattr(node, "load_config", lambda *a, **kw: copy.deepcopy(c.config))
    monkeypatch.setattr(node, "_build_engine", lambda config: SimpleNamespace(store=c.store, runtime=c.runtime))
    monkeypatch.setattr(node, "_load_or_raise", lambda path: handoff.load_manifest(path))
    monkeypatch.setattr(manifest_module, "manifest_write_lock", lambda path: nullcontext())
    args = ["node", "continue-evidence", c.path, c.key]
    assert main([*args, "--prepare-retirement", TOKEN]) == 0
    prepared = json.loads(capsys.readouterr().out)
    file = tmp_path / "retirement.json"
    file.write_text(json.dumps(prepared))
    assert main([*args, "--retire-request", str(file), "--request-sha256", prepared["request_sha256"], "--authority", "offline Root CLI", "--reason", "explicit Source-bound retirement"]) == 0
    capsys.readouterr()
    current = handoff.load_manifest(c.path)
    assert handoff.retired_evidence_resolutions(current) == {TOKEN}
    assert current.meta[handoff.RESOLUTIONS] == c.manifest.meta[handoff.RESOLUTIONS]
    for change in ["state", "approval", "request", "old_entry", "old_journal", "marker_native", "fixture_native"]:
        modified = copy.deepcopy(current)
        cert = modified.meta[handoff.RETIREMENTS][TOKEN]
        if change == "state":
            cert["state"] = "consumed"
        elif change == "approval":
            cert["approval"]["reason"] = "forged"
        elif change == "request":
            cert["request"]["current_manifest_source_sha256"] = "0" * 64
        elif change == "old_entry":
            modified.meta[handoff.RESOLUTIONS][TOKEN]["approval"]["reason"] = "changed"
        elif change == "old_journal":
            modified.meta.setdefault(handoff.JOURNAL, {})[TOKEN] = {"state": "consumed"}
        elif change == "marker_native":
            cert["request"]["marker_native"] = ["full_control", "runs"]
        else:
            cert["request"]["fixture_native"]["budget"]["remaining"]["review"] = 20
        with pytest.raises(ValidationError):
            handoff.retired_evidence_resolutions(modified)
    # Even recomputing an approval digest cannot change the qualified native case.
    forged = copy.deepcopy(current)
    cert = forged.meta[handoff.RETIREMENTS][TOKEN]
    cert["request"]["marker_native"]["full_control"]["bounces"]["review"] = 0
    cert["approval"]["request_sha256"] = handoff._digest(cert["request"])
    cert["approval_sha256"] = handoff._digest(cert["approval"])
    with pytest.raises(ValidationError):
        handoff.retired_evidence_resolutions(forged)


def test_explicit_retirement_approval_and_final_source_cas(captured):
    c = captured
    request = prepare(c)
    before = c.disk.read_bytes()
    for authority, reason, sha in [("", "reason", handoff._digest(request)), ("Root", "", handoff._digest(request)), ("Root", "reason", "0" * 64)]:
        with pytest.raises(ValidationError):
            handoff.resolve_evidence_retirement(c.store, c.runtime, c.path, c.key, request, request_sha256=sha, authority=authority, reason=reason, config=c.config)
    assert c.disk.read_bytes() == before
    changed = handoff.load_manifest(c.path)
    changed.meta["unrelated_source_change"] = "unsupported changed Source"
    handoff.save_manifest(changed, c.path)
    with pytest.raises(ValidationError):
        resolve(c, request)
    assert handoff.RETIREMENTS not in handoff.load_manifest(c.path).meta
