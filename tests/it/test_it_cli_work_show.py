"""`omac work show` and `omac work read` on a real Multica: what an Agent reads for an issue the platform holds.

The projection of a WorkItem into the Agent's view needs no platform: tests/test_work_show.py.
"""
import json

import pytest

from _support.workitems import EXPECTED_GUIDE_REFS
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus

CONTRACT = Contract(objective="实现 X", acceptance=["A 工作", "B 工作"], non_goals=["不做 Y"], verification_commands=["pytest -q"],
                    integration_gates=[], pr_base="feature/v1", coverage_gate=90)
VERIFICATION = {"commands": [{"cmd": "pytest -q", "exit_code": 0, "summary": "ok"}], "pr_base": "feature/v1", "coverage": 92,
                "env_setup": ["docker compose up -d db"]}


@pytest.fixture()
def show(kit, capsys):
    """``omac work show <args>`` in this process: (exit code, stdout, stderr)."""
    def run(*args):
        capsys.readouterr()
        code = main(["work", *args])
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


def issue(kit, kind, phase, key, *, contract=None, **fields):
    item = kit.store.create_work_item(kit.store.config.workspace_id, f"title-{kind.value}", "desc", dag_key=key, worker="alice",
                                      reviewer="bob", kind=kind)
    if contract is not None:
        kit.store.set_node_contract(item.id, contract)
    kit.store.update_work_item_metadata(item.id, phase=phase, **fields)
    return kit.store.get_work_item(item.id)


def test_show_prints_the_agent_json_for_each_kind_and_phase_and_the_table_view_on_request(kit, show):
    develop = issue(kit, TaskKind.DEVELOP, TaskPhase.AUTHORING, "show-develop", contract=CONTRACT)
    plan = issue(kit, TaskKind.PLAN, TaskPhase.AUTHORING, "show-plan")
    review = issue(kit, TaskKind.PLAN, TaskPhase.REVIEW, "show-plan-review", deliverable="# 计划正文",
                   project_rules="## Project rules\n\n- Preserve compatibility.\n")
    develop_review = issue(kit, TaskKind.DEVELOP, TaskPhase.REVIEW, "show-develop-review", contract=CONTRACT, deliverable="# 计划正文",
                           artifacts={"pr_url": "https://example.test/pr/42"}, verification=VERIFICATION)

    code, out, _ = show("show", develop.id, "--output", "json")
    data = json.loads(out)
    assert code == exit_codes.OK and (data["task"]["kind"], data["task"]["phase"]) == ("develop", "authoring")
    assert data["context"]["contract"]["objective"] == "实现 X" and data["submit"].startswith(f"omac work submit {develop.id}")
    assert data["context"]["contract"]["acceptance"] == ["A 工作", "B 工作"] and data["context"]["contract"]["pr_base"] == "feature/v1"

    code, out, _ = show("show", plan.id)                                           # the default is the Agent's JSON
    data = json.loads(out)
    assert code == exit_codes.OK and data["task"]["title"] == plan.title
    assert data["guide_refs"] == EXPECTED_GUIDE_REFS[(TaskKind.PLAN, TaskPhase.AUTHORING)]

    code, out, _ = show("show", plan.id, "--output", "table")                      # the role names who is acting
    assert code == exit_codes.OK and "planner" in out and "worker:" not in out
    assert "# Task" in out and "## What to do now" in out and "## Submit when finished" in out

    code, out, _ = show("show", review.id)
    assert code == exit_codes.OK and json.loads(out)["context"]["project_rules"].startswith("## Project rules")

    code, out, _ = show("show", develop_review.id, "--output", "table")
    assert code == exit_codes.OK and "Review target" in out and "docker compose up -d db" in out and "omac guide role reviewer" in out


def test_upstream_issue_commands_in_the_table_view_are_copyable(kit, show, stack):
    item = issue(kit, TaskKind.DEVELOP, TaskPhase.AUTHORING, "show-upstream", contract=CONTRACT,
                 source_refs=[{"label": "设计方案", "issue_id": "plan-1"}])

    code, out, _ = show("show", item.id, "--output", "table")

    assert code == exit_codes.OK and "## Upstream issues (stay on target)" in out
    assert (f"OMAC_ENGINE=multica OMAC_WORKSPACE_ID={stack.workspace_id} OMAC_PROJECT_ID={stack.project_id} omac work show plan-1 --output json") in out


def test_the_language_changes_the_prose_and_never_the_facts(kit, show):
    item = issue(kit, TaskKind.PLAN, TaskPhase.AUTHORING, "show-language", contract=CONTRACT)
    assert main(["config", "set", "language", "cn"]) == exit_codes.OK
    chinese = json.loads(show("show", item.id)[1])
    assert main(["config", "set", "language", "en"]) == exit_codes.OK
    english = json.loads(show("show", item.id)[1])

    assert chinese["protocol"] != english["protocol"] and english["protocol"].startswith("Write two required artifacts:")
    assert chinese["authority"] != english["authority"] and english["authority"][0] == "Current facts from work show"
    assert all(chinese[key] == english[key] for key in ("task", "context", "guide_refs", "submit"))


def test_work_read_materialises_a_named_upstream_deliverable(kit, show, tmp_path):
    upstream = issue(kit, TaskKind.ACCEPTANCE, TaskPhase.AUTHORING, "read-acceptance-p1", deliverable="schema: omac.acceptance/v1\nflows: []\n")
    current = issue(kit, TaskKind.DECOMPOSE, TaskPhase.AUTHORING, "read-decompose-p1",
                    source_refs=[{"label": "acceptance", "issue_id": upstream.id}])
    target = tmp_path / "acceptance.yaml"

    code, out, _ = show("read", current.id, "--source", "acceptance", "--output-file", str(target))

    payload = json.loads(out)
    assert code == exit_codes.OK and (payload["ok"], payload["source"], payload["source_issue_id"]) == (True, "acceptance", upstream.id)
    assert payload["bytes"] == target.stat().st_size and target.read_text() == "schema: omac.acceptance/v1\nflows: []\n"


def test_an_unknown_issue_is_a_teaching_validation_error(kit, show):
    code, _, err = show("show", "99999")

    error = json.loads(err)
    assert code == exit_codes.VALIDATION and error["ok"] is False and error["error"]["exit_code"] == exit_codes.VALIDATION
    assert "99999" in error["error"]["message"]


def test_a_legacy_convergence_snapshot_is_a_structured_decision_not_a_traceback(kit, show, legacy_convergence_snapshot):
    snapshot = legacy_convergence_snapshot["work_item"]
    item = kit.store.create_work_item(kit.store.config.workspace_id, "legacy convergence snapshot", "redacted production snapshot",
                                      dag_key=snapshot["dag_key"], worker=snapshot["worker_handoff"]["target_worker"],
                                      reviewer="bob", kind=TaskKind(snapshot["kind"]), initial_status=WorkItemStatus(snapshot["status"]))
    kit.store.update_work_item_metadata(
        item.id, phase=TaskPhase(snapshot["phase"]), worker_bounce=snapshot["bounces"]["worker"], ci_bounce=snapshot["bounces"]["ci"],
        review_bounce=snapshot["bounces"]["review"], merge_bounce=snapshot["bounces"]["merge"],
        decision_required=snapshot["decision_required"], review_ledger=snapshot["review_ledger"], worker_handoff=snapshot["worker_handoff"])

    code, out, err = show("show", item.id, "--output", "json")

    output = json.loads(out)
    assert code == exit_codes.NEEDS_DECISION and output["ok"] is False and output["exit_code"] == exit_codes.NEEDS_DECISION
    assert output["decision_required"]["reason_code"] == "review-convergence-ledger-unverifiable"
    assert output["next_action"].startswith("omac dag amend propose ") and "Traceback" not in err
