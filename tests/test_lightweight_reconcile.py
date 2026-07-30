"""Large-DAG reconcile uses two-phase control observation and evidence hydration."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity, TaskPhase
from omac.engines.models import (
    EngineConfig,
    PullRequestObservation,
    PullRequestReadiness,
    PullRequestState,
    WorkItem,
    WorkItemPayload,
    WorkItemStatus,
)
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.multica import MulticaStore
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.tasks import _pristine_amendment_activity_projection


class _RemoteFixture:
    def __init__(self, issues: dict[str, dict], attachments: dict[str, tuple[str, bytes]]):
        self.issues = issues
        self.attachments = attachments
        self.issue_gets = 0
        self.attachment_downloads = 0
        self.pr_observations = 0
        self.calls: list[tuple[str, str]] = []
        self.fail_attachment_id: str | None = None

    def run(self, args, capture=True):
        if args[:2] == ["issue", "get"]:
            item_id = args[2]
            self.issue_gets += 1
            self.calls.append(("issue", item_id))
            return copy.deepcopy(self.issues[item_id])
        if args[:2] == ["attachment", "download"]:
            attachment_id = args[2]
            self.attachment_downloads += 1
            self.calls.append(("attachment", attachment_id))
            if attachment_id == self.fail_attachment_id:
                raise PlatformError(f"attachment read failed: {attachment_id}")
            filename, body = self.attachments[attachment_id]
            output_dir = Path(args[args.index("--output-dir") + 1])
            (output_dir / filename).write_bytes(body)
            return None
        if args[:3] == ["issue", "comment", "list"]:
            item_id = args[3]
            comment_id = args[args.index("--thread") + 1]
            issue = self.issues[item_id]
            verification_ref = issue["metadata"]["verification_ref"]
            return [{
                "id": comment_id,
                "attachments": [{
                    "id": verification_ref["attachment_id"],
                    "filename": verification_ref["filename"],
                    "uploader_type": "agent",
                    "uploader_id": "agent-worker",
                    "task_id": "run-worker",
                    "created_at": "2026-07-30T01:00:00Z",
                }],
            }]
        if args[:2] == ["issue", "update"] and "--status" in args:
            item_id = args[2]
            self.issues[item_id]["status"] = args[args.index("--status") + 1]
            return None
        raise AssertionError(f"unexpected command: {args}")

    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        self.pr_observations += 1
        self.calls.append(("pr", pr_url))
        return PullRequestObservation(PullRequestState.UNKNOWN)


def _store(remote: _RemoteFixture) -> MulticaStore:
    store = MulticaStore(EngineConfig(
        engine_type="multica", workspace_id="ws", project_id="project-1"))
    store._run_multica = remote.run
    store.observe_pull_request = remote.observe_pull_request
    return store


def _ref(item_id: str, label: str, filename: str, body: bytes) -> tuple[dict, tuple[str, bytes]]:
    attachment_id = f"{item_id}-{label}"
    return ({
        "comment_id": f"comment-{attachment_id}",
        "attachment_id": attachment_id,
        "filename": filename,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }, (filename, body))


def _issue(
    item_id: str,
    *,
    status: str,
    phase: str,
    review_verdict: str | None = None,
    review_subject: str | None = None,
    delivery_identity: dict | None = None,
    worker_handoff: dict | None = None,
    unknown: bool = False,
) -> tuple[dict, dict[str, tuple[str, bytes]]]:
    verification_body = yaml.safe_dump({
        "commands": [{"cmd": "pytest -q", "exit_code": 0}],
        "integration_gates": [],
        "coverage": 100,
    }, sort_keys=False).encode()
    report_body = yaml.safe_dump({
        "full_review_completed": True,
        "review_goals": ["verify current delivery"],
        "blockers": [],
    }, sort_keys=False).encode()
    ledger_body = yaml.safe_dump({
        "schema": "omac.review-ledger/v1", "cycles": [], "blockers": [],
    }, sort_keys=False).encode()
    contract_body = yaml.safe_dump({
        "objective": "deliver", "verification_commands": ["pytest -q"],
        "integration_gates": [], "pr_base": "main",
    }, sort_keys=False).encode()
    refs = {}
    attachments = {}
    for label, filename, body in (
        ("verification", "verification.yaml", verification_body),
        ("review_report", "review-report.yaml", report_body),
        ("review_ledger", "review-ledger.yaml", ledger_body),
        ("contract", "contract.yaml", contract_body),
    ):
        refs[label], attachment = _ref(item_id, label, filename, body)
        attachments[refs[label]["attachment_id"]] = attachment

    metadata = {
        "dag_key": item_id,
        "worker": "worker",
        "reviewer": "reviewer",
        "kind": "develop",
        "phase": phase,
        "artifacts": {
            "pr_url": f"https://github.com/acme/repo/pull/{item_id}",
            "head_sha": f"head-{item_id}",
        },
        "verification_ref": refs["verification"],
        "review_report_ref": refs["review_report"],
        "review_ledger_ref": refs["review_ledger"],
        "contract_ref": refs["contract"],
    }
    if review_verdict is not None:
        metadata["review_verdict"] = review_verdict
    if review_subject is not None:
        metadata["review_subject_digest"] = review_subject
    if delivery_identity is not None:
        metadata["delivery_identity"] = delivery_identity
    if worker_handoff is not None:
        metadata["worker_handoff"] = worker_handoff
    if unknown:
        metadata["future_execution_fact"] = {"generation": 2}
    issue = {
        "id": item_id,
        "title": item_id,
        "description": item_id,
        "status": status,
        "metadata": metadata,
    }
    return issue, attachments


def _manifest_path(tmp_path, nodes: dict[str, Node]) -> tuple[Manifest, str]:
    manifest = Manifest(meta={"name": "large-dag"}, nodes=nodes)
    path = str(tmp_path / "manifest.yaml")
    save_manifest(manifest, path)
    return manifest, path


def _delivery_identity(issue: dict) -> dict:
    metadata = issue["metadata"]
    verification_ref = metadata["verification_ref"]
    return DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation=f"handoff-{issue['id']}",
        worker="worker",
        agent_id="agent-worker",
        run_id=f"run-{issue['id']}",
        pr_url=metadata["artifacts"]["pr_url"],
        pr_head_sha=metadata["artifacts"]["head_sha"],
        verification_sha256=verification_ref["sha256"],
        verification_attachment_id=verification_ref["attachment_id"],
        verification_comment_id=verification_ref["comment_id"],
        verification_uploader_id="agent-worker",
        verification_uploader_type="agent",
        verification_task_id=f"run-{issue['id']}",
        verification_created_at="2026-07-30T01:00:00Z",
    ).as_dict()


def test_146_node_observation_reads_every_issue_but_hydrates_only_needed_evidence(
    tmp_path,
):
    issues = {}
    attachments = {}
    nodes = {}
    active_ids = {f"active-{index}" for index in range(7)} | {"confirm-0"}
    historical_ids = [f"done-{index}" for index in range(69)] + [
        f"blocked-{index}" for index in range(69)
    ]
    for item_id in [*historical_ids, *sorted(active_ids)]:
        if item_id.startswith("done-"):
            issue_status, phase, manifest_status = "done", "review", "done"
        elif item_id.startswith("blocked-"):
            blocked_index = int(item_id.rsplit("-", 1)[1])
            issue_status = "blocked"
            phase = "review" if blocked_index % 2 == 0 else "authoring"
            manifest_status = "blocked"
        elif item_id == "confirm-0":
            issue_status, phase, manifest_status = "done", "review", "done"
        else:
            issue_status, phase, manifest_status = "done", "authoring", "in_progress"
        issue, bodies = _issue(
            item_id, status=issue_status, phase=phase,
            review_verdict=(
                "pass" if manifest_status == "done"
                else "reject" if manifest_status == "blocked" and phase == "review"
                else None
            ))
        issues[item_id] = issue
        if item_id.startswith("done-"):
            issue["metadata"]["delivery_identity"] = _delivery_identity(issue)
        attachments.update(bodies)
        nodes[item_id] = Node(
            id=item_id,
            worker="worker",
            reviewer=None if item_id == "confirm-0" else "reviewer",
            work_item_id=item_id,
            status=manifest_status,
            merged=item_id.startswith("done-"),
            merged_at=(
                "2026-07-30T00:00:00Z" if item_id.startswith("done-") else None
            ),
        )
    manifest, path = _manifest_path(tmp_path, nodes)

    legacy_remote = _RemoteFixture(issues, attachments)
    legacy_store = _store(legacy_remote)
    for key, node in nodes.items():
        item = legacy_store.get_work_item(node.work_item_id)
        if loop._requires_pull_request_observation(node, item):
            legacy_store.observe_pull_request(loop._pull_request_url(item))

    optimized_remote = _RemoteFixture(issues, attachments)
    optimized_store = _store(optimized_remote)
    loop._observe_reconcile_inputs(optimized_store, manifest)

    assert (
        legacy_remote.issue_gets,
        legacy_remote.attachment_downloads,
        legacy_remote.pr_observations,
    ) == (146, 584, 1)
    assert (
        optimized_remote.issue_gets,
        optimized_remote.attachment_downloads,
        optimized_remote.pr_observations,
    ) == (146, 18, 1)
    assert all(kind == "issue" for kind, _ in optimized_remote.calls[:146])
    assert all(kind == "attachment" for kind, _ in optimized_remote.calls[146:-1])
    assert optimized_remote.calls[-1][0] == "pr"
    assert load_manifest(path).nodes["active-0"].status == "in_progress"


@pytest.mark.parametrize(
    ("manifest_status", "merged", "merged_at"),
    [
        ("blocked", False, None),
        ("done", True, "2026-07-29T00:00:00Z"),
    ],
)
def test_stale_manifest_hydrates_new_platform_delivery_and_reenters_gate(
    tmp_path, manifest_status, merged, merged_at,
):
    issue, attachments = _issue(
        "node-a", status="done", phase="authoring", review_verdict=None)
    remote = _RemoteFixture({"node-a": issue}, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a", worker="worker", reviewer="reviewer",
            work_item_id="node-a", status=manifest_status,
            merged=merged, merged_at=merged_at),
    })

    assert loop.reconcile(store, manifest, path) is True

    assert manifest.nodes["node-a"].status == "in_progress"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 2


def test_control_projection_preserves_unknown_fields_without_hydrating_payloads():
    issue, attachments = _issue(
        "node-a", status="todo", phase="authoring", unknown=True)
    remote = _RemoteFixture({"node-a": issue}, attachments)
    projection = _store(remote).observe_work_item_control("node-a")

    assert projection.work_item.unknown_persisted_fields == {
        "metadata.future_execution_fact": {"generation": 2},
    }
    assert _pristine_amendment_activity_projection(
        projection.work_item)["unknown_persisted_fields"]
    assert WorkItemPayload.VERIFICATION in projection.deferred_payloads
    assert projection.work_item.verification is None
    assert remote.attachment_downloads == 0


def test_running_delivery_hydration_preserves_digest_identity_and_subject_binding():
    item_id = "node-a"
    issue, attachments = _issue(item_id, status="done", phase="review")
    verification_ref = issue["metadata"]["verification_ref"]
    verification_body = attachments[verification_ref["attachment_id"]][1]
    verification = yaml.safe_load(verification_body)
    identity = DeliveryIdentity(
        schema=DELIVERY_IDENTITY_SCHEMA,
        handoff_generation="handoff-1",
        worker="worker",
        agent_id="agent-worker",
        run_id="run-worker",
        pr_url=issue["metadata"]["artifacts"]["pr_url"],
        pr_head_sha=issue["metadata"]["artifacts"]["head_sha"],
        verification_sha256=verification_ref["sha256"],
        verification_attachment_id=verification_ref["attachment_id"],
        verification_comment_id=verification_ref["comment_id"],
        verification_uploader_id="agent-worker",
        verification_uploader_type="agent",
        verification_task_id="run-worker",
        verification_created_at="2026-07-30T01:00:00Z",
    )
    expected = WorkItem(
        id=item_id, workspace_id="ws", title=item_id, description=item_id,
        status=WorkItemStatus.DONE, dag_key=item_id, worker="worker",
        reviewer="reviewer", artifacts=issue["metadata"]["artifacts"],
        verification=verification, verification_ref=verification_ref,
        delivery_identity=identity, phase=TaskPhase.REVIEW,
    )
    subject = review_subject_digest(expected, 1)
    issue["metadata"]["delivery_identity"] = identity.as_dict()
    issue["metadata"]["review_subject_digest"] = subject
    issue["metadata"]["review_verdict"] = "pass"
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    store.read_pull_request_readiness = lambda _url: PullRequestReadiness(
        False, "OPEN", head_sha=issue["metadata"]["artifacts"]["head_sha"])

    projection = store.observe_work_item_control(item_id)
    plan = loop._build_work_item_hydration_plan(
        Node(id=item_id, worker="worker", reviewer="reviewer",
             work_item_id=item_id, status="in_review"),
        projection,
    )
    observed = store.hydrate_work_item_evidence(projection, plan)

    assert observed.verification == verification
    assert loop._review_subject_is_current(
        Manifest(meta={}, nodes={item_id: Node(
            id=item_id, worker="worker", reviewer="reviewer",
            work_item_id=item_id, status="in_review")}),
        item_id,
        observed,
    )
    loop._validate_controller_sealed_delivery(store, observed)


def test_new_head_cannot_reuse_old_verification_or_verdict():
    item_id = "node-a"
    old_verification = {"commands": [{"cmd": "pytest old", "exit_code": 0}]}
    old_item = WorkItem(
        id=item_id, workspace_id="ws", title=item_id, description=item_id,
        status=WorkItemStatus.DONE, dag_key=item_id, worker="worker",
        reviewer="reviewer",
        artifacts={"pr_url": "https://github.com/acme/repo/pull/1", "head_sha": "old-head"},
        verification=old_verification,
        phase=TaskPhase.REVIEW,
    )
    old_subject = review_subject_digest(old_item, 1)
    issue, attachments = _issue(
        item_id, status="done", phase="review",
        review_verdict="pass", review_subject=old_subject)
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    store.read_pull_request_readiness = lambda _url: PullRequestReadiness(
        False, "OPEN", head_sha=issue["metadata"]["artifacts"]["head_sha"])
    projection = store.observe_work_item_control(item_id)
    plan = loop._build_work_item_hydration_plan(
        Node(id=item_id, worker="worker", reviewer="reviewer",
             work_item_id=item_id, status="in_review"),
        projection,
    )
    current = store.hydrate_work_item_evidence(projection, plan)

    assert loop._current_delivery_passed_review(current) is False


def test_legacy_confirmed_done_hydrates_new_review_phase_verification(
    tmp_path,
):
    item_id = "node-a"
    old_verification = {"commands": [{"cmd": "pytest old", "exit_code": 0}]}
    old_item = WorkItem(
        id=item_id, workspace_id="ws", title=item_id, description=item_id,
        status=WorkItemStatus.DONE, dag_key=item_id, worker="worker",
        reviewer="reviewer",
        artifacts={"pr_url": "https://github.com/acme/repo/pull/1", "head_sha": "old-head"},
        verification=old_verification,
        phase=TaskPhase.REVIEW,
    )
    issue, attachments = _issue(
        item_id,
        status="done",
        phase="review",
        review_verdict="pass",
        review_subject=review_subject_digest(old_item, 1),
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="done",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
    })

    assert loop.reconcile(_store(remote), manifest, path) is True

    assert manifest.nodes[item_id].status == "in_progress"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 4


def test_unknown_control_fact_disables_confirmed_done_fast_path():
    item_id = "node-a"
    issue, attachments = _issue(
        item_id, status="done", phase="review", review_verdict="pass", unknown=True)
    issue["metadata"]["delivery_identity"] = _delivery_identity(issue)
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    projection = store.observe_work_item_control(item_id)

    plan = loop._build_work_item_hydration_plan(
        Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="done",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
        projection,
    )
    store.hydrate_work_item_evidence(projection, plan)

    assert plan == frozenset(WorkItemPayload)
    assert remote.attachment_downloads == 4


def test_missing_attachment_digest_disables_confirmed_done_fast_path():
    item_id = "node-a"
    issue, attachments = _issue(
        item_id, status="done", phase="review", review_verdict="pass")
    issue["metadata"]["delivery_identity"] = _delivery_identity(issue)
    issue["metadata"]["verification_ref"].pop("sha256")
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    projection = store.observe_work_item_control(item_id)

    plan = loop._build_work_item_hydration_plan(
        Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="done",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
        projection,
    )
    store.hydrate_work_item_evidence(projection, plan)

    assert plan == frozenset(WorkItemPayload)
    assert remote.attachment_downloads == 4


def test_required_attachment_failure_is_atomic_after_all_control_reads(tmp_path):
    issues = {}
    attachments = {}
    nodes = {}
    for item_id in ("node-a", "node-b"):
        issue, bodies = _issue(item_id, status="done", phase="authoring")
        issues[item_id] = issue
        attachments.update(bodies)
        nodes[item_id] = Node(
            id=item_id, worker="worker", reviewer="reviewer",
            work_item_id=item_id, status="blocked")
    remote = _RemoteFixture(issues, attachments)
    remote.fail_attachment_id = issues["node-b"]["metadata"]["verification_ref"][
        "attachment_id"]
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)
    before = Path(path).read_bytes()

    with pytest.raises(PlatformError, match="attachment read failed"):
        loop.reconcile(store, manifest, path)

    assert [kind for kind, _ in remote.calls[:2]] == ["issue", "issue"]
    assert manifest.nodes["node-a"].status == "blocked"
    assert manifest.nodes["node-b"].status == "blocked"
    assert Path(path).read_bytes() == before


def test_full_get_work_item_still_hydrates_every_referenced_payload():
    issue, attachments = _issue("node-a", status="in_review", phase="review")
    remote = _RemoteFixture({"node-a": issue}, attachments)

    item = _store(remote).get_work_item("node-a")

    assert item.verification["commands"][0]["cmd"] == "pytest -q"
    assert item.review_report["full_review_completed"] is True
    assert item.review_ledger["schema"] == "omac.review-ledger/v1"
    assert item.contract["objective"] == "deliver"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 4


def test_unknown_pr_state_never_preserves_unconfirmed_done(tmp_path):
    issue, attachments = _issue(
        "node-a", status="done", phase="review", review_verdict="pass")
    remote = _RemoteFixture({"node-a": issue}, attachments)
    store = _store(remote)
    store.observe_pull_request = lambda _url: PullRequestObservation(
        PullRequestState.UNKNOWN, detail="remote state unavailable")
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a", worker="worker", reviewer="reviewer",
            work_item_id="node-a", status="done",
            merge_request_state="requested"),
    })

    loop.reconcile(store, manifest, path)

    assert manifest.nodes["node-a"].status == "merging"
    assert manifest.nodes["node-a"].merged is False


def test_tick_collects_delivery_persisted_after_reconcile_control_snapshot(
    tmp_path,
):
    store = MockStore(EngineConfig(
        engine_type="mock",
        workspace_id="fresh-observation",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))
    runtime = MockRuntime(store)
    item = store.create_work_item(
        "fresh-observation",
        "node-a",
        "node-a",
        dag_key="node-a",
        worker="alice",
        reviewer="bob",
    )
    store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a",
            worker="alice",
            reviewer="bob",
            work_item_id=item.id,
            status="in_progress",
        ),
    })
    original_observe = store.observe_work_item_control
    delivered = False

    def observe_then_deliver(item_id):
        nonlocal delivered
        projection = original_observe(item_id)
        if not delivered:
            verification = {
                "commands": [{"cmd": "pytest -q", "exit_code": 0}],
                "integration_gates": [],
                "coverage": 100,
            }
            store.update_work_item_metadata(
                item_id,
                artifacts={
                    "pr_url": "https://github.com/acme/repo/pull/1",
                    "head_sha": "head-node-a",
                },
                verification=verification,
                verification_source=yaml.safe_dump(verification),
                phase=TaskPhase.AUTHORING,
            )
            store.update_status(item_id, WorkItemStatus.DONE)
            delivered = True
        return projection

    store.observe_work_item_control = observe_then_deliver

    result = loop.tick(
        store, runtime, manifest, path, max_parallel=1, config={})

    current = store.get_work_item(item.id)
    assert result.state == "running"
    assert manifest.nodes["node-a"].status == "in_review"
    assert current.phase is TaskPhase.REVIEW
    assert current.status is WorkItemStatus.IN_REVIEW
