"""A plan pipeline released only by the commands its own stops print: every next_action is run exactly as printed.

Four releases of this loop (2.0.1-2.0.4) each fixed one stop whose printed command did not work. This walks a whole
pipeline through repeated pass-with-nits reviews, a budget authorization and the final acceptance of the nits, never
adding an argument by hand, for a pipeline made from a design directory and for one made from a request.
"""
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
from omac.core.taskmeta import TaskKind
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
