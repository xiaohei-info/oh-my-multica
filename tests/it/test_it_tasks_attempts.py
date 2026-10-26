"""Deterministic amendment attempts on a real Multica: a crashed creation is finished before anything is dispatched, and a shell
that is no longer pristine is refused without a single write (the facts that count as "started" are tests/test_tasks_shell.py)."""
import uuid

import pytest

import omac.pipeline.tasks as tasks_module
from _support.tasksworld import World, payload
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemStatus
from omac.errors import NeedsDecision, PlatformError
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task, run_task

REFS = [{"issue_id": "old-issue", "issue_key": "PROJ-811", "relation": "supersedes", "report_sha256": "report-digest",
         "docs_sha256": "docs-digest"}]


@pytest.fixture()
def world(kit, tmp_path):
    return World(kit, tmp_path)


def attempt_of(token):
    return {"schema": "omac.amendment-attempt/v1", "attempt_id": token, "request_digest": f"request-{token}", "report_sha256": "report-digest",
            "docs_sha256": "docs-digest", "docs_file_count": 1, "supersedes_issue_id": "old-issue", "supersedes_issue_key": "PROJ-811"}


def new_key():
    return f"amend-project-attempt-{uuid.uuid4().hex[:10]}"


def spec_for(world, dag_key, attempt, **over):
    return AuthoringTaskSpec(kind=TaskKind.AMENDMENT, title="new amendment", dag_key=dag_key, assignee="alice",
                             contract=payload()["contract"], source_refs=REFS, amendment_attempt=attempt, **over)


def run_attempt(world, dag_key, attempt, **kwargs):
    return world.run(TaskKind.AMENDMENT, payload(title="new amendment"), "alice", dag_key=dag_key, source_refs=REFS,
                     amendment_attempt=attempt, reuse_dag_key=True, **kwargs)


def raw_shell(world, dag_key, completed_step, spec):
    """The issue an interrupted creation leaves behind: the creation steps are done up to and including ``completed_step``."""
    import json

    kit = world.kit
    steps = ["create", "dag_key", "kind", "contract", "attempt_metadata", "body"]
    done = steps.index(completed_step)
    issue = json.loads(kit.stack.multica("issue", "create", "--title", f"[DAG:{dag_key}] new amendment", "--status", "todo",
                                         "--description", "new amendment", "--project", kit.stack.project_id, "--output", "json"))
    item_id = issue["id"]
    kit.store.created_ids.append(item_id)
    if done >= steps.index("dag_key"):
        kit.store._set_metadata(item_id, "dag_key", dag_key)
    if done >= steps.index("kind"):
        kit.store.set_authoring_identity(item_id, dag_key=dag_key, kind=TaskKind.AMENDMENT)
    if done >= steps.index("contract"):
        kit.store.set_node_contract(item_id, spec.contract)
    if done >= steps.index("attempt_metadata"):
        kit.store.update_work_item_metadata(item_id, amendment_attempt=spec.amendment_attempt, source_refs=REFS)
    if done >= steps.index("body"):
        body, _refs = tasks_module._authoring_materialization(kit.engine, item_id, spec, kit.store.get_work_item(item_id))
        kit.store.update_work_item_metadata(item_id, description=body)
    return item_id


@pytest.mark.parametrize("completed_step", ["create", "dag_key", "kind", "contract", "attempt_metadata", "body"])
def test_a_crashed_attempt_creation_is_finished_before_the_author_is_dispatched(world, completed_step):
    kit = world.kit
    dag_key, attempt = new_key(), attempt_of(uuid.uuid4().hex[:8])
    spec = spec_for(world, dag_key, attempt)
    shell_id = raw_shell(world, dag_key, completed_step, spec)
    world.author(world.amendment(world.amendment_text()))
    creations = kit.record(kit.store, "create_work_item")
    identities = kit.record(kit.store, "set_authoring_identity")

    def finalized_at_dispatch(item_id, assignee, role, **_kw):
        current = kit.store.get_work_item(item_id)
        body, refs = tasks_module._authoring_materialization(kit.engine, item_id, spec, current)
        identity_errors, activity = tasks_module._pristine_amendment_shell_errors(
            current, spec, body=body, refs=refs, workspace_id=kit.workspace, finalized=True)
        assert identity_errors == [] and activity == ["status"], (identity_errors, activity)   # the dispatch itself starts the status change

    kit.before(kit.store, "assign_work_item", finalized_at_dispatch, only=lambda item_id, assignee, role, **kw: role == "worker")

    result = run_attempt(world, dag_key, attempt)

    current = kit.store.get_work_item(shell_id)
    assert result["item_id"] == shell_id and not creations
    assert (current.amendment_attempt, current.dag_key, current.kind, current.source_refs) == (attempt, dag_key, TaskKind.AMENDMENT, REFS)
    assert current.contract is not None and current.deliverable
    assert [c[1][0] for c in identities] == [shell_id]


def test_an_orphan_shell_that_carries_the_creation_assignee_is_finalized_and_unassigned(world):
    kit = world.kit
    attempt = attempt_of("orphan")
    dag_key = new_key()
    spec = spec_for(world, dag_key, attempt)
    item = kit.store.create_work_item(kit.workspace, spec.title, spec.title, dag_key, "alice", kind=TaskKind.AMENDMENT)
    kit.store.set_node_contract(item.id, spec.contract)      # publishing a contract comment would itself clear the assignee on Multica
    kit.store.assign_work_item(item.id, "alice", "worker", start_run=False)          # the platform's auto-assignment, no Run
    shell = kit.store.get_work_item(item.id)
    assert shell.platform_assignee_id and tasks_module._is_pristine_created_amendment_shell(kit.engine, shell, spec) is True
    wakes = kit.record(kit.runtime, "wake")

    finalized = tasks_module.finalize_authoring_shell(kit.engine, shell, spec, allow_created_platform_assignee=True)

    assert finalized.id == item.id and finalized.platform_assignee_id is None
    assert finalized.amendment_attempt == attempt and finalized.phase is TaskPhase.AUTHORING
    assert not wakes and not kit.runtime.list_runs(item.id)


def test_finalizing_a_shell_twice_publishes_the_contract_once(world):
    kit = world.kit
    dag_key, attempt = new_key(), attempt_of("idempotent")
    spec = spec_for(world, dag_key, attempt)
    item = kit.store.create_work_item(kit.workspace, spec.title, spec.title, dag_key, "alice")   # created as an ordinary (develop) issue
    contract_writes = kit.record(kit.store, "set_node_contract")
    assigns = kit.record(kit.store, "assign_work_item")

    first = tasks_module.finalize_authoring_shell(kit.engine, kit.store.get_work_item(item.id), spec)
    second = tasks_module.finalize_authoring_shell(kit.engine, first, spec)

    assert second.id == item.id and second.kind is TaskKind.AMENDMENT and [c[1][0] for c in contract_writes] == [item.id]
    assert not assigns


def started(world, dag_key, attempt, arrange):
    kit = world.kit
    item = kit.store.create_work_item(kit.workspace, "new amendment", "new amendment", dag_key, "alice", kind=TaskKind.AMENDMENT)
    kit.store.update_work_item_metadata(item.id, amendment_attempt=attempt)
    arrange(kit, item)
    return item


def _verification_partial(kit, item):
    kit.store.update_work_item_metadata(item.id, artifacts={"pr_url": "https://example.test/pr/partial"},
                                        verification={"commands": [{"command": "pytest", "exit_code": 0}]},
                                        verification_source="commands: []\n")


STARTED = {
    "split-submit-artifacts": (_verification_partial, {"artifacts", "verification"}),
    "in-progress": (lambda kit, item: kit.store.update_status(item.id, WorkItemStatus.IN_PROGRESS), {"status"}),
    "review-phase": (lambda kit, item: kit.store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW), {"phase"}),
    "delivery": (lambda kit, item: kit.store.update_work_item_metadata(item.id, deliverable="existing delivery"), {"deliverable"}),
    "review-history": (lambda kit, item: kit.store.update_work_item_metadata(item.id, review_subject_digest="subject"),
                       {"review_subject_digest"}),
    "unknown-fact": (lambda kit, item: kit.store._set_metadata(item.id, "future_execution_fact", "false"), {"unknown_persisted_fields"}),
}


def test_an_attempt_that_already_started_is_refused_with_nothing_written(world):
    kit = world.kit
    writes = {name: kit.record(kit.store, name) for name in ("create_work_item", "assign_work_item", "update_status",
                                                              "set_authoring_identity")}
    writes["wake"] = kit.record(kit.runtime, "wake")
    for name, (arrange, expected_fields) in STARTED.items():
        dag_key, attempt = new_key(), attempt_of(name)
        item = started(world, dag_key, attempt, arrange)
        before = {key: len(events) for key, events in writes.items()}

        with pytest.raises(NeedsDecision) as raised:
            run_attempt(world, dag_key, attempt, poll=lambda: pytest.fail("a started attempt must not be polled"))

        report = raised.value.report
        assert report["reason_code"] == "amendment-attempt-already-started", name
        assert expected_fields <= set(report["fields"]), (name, report["fields"])
        assert report["next_action"] == f"omac work show {item.id} --output json" and "--resume-issue-id" in report["recovery"]
        assert {key: len(events) for key, events in writes.items()} == before, name


@pytest.mark.parametrize("conflict", ["other-attempt", "other-kind"])
def test_a_shell_that_belongs_to_another_attempt_fails_without_dispatch(world, conflict):
    kit = world.kit
    dag_key, attempt = new_key(), attempt_of(conflict)
    item = kit.store.create_work_item(kit.workspace, "new amendment", "new amendment", dag_key, "alice", kind=TaskKind.AMENDMENT)
    if conflict == "other-attempt":
        kit.store.update_work_item_metadata(item.id, amendment_attempt={"request_digest": "other"})
    else:
        kit.store.update_work_item_metadata(item.id, amendment_attempt=attempt)
        kit.store.set_authoring_identity(item.id, dag_key=dag_key, kind=TaskKind.PLAN)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(NeedsDecision) as raised:
        run_attempt(world, dag_key, attempt, poll=lambda: pytest.fail("a conflicting shell must not be polled"))

    assert raised.value.report["reason_code"] == "amendment-attempt-shell-conflict"
    assert raised.value.report["fields"] == (["amendment_attempt"] if conflict == "other-attempt" else ["kind"])


def test_an_attempt_with_an_active_run_is_neither_finalized_nor_dispatched(world):
    kit = world.kit
    dag_key = new_key()
    item = kit.store.create_work_item(kit.workspace, "new amendment", "new amendment", dag_key, "alice")    # not yet an amendment shell
    kit.stack.set_scenario("alice", [[{"hang": 120}]])
    kit.assign(item.id, "alice")
    kit.wait_for(lambda: kit.runtime.is_active(item.id), what="the Run to be active")
    kit.forbid(kit.store, "set_authoring_identity")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(NeedsDecision) as raised:
        run_attempt(world, dag_key, attempt_of("active"), poll=lambda: pytest.fail("an active shell must not be polled"))

    assert raised.value.report["reason_code"] == "amendment-attempt-shell-active"
    assert raised.value.report["next_action"] == f"omac work show {item.id} --output json"


def test_an_attempt_whose_state_cannot_be_observed_fails_closed(world):
    kit = world.kit
    dag_key = new_key()
    item = kit.store.create_work_item(kit.workspace, "new amendment", "new amendment", dag_key, "alice", kind=TaskKind.AMENDMENT)
    kit.fault(kit.runtime, "is_active", when="before", error=PlatformError("runtime unavailable"))
    kit.forbid(kit.store, "assign_work_item")

    with pytest.raises(NeedsDecision) as raised:
        run_attempt(world, dag_key, attempt_of("runtime-error"), poll=lambda: pytest.fail("unknown activity must not be polled"))

    assert raised.value.report["reason_code"] == "amendment-attempt-observation-failed"
    assert raised.value.report["next_action"] == f"omac work show {item.id} --output json"


def test_an_attempt_whose_identity_cannot_be_looked_up_creates_nothing(world):
    kit = world.kit
    kit.fault(kit.store, "find_work_item_by_dag_key", when="before", error=PlatformError("store unavailable"))
    kit.forbid(kit.store, "create_work_item")

    with pytest.raises(NeedsDecision) as raised:
        run_attempt(world, new_key(), attempt_of("store-error"), poll=lambda: pytest.fail("unknown identity must not be polled"))

    assert raised.value.report["reason_code"] == "amendment-attempt-observation-failed"
    assert raised.value.report["next_action"] == "omac work show <attempt-issue-id> --output json"


def test_a_fact_that_lands_after_the_shell_was_finalized_stops_the_dispatch(world):
    kit = world.kit
    original = tasks_module.finalize_authoring_shell

    def finalize_then_receive_a_submit(engine, item, spec, **kwargs):
        finalized = original(engine, item, spec, **kwargs)
        kit.store.update_work_item_metadata(finalized.id, artifacts={"pr_url": "https://example.test/pr/after-finalize"})
        return finalized

    kit.monkeypatch.setattr(tasks_module, "finalize_authoring_shell", finalize_then_receive_a_submit)
    kit.forbid(kit.store, "assign_work_item")
    kit.forbid(kit.runtime, "wake")

    with pytest.raises(NeedsDecision) as raised:
        run_attempt(world, new_key(), attempt_of("post-finalize"), poll=lambda: pytest.fail("must not poll"))

    assert raised.value.report["reason_code"] == "amendment-attempt-already-started" and raised.value.report["fields"] == ["artifacts"]


def test_an_issue_created_before_an_ambiguous_failure_is_adopted_and_dispatched_once(world):
    kit = world.kit
    dag_key, attempt = new_key(), attempt_of("ambiguous")
    kit.fault(kit.store, "create_work_item", when="after", error=PlatformError("resource conflict after issue create"))
    world.author(world.amendment(world.amendment_text()))
    wakes = kit.record(kit.runtime, "wake")

    result = run_attempt(world, dag_key, attempt)

    adopted = kit.store.find_work_item_by_dag_key(kit.workspace, dag_key)
    assert result["item_id"] == adopted.id and adopted.amendment_attempt == attempt and adopted.deliverable
    assert len(kit.runtime.list_runs(adopted.id)) == 1 and len(wakes) <= 1
