"""`omac dag status` and the reports behind it, against a real Multica (the pure schema and rendering rules: tests/test_cli_dag.py)."""
import json
import traceback

import pytest
import yaml

from _support.loopkit import contract, fails
from _support.state import save_literal
from omac.core.manifest import load_manifest
from omac.core.workitem import WorkItemStatus
from omac.pipeline.loop import tick
from omac.pipeline.report import NEEDS_DECISION_KEYS, NODE_KEYS, PROGRESS_KEYS, STATUS_REPORT_KEYS, build_status_report


def write_manifest(kit, nodes, name="dag"):
    return save_literal(kit.project.work / ".omac" / f"{name}.yaml", {"meta": {"name": "test-dag"}, "nodes": nodes})


def mixed(kit):
    """a done and merged, b in progress, c not started, d blocked by a rejection, e done without an issue, f abandoned."""
    pr = f"https://example.com/pr/{kit.stack.workspace_id[:8]}-a"
    kit.pr(pr, merged=True, merged_at="2026-07-26T08:00:00Z")
    a = kit.item("a", worker="alice", artifacts={"pr_url": pr}, status=WorkItemStatus.DONE)
    b = kit.item("b", worker="bob", status=WorkItemStatus.IN_PROGRESS)
    d = kit.item("d", worker="alice", review_verdict="reject", review_comment="tests missing", status=WorkItemStatus.BLOCKED)
    path = write_manifest(kit, [
        {"id": "a", "worker": "alice", "status": "done", "work_item_id": a.id, "merged": True, "merged_at": "2026-07-26T08:00:00Z"},
        {"id": "b", "worker": "bob", "status": "in_progress", "work_item_id": b.id, "blocked_by": ["a"]},
        {"id": "c", "worker": "carol", "status": "todo"},
        {"id": "d", "worker": "alice", "status": "blocked", "work_item_id": d.id, "blocked_by": ["b"]},
        {"id": "e", "worker": "bob", "status": "done"},
        {"id": "f", "worker": "carol", "status": "abandoned"},
    ])
    return path, pr, (a, b, d)


def report_of(kit, path):
    return build_status_report(load_manifest(path), kit.store, kit.code_host, path)


def test_the_report_has_exactly_the_locked_schema(kit):
    path, *_ = mixed(kit)

    report = report_of(kit, path)

    assert set(report) == set(STATUS_REPORT_KEYS) and set(report["progress"]) == set(PROGRESS_KEYS)
    assert all(set(node) == set(NODE_KEYS) for node in report["nodes"])
    assert report["needs_decision"] is not None and set(report["needs_decision"]) == set(NEEDS_DECISION_KEYS)


def test_the_report_counts_nodes_by_their_reconciled_state_and_lists_what_needs_a_decision(kit):
    path, pr, (a, b, d) = mixed(kit)

    report = report_of(kit, path)

    progress = report["progress"]
    assert (progress["total"], progress["done"], progress["running"], progress["todo"], progress["blocked"], progress["abandoned"]) == (6, 1, 1, 1, 2, 1)
    assert progress["failed"] == 0 and progress["converged"] is False                  # e is done without an issue: blocked, not converged
    nodes = {node["key"]: node for node in report["nodes"]}
    assert (nodes["a"]["status"], nodes["a"]["work_item_id"], nodes["a"]["pr_url"], nodes["a"]["worker"]) == ("done", a.id, pr, "alice")
    assert (nodes["c"]["status"], nodes["c"]["work_item_id"], nodes["c"]["pr_url"]) == ("todo", None, None) and nodes["d"]["blocked_by"] == ["b"]
    decision = report["needs_decision"]
    assert "d" in {failed["key"] for failed in decision["failed_nodes"]} and isinstance(decision["blocked_downstream"], list)
    assert decision["next_actions"] and all(action.startswith("omac node ") for action in decision["next_actions"])


def test_reconcile_syncs_the_platform_state_into_the_manifest_and_forgets_issues_that_are_gone(kit):
    pr = f"https://example.com/pr/{kit.stack.workspace_id[:8]}-sync"
    kit.pr(pr, merged=True, merged_at="2026-07-26T08:00:00Z")
    done = kit.item("sync-a", worker="alice", artifacts={"pr_url": pr}, status=WorkItemStatus.DONE)
    path = write_manifest(kit, [
        {"id": "a", "worker": "alice", "status": "todo", "work_item_id": done.id},
        {"id": "gone", "worker": "alice", "status": "in_progress", "work_item_id": "00000000-0000-0000-0000-000000000999"},
    ])

    report_of(kit, path)

    reloaded = load_manifest(path)
    assert reloaded.nodes["a"].status == "done"
    assert reloaded.nodes["gone"].work_item_id is None and reloaded.nodes["gone"].status == "todo"


def test_a_merging_node_counts_as_running_and_done_nodes_without_an_issue_do_not_converge(kit):
    path = write_manifest(kit, [{"id": "a", "worker": "alice", "status": "merging"}])
    assert report_of(kit, path)["progress"]["running"] == 1 and report_of(kit, path)["progress"]["todo"] == 0

    path = write_manifest(kit, [{"id": "a", "worker": "alice", "status": "done"},
                                {"id": "b", "worker": "bob", "status": "done", "blocked_by": ["a"]}], name="noissue")
    report = report_of(kit, path)

    assert report["progress"]["converged"] is False
    assert {node["key"] for node in report["needs_decision"]["failed_nodes"]} == {"a", "b"}


def test_a_failed_node_puts_its_downstream_in_the_report_and_a_healthy_dag_has_no_decision(kit):
    kit.agents([fails("business validation failed")], names=("alice",))
    manifest = kit.manifest([kit.node("a", contract=contract()), kit.node("b", blocked_by=["a"], worker="bob"),
                             kit.node("c", blocked_by=["b"], worker="carol")])
    path = kit.save(manifest)
    kit.settle(manifest, path)

    decision = report_of(kit, path)["needs_decision"]

    assert decision is not None and "a" in {node["key"] for node in decision["failed_nodes"]}
    assert {"b", "c"} <= set(decision["blocked_downstream"])

    healthy = write_manifest(kit, [{"id": "a", "worker": "alice", "status": "todo"}], name="healthy")
    assert report_of(kit, healthy)["needs_decision"] is None


# ---- the command ------------------------------------------------------------------------------------------------------------

def test_dag_status_always_exits_zero_prints_a_table_or_the_locked_json(project, stack):
    manifest = project.manifest("statuscli", [{"id": "a", "worker": "alice", "status": "done"},
                                              {"id": "b", "worker": "bob", "status": "blocked", "blocked_by": ["a"]}])

    table = project.omac("dag", "status", manifest)
    as_json = project.omac("dag", "status", manifest, "--output", "json")

    assert table.returncode == 0 and as_json.returncode == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(table.stdout)
    data = json.loads(as_json.stdout)
    assert set(data) == set(STATUS_REPORT_KEYS) and data["progress"]["total"] == 2 and data["progress"]["done"] == 0 and data["progress"]["blocked"] == 2
    assert data["needs_decision"] is not None


def test_dag_status_reads_the_config_next_to_an_absolute_manifest_and_honours_flags(project, stack, tmp_path):
    manifest = project.manifest("abscli", [{"id": "a", "worker": "alice", "status": "done"}])
    outside = tmp_path / "outside"
    outside.mkdir()
    import os, subprocess, sys

    env = {**os.environ, **stack.cli_env(), **stack.omac_env()}
    from_outside = subprocess.run([sys.executable, "-m", "omac", "dag", "status", manifest, "--output", "json"], cwd=outside, env=env,
                                  capture_output=True, text=True)
    flagged = project.omac("dag", "status", manifest, "--engine", "multica", "--workspace", stack.workspace_id, "--output", "json")

    assert from_outside.returncode == 0 and json.loads(from_outside.stdout)["progress"]["done"] == 0
    assert flagged.returncode == 0 and json.loads(flagged.stdout)["progress"]["done"] == 0


def test_dag_tick_refuses_a_manifest_whose_declared_closeout_or_acceptance_file_is_missing(project, stack):
    missing_closeout = project.manifest("closeout", [{"id": "a", "worker": "alice", "status": "todo"}])
    data = yaml.safe_load(open(missing_closeout))
    data["meta"]["closeout_node"] = "closeout"
    open(missing_closeout, "w").write(yaml.safe_dump(data, sort_keys=False))
    missing_doc = project.manifest("nodoc", [{"id": "a", "worker": "alice", "status": "todo"}])
    data = yaml.safe_load(open(missing_doc))
    data["meta"].update(acceptance_required=True, acceptance_file="nodoc.acceptance.yaml")
    open(missing_doc, "w").write(yaml.safe_dump(data, sort_keys=False))

    first = project.omac("dag", "tick", missing_closeout, "--output", "json")
    second = project.omac("dag", "tick", missing_doc, "--output", "json")

    assert first.returncode == 5 and "closeout_node" in first.stderr
    assert second.returncode == 5 and "Acceptance document" in second.stderr


# ---- the needs-decision report of a tick --------------------------------------------------------------------------------------

def failed_chain(kit):
    kit.agents([fails("business validation failed")], names=("alice",))
    manifest = kit.manifest([kit.node("a", contract=contract()), kit.node("b", worker="bob", blocked_by=["a"]),
                             kit.node("c", worker="carol", blocked_by=["b"])])
    return manifest, kit.save(manifest)


def test_the_tick_report_and_the_status_report_are_the_same_document(kit):
    manifest, path = failed_chain(kit)

    result = kit.settle(manifest, path)
    status = report_of(kit, path)["needs_decision"]

    assert result.state == "needs_decision" and set(result.report) == set(NEEDS_DECISION_KEYS) and set(status) == set(NEEDS_DECISION_KEYS)
    assert sorted(n["key"] for n in result.report["failed_nodes"]) == sorted(n["key"] for n in status["failed_nodes"])
    assert result.report["blocked_downstream"] == status["blocked_downstream"] and result.report["next_actions"] == status["next_actions"]
    by_tick, by_status = ({n["key"]: n for n in doc["failed_nodes"]} for doc in (result.report, status))
    assert all(set(by_tick[key]) == set(by_status[key]) for key in by_status)
    assert "b" in result.report["blocked_downstream"] and all(a.startswith("omac node ") for a in status["next_actions"])


def test_building_the_needs_decision_report_reads_no_work_item_again(kit):
    manifest, path = failed_chain(kit)
    reads = []
    kit.before(kit.store, "get_work_item", lambda *_a, **_k: reads.append(traceback.extract_stack()))

    result = kit.settle(manifest, path)

    assert result.state == "needs_decision" and reads                             # reconcile and collection do read the platform
    assert [stack for stack in reads if any(frame.filename.endswith("report.py") for frame in stack)] == []
