"""The final-acceptance outer loop (`omac dag run` after the DAG converged) against a real Multica with scripted agents.

alice delivers every node, charlie is the acceptor (one scenario attempt per round), bob is the orchestrator who answers a
failed round with an incremental manifest. Round results and increments are files the scripted agents submit with the real
`omac work submit`.
"""
import json

import pytest
import yaml

from _support.it_project import contract
from _support.taskkit import submit_step
from omac.core.manifest import load_manifest
from omac.core.taskmeta import TaskKind


def acceptance_doc(*flow_ids):
    return yaml.safe_dump({"flows": [{"id": fid, "name": fid.title(), "actions": [
        {"step": f"open {fid} {i}", "how": f"click {fid} {i}", "expected": f"{fid} shows result {i}"} for i in range(2)]}
        for fid in flow_ids]})


def results_file(project, name, **statuses):
    path = project.work / f"{name}.json"
    path.write_text(json.dumps([{"id": fid, "status": status, "evidence": "walked through", **({"notes": f"{fid} broken"} if status == "fail" else {})}
                                for fid, status in statuses.items()]))
    return str(path)


def increment_file(project, name, *fixes):
    nodes = [{"id": fix, "worker": "alice", "blocked_by": ["build"], "title": fix, "description": f"Fix {fix}.",
              "contract": contract(scope_paths=[f"src/{fix}/**"], evidence_mode="fixture", produces=[], consumes=[],
                                   source_of_truth=["docs/a.md"])} for fix in fixes]
    path = project.work / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"meta": {"name": name}, "nodes": nodes}, sort_keys=False))
    return str(path)


def acceptance_project(project, stack, name, *, flows, rounds, increments, max_rounds=3, meta=None):
    """A converging one-node DAG plus its acceptance document; ``rounds``: results files, ``increments``: manifests for failed rounds."""
    project.configure(roles={"acceptor": "charlie", "orchestrator": "bob", "workers": ["alice", "bob"], "reviewers": ["carol"]},
                      acceptance={"max_rounds": max_rounds})
    stack.set_scenario("alice", [[stack.tool("worker-submit")]])
    stack.set_scenario("charlie", [[submit_step(stack, "--acceptance-results-file", path)] for path in rounds])
    stack.set_scenario("bob", [[submit_step(stack, "--manifest-file", path)] for path in increments] or [[]])
    manifest = project.manifest(name, [{"id": "build", "worker": "alice", "title": "build", "description": "Build it.",
                                        "contract": contract(scope_paths=["src/build/**"], evidence_mode="fixture", produces=[], consumes=[],
                                                             source_of_truth=["docs/a.md"])}])
    data = yaml.safe_load(open(manifest))
    data["meta"].update(meta or {})
    open(manifest, "w").write(yaml.safe_dump(data, sort_keys=False))
    (project.work / ".omac" / f"{name}.acceptance.yaml").write_text(acceptance_doc(*flows))
    return manifest


def final_acceptance_items(store, workspace):
    return [item for item in store.list_work_items(workspace) if item.kind == TaskKind.FINAL_ACCEPTANCE]


def project_store(stack):
    from omac.engines import create_engine
    from omac.engines.models import EngineConfig

    return create_engine("multica", EngineConfig(engine_type="multica", workspace_id=stack.workspace_id, project_id=stack.project_id)).store


def run_dag(project, manifest, minutes=3):
    done = project.omac("dag", "run", manifest, "--max-minutes", str(minutes), "--output", "json", timeout=minutes * 60 + 60)
    return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}), done


def test_two_failed_flows_get_fix_nodes_and_the_second_round_passes(project, stack, cli_env):
    first = results_file(project, "r1", login="pass", export="fail", search="fail")
    second = results_file(project, "r2", login="pass", export="pass", search="pass")
    manifest = acceptance_project(project, stack, "twofail", flows=["login", "export", "search"], rounds=[first, second],
                                  increments=[increment_file(project, "inc1", "fix-export", "fix-search")])

    code, data, done = run_dag(project, manifest)

    assert code == 0, done.stdout + done.stderr
    reloaded = load_manifest(manifest)                                          # the increment is persisted and resumable
    assert {"build", "fix-export", "fix-search"} <= set(reloaded.nodes)
    assert reloaded.nodes["fix-export"].blocked_by == ["build"] and reloaded.nodes["fix-search"].blocked_by == ["build"]
    assert all(reloaded.nodes[key].status == "done" for key in ("build", "fix-export", "fix-search"))
    store = project_store(stack)
    assert len([i for i in final_acceptance_items(store, stack.workspace_id) if i.dag_key.endswith(("-r1", "-r2"))]) >= 2
    decompose = next(i for i in store.list_work_items(stack.workspace_id) if i.kind == TaskKind.DECOMPOSE and i.dag_key == "decompose-r1")
    assert decompose.contract["mode"] == "incremental" and decompose.contract["failed_items"] == ["export", "search"]
    assert isinstance(decompose.contract["manifest"], dict) and decompose.source_refs[-1]["label"] == "Acceptance trigger · Round 1"
    assert "export" in decompose.description and "当前 Manifest" not in decompose.description


def test_acceptance_that_keeps_failing_stops_at_max_rounds_with_the_failed_items(project, stack):
    results = [results_file(project, f"x{n}", login="pass", export="fail") for n in (1, 2)]
    manifest = acceptance_project(project, stack, "exhaust", flows=["login", "export"], rounds=results, max_rounds=2,
                                  increments=[increment_file(project, "incx1", "fix-export-r1"), increment_file(project, "incx2", "fix-export-r2")])

    code, data, done = run_dag(project, manifest)

    assert code == 20, done.stdout + done.stderr
    assert data["state"] == "needs_decision" and data["done"] == ["build", "fix-export-r1", "fix-export-r2"]
    report = data["report"]["acceptance"]
    assert report["rounds"] == 2 and report["failed_items"] == ["export"]
    assert [round_["failed_items"] for round_ in report["reports"]] == [["export"], ["export"]]
    assert {"fix-export-r1", "fix-export-r2"} <= set(load_manifest(manifest).nodes)


# ---- in process: what the acceptor and the orchestrator are given ---------------------------------------------------------

def test_a_passing_first_round_ends_the_loop_and_the_acceptor_gets_the_complete_context(kit, tmp_path):
    import time

    from omac.core.acceptance import load_acceptance_doc
    from omac.core.manifest import Contract
    from omac.pipeline.acceptance import run_acceptance_loop

    project = kit.store.create_project(kit.workspace, "demo", repo_urls=["git@github.com:owner/demo.git"])
    kit.store.config.project_id = project.id
    build, build_item = kit.done_dependency("build")
    build.contract = Contract(pr_base="main")
    closeout, closeout_item = kit.done_dependency("closeout")
    closeout.blocked_by, closeout.contract = ["build"], Contract(pr_base="main")
    manifest = kit.manifest([build, closeout], meta={"name": "Demo", "plan_id": "p-1234abcd", "closeout_node": "closeout",
                                                      "source_issues": ["plan-issue", "acceptance-issue", "decompose-issue"]})
    path = kit.save(manifest)
    doc = load_acceptance_doc(yaml.safe_load(acceptance_doc("login")))
    passing = tmp_path / "pass.json"
    passing.write_text(json.dumps([{"id": "login", "status": "pass", "evidence": "walked through"}]))
    kit.stack.set_scenario("charlie", [[submit_step(kit.stack, "--acceptance-results-file", str(passing))]])
    config = {"engine": "multica", "roles": {"acceptor": "charlie", "orchestrator": "bob", "reviewers": ["carol"]}}

    outcome = run_acceptance_loop(kit.engine, manifest, path, doc, config, poll=lambda: time.sleep(0.3))

    assert (outcome.exit_code, outcome.rounds) == (0, 1)
    item = next(i for i in final_acceptance_items(kit.store, kit.workspace) if i.dag_key == "final-acceptance-p-1234abcd-r1")
    env = f"OMAC_ENGINE=multica OMAC_WORKSPACE_ID={kit.workspace} OMAC_PROJECT_ID={project.id}"
    assert env in item.description and "PR base: `main`" in item.description
    assert f"omac work show {item.id} --output json" in item.description and "omac work submit" not in item.description
    assert "git@github.com:owner/demo.git" in item.description and "Final implementation delivery" in item.description
    assert item.contract["acceptance_doc"]["flows"][0]["id"] == "login" and item.contract["repo_urls"] == ["git@github.com:owner/demo.git"]
    assert item.source_refs[-1]["issue_id"] == closeout_item.id and "acceptance_doc:" not in item.description


def test_a_disabled_acceptance_runs_no_round_and_creates_no_issue(kit):
    from omac.core.acceptance import load_acceptance_doc
    from omac.pipeline.acceptance import run_acceptance_loop

    build, _ = kit.done_dependency("build")
    manifest = kit.manifest([build])
    created = kit.record(kit.store, "create_work_item")

    outcome = run_acceptance_loop(kit.engine, manifest, kit.save(manifest), load_acceptance_doc(yaml.safe_load(acceptance_doc("login"))),
                                  {"engine": "multica"}, no_acceptance=True)

    assert (outcome.exit_code, outcome.rounds) == (0, 0) and not created


# ---- the real `work submit` paths of the two acceptance tasks ---------------------------------------------------------------

def _authoring_issue(kit, kind, dag_key, assignee, contract=None):
    from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task

    return create_authoring_task(kit.engine, AuthoringTaskSpec(kind=kind, title=dag_key, dag_key=dag_key, assignee=assignee, contract=contract))


def _existing_manifest(project_like):
    from omac.core.manifest import Contract, Manifest, Node

    return Manifest(meta={"pr_base": "feature/v1"}, nodes={"b": Node(
        id="b", worker="bob", blocked_by=[], status="done", merged=True, merged_at="2026-07-26T08:00:00Z",
        contract=Contract(pr_base="feature/v1"))})


def _incremental_issue(kit, key):
    from omac.pipeline.acceptance import _dump_manifest

    return _authoring_issue(kit, TaskKind.DECOMPOSE, key, "bob", {
        "manifest": _dump_manifest(_existing_manifest(None)), "failed_items": ["f1"], "acceptance": ["f1"], "pr_base": "feature/v1",
        "repo_urls": [], "mode": "incremental"})


def test_an_incremental_decomposition_may_depend_on_the_nodes_it_extends(kit, tmp_path):
    from omac.pipeline import dispatch

    item = _incremental_issue(kit, "decompose-submit-ok")
    path = tmp_path / "inc.yaml"
    path.write_text(increment_for(tmp_path, "fix-b", blocked_by=["b"]))

    result = dispatch.submit(kit.store, item.id, code_host=kit.code_host, manifest_file=str(path), agent_pool={"alice", "bob"})

    from omac.core.workitem import WorkItemStatus
    updated = kit.store.get_work_item(item.id)
    assert result.kind == TaskKind.DECOMPOSE and result.advanced_to == WorkItemStatus.IN_REVIEW and updated.status == WorkItemStatus.IN_REVIEW
    assert any(node["id"] == "fix-b" for node in yaml.safe_load(updated.deliverable)["nodes"])


def test_an_incremental_decomposition_that_depends_on_an_unknown_node_is_refused_and_leaves_the_issue_alone(kit, tmp_path):
    from omac.core.workitem import WorkItemStatus
    from omac.errors import ValidationError
    from omac.pipeline import dispatch

    item = _incremental_issue(kit, "decompose-submit-bad")
    path = tmp_path / "bad.yaml"
    path.write_text(increment_for(tmp_path, "fix-b", blocked_by=["nonexistent"]))

    with pytest.raises(ValidationError, match="unknown node"):
        dispatch.submit(kit.store, item.id, code_host=kit.code_host, manifest_file=str(path), agent_pool={"alice", "bob"})

    assert kit.store.get_work_item(item.id).status == WorkItemStatus.TODO and not kit.store.get_work_item(item.id).deliverable


def increment_for(tmp_path, key, blocked_by):
    nodes = [{"id": key, "worker": "alice", "blocked_by": blocked_by, "title": key, "description": f"Fix {key}.",
              "contract": contract(scope_paths=[f"src/{key}/**"], evidence_mode="fixture", produces=[], consumes=[], source_of_truth=["docs/a.md"],
                                   pr_base="feature/v1")}]
    return yaml.safe_dump({"meta": {"name": "inc"}, "nodes": nodes}, sort_keys=False)


def test_final_acceptance_results_are_submitted_against_the_acceptance_document_in_the_contract(kit, tmp_path):
    from omac.core.workitem import WorkItemStatus
    from omac.pipeline import dispatch

    raw = yaml.safe_load(acceptance_doc("f1", "f2"))
    item = _authoring_issue(kit, TaskKind.FINAL_ACCEPTANCE, "final-acceptance-submit-ok", "charlie", {"acceptance_doc": raw})
    path = tmp_path / "results.json"
    path.write_text(json.dumps([{"id": "f1", "status": "pass", "evidence": "ok"}, {"id": "f2", "status": "pass", "evidence": "ok"}]))

    result = dispatch.submit(kit.store, item.id, code_host=kit.code_host, acceptance_results_file=str(path))

    updated = kit.store.get_work_item(item.id)
    assert result.kind == TaskKind.FINAL_ACCEPTANCE and result.advanced_to == WorkItemStatus.DONE and updated.status == WorkItemStatus.DONE
    assert [row["id"] for row in yaml.safe_load(updated.deliverable)] == ["f1", "f2"]


def test_final_acceptance_results_cannot_be_submitted_without_the_acceptance_document(kit, tmp_path):
    from omac.pipeline import dispatch

    item = _authoring_issue(kit, TaskKind.FINAL_ACCEPTANCE, "final-acceptance-no-contract", "charlie")
    path = tmp_path / "results.json"
    path.write_text(json.dumps([{"id": "f1", "status": "pass"}]))

    with pytest.raises(Exception, match="acceptance_doc"):
        dispatch.submit(kit.store, item.id, code_host=kit.code_host, acceptance_results_file=str(path))
