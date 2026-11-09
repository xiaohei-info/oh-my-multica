"""Upstream-defect feedback on a real Multica: the system amendment really recovers the reporter's authoring stage
(clears its ``decision_required``, back to todo), and a crash around that apply leaves exactly one fix node.
(Routing, budgets and the amendment whitelist are pure functions: tests/test_defects.py.)"""
import json
import shlex

import pytest
import yaml

from _support.loopkit import contract
from omac.core.defects import fingerprint
from omac.core.manifest import load_manifest
from omac.core.workitem import WorkItemStatus
from omac.pipeline import amendment as amendment_pipeline
from omac.pipeline import defects as shell
from omac.pipeline.dispatch import build_show_output, report_worker_blocker

DEFECT = {
    "class": "design-gap", "suspected_node": "lib", "anchors": ["FLOW-1/ACT-1"], "design_ref": "docs/design.md#4",
    "expected": "GET /items returns views", "observed": "views missing", "locations": ["lib/items.py:list_items"],
    "repro": {"command": "pytest -q tests/e2e", "exit_code": 1}}


class Case:
    """``lib`` is merged; ``api`` (after it) has reported ``lib``'s defect and is blocked, as after a real ``omac work block``."""

    def __init__(self, kit, tmp_path):
        self.kit = kit
        sources = contract(source_of_truth=["docs/design.md"])
        lib_item = kit.item("lib", status=WorkItemStatus.DONE, artifacts={"pr_url": "https://example.test/pr/lib"})
        lib = kit.node("lib", contract=sources)
        lib.status, lib.merged, lib.merged_at, lib.work_item_id = "done", True, "2026-07-26T08:00:00Z", lib_item.id
        self.manifest = kit.manifest([lib, kit.node("api", blocked_by=["lib"], reviewer="bob", contract=sources)])
        self.path = kit.save(self.manifest)
        kit.tick(self.manifest, self.path, max_parallel=1)
        self.api_id = self.manifest.nodes["api"].work_item_id
        kit.wait_for(lambda: kit.runtime.is_active(self.api_id), what="the Worker Run")
        self.report_defect(tmp_path)
        self.fp = fingerprint("lib", ["FLOW-1/ACT-1"])

    def report_defect(self, tmp_path):
        kit = self.kit
        item = kit.store.get_work_item(self.api_id)
        run = next(run for run in kit.runtime.list_runs(self.api_id) if run.kind == "direct")
        template = build_show_output(item, "worker:alice")["control"]["blocker_report_template"]
        report = {**template, "reason_code": "upstream-defect", "run_id": run.id, "defects": [DEFECT],
                  "summary": "The e2e flow needs a field lib does not return", "decision_needed": "Fix lib, then rerun this node",
                  "contract_ref": "objective",
                  "evidence": [{"ref": "artifacts/e2e.log", "observation": "e2e fails on the missing field"}]}
        file = tmp_path / "block.yaml"
        file.write_text(yaml.safe_dump(report))
        assert report_worker_blocker(kit.store, self.api_id, str(file), runtime=kit.runtime)["exit_code"] == 20
        kit.runtime.cancel(self.api_id)
        kit.wait_runs_ended(self.api_id)
        assert kit.collect(self.manifest, self.path) and self.manifest.nodes["api"].defect_pending

    def advance(self, **kwargs):
        return shell.advance(self.kit.engine, self.manifest, self.path, {}, **kwargs)

    def restart(self):
        """A new process: the manifest comes from disk, nothing else survives."""
        self.manifest = load_manifest(self.path)

    @property
    def api(self):
        return self.kit.store.get_work_item(self.api_id)

    def fix_nodes(self):
        return sorted(key for key in load_manifest(self.path).nodes if key.startswith("fix."))


@pytest.fixture()
def case(kit, tmp_path):
    return Case(kit, tmp_path)


def test_s3_a_reported_upstream_defect_becomes_a_fix_node_that_recovers_the_reporter_on_the_platform(case):
    kit = case.kit
    assert case.api.status == WorkItemStatus.BLOCKED and case.api.decision_required

    assert case.advance() is True

    on_disk = load_manifest(case.path)
    fix = case.fix_nodes()
    assert len(fix) == 1 and fix[0] in on_disk.nodes["api"].blocked_by
    assert (on_disk.nodes["api"].status, on_disk.nodes["api"].defect_pending) == ("todo", False)
    recovered = case.api
    assert recovered.status == WorkItemStatus.TODO and not recovered.decision_required        # the authoring recovery cleared the report
    assert shell.ledger_of(on_disk)["entries"][case.fp]["state"] == "fixing"

    api_runs = len(kit.runtime.list_runs(case.api_id))
    kit.tick(case.manifest, case.path, max_parallel=4)
    assert case.manifest.nodes[fix[0]].work_item_id and len(kit.runtime.list_runs(case.api_id)) == api_runs     # only the fix goes out


@pytest.mark.parametrize("crash", ["before-file", "before-apply", "mid-apply", "after-apply"])
def test_s15_a_crash_around_the_system_amendment_leaves_exactly_one_fix_node_and_a_consistent_ledger(case, monkeypatch, crash):
    kit = case.kit

    def apply_then_crash(*args, **kwargs):
        amendment_pipeline.apply_amendment(*args, **kwargs)
        raise RuntimeError("killed after the amendment was applied")

    def crash_before(*_args, **_kwargs):
        raise RuntimeError("killed before the amendment was applied")

    write_file = shell._write_yaml_atomic
    fault = None
    if crash == "before-file":
        monkeypatch.setattr(shell, "_write_yaml_atomic", crash_before)
    elif crash == "mid-apply":
        fault = kit.fault(kit.store, "restore_authoring_generation", when="before", error=RuntimeError("killed while applying"))
    with pytest.raises(RuntimeError, match="killed"):
        case.advance(apply_system={"before-apply": crash_before, "after-apply": apply_then_crash}.get(crash))
    assert shell.ledger_of(load_manifest(case.path))["applying"]                  # the intent is durable, nothing is charged yet
    monkeypatch.setattr(shell, "_write_yaml_atomic", write_file)
    if fault:
        assert fault["fired"]
        fault["armed"] = False

    case.restart()                                                                # the next `dag run` finishes or withdraws it
    shell.resume_system_amendment(kit.engine, case.manifest, case.path, {})
    case.advance()

    on_disk = load_manifest(case.path)
    ledger = shell.ledger_of(on_disk)
    fix = case.fix_nodes()
    assert len(fix) == 1 and fix[0] in on_disk.nodes["api"].blocked_by
    assert "applying" not in ledger and ledger["used_global"] == 1 and ledger["entries"][case.fp]["state"] == "fixing"
    assert (on_disk.nodes["api"].status, on_disk.nodes["api"].defect_pending) == ("todo", False)
    assert case.api.status == WorkItemStatus.TODO and not case.api.decision_required


def test_a_stopped_defect_is_closed_with_resolve_and_the_printed_retry_command_restarts_the_reporter(case):
    shell.advance(case.kit.engine, case.manifest, case.path, {"defects": {"auto_fix": "off"}})
    assert case.api.decision_required["reason_code"] == "defect-needs-owner"

    resolved = case.kit.project.omac("defect", "resolve", case.path, case.fp, "--pr", "https://example.test/pr/1")
    assert resolved.returncode == 0, resolved.stderr
    retry = json.loads(resolved.stdout)["next_actions"][0]
    words = shlex.split(retry)
    assert words[:3] == ["omac", "node", "retry"]
    assert case.kit.project.omac(*words[1:]).returncode == 0

    on_disk = load_manifest(case.path)
    assert shell.ledger_of(on_disk)["entries"][case.fp]["state"] == "fixed" and on_disk.nodes["api"].status == "todo"
    assert case.fix_nodes() == []
