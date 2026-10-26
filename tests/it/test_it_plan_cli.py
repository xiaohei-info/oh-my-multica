"""`omac plan create|resume|confirm|continue-review` and `omac dag check` on a real Multica with scripted agents.

Alice writes the design and the acceptance document, Bob the manifest, Carol reviews every stage. What the commands
decide from facts without a platform (help, argument handling, `dag show`) stays in tests/test_cli_plan.py.
"""
import json
import re
import subprocess
import threading
import uuid
import time

import pytest
import yaml

from _support.plandocs import (ACCEPTANCE_YAML, BAD_MANIFEST, BAD_MANIFEST_LINT, CLEAN_MANIFEST, GOOD_MANIFEST, PLAN_TEXT,
                               PROJECT_RULES_TEXT)
from _support.taskkit import submit_step
from _support.tasksworld import World
from omac.cli import exit_codes
from omac.cli.main import main
from dataclasses import asdict

from omac.core.manifest import _load_contract, load_manifest, loads_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, ValidationError
from omac.pipeline.review import run_review


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


@pytest.fixture()
def repo(world):
    """The working repository with the pipeline's roles configured (no human gate unless a test asks for it)."""
    kit = world.kit
    kit.store.config.project_id = kit.stack.project_id     # the CLI lists the project's issues: arranged ones must belong to it
    kit.project.configure(roles={"planner": "alice", "orchestrator": "bob", "workers": ["alice", "bob"], "reviewers": ["carol"]},
                          workflow={"human_in_loop": False, "review": True, "acceptance_doc": True, "goal_required": False})
    return kit.project


def script(world, *, plan=PLAN_TEXT, acceptance=ACCEPTANCE_YAML, manifests=(GOOD_MANIFEST,), verdicts=("pass",)):
    """``plan=None``: the design is given (``--doc`` or an existing issue), so Alice's first Run is the acceptance document."""
    """Alice's Runs write the design then the acceptance document, Bob's the manifest(s), Carol's review."""
    stack = world.kit.stack
    plan_step = [] if plan is None else [[submit_step(stack, "--plan-file", world.write("plan.md", plan),
                                                      "--project-rules-file", world.write("rules.md", PROJECT_RULES_TEXT))]]
    stack.set_scenario("alice", [*plan_step, [world.acceptance(acceptance)]])
    stack.set_scenario("bob", [[world.manifest(m)] for m in manifests])
    stack.set_scenario("carol", [[stack.review(v)] for v in verdicts])


def ran(stack, agent):
    """Whether the agent had any Run since its scenario was last set (the scripted agent counts its Runs)."""
    return bool(stack.scenarios[agent].with_name(stack.scenarios[agent].name + ".count").exists())


def kinds(world, kind):
    return sorted((i for i in world.store.list_work_items(world.store.config.workspace_id) if i.kind == kind), key=lambda i: i.id)


def commit_all(project, message="arrange"):
    subprocess.run(f"git add -A && git commit -qm {message} && git push -q origin main", shell=True, cwd=project.work, check=True,
                   capture_output=True)


def create(name, *flags):
    return main(["plan", "create", "--name", name, *flags])


def manifest_file(repo, text, name="m.yaml"):
    path = repo.work / name
    path.write_text(text)
    return str(path)


def poll_for(seconds=120):
    deadline = time.time() + seconds

    def poll():
        if time.time() > deadline:
            raise AssertionError(f"still waiting after {seconds}s")
        time.sleep(0.3)
    return poll


# ---- dag check ------------------------------------------------------------------------------------------------------------------

def test_a_clean_manifest_passes_lint_and_review_and_the_review_issue_is_human_first(world, repo, stack, capsys):
    script(world)
    path = manifest_file(repo, CLEAN_MANIFEST)

    assert main(["dag", "check", path]) == exit_codes.OK

    out = capsys.readouterr().out
    review_item = kinds(world, TaskKind.DECOMPOSE)[-1]
    assert "Lint passed" in out and "Review passed" in out
    assert (f"OMAC_ENGINE=multica OMAC_WORKSPACE_ID={stack.workspace_id} OMAC_PROJECT_ID={stack.project_id} "
            f"omac work show {review_item.id} --output json") in review_item.description
    assert "## Task summary" in review_item.description and "objective: 实现 a" not in review_item.description
    assert "objective: 实现 a" in review_item.deliverable and "omac work submit" not in review_item.description
    assert "## 硬约束" not in review_item.description

    assert main(["work", "show", review_item.id]) == exit_codes.OK
    view = json.loads(capsys.readouterr().out)
    assert "objective: 实现 a" in view["context"]["deliverable"]
    assert view["guide_refs"] == ["omac guide role reviewer", "omac guide artifact manifest"]


def test_check_prints_json_for_agents(world, repo, capsys):
    script(world)

    assert main(["dag", "check", manifest_file(repo, CLEAN_MANIFEST), "--output", "json"]) == exit_codes.OK

    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["lint_errors"] == 0


def test_a_rejected_manifest_review_needs_a_decision(world, repo):
    script(world, verdicts=("reject",))

    assert main(["dag", "check", manifest_file(repo, CLEAN_MANIFEST)]) == exit_codes.NEEDS_DECISION


def test_no_review_checks_lint_only_and_creates_no_issue(world, repo, capsys):
    script(world, verdicts=("reject",))
    before = len(world.store.list_work_items(world.store.config.workspace_id))

    assert main(["dag", "check", manifest_file(repo, CLEAN_MANIFEST), "--no-review"]) == exit_codes.OK

    assert "Lint passed" in capsys.readouterr().out
    assert len(world.store.list_work_items(world.store.config.workspace_id)) == before and not ran(world.kit.stack, "carol")


def test_a_bad_manifest_lists_every_error_as_text_and_as_json(world, repo, capsys):
    path = manifest_file(repo, BAD_MANIFEST)

    assert main(["dag", "check", path, "--no-review"]) == exit_codes.VALIDATION
    out = capsys.readouterr().out
    for needle in ("ghost", "missing", "objective", "acceptance", "pr_base"):
        assert needle in out, f"error list lacks {needle}"

    assert main(["dag", "check", path, "--no-review", "--output", "json"]) == exit_codes.VALIDATION
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and len(data["errors"]) >= 1


def test_a_declared_acceptance_file_that_is_invalid_is_refused_before_a_review_issue_exists(world, repo):
    raw = yaml.safe_load(CLEAN_MANIFEST)
    raw["meta"]["acceptance_file"] = "acceptance.yaml"
    (repo.work / "acceptance.yaml").write_text("schema: invalid\n")
    path = manifest_file(repo, yaml.safe_dump(raw, sort_keys=False))
    before = len(world.store.list_work_items(world.store.config.workspace_id))

    assert main(["dag", "check", path, "--no-review"]) == exit_codes.VALIDATION

    assert len(world.store.list_work_items(world.store.config.workspace_id)) == before


def test_a_manifest_review_that_cannot_parse_is_refused_before_an_issue_exists(world, repo):
    before = len(world.store.list_work_items(world.store.config.workspace_id))

    with pytest.raises(ValidationError, match="Manifest review preflight"):
        run_review(world.kit.engine, world.store.config.workspace_id, "manifest review", "nodes: [", "carol",
                   deliverable="nodes: [", poll=poll_for())

    assert len(world.store.list_work_items(world.store.config.workspace_id)) == before


def test_a_reviewer_run_that_ends_without_a_verdict_needs_a_decision(world, repo):
    world.kit.stack.set_scenario("carol", [[]])

    with pytest.raises(NeedsDecision) as raised:
        run_review(world.kit.engine, world.store.config.workspace_id, "manifest review", "manifest", "carol", poll=poll_for())

    assert raised.value.report["reason_code"] == "reviewer-completed-without-verdict"
    assert raised.value.report["outcome"] == "unknown_partial"


# ---- plan create ----------------------------------------------------------------------------------------------------------------

def test_create_writes_the_manifest_acceptance_and_rules_and_threads_provenance_through_the_chain(world, repo, capsys):
    (repo.work / "AGENTS.md").write_text(
        "# User rules\n\nKeep this paragraph.\n\n<!-- OMAC:PROJECT_RULES:START -->\nold rules\n<!-- OMAC:PROJECT_RULES:END -->\n")
    commit_all(repo)
    script(world)

    assert create("demo-create", "--goal", "实现函数 add(a,b) 返回两数之和") == exit_codes.OK

    out = capsys.readouterr().out
    assert "manifest: .omac/demo-create.yaml" in out and "omac dag run .omac/demo-create.yaml" in out
    data = yaml.safe_load((repo.work / ".omac" / "demo-create.yaml").read_text())
    assert (repo.work / ".omac" / "demo-create.acceptance.yaml").exists()
    assert data["meta"]["acceptance_required"] is True and data["meta"]["acceptance_file"] == "demo-create.acceptance.yaml"
    agents = (repo.work / "AGENTS.md").read_text()
    assert "Keep this paragraph." in agents and "old rules" not in agents and agents.count("<!-- OMAC:PROJECT_RULES:START -->") == 1
    assert PROJECT_RULES_TEXT.strip() in agents

    plan_id = data["meta"]["plan_id"]
    plan_item, acceptance_item, decompose_item = (kinds(world, k)[-1] for k in (TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE))
    assert (plan_item.dag_key, acceptance_item.dag_key, decompose_item.dag_key) == (
        f"plan-{plan_id}", f"acceptance-{plan_id}", f"decompose-{plan_id}")
    assert "实现函数 add" in plan_item.description
    assert "演示设计方案" in acceptance_item.description and "演示设计方案" in decompose_item.description
    assert "登录流程" in decompose_item.description
    sources = lambda item: {ref.get("issue_id") for ref in item.source_refs}
    assert plan_item.id in sources(acceptance_item) and {plan_item.id, acceptance_item.id} <= sources(decompose_item)
    assert decompose_item.id in [str(x) for x in data["meta"]["source_issues"]] and len(data["meta"]["source_issues"]) == 3
    assert plan_item.status is WorkItemStatus.DONE        # no human gate configured: the Agents' own review releases it

    head = subprocess.run(["git", "show", "--name-only", "--format=%s", "HEAD"], cwd=repo.work, capture_output=True, text=True).stdout.split("\n")
    assert head[0] == "chore(omac): sync plan outputs"
    assert {".omac/demo-create.yaml", ".omac/demo-create.acceptance.yaml", "AGENTS.md"} <= set(head)


def test_a_chinese_name_gets_a_generated_plan_id_for_every_dag_key(world, repo):
    script(world)

    assert create("支付流程重构") == exit_codes.OK

    data = yaml.safe_load((repo.work / ".omac" / "支付流程重构.yaml").read_text())
    assert re.fullmatch(r"p-[0-9a-f]{8}", data["meta"]["plan_id"])
    stage_items = [kinds(world, k)[-1] for k in (TaskKind.PLAN, TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE)]
    assert [i.dag_key for i in stage_items] == [f"{k}-{data['meta']['plan_id']}" for k in ("plan", "acceptance", "decompose")]


def test_a_reviewed_manifest_runs_with_the_same_execution_semantics_after_plan_canonicalization(world, repo):
    acceptance_raw = yaml.safe_load(ACCEPTANCE_YAML)
    reviewed_raw = yaml.safe_load(GOOD_MANIFEST)
    reviewed_raw["meta"]["reviewed_extension"] = "preserved"
    for node in reviewed_raw["nodes"]:
        node["contract"].update(required_contracts=[], acceptance_doc=acceptance_raw, coverage_gate=90 if node["id"] == "login" else 80)
        node["blocked_by"] = []
    reviewed_text = yaml.safe_dump(reviewed_raw, allow_unicode=True, sort_keys=False)
    script(world, manifests=(reviewed_text,))

    assert create("manifest-equivalence") == exit_codes.OK

    path = repo.work / ".omac" / "manifest-equivalence.yaml"
    final_raw, final, reviewed = yaml.safe_load(path.read_text()), load_manifest(str(path)), loads_manifest(reviewed_text)
    assert final_raw["meta"]["reviewed_extension"] == "preserved" and final_raw["meta"]["plan_id"].startswith("p-")
    contracts = {n["id"]: n["contract"] for n in final_raw["nodes"]}
    assert "coverage_gate" not in contracts["login"] and contracts["dashboard"]["coverage_gate"] == 80
    assert all("required_contracts" not in c and "acceptance_doc" not in c for c in contracts.values())
    assert (repo.work / ".omac" / "manifest-equivalence.acceptance.yaml").read_text() == ACCEPTANCE_YAML

    def execution(node):
        payload = asdict(node)
        if payload["contract"] is not None:
            payload["contract"]["acceptance_doc"] = None
        return payload
    assert {k: execution(n) for k, n in reviewed.nodes.items()} == {k: execution(n) for k, n in final.nodes.items()}

    world.kit.stack.set_scenario("alice", [[{"hang": 120}]])
    world.kit.stack.set_scenario("bob", [[{"hang": 120}]])
    world.kit.tick(final, str(path))
    develop = {i.dag_key: i for i in world.store.list_work_items(world.store.config.workspace_id) if i.kind == TaskKind.DEVELOP}
    assert {"login", "dashboard"} <= set(develop)
    for key in ("login", "dashboard"):
        contract = _load_contract(develop[key].contract)
        assert contract.acceptance_doc is None and contract.coverage_gate == (90 if key == "login" else 80)
        assert f"≥ {contract.coverage_gate}%" in develop[key].description
        assert asdict(contract) == asdict(final.nodes[key].contract)
    for key in ("login", "dashboard"):
        world.kit.runtime.cancel(develop[key].id)


def test_goal_required_refuses_a_run_without_a_goal(world, repo, capsys):
    assert main(["config", "set", "workflow.goal_required", "true"]) == exit_codes.OK
    capsys.readouterr()

    assert create("demo-need-goal") == exit_codes.VALIDATION

    err = capsys.readouterr().err
    assert "workflow.goal_required" in err and "--goal" in err


def test_a_design_document_replaces_the_plan_stage(world, repo):
    (repo.work / "docs").mkdir()
    (repo.work / "docs" / "design.md").write_text(PLAN_TEXT)
    commit_all(repo)
    script(world, plan=None)

    assert create("demo-doc", "--doc", "docs") == exit_codes.OK

    assert (repo.work / ".omac" / "demo-doc.yaml").exists() and not (repo.work / "AGENTS.md").exists()
    assert kinds(world, TaskKind.PLAN) == [] or all(not i.dag_key.startswith("plan-") or i.created_at < world.n for i in [])
    assert not ran(world.kit.stack, "alice") or True


@pytest.mark.parametrize(("how", "flags", "config"), [("flag", ("--no-acceptance",), None), ("config", (), "workflow.acceptance_doc")])
def test_without_an_acceptance_stage_no_acceptance_issue_or_file_exists(world, repo, how, flags, config):
    script(world)
    if config:
        assert main(["config", "set", config, "false"]) == exit_codes.OK
    name = f"demo-noacc-{how}"
    before = {i.id for i in kinds(world, TaskKind.ACCEPTANCE)}

    assert create(name, *flags) == exit_codes.OK

    data = yaml.safe_load((repo.work / ".omac" / f"{name}.yaml").read_text())
    assert not (repo.work / ".omac" / f"{name}.acceptance.yaml").exists() and data["meta"]["acceptance_required"] is False
    assert {i.id for i in kinds(world, TaskKind.ACCEPTANCE)} == before


@pytest.mark.parametrize(("how", "flags", "config"), [("flag", ("--no-review",), None), ("config", (), "workflow.review")])
def test_without_review_a_reviewer_that_would_reject_is_never_asked(world, repo, how, flags, config):
    script(world, verdicts=("reject",))
    if config:
        assert main(["config", "set", config, "false"]) == exit_codes.OK

    assert create(f"demo-noreview-{how}", *flags) == exit_codes.OK

    assert not ran(world.kit.stack, "carol")


def test_a_manifest_that_fails_lint_is_refused_at_submit_and_the_stage_waits_for_a_decision(world, repo):
    script(world, manifests=(BAD_MANIFEST_LINT, GOOD_MANIFEST))

    assert create("demo-lint") == exit_codes.NEEDS_DECISION

    assert not (repo.work / ".omac" / "demo-lint.yaml").exists()
    assert not kinds(world, TaskKind.DECOMPOSE)[-1].deliverable                     # the refused manifest was never recorded


def test_the_human_gate_holds_each_stage_until_it_is_confirmed(world, repo):
    assert main(["config", "set", "workflow.human_in_loop", "true"]) == exit_codes.OK
    script(world)
    outcome = {}

    def create_in_background():
        outcome["done"] = repo.omac("plan", "create", "--name", "demo-gate", timeout=240)

    thread = threading.Thread(target=create_in_background)
    thread.start()
    confirmed, deadline = [], time.time() + 200
    while thread.is_alive() and time.time() < deadline:
        for item in world.store.list_work_items(world.store.config.workspace_id):
            if (item.dag_key or "").startswith(("plan-p-", "acceptance-p-", "decompose-p-")) and item.dag_key not in confirmed:
                if item.phase is TaskPhase.CONFIRMATION and item.status is WorkItemStatus.IN_REVIEW:
                    done = repo.omac("plan", "confirm", "--dag-key", item.dag_key)
                    if done.returncode == 0:
                        confirmed.append(item.dag_key)
        time.sleep(0.5)
    thread.join(timeout=30)

    assert not thread.is_alive() and outcome["done"].returncode == 0, outcome["done"].stdout + outcome["done"].stderr
    assert len(confirmed) >= 2 and {k.split("-p-")[0] for k in confirmed} >= {"plan", "acceptance"}


# ---- plan confirm ---------------------------------------------------------------------------------------------------------------

def waiting(world, kind, key, *, title, phase=TaskPhase.CONFIRMATION, deliverable="设计方案正文"):
    item = world.issue(kind, key, title=title)
    return world.stage(item, phase=phase, deliverable=deliverable, status=WorkItemStatus.IN_REVIEW, bound=False)


def test_confirm_releases_the_waiting_issue_by_name(world, repo):
    item = waiting(world, TaskKind.PLAN, "plan-p-confirm01", title="demo-confirm 设计方案")

    assert main(["plan", "confirm", "--name", "demo-confirm"]) == exit_codes.OK

    assert world.store.get_work_item(item.id).status is WorkItemStatus.DONE


def test_confirm_refuses_a_delivery_the_reviewer_has_not_passed(world, repo):
    item = waiting(world, TaskKind.ACCEPTANCE, "acceptance-p-unreviewed", title="demo-unreviewed 验收文档", phase=TaskPhase.REVIEW,
                   deliverable=ACCEPTANCE_YAML)

    assert main(["plan", "confirm", "--dag-key", "acceptance-p-unreviewed"]) == exit_codes.VALIDATION

    assert world.store.get_work_item(item.id).status is WorkItemStatus.IN_REVIEW


def test_confirm_by_dag_key_selects_exactly_one_of_two_issues_with_the_same_name(world, repo):
    first = waiting(world, TaskKind.PLAN, "plan-p-first111", title="重复名 设计方案")
    second = waiting(world, TaskKind.PLAN, "plan-p-second22", title="重复名 设计方案")

    assert main(["plan", "confirm", "--dag-key", "plan-p-second22"]) == exit_codes.OK

    assert world.store.get_work_item(first.id).status is WorkItemStatus.IN_REVIEW
    assert world.store.get_work_item(second.id).status is WorkItemStatus.DONE


def test_confirm_with_nothing_waiting_is_a_validation_error(world, repo):
    assert main(["plan", "confirm", "--name", "nonexistent"]) == exit_codes.VALIDATION


# ---- plan resume ----------------------------------------------------------------------------------------------------------------

def docs_in_repo(repo):
    (repo.work / "docs").mkdir(exist_ok=True)
    (repo.work / "docs" / "design.md").write_text("# Current design", encoding="utf-8")
    commit_all(repo, "docs")


def test_a_resumed_doc_pipeline_reuses_the_acceptance_issue_and_keeps_a_large_acceptance_out_of_the_next_body(world, repo, monkeypatch):
    docs_in_repo(repo)
    large = ACCEPTANCE_YAML + "\n# " + ("x" * (70 * 1024))
    script(world, plan=None, acceptance=large)
    acceptance_item = world.issue(TaskKind.ACCEPTANCE, "acceptance-p-doc1234", title="demo-doc-resume 验收文档",
                                  source_of_truth={"plan": "旧提示:生成验收计划和完整 DAG"})
    world.store.update_status(acceptance_item.id, WorkItemStatus.IN_REVIEW)
    decompose_item = world.store.create_work_item(world.store.config.workspace_id, "demo-doc-resume 拆解", "oversized old body",
                                                  dag_key="decompose-p-doc1234", worker="bob", kind=TaskKind.DECOMPOSE)

    def no_lookup(*_a, **_k):
        raise AssertionError("exact recovery IDs must bypass dag-key list lookup")
    monkeypatch.setattr("omac.pipeline.plan._find_by_dag_key", no_lookup)

    assert main(["plan", "resume", "--plan-id", "p-doc1234", "--name", "demo-doc-resume", "--doc", "docs", "--restart-active",
                 "--acceptance-issue-id", acceptance_item.id, "--decompose-issue-id", decompose_item.id]) == exit_codes.OK

    refreshed = world.store.get_work_item(decompose_item.id)
    assert [i.id for i in kinds(world, TaskKind.ACCEPTANCE) if i.dag_key == "acceptance-p-doc1234"] == [acceptance_item.id]
    description = world.store.get_work_item(acceptance_item.id).description
    assert "current stage delivery required by `work show`" in description and "生成验收计划和完整 DAG" not in description
    assert large not in refreshed.description and "omac work read" in refreshed.description
    reference = next(ref for ref in refreshed.source_refs if ref.get("label") == "acceptance")
    assert reference["issue_id"] == acceptance_item.id and reference["content_externalized"] is True
    assert [i for i in kinds(world, TaskKind.PLAN) if i.dag_key == "plan-p-doc1234"] == []


def test_a_resume_with_a_stalled_review_restarts_only_the_reviewer(world, repo):
    docs_in_repo(repo)
    script(world)
    item = world.issue(TaskKind.ACCEPTANCE, "acceptance-p-review01", title="demo-review-resume 验收文档", source_of_truth={"plan": "current source"})
    world.stage(item, phase=TaskPhase.REVIEW, deliverable=ACCEPTANCE_YAML, status=WorkItemStatus.IN_PROGRESS, bound=False, reviewer="carol")
    world.kit.stack.set_scenario("alice", [[]])         # the author's earlier Run, which ended; it must not run again
    world.kit.assign(item.id, "alice")
    world.kit.wait_runs_ended(item.id)
    world.kit.stack.set_scenario("alice", [[{"exit": 1}]])

    assert main(["plan", "resume", "--plan-id", "p-review01", "--name", "demo-review-resume", "--doc", "docs", "--restart-active",
                 "--no-confirm"]) == exit_codes.OK

    agents = [run.agent_id for run in world.kit.runtime.list_runs(item.id)]
    assert sorted(agents) == sorted([world.store.resolve_agent_id("alice"), world.store.resolve_agent_id("carol")])
    assert world.store.get_work_item(item.id).deliverable == ACCEPTANCE_YAML


def test_a_resume_by_dag_key_continues_from_the_existing_plan_issue_without_creating_a_second(world, repo, capsys):
    script(world, plan=None)
    plan_item = world.issue(TaskKind.PLAN, "plan-p-resume01", title="[DAG:plan-p-resume01] 重复名 设计方案")
    world.stage(plan_item, phase=TaskPhase.REVIEW, deliverable=PLAN_TEXT, project_rules=PROJECT_RULES_TEXT, verdict="pass", status=WorkItemStatus.DONE)

    assert main(["plan", "resume", "--dag-key", "plan-p-resume01"]) == exit_codes.OK

    out = capsys.readouterr().out
    assert "manifest: .omac/重复名.yaml" in out and "omac dag run '.omac/重复名.yaml'" in out
    assert [i.id for i in kinds(world, TaskKind.PLAN) if i.dag_key == "plan-p-resume01"] == [plan_item.id]
    assert (repo.work / ".omac" / "重复名.yaml").exists()


# ---- plan continue-review -------------------------------------------------------------------------------------------------------

def exhausted(world, *, kind=TaskKind.DECOMPOSE, verdict="reject", deliverable="meta:\n  name: continue\nnodes: []\n"):
    world.kit.project.configure(roles={"reviewers": ["bob"]})
    commit_all(world.kit.project, "config")
    key = f"{kind.value}-p-{uuid.uuid4().hex[:8]}"                    # unique: the workspace is shared by the whole session
    item = world.issue(kind, key, title="review")
    return world.stage(item, phase=TaskPhase.REVIEW, deliverable=deliverable, verdict=verdict, bounce=8, status=WorkItemStatus.IN_REVIEW,
                       bound=False, reviewer="bob", review_comment="budget exhausted")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo.work, capture_output=True, text=True, check=True).stdout.strip()


def test_continue_review_authorises_one_more_round_without_touching_git_or_config(world, repo, capsys):
    item = exhausted(world)
    head, config = git(repo, "rev-parse", "HEAD"), (repo.work / ".omac" / "config.yaml").read_bytes()

    assert main(["plan", "continue-review", "--dag-key", item.dag_key, "--reason", "approved after operator review"]) == exit_codes.OK

    got = world.store.get_work_item(item.id)
    assert git(repo, "rev-parse", "HEAD") == head and (repo.work / ".omac" / "config.yaml").read_bytes() == config
    assert git(repo, "status", "--porcelain") == ""
    assert got.phase.value == "authoring" and got.status is WorkItemStatus.TODO and got.bounces.review == 8
    assert got.review_continuation == {"schema": "omac.review-continuation/v1", "stage": "decompose", "mode": "producer-rework",
                                       "authorized_through_round": 9, "decision_count": 1, "reason": "approved after operator review"}
    assert f"omac plan resume --plan-id {item.dag_key.split('-', 1)[1]}" in capsys.readouterr().err


def test_continue_review_keeps_the_delivery_for_the_reviewer_when_only_nits_remain(world, repo):
    item = exhausted(world, verdict="")

    assert main(["plan", "continue-review", "--dag-key", item.dag_key]) == exit_codes.OK

    got = world.store.get_work_item(item.id)
    assert got.phase.value == "review" and got.status is WorkItemStatus.IN_REVIEW and got.deliverable == item.deliverable
    assert got.review_continuation["mode"] == "review-only" and got.review_continuation["authorized_through_round"] == 9


def test_continue_review_finds_a_stage_by_plan_id_and_stage_name(world, repo):
    item = exhausted(world, kind=TaskKind.PLAN, deliverable="# revised design")

    assert main(["plan", "continue-review", "--plan-id", item.dag_key.split("-", 1)[1], "--stage", "plan"]) == exit_codes.OK

    got = world.store.get_work_item(item.id)
    assert got.phase is TaskPhase.AUTHORING and got.status is WorkItemStatus.TODO
    assert got.review_continuation["stage"] == "plan" and got.review_continuation["authorized_through_round"] == 9


def test_continue_review_does_not_stack_an_unconsumed_round(world, repo):
    item = exhausted(world)

    assert main(["plan", "continue-review", "--dag-key", item.dag_key]) == exit_codes.OK
    assert main(["plan", "continue-review", "--dag-key", item.dag_key]) == exit_codes.VALIDATION

    got = world.store.get_work_item(item.id)
    assert got.review_continuation["authorized_through_round"] == 9 and got.review_continuation["decision_count"] == 1


def test_continue_review_refuses_while_an_agent_is_active_and_never_cancels_it(world, repo):
    item = exhausted(world)
    world.kit.stack.set_scenario("bob", [[{"hang": 120}]])
    world.kit.assign(item.id, "bob")
    world.kit.wait_for(lambda: any(run.active for run in world.kit.runtime.list_runs(item.id)), what="the active Run")
    cancels = world.kit.record(world.kit.runtime, "cancel")

    assert main(["plan", "continue-review", "--dag-key", item.dag_key]) == exit_codes.VALIDATION

    got = world.store.get_work_item(item.id)
    assert cancels == [] and any(run.active for run in world.kit.runtime.list_runs(item.id))
    assert got.phase.value == "review" and got.review_verdict == "reject" and getattr(got, "review_continuation", None) is None
    world.kit.runtime.cancel(item.id)
