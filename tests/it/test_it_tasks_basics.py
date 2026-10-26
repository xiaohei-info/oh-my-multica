"""run_task on a real Multica: one authoring pass, the issue body it renders, and resuming an authoring issue in place."""
import pytest
from structlog.testing import capture_logs

import omac.pipeline.tasks as tasks_module
from _support.tasksworld import World, payload
from _support.taskkit import decompose_manifest
from omac.core import logsetup
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError
from omac.pipeline.dispatch import build_show_output
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task, run_task


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


def env_prefix(kit):
    return f"OMAC_ENGINE=multica OMAC_WORKSPACE_ID={kit.workspace}"


def test_a_large_machine_feedback_is_externalized_for_the_author(world):
    kit = world.kit
    errors = [f"node node-{i}: deterministic failure with enough detail for repair" for i in range(240)]
    kit.monkeypatch.setattr(tasks_module, "run_review_preflight", lambda _item: list(errors))
    world.author(world.manifest(decompose_manifest("go test ./cmd/...")))
    world.review("pass")

    with pytest.raises(NeedsDecision) as raised:
        world.run(TaskKind.DECOMPOSE, payload(title="large machine feedback"), "alice", reviewers=["bob"], max_revisions=1)

    item = kit.store.get_work_item(raised.value.report["item_id"])
    assert len((item.review_comment or "").encode("utf-8")) < 8192
    assert f"omac work show {item.id} --output json" in item.review_comment and "context.machine_feedback" in item.review_comment
    assert item.machine_feedback == {"schema": "omac.machine-feedback/v1", "gate": "machine-gate", "error_count": len(errors), "errors": errors}
    assert item.machine_feedback_ref["bytes"] > 8192 and item.machine_feedback_ref["sha256"]
    shown = build_show_output(item, "orchestrator:alice")
    assert shown["context"]["machine_feedback"] == item.machine_feedback and shown["context"]["machine_feedback_ref"] == item.machine_feedback_ref


def test_machine_feedback_is_cleared_when_the_review_is_reset_or_a_new_cycle_starts(world):
    kit = world.kit
    item = create_authoring_task(kit.engine, AuthoringTaskSpec(kind=TaskKind.DECOMPOSE, title="lifecycle", dag_key="decompose-lifecycle", assignee="alice"))
    feedback = {"schema": "omac.machine-feedback/v1", "gate": "machine-gate", "error_count": 1, "errors": ["fix the local target"]}

    kit.store.update_work_item_metadata(item.id, machine_feedback=feedback, review_comment="bounded summary")
    kit.store.reset_review(item.id)
    reset = kit.store.get_work_item(item.id)
    assert reset.machine_feedback is None and reset.machine_feedback_ref is None

    kit.store.update_work_item_metadata(item.id, machine_feedback=feedback, review_comment="bounded summary")
    prepared = kit.store.prepare_review_cycle(item.id, "subject-v2")
    assert prepared.machine_feedback is None and prepared.machine_feedback_ref is None


def test_creating_an_authoring_task_renders_the_body_contract_and_source_refs(world):
    kit = world.kit
    project = kit.store.create_project(kit.workspace, "demo", repo_urls=["git@github.com:owner/demo.git"])
    kit.monkeypatch.setattr(kit.store.config, "project_id", project.id)
    spec = AuthoringTaskSpec(
        kind=TaskKind.FINAL_ACCEPTANCE, title="最终验收 · Demo · 第 1 轮", dag_key="final-acceptance-p-demo-r1", assignee="alice",
        description="按 ACC-001 逐项走查。",
        contract={"acceptance_doc": {"flows": []}, "acceptance": ["ACC-001"], "pr_base": "main", "repo_urls": ["git@github.com:owner/demo.git"]},
        source_refs=[{"label": "最终开发交付", "issue_id": "closeout-1"}])

    item = create_authoring_task(kit.engine, spec)

    assert env_prefix(kit) in item.description and f"omac work show {item.id}" in item.description
    assert "PR base: `main`" in item.description and "git@github.com:owner/demo.git" in item.description
    assert item.contract["acceptance_doc"] == {"flows": []}
    assert item.source_refs == [{"label": "最终开发交付", "issue_id": "closeout-1"}]


def test_run_task_creates_its_issue_through_the_shared_primitive(world):
    kit = world.kit
    world.author(world.plan())
    original, calls = tasks_module.create_authoring_task, []
    kit.monkeypatch.setattr(tasks_module, "create_authoring_task", lambda engine, spec: (calls.append(spec), original(engine, spec))[1])

    result = world.run(TaskKind.PLAN, payload(), "alice")

    assert result["verdict"] == "pass" and len(calls) == 1
    assert (calls[0].kind, calls[0].assignee) == (TaskKind.PLAN, "alice")


def test_the_poll_hook_is_a_required_argument():
    with pytest.raises(TypeError):
        run_task(None, TaskKind.PLAN, payload(), "alice")


def test_one_pass_without_reviewers_finishes_on_the_same_issue(world):
    kit = world.kit
    world.author(world.plan())

    with capture_logs() as events:
        res = world.run(TaskKind.PLAN, payload(), "alice")

    dispatched = next(event for event in events if event["event"] == logsetup.EVT_DISPATCH)
    assert dispatched["worker"] == "alice" and logsetup.EVT_NODE_DONE in [event["event"] for event in events]
    item = kit.store.get_work_item(res["item_id"])
    assert res["delivery"] == {"plan": "计划正文", "project_rules": "## Rules\n"} and (res["rounds"], res["verdict"], res["kind"]) == (0, "pass", "plan")
    assert item.status == WorkItemStatus.DONE
    assert f"omac work show {res['item_id']}" in item.description and f"omac work submit {res['item_id']}" not in item.description
    assert len([i for i in kit.store.list_work_items(kit.workspace) if i.dag_key == item.dag_key]) == 1


def test_a_task_without_a_dag_key_gets_a_unique_one(world):
    kit = world.kit
    world.author(world.plan(), world.plan())

    first = world.run(TaskKind.PLAN, payload(title="重复计划"), "alice")
    second = world.run(TaskKind.PLAN, payload(title="重复计划"), "alice")

    keys = [kit.store.get_work_item(r["item_id"]).dag_key for r in (first, second)]
    assert all(k.startswith("plan-") and k != "plan" for k in keys) and keys[0] != keys[1]


def test_the_deliverable_run_task_returns_is_the_one_the_real_submit_stored(world):
    world.author(world.plan("计划正文-真实路径"))
    world.review("pass")

    res = world.run(TaskKind.PLAN, payload(), "alice", reviewers=["bob"])

    item = world.store.get_work_item(res["item_id"])
    assert res["delivery"]["plan"] == "计划正文-真实路径" and res["verdict"] == "pass"
    assert item.status == WorkItemStatus.DONE and item.deliverable == "计划正文-真实路径"


def test_upstream_issues_are_rendered_as_links_only(world):
    world.author(world.acceptance())

    res = world.run(TaskKind.ACCEPTANCE, payload(), "alice", source_refs=["7", "8"])

    description = world.store.get_work_item(res["item_id"]).description
    assert "## Upstream issues (stay on target)" in description and "- `#7`" in description and "- `#8`" in description
    assert "omac work show 7" not in description and "omac work show 8" not in description


def test_the_issue_body_has_a_single_agent_entry_in_a_code_block(world):
    world.author(world.manifest(decompose_manifest("go test ./cmd/...")))

    res = world.run(TaskKind.DECOMPOSE, payload(), "alice")

    description = world.store.get_work_item(res["item_id"]).description
    assert f"```bash\n{env_prefix(world.kit)} omac work show {res['item_id']} --output json\n```" in description
    assert "omac guide role orchestrator" not in description and "omac work submit" not in description


def test_an_upstream_markdown_source_is_rendered_collapsible_and_not_wrapped_in_a_code_block(world):
    world.author(world.acceptance())
    upstream = "# 设计方案\n\n```ts\nexport const ok = true\n```\n\n## 下一节"

    res = world.run(TaskKind.ACCEPTANCE, payload(source_of_truth={"plan": upstream}), "alice")

    description = world.store.get_work_item(res["item_id"]).description
    assert "### plan" in description and "<details>" in description and "<details open>" not in description
    assert "<summary>View upstream artifact: plan</summary>" in description
    assert "# 设计方案" in description and "```ts\nexport const ok = true\n```" in description and "## 下一节" in description
    assert "### plan\n````" not in description


def test_a_large_issue_backed_source_stays_in_the_source_issue_and_not_in_the_body(world):
    kit = world.kit
    upstream = kit.store.create_work_item(kit.workspace, "acceptance", "acceptance", dag_key="acceptance-p1", worker="alice", kind=TaskKind.ACCEPTANCE)
    large = "schema: omac.acceptance/v1\n" + "x" * (70 * 1024)
    kit.store.update_work_item_metadata(upstream.id, deliverable=large, phase=TaskPhase.CONFIRMATION)
    kit.store.update_status(upstream.id, WorkItemStatus.DONE)

    item = create_authoring_task(kit.engine, AuthoringTaskSpec(
        kind=TaskKind.DECOMPOSE, title="decompose", dag_key="decompose-p1", assignee="bob",
        source_refs=[{"label": "acceptance", "issue_id": upstream.id}], source_of_truth={"acceptance": large}))

    assert large not in item.description and "Large upstream artifact omitted from the issue body" in item.description
    assert f"omac work read {item.id} --source acceptance --output-file /tmp/omac-acceptance.yaml" in item.description
    ref = item.source_refs[0]
    assert ref["content_externalized"] is True and ref["content_bytes"] == len(large.encode("utf-8")) and ref["content_sha256"]


def test_resuming_refreshes_an_unstarted_authoring_issue_in_place(world):
    kit = world.kit
    item = kit.store.create_work_item(kit.workspace, "decompose", "decompose", dag_key="decompose-resume-refresh", worker="bob", kind=TaskKind.DECOMPOSE)
    kit.stack.set_scenario("bob", [[world.manifest(decompose_manifest("go test ./cmd/...", worker="bob"))]])

    result = world.run(TaskKind.DECOMPOSE, payload(title="decompose", source_of_truth={"acceptance": "small acceptance"}), "bob",
                       source_refs=[{"label": "acceptance", "issue_id": "acceptance-1"}], resume_item_id=item.id)

    refreshed = kit.store.get_work_item(item.id)
    assert result["item_id"] == item.id
    assert len([i for i in kit.store.list_work_items(kit.workspace) if i.dag_key == "decompose-resume-refresh"]) == 1
    assert "small acceptance" in refreshed.description and refreshed.source_refs == [{"label": "acceptance", "issue_id": "acceptance-1"}]


def test_a_failed_first_store_read_on_resume_never_lets_a_pristine_snapshot_authorize_a_refresh(world):
    import copy

    kit = world.kit
    item = create_authoring_task(kit.engine, AuthoringTaskSpec(kind=TaskKind.DECOMPOSE, title="decompose", dag_key="decompose-read-failure", assignee="bob"))
    snapshot = copy.deepcopy(item)
    world.stage(item, phase=TaskPhase.CONFIRMATION, deliverable="current manifest", verdict="pass", report=world_report(), status=WorkItemStatus.IN_REVIEW)
    before = kit.store.get_work_item(item.id)
    state = kit.fail_first_read(item.id, PlatformError("first Store read failed"))
    writes = kit.record(kit.store, "set_node_contract", "update_work_item_metadata", "assign_work_item")
    kit.forbid(kit.runtime, "is_active", "wake")

    with pytest.raises(PlatformError, match="first Store read failed"):
        run_task(kit.engine, TaskKind.DECOMPOSE, payload(title="decompose", source_of_truth={"acceptance": "new acceptance"}), "bob",
                 source_refs=[{"label": "acceptance", "issue_id": "new-acceptance"}], poll=lambda: pytest.fail("a failed read must not poll"),
                 resume_item_id=item.id, resume_item_snapshot=snapshot)

    assert state["fired"] and not writes and kit.store.get_work_item(item.id) == before


def world_report():
    from _support.tasksworld import review_report

    return review_report()
