"""A plan pipeline released only by the commands its own stops print: every next_action is run exactly as printed.

Four releases of this loop (2.0.1-2.0.4) each fixed one stop whose printed command did not work. This walks a whole
pipeline through repeated pass-with-nits reviews, a budget authorization and the final acceptance of the nits, never
adding an argument by hand, for a pipeline made from a design directory and for one made from a request.
"""
import hashlib
import json
import shlex
import subprocess

import pytest
import yaml

from _support.plandocs import ACCEPTANCE_YAML, GOOD_MANIFEST, PLAN_TEXT, PROJECT_RULES_TEXT
from _support.taskkit import submit_step
from _support.tasksworld import World
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


@pytest.fixture()
def repo(world):
    kit = world.kit
    kit.store.config.project_id = kit.stack.project_id
    kit.project.configure(roles={"planner": "alice", "orchestrator": "bob", "workers": ["alice", "bob"], "reviewers": ["carol"]},
                          workflow={"human_in_loop": False, "review": True, "acceptance_doc": True, "goal_required": False},
                          retry={"review": 2})
    return kit.project


def commit_all(project, message):
    subprocess.run(f"git add -A && git commit -qm {message} && git push -q origin main", shell=True, cwd=project.work, check=True,
                   capture_output=True)


def printed(capsys):
    """The structured report a command printed on stdout (what a calling Agent reads)."""
    return json.loads(capsys.readouterr().out)


def run_printed(command):
    argv = shlex.split(command)
    assert argv[:2] == ["omac", "plan"], command
    return main(argv[1:])


def stage_items(world, kind, plan_id):
    return [i for i in world.store.list_work_items(world.store.config.workspace_id) if i.kind == kind and i.dag_key.endswith(plan_id)]


@pytest.mark.parametrize("source", ["doc", "goal"])
def test_every_stop_of_a_nits_loop_is_released_by_the_command_it_prints(world, repo, capsys, source):
    stack = world.kit.stack
    (repo.work / "docs").mkdir()
    (repo.work / "docs" / "design.md").write_text(PLAN_TEXT, encoding="utf-8")
    (repo.work / "docs" / "appendix-a.md").write_text("appendix", encoding="utf-8")
    commit_all(repo, "docs")
    acceptances = [[world.acceptance(f"{ACCEPTANCE_YAML}\n# revision {n}\n")] for n in range(1, 4)]
    design = [] if source == "doc" else [[submit_step(stack, "--plan-file", world.write("plan.md", PLAN_TEXT),
                                                      "--project-rules-file", world.write("rules.md", PROJECT_RULES_TEXT))]]
    stack.set_scenario("alice", [*design, *acceptances])
    stack.set_scenario("bob", [[world.manifest(GOOD_MANIFEST)]])
    stack.set_scenario("carol", [[stack.review(v)] for v in (["pass"] if design else []) + ["pass-with-nits"] * 3 + ["pass"]])
    capsys.readouterr()

    # round 1: the review passes with nits; the stage stops with the review it stands on untouched
    assert main(["plan", "create", "--name", "loop", *(["--doc", "docs"] if source == "doc" else ["--goal", "add two numbers"])]) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)
    assert stop["reason_code"] == "review-nits-acceptance-required"
    plan_id = stop["next_action"].rsplit("acceptance-", 1)[1]
    [acceptance] = stage_items(world, TaskKind.ACCEPTANCE, plan_id)
    stopped = world.store.get_work_item(acceptance.id)
    assert (stopped.review_verdict, stopped.status) == ("pass-with-nits", WorkItemStatus.BLOCKED) and stopped.bounces.review == 0
    delivered, report_ref = stopped.deliverable, stopped.review_report_ref
    assert len(world.kit.runtime.list_runs(acceptance.id)) == 2          # one author Run and one Reviewer Run: nobody was sent back

    # decline the nits inside the budget (round 1 of 2), then run the next_action as printed
    assert run_printed(stop["alternatives"][0]) == exit_codes.OK
    resume = printed(capsys)["next_action"]
    assert resume == f"omac plan resume --plan-id {plan_id}" and "--doc" not in resume
    assert run_printed(resume) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)
    assert stop["reason_code"] == "review-nits-acceptance-required" and world.store.get_work_item(acceptance.id).deliverable != delivered

    # round 2 is the last of the budget: declining authorizes one more, and the printed resume still needs no argument
    assert run_printed(stop["alternatives"][0]) == exit_codes.OK
    authorized = printed(capsys)
    assert authorized["authorized_through_round"] == 3 and authorized["next_action"] == resume
    assert run_printed(authorized["next_action"]) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)

    # round 3: accept the nits of the delivery the Reviewer saw, then finish the pipeline
    sealed = world.store.get_work_item(acceptance.id)
    assert run_printed(stop["next_action"]) == exit_codes.OK
    accepted = printed(capsys)
    assert world.store.get_work_item(acceptance.id).deliverable == sealed.deliverable
    assert run_printed(accepted["next_action"]) == exit_codes.OK

    data = yaml.safe_load((repo.work / ".omac" / "loop.yaml").read_text())
    assert data["meta"]["plan_id"] == plan_id and (repo.work / ".omac" / "loop.acceptance.yaml").exists()
    assert [len(stage_items(world, kind, plan_id)) for kind in (TaskKind.ACCEPTANCE, TaskKind.DECOMPOSE)] == [1, 1]
    origin = stage_items(world, TaskKind.PLAN, plan_id)
    assert [i.dag_key.split("-")[0] for i in origin] == ["doc" if source == "doc" else "plan"] and origin[0].id in data["meta"]["source_issues"]
    assert len(world.kit.runtime.list_runs(acceptance.id)) == 6          # three author Runs and three Reviewer Runs
    assert delivered != sealed.deliverable and report_ref is not None


# ---- the human gate: a stop like every other, released by the commands it prints ---------------------------------------------

@pytest.fixture()
def gated(world, repo):
    assert main(["config", "set", "workflow.human_in_loop", "true"]) == exit_codes.OK
    (repo.work / "docs").mkdir()
    (repo.work / "docs" / "design.md").write_text(PLAN_TEXT, encoding="utf-8")
    commit_all(repo, "docs")
    return repo


def script_doc_pipeline(world, *, acceptances=1, verdicts=1):
    stack = world.kit.stack
    stack.set_scenario("alice", [[world.acceptance(ACCEPTANCE_YAML)] for _ in range(acceptances)])
    stack.set_scenario("bob", [[world.manifest(GOOD_MANIFEST)]])
    stack.set_scenario("carol", [[stack.review("pass")] for _ in range(verdicts)])


def test_the_human_gate_stops_with_exit_20_and_is_confirmed_or_returned_by_the_commands_it_prints(world, gated, capsys):
    script_doc_pipeline(world, acceptances=2, verdicts=3)
    capsys.readouterr()

    # the acceptance document passed review: the process ends instead of waiting for a person
    assert main(["plan", "create", "--name", "gate", "--doc", "docs"]) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)
    plan_id = stop["next_action"].rsplit("acceptance-", 1)[1]
    assert stop["reason_code"] == "confirmation-required" and stop["phase"] == "confirmation"
    assert stop["alternatives"][0].endswith('--reject --reason "<what to change>"')
    assert stop["then"] == f"omac plan resume --plan-id {plan_id}"
    [acceptance] = stage_items(world, TaskKind.ACCEPTANCE, plan_id)
    gate = world.store.get_work_item(acceptance.id)
    assert (gate.status, gate.phase, gate.review_verdict) == (WorkItemStatus.IN_REVIEW, TaskPhase.CONFIRMATION, "pass")
    first = gate.deliverable

    # a person returns it with a reason: the author gets the reason, the review budget is untouched
    reject = stop["alternatives"][0].replace("<what to change>", "cover the appendix flows")
    assert run_printed(reject) == exit_codes.OK
    returned = printed(capsys)
    returned_item = world.store.get_work_item(acceptance.id)
    assert returned["next_action"] == stop["then"] and returned["mode"] == "reject-confirmation"
    assert (returned_item.status, returned_item.phase, returned_item.bounces.review) == (WorkItemStatus.TODO, TaskPhase.AUTHORING, 0)
    assert returned_item.machine_feedback["errors"] == ["cover the appendix flows"]
    assert run_printed(returned["next_action"]) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)
    assert stop["reason_code"] == "confirmation-required" and world.store.get_work_item(acceptance.id).deliverable
    assert len(world.kit.runtime.list_runs(acceptance.id)) == 4        # author and Reviewer ran once more; nobody waited

    # confirming releases it; the printed resume finishes the pipeline (the decompose stage has no gate)
    assert run_printed(stop["next_action"]) == exit_codes.OK
    assert run_printed(printed(capsys)["next_action"]) == exit_codes.OK
    assert (gated.work / ".omac" / "gate.yaml").exists() and first


def test_a_replaced_design_source_reaches_the_author_and_the_reviewer_even_when_the_delivery_comes_back_unchanged(world, gated, capsys):
    script_doc_pipeline(world, acceptances=2, verdicts=2)
    capsys.readouterr()
    assert main(["plan", "create", "--name", "reissue", "--doc", "docs"]) == exit_codes.NEEDS_DECISION
    stop = printed(capsys)
    plan_id = stop["next_action"].rsplit("acceptance-", 1)[1]
    [acceptance] = stage_items(world, TaskKind.ACCEPTANCE, plan_id)
    before = world.store.get_work_item(acceptance.id)

    # the design grows by a file; the same pipeline is resumed with the new source and the author hands back the same text
    (gated.work / "docs" / "appendix-b.md").write_text("new flows", encoding="utf-8")
    commit_all(gated, "appendix")
    assert main(["plan", "resume", "--plan-id", plan_id, "--doc", "docs"]) == exit_codes.NEEDS_DECISION
    assert printed(capsys)["reason_code"] == "confirmation-required"

    after = world.store.get_work_item(acceptance.id)
    assert after.deliverable == before.deliverable                          # what the real run showed: nothing new was written
    assert "Design source changed since your previous delivery" in after.description
    assert "+- `docs/appendix-b.md`" in after.description                   # the new file is in the diff the author reads
    [ref] = [r for r in after.source_refs if r.get("kind") == "plan"]
    assert ref["previous_delivery_sha256"] == hashlib.sha256(before.deliverable.encode()).hexdigest()
    [obligation] = [o for o in after.review_obligations if o["category"] == "source-change"]
    assert obligation["obligation_id"] == "source-change:plan" and obligation["subject"]["delivery_unchanged"] is True
    assert obligation["obligation_id"] in {r["obligation_id"] for r in after.review_report["obligation_results"]}
