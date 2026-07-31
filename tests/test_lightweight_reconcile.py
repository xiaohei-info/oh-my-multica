"""Large-DAG reconcile uses two-phase control observation and evidence hydration."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.core.review_convergence import review_subject_digest
from omac.core.taskmeta import (
    DELIVERY_IDENTITY_SCHEMA,
    DeliveryIdentity,
    TaskPhase,
    WorkerHandoffIntent,
)
from omac.engines.models import (
    AgentRunObservation,
    EngineConfig,
    PullRequestObservation,
    PullRequestReadiness,
    PullRequestState,
    WorkItem,
    WorkItemControlProjection,
    WorkItemPayload,
    WorkItemStatus,
)
from omac.engines.mock import MockRuntime, MockStore
from omac.engines.multica import MulticaStore
from omac.errors import PlatformError
from omac.pipeline import loop
from omac.pipeline.report import build_status_report
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
        self.attachment_error: Exception | None = None
        self.issue_get_hook = None

    def run(self, args, capture=True):
        if args[:2] == ["issue", "get"]:
            item_id = args[2]
            self.issue_gets += 1
            self.calls.append(("issue", item_id))
            if self.issue_get_hook is not None:
                self.issue_get_hook(item_id, self.issue_gets)
            return copy.deepcopy(self.issues[item_id])
        if args[:2] == ["attachment", "download"]:
            attachment_id = args[2]
            self.attachment_downloads += 1
            self.calls.append(("attachment", attachment_id))
            if attachment_id == self.fail_attachment_id:
                raise self.attachment_error or PlatformError(
                    f"attachment read failed: {attachment_id}")
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


class _ParallelHydrationStore:
    """Read-only Store double with observable work-item hydration concurrency."""

    def __init__(
        self,
        item_ids: list[str],
        *,
        delays: dict[str, float] | None = None,
        fail_item_id: str | None = None,
        shared_attachment_id: str | None = None,
        safe_parallelism: int | None = None,
    ):
        self.delays = delays or {}
        self.fail_item_id = fail_item_id
        self.safe_parallelism = safe_parallelism
        self.projections: dict[str, WorkItemControlProjection] = {}
        self.hydration_plans: dict[str, frozenset[WorkItemPayload]] = {}
        self.hydration_finished: list[str] = []
        self.pr_observations = 0
        self.active_hydrations = 0
        self.max_active_hydrations = 0
        self._lock = threading.Lock()
        for item_id in item_ids:
            attachment_id = shared_attachment_id or f"verification-{item_id}"
            item = WorkItem(
                id=item_id,
                workspace_id="ws",
                title=item_id,
                description=item_id,
                status=WorkItemStatus.DONE,
                dag_key=item_id,
                worker="worker",
                reviewer="reviewer",
                artifacts={
                    "pr_url": f"https://github.com/acme/repo/pull/{item_id}",
                    "head_sha": f"head-{item_id}",
                },
                verification_ref={"attachment_id": attachment_id},
                contract_ref={"attachment_id": f"contract-{item_id}"},
                phase=TaskPhase.AUTHORING,
            )
            self.projections[item_id] = WorkItemControlProjection(
                item,
                frozenset({
                    WorkItemPayload.VERIFICATION,
                    WorkItemPayload.CONTRACT,
                }),
            )

    def evidence_hydration_parallelism(self, requested: int) -> int:
        return requested if self.safe_parallelism is None else self.safe_parallelism

    def observe_work_item_control(self, item_id: str) -> WorkItemControlProjection:
        return self.projections[item_id]

    def hydrate_work_item_evidence(
        self,
        projection: WorkItemControlProjection,
        plan: frozenset[WorkItemPayload],
    ) -> WorkItem:
        item_id = projection.work_item.id
        with self._lock:
            self.active_hydrations += 1
            self.max_active_hydrations = max(
                self.max_active_hydrations, self.active_hydrations)
            self.hydration_plans[item_id] = plan
        try:
            time.sleep(self.delays.get(item_id, 0.0))
            if item_id == self.fail_item_id:
                raise PlatformError(f"hydrate failed: {item_id}")
            return replace(
                projection.work_item,
                verification={"item_id": item_id},
                contract={"item_id": item_id},
            )
        finally:
            with self._lock:
                self.active_hydrations -= 1
                self.hydration_finished.append(item_id)

    def observe_pull_request(self, _pr_url: str) -> PullRequestObservation:
        self.pr_observations += 1
        return PullRequestObservation(PullRequestState.UNKNOWN)


def _parallel_hydration_manifest(item_ids: list[str]) -> Manifest:
    return Manifest(
        meta={"name": "parallel-hydration"},
        nodes={
            item_id: Node(
                id=item_id,
                worker="worker",
                reviewer="reviewer",
                work_item_id=item_id,
                status="in_progress",
            )
            for item_id in item_ids
        },
    )


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


def _worker_handoff(
    issue: dict,
    *,
    item_id: str,
    baseline_attachment_id: str | None = None,
    terminal_observed_at: str | None = None,
) -> dict:
    verification_ref = issue["metadata"]["verification_ref"]
    return WorkerHandoffIntent(
        schema="omac.worker-handoff/v1",
        state="pending",
        target_worker="worker",
        gate="review",
        source_review_subject_digest=f"subject-{item_id}",
        source_review_round=1,
        target_review_bounce=1,
        generation=f"handoff-{item_id}",
        target_agent_id="agent-worker",
        baseline_direct_run_ids=(f"run-old-{item_id}",),
        baseline_verification_attachment_id=(
            baseline_attachment_id
            if baseline_attachment_id is not None
            else verification_ref["attachment_id"]
        ),
        target_run_id="run-worker",
        target_worker_bounce=0,
        terminal_observed_at=terminal_observed_at,
    ).as_dict()


def _terminal_runtime():
    return SimpleNamespace(
        list_runs=lambda _item_id: [AgentRunObservation(
            id="run-worker",
            kind="direct",
            status="completed",
            agent_id="agent-worker",
            created_at="2026-07-31T00:00:00Z",
            updated_at="2026-07-31T00:01:00Z",
        )],
        wake=lambda *_args: pytest.fail("terminal handoff must not wake Worker"),
    )


def _large_dag_fixture():
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
            issue_status, phase, manifest_status = (
                "in_progress", "authoring", "in_progress")
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
        elif item_id.startswith("active-"):
            stale_identity = _delivery_identity(issue)
            stale_identity["pr_head_sha"] = f"stale-{item_id}"
            issue["metadata"]["delivery_identity"] = stale_identity
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
    return issues, attachments, nodes


def test_reconcile_hydrates_eight_work_items_with_bounded_parallelism():
    item_ids = [f"item-{index}" for index in range(8)]
    store = _ParallelHydrationStore(
        item_ids,
        delays={item_id: 0.12 for item_id in item_ids},
    )
    manifest = _parallel_hydration_manifest(item_ids)

    started = time.monotonic()
    observations, _ = loop._observe_reconcile_inputs(
        store, manifest, max_parallel=4)
    elapsed = time.monotonic() - started

    assert elapsed < 0.55
    assert store.max_active_hydrations == 4
    assert list(observations) == item_ids
    assert {
        key: observation.work_item.verification["item_id"]
        for key, observation in observations.items()
    } == {item_id: item_id for item_id in item_ids}
    assert all(
        plan == frozenset({
            WorkItemPayload.VERIFICATION,
            WorkItemPayload.CONTRACT,
        })
        for plan in store.hydration_plans.values()
    )


def test_reconcile_hydration_completion_order_does_not_change_manifest_order():
    item_ids = [f"item-{index}" for index in range(4)]
    store = _ParallelHydrationStore(
        item_ids,
        delays={
            "item-0": 0.12,
            "item-1": 0.01,
            "item-2": 0.08,
            "item-3": 0.02,
        },
    )

    observations, _ = loop._observe_reconcile_inputs(
        store, _parallel_hydration_manifest(item_ids), max_parallel=4)

    assert store.hydration_finished != item_ids
    assert list(observations) == item_ids


def test_reconcile_respects_store_serial_hydration_capability():
    item_ids = [f"item-{index}" for index in range(4)]
    store = _ParallelHydrationStore(
        item_ids,
        delays={item_id: 0.02 for item_id in item_ids},
        safe_parallelism=1,
    )

    loop._observe_reconcile_inputs(
        store, _parallel_hydration_manifest(item_ids), max_parallel=8)

    assert store.max_active_hydrations == 1


def test_reconcile_hydration_failure_precedes_pr_reads_and_candidate_writes(
    tmp_path, monkeypatch,
):
    item_ids = [f"item-{index}" for index in range(8)]
    store = _ParallelHydrationStore(
        item_ids,
        delays={item_id: 0.02 for item_id in item_ids},
        fail_item_id="item-3",
    )
    manifest, path = _manifest_path(
        tmp_path,
        _parallel_hydration_manifest(item_ids).nodes,
    )
    monkeypatch.setattr(
        loop, "_requires_pull_request_observation", lambda *_args: True)
    candidate_calls = []
    monkeypatch.setattr(
        loop,
        "_reconcile_candidate",
        lambda *_args: candidate_calls.append(True),
    )

    with pytest.raises(PlatformError, match="hydrate failed: item-3"):
        loop.reconcile_with_observations(
            store, manifest, path, max_parallel=4)

    assert store.pr_observations == 0
    assert candidate_calls == []
    assert load_manifest(path).nodes["item-0"].status == "in_progress"


@pytest.mark.parametrize("item_ids", [[], ["item-0"]])
def test_reconcile_hydration_handles_zero_or_one_work_item(item_ids):
    store = _ParallelHydrationStore(item_ids)

    observations, pull_requests = loop._observe_reconcile_inputs(
        store, _parallel_hydration_manifest(item_ids), max_parallel=8)

    assert list(observations) == item_ids
    assert pull_requests == {}
    assert store.max_active_hydrations == (1 if item_ids else 0)


def test_reconcile_does_not_share_duplicate_attachment_refs_across_work_items():
    item_ids = ["item-a", "item-b"]
    store = _ParallelHydrationStore(
        item_ids,
        shared_attachment_id="shared-verification",
    )

    observations, _ = loop._observe_reconcile_inputs(
        store, _parallel_hydration_manifest(item_ids), max_parallel=2)

    assert set(store.hydration_plans) == set(item_ids)
    assert observations["item-a"].work_item.verification == {
        "item_id": "item-a"}
    assert observations["item-b"].work_item.verification == {
        "item_id": "item-b"}


def test_146_node_reconcile_phase_reads_every_issue_and_hydrates_only_needed_evidence(
    tmp_path,
):
    issues, attachments, nodes = _large_dag_fixture()
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


def test_146_node_status_reuses_reconcile_observation_budget(tmp_path):
    issues, attachments, nodes = _large_dag_fixture()
    remote = _RemoteFixture(issues, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)

    report = build_status_report(manifest, store, path)

    assert report["progress"]["total"] == 146
    assert (
        remote.issue_gets,
        remote.attachment_downloads,
        remote.pr_observations,
    ) == (146, 18, 1)


def test_146_node_full_tick_reuses_reconcile_observations_for_collect(
    tmp_path, monkeypatch,
):
    """A full tick must not repeat fresh reads already completed by reconcile."""
    issues, attachments, nodes = _large_dag_fixture()
    remote = _RemoteFixture(issues, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)
    wakes = []
    merges = []
    runtime = SimpleNamespace(
        wake=lambda item_id, worker, role: wakes.append((item_id, worker, role)),
        list_runs=lambda _item_id: [],
    )
    monkeypatch.setattr(
        loop,
        "_complete_merge_if_confirmed",
        lambda *_args, **_kwargs: merges.append(True) or "pending",
    )

    result = loop.tick(
        store, runtime, manifest, path, max_parallel=8, config={})

    assert result.state == "running"
    assert wakes == []
    assert merges == [True]
    assert sum(
        node.status in loop.RUNNING_STATUSES and node.work_item_id is not None
        for node in manifest.nodes.values()
    ) == 8
    assert (
        remote.issue_gets,
        remote.attachment_downloads,
        remote.pr_observations,
    ) == (146, 18, 1)


def test_terminal_worker_handoff_observation_downloads_no_attachments(tmp_path):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id, status="in_progress", phase="authoring")
    issue["metadata"]["worker_handoff"] = _worker_handoff(
        issue,
        item_id=item_id,
        terminal_observed_at=loop._utcnow().isoformat(),
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    manifest, _path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        ),
    })
    intent = store.observe_work_item_control(
        item_id).work_item.worker_handoff
    remote.issue_gets = 0
    remote.attachment_downloads = 0
    remote.calls.clear()

    for _ in range(3):
        result = loop._observe_worker_handoff(
            store, _terminal_runtime(), manifest, item_id, intent)
        assert result.state == "pending-submit"
        assert result.intent == intent

    assert remote.issue_gets == 3
    assert remote.attachment_downloads == 0


def test_terminal_worker_handoff_late_submit_hydrates_only_worker_evidence(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(item_id, status="done", phase="authoring")
    issue["metadata"]["worker_handoff"] = _worker_handoff(
        issue,
        item_id=item_id,
        baseline_attachment_id="verification-before-worker",
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    manifest, _path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        ),
    })
    store.read_pull_request_readiness = lambda _url: PullRequestReadiness(
        False, "OPEN", head_sha=issue["metadata"]["artifacts"]["head_sha"])

    def update_metadata(_item_id, **metadata):
        for key, value in metadata.items():
            if hasattr(value, "as_dict"):
                value = value.as_dict()
            issue["metadata"][key] = value

    monkeypatch.setattr(store, "update_work_item_metadata", update_metadata)
    intent = store.observe_work_item_control(
        item_id).work_item.worker_handoff
    remote.issue_gets = 0
    remote.attachment_downloads = 0
    remote.calls.clear()

    result = loop._observe_worker_handoff(
        store, _terminal_runtime(), manifest, item_id, intent)

    downloaded = [value for kind, value in remote.calls if kind == "attachment"]
    assert result.state == "complete"
    assert result.delivery_identity is not None
    assert len(downloaded) == 1
    assert set(downloaded) == {
        issue["metadata"]["verification_ref"]["attachment_id"]}
    assert "delivery_identity" not in issue["metadata"]
    assert issue["metadata"]["worker_handoff"] != {}


def test_late_submit_contract_hydration_failure_keeps_handoff_uncommitted(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id, status="in_progress", phase="authoring")
    original_handoff = _worker_handoff(issue, item_id=item_id)
    issue["metadata"]["worker_handoff"] = original_handoff
    fresh_body = yaml.safe_dump({
        "commands": [{"cmd": "pytest fresh", "exit_code": 0}],
        "integration_gates": [],
        "coverage": 100,
    }, sort_keys=False).encode()
    fresh_ref, fresh_attachment = _ref(
        item_id, "verification-fresh", "verification-fresh.yaml", fresh_body)
    attachments[fresh_ref["attachment_id"]] = fresh_attachment
    remote = _RemoteFixture({item_id: issue}, attachments)
    remote.fail_attachment_id = issue["metadata"]["contract_ref"][
        "attachment_id"]

    def submit_after_reconcile(_item_id, issue_get_number):
        if issue_get_number == 2:
            issue["status"] = "done"
            issue["metadata"]["verification_ref"] = fresh_ref

    remote.issue_get_hook = submit_after_reconcile
    store = _store(remote)
    store.read_pull_request_readiness = lambda _url: PullRequestReadiness(
        False, "OPEN", head_sha=issue["metadata"]["artifacts"]["head_sha"])
    writes = []

    def update_metadata(_item_id, **metadata):
        writes.append(copy.deepcopy(metadata))
        for key, value in metadata.items():
            issue["metadata"][key] = (
                value.as_dict() if hasattr(value, "as_dict") else value)

    monkeypatch.setattr(store, "update_work_item_metadata", update_metadata)
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        ),
    })

    with pytest.raises(PlatformError, match="attachment read failed"):
        loop.tick(
            store,
            _terminal_runtime(),
            manifest,
            path,
            max_parallel=1,
            config={},
        )

    assert "delivery_identity" not in issue["metadata"]
    persisted_handoff = issue["metadata"]["worker_handoff"]
    assert persisted_handoff["generation"] == original_handoff["generation"]
    assert persisted_handoff["target_run_id"] == original_handoff["target_run_id"]
    assert persisted_handoff["baseline_verification_attachment_id"] == (
        original_handoff["baseline_verification_attachment_id"])
    assert persisted_handoff["terminal_observed_at"]
    assert not any("delivery_identity" in write for write in writes)
    assert not any(write.get("worker_handoff") == {} for write in writes)
    assert manifest.nodes[item_id].status == "in_progress"
    downloaded = [value for kind, value in remote.calls if kind == "attachment"]
    assert downloaded.count(fresh_ref["attachment_id"]) == 1
    assert downloaded.count(remote.fail_attachment_id) == 1

    remote.fail_attachment_id = None
    remote.issue_get_hook = None
    remote.calls.clear()
    monkeypatch.setattr(loop, "validate_worker_evidence", lambda *_args: [])
    monkeypatch.setattr(loop, "advance_delivery", lambda *_args, **_kwargs: "bounce")

    loop.tick(
        store,
        _terminal_runtime(),
        manifest,
        path,
        max_parallel=1,
        config={},
    )

    assert issue["metadata"]["delivery_identity"]["handoff_generation"] == (
        original_handoff["generation"])
    assert issue["metadata"]["worker_handoff"] == {}
    retried_downloads = [
        value for kind, value in remote.calls if kind == "attachment"]
    assert retried_downloads.count(fresh_ref["attachment_id"]) == 1
    assert retried_downloads.count(
        issue["metadata"]["contract_ref"]["attachment_id"]) == 1


def test_late_submit_commits_handoff_only_after_complete_worker_evidence(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id, status="in_progress", phase="authoring")
    issue["metadata"]["worker_handoff"] = _worker_handoff(
        issue, item_id=item_id)
    fresh_body = yaml.safe_dump({
        "commands": [{"cmd": "pytest fresh", "exit_code": 0}],
        "integration_gates": [],
        "coverage": 100,
    }, sort_keys=False).encode()
    fresh_ref, fresh_attachment = _ref(
        item_id, "verification-fresh", "verification-fresh.yaml", fresh_body)
    attachments[fresh_ref["attachment_id"]] = fresh_attachment
    remote = _RemoteFixture({item_id: issue}, attachments)

    def submit_after_reconcile(_item_id, issue_get_number):
        if issue_get_number == 2:
            issue["status"] = "done"
            issue["metadata"]["verification_ref"] = fresh_ref

    remote.issue_get_hook = submit_after_reconcile
    store = _store(remote)
    store.read_pull_request_readiness = lambda _url: PullRequestReadiness(
        False, "OPEN", head_sha=issue["metadata"]["artifacts"]["head_sha"])

    def update_metadata(_item_id, **metadata):
        for key, value in metadata.items():
            issue["metadata"][key] = (
                value.as_dict() if hasattr(value, "as_dict") else value)

    monkeypatch.setattr(store, "update_work_item_metadata", update_metadata)
    evidence = []
    monkeypatch.setattr(
        loop,
        "validate_worker_evidence",
        lambda _node, item: evidence.append(item) or [],
    )
    monkeypatch.setattr(loop, "advance_delivery", lambda *_args, **_kwargs: "bounce")
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        ),
    })

    loop.tick(
        store,
        _terminal_runtime(),
        manifest,
        path,
        max_parallel=1,
        config={},
    )

    assert issue["metadata"]["delivery_identity"]
    assert issue["metadata"]["worker_handoff"] == {}
    assert len(evidence) == 1
    assert evidence[0].verification["commands"][0]["cmd"] == "pytest fresh"
    assert evidence[0].contract["objective"] == "deliver"
    downloaded = [value for kind, value in remote.calls if kind == "attachment"]
    assert downloaded.count(fresh_ref["attachment_id"]) == 1
    assert downloaded.count(issue["metadata"]["contract_ref"]["attachment_id"]) == 1


def test_eight_terminal_worker_handoffs_use_zero_attachment_reads(tmp_path):
    issues = {}
    attachments = {}
    nodes = {}
    observed_at = loop._utcnow().isoformat()
    for index in range(8):
        item_id = f"handoff-{index}"
        issue, bodies = _issue(
            item_id, status="in_progress", phase="authoring")
        issue["metadata"]["worker_handoff"] = _worker_handoff(
            issue,
            item_id=item_id,
            terminal_observed_at=observed_at,
        )
        issues[item_id] = issue
        attachments.update(bodies)
        nodes[item_id] = Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        )
    remote = _RemoteFixture(issues, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)

    result = loop.tick(
        store,
        _terminal_runtime(),
        manifest,
        path,
        max_parallel=8,
        config={},
    )

    assert result.state == "running"
    assert remote.issue_gets == 32
    assert remote.attachment_downloads == 0


def test_worker_handoff_required_hydration_failure_is_fail_closed(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(item_id, status="done", phase="authoring")
    issue["metadata"]["worker_handoff"] = _worker_handoff(
        issue,
        item_id=item_id,
        baseline_attachment_id="verification-before-worker",
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    remote.fail_attachment_id = issue["metadata"]["verification_ref"][
        "attachment_id"]
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        ),
    })
    effects = []
    for method_name in (
        "update_status",
        "update_work_item_metadata",
        "assign_work_item",
        "clear_assignment",
        "reset_review",
    ):
        monkeypatch.setattr(
            store,
            method_name,
            lambda *args, _method=method_name, **kwargs: effects.append(
                (_method, args, kwargs)),
        )

    with pytest.raises(PlatformError, match="attachment read failed"):
        loop.tick(
            store,
            _terminal_runtime(),
            manifest,
            path,
            max_parallel=1,
            config={},
        )

    assert effects == []


def test_146_node_late_submit_after_reconcile_is_collected_next_tick_without_wake(
    tmp_path, monkeypatch,
):
    issues, attachments, nodes = _large_dag_fixture()
    remote = _RemoteFixture(issues, attachments)
    old_verification_id = issues["active-0"]["metadata"][
        "verification_ref"]["attachment_id"]
    new_body = yaml.safe_dump({
        "commands": [{"cmd": "pytest fresh", "exit_code": 0}],
        "integration_gates": [],
        "coverage": 100,
    }, sort_keys=False).encode()
    new_ref, new_attachment = _ref(
        "active-0", "verification-fresh", "verification-fresh.yaml", new_body)
    attachments[new_ref["attachment_id"]] = new_attachment

    def submit_early_node_after_its_reconcile_read(_item_id, issue_get_number):
        if issue_get_number != 146:
            return
        issues["active-0"]["status"] = "done"
        issues["active-0"]["metadata"]["verification_ref"] = new_ref

    remote.issue_get_hook = submit_early_node_after_its_reconcile_read
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)
    wakes = []
    reviews = []
    runtime = SimpleNamespace(
        wake=lambda item_id, worker, role: wakes.append((item_id, worker, role)),
        list_runs=lambda _item_id: [],
    )
    monkeypatch.setattr(
        loop,
        "_dispatch_reviewer_for_current_subject",
        lambda _store, _runtime, _manifest, key: reviews.append(key) or True,
    )
    monkeypatch.setattr(loop, "commit_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        loop,
        "_complete_merge_if_confirmed",
        lambda *_args, **_kwargs: "pending",
    )

    first = loop.tick(
        store, runtime, manifest, path, max_parallel=2, config={})

    assert first.state == "running"
    assert issues["active-0"]["status"] == "done"
    assert manifest.nodes["active-0"].status == "in_progress"
    assert wakes == []
    assert reviews == []
    assert remote.issue_gets == 146

    remote.issue_get_hook = None
    second = loop.tick(
        store, runtime, manifest, path, max_parallel=2, config={})

    assert second.state == "running"
    assert manifest.nodes["active-0"].status == "in_review"
    assert reviews == ["active-0"]
    assert wakes == []
    downloaded = [value for kind, value in remote.calls if kind == "attachment"]
    assert old_verification_id not in downloaded[18:]
    assert new_ref["attachment_id"] in downloaded[18:]


def test_reconcile_observations_cover_collect_required_evidence(tmp_path):
    issues, attachments, nodes = _large_dag_fixture()
    remote = _RemoteFixture(issues, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)

    result = loop.reconcile_with_observations(store, manifest, path)

    for key, node in manifest.nodes.items():
        if node.status not in loop.RUNNING_STATUSES or not node.work_item_id:
            continue
        projection = result.observations[key]
        assert projection is not None
        required = loop._build_work_item_hydration_plan(node, projection)
        assert required.isdisjoint(projection.deferred_payloads)


def test_collect_results_without_observations_keeps_read_fallback_without_redispatch(
    tmp_path,
):
    issue, attachments = _issue(
        "node-a", status="in_progress", phase="authoring")
    remote = _RemoteFixture({"node-a": issue}, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a",
            worker="worker",
            work_item_id="node-a",
            status="in_progress",
        ),
    })
    wakes = []
    runtime = SimpleNamespace(
        wake=lambda *args: wakes.append(args),
        list_runs=lambda _item_id: [],
    )

    assert loop.collect_results(
        store, runtime, manifest, path, config={}) == {}

    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 4
    assert wakes == []


def test_tick_required_hydration_failure_precedes_lifecycle_side_effects(
    tmp_path, monkeypatch,
):
    issues = {}
    attachments = {}
    nodes = {}
    for item_id in ("node-a", "node-b"):
        issue, bodies = _issue(
            item_id, status="done", phase="authoring")
        issues[item_id] = issue
        attachments.update(bodies)
        nodes[item_id] = Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
        )
    remote = _RemoteFixture(issues, attachments)
    remote.fail_attachment_id = issues["node-b"]["metadata"][
        "verification_ref"]["attachment_id"]
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)
    before_manifest = copy.deepcopy(manifest)
    before_file = Path(path).read_bytes()
    effects = []
    runtime = SimpleNamespace(
        wake=lambda *args: effects.append(("wake", args)),
        list_runs=lambda _item_id: [],
    )
    for method_name in (
        "normalize_confirmed_merge",
        "update_status",
        "update_work_item_metadata",
        "assign_work_item",
        "add_comment",
        "reset_review",
        "clear_assignment",
        "request_pull_request_merge",
    ):
        monkeypatch.setattr(
            store,
            method_name,
            lambda *args, _method=method_name, **kwargs: effects.append(
                (_method, args, kwargs)),
        )
    monkeypatch.setattr(
        loop, "commit_manifest", lambda *args, **kwargs: effects.append(
            ("commit_manifest", args, kwargs)))

    with pytest.raises(PlatformError, match="attachment read failed"):
        loop.tick(store, runtime, manifest, path, max_parallel=2, config={})

    assert effects == []
    assert manifest == before_manifest
    assert Path(path).read_bytes() == before_file


@pytest.mark.parametrize("failed_item_id", ["node-a", "node-b"])
@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (PlatformError, "platform attachment read failed"),
        (ConnectionError, "network attachment read failed"),
        (RuntimeError, "unknown attachment read failed"),
    ],
)
def test_collect_results_fresh_read_failure_precedes_all_cross_node_side_effects(
    tmp_path, monkeypatch, failed_item_id, error_type, message,
):
    issues = {}
    attachments = {}
    nodes = {}
    for item_id in ("node-a", "node-b"):
        issue, bodies = _issue(
            item_id, status="in_progress", phase="authoring")
        issues[item_id] = issue
        attachments.update(bodies)
        nodes[item_id] = Node(
            id=item_id,
            worker="worker",
            work_item_id=item_id,
            status="in_progress",
        )
    remote = _RemoteFixture(issues, attachments)
    remote.fail_attachment_id = issues[failed_item_id]["metadata"][
        "verification_ref"]["attachment_id"]
    remote.attachment_error = error_type(message)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, nodes)
    before_manifest = copy.deepcopy(manifest)
    before_file = Path(path).read_bytes()
    before_issues = copy.deepcopy(remote.issues)
    effects = []
    runtime = SimpleNamespace(
        wake=lambda *args: effects.append(("wake", args)),
        list_runs=lambda _item_id: [],
    )
    for method_name in (
        "update_status",
        "update_work_item_metadata",
        "assign_work_item",
        "add_comment",
        "reset_review",
        "clear_assignment",
        "request_pull_request_merge",
    ):
        monkeypatch.setattr(
            store,
            method_name,
            lambda *args, _method=method_name, **kwargs: effects.append(
                (_method, args, kwargs)),
        )

    with pytest.raises(error_type, match=message):
        loop.collect_results(store, runtime, manifest, path, config={})

    assert effects == []
    assert remote.issue_gets == (1 if failed_item_id == "node-a" else 2)
    assert manifest == before_manifest
    assert Path(path).read_bytes() == before_file
    assert remote.issues == before_issues


@pytest.mark.parametrize(
    (
        "manifest_status", "merged", "merged_at", "merge_request_state",
        "expected_downloads",
    ),
    [
        ("blocked", False, None, None, 2),
        ("done", False, None, None, 4),
        ("done", True, "2026-07-29T00:00:00Z", "requested", 4),
    ],
)
def test_stale_manifest_hydrates_new_platform_delivery_and_reenters_gate(
    tmp_path, manifest_status, merged, merged_at, merge_request_state,
    expected_downloads,
):
    issue, attachments = _issue(
        "node-a", status="done", phase="authoring", review_verdict=None)
    remote = _RemoteFixture({"node-a": issue}, attachments)
    store = _store(remote)
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a", worker="worker", reviewer="reviewer",
            work_item_id="node-a", status=manifest_status,
            merged=merged, merged_at=merged_at,
            merge_request_state=merge_request_state),
    })

    assert loop.reconcile(store, manifest, path) is True

    assert manifest.nodes["node-a"].status == "in_progress"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == expected_downloads


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


def test_confirmed_merge_ignores_platform_authoring_delivery_without_hydration(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id,
        status="done",
        phase="authoring",
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

    wakes = []
    runtime = SimpleNamespace(
        wake=lambda *args: wakes.append(args),
        list_runs=lambda _item_id: [],
    )
    monkeypatch.setattr(loop, "commit_manifest", lambda *args, **kwargs: None)

    result = loop.tick(
        _store(remote), runtime, manifest, path, max_parallel=1, config={})

    assert result.state == "converged"
    assert load_manifest(path).nodes[item_id].status == "done"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 0
    assert remote.pr_observations == 0
    assert wakes == []


def test_cli_status_recovers_confirmed_merge_from_one_control_read_without_hydration(
    tmp_path, monkeypatch, capsys,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id,
        status="in_review",
        phase="authoring",
    )
    issue["assignee_id"] = "agent-reviewer"
    remote = _RemoteFixture({item_id: issue}, attachments)
    store = _store(remote)
    normalizations = []

    def normalize_confirmed_merge(observed_item_id):
        normalizations.append(observed_item_id)
        remote.issues[observed_item_id]["status"] = "done"
        remote.issues[observed_item_id].pop("assignee_id", None)

    monkeypatch.setattr(
        store, "normalize_confirmed_merge", normalize_confirmed_merge)
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="in_progress",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
    })

    from omac.cli.commands import dag

    monkeypatch.setattr(
        dag,
        "_assemble_engine",
        lambda _args: (SimpleNamespace(store=store), store.config),
    )

    assert main(["dag", "status", path, "--output", "json"]) == exit_codes.OK
    report = json.loads(capsys.readouterr().out)

    assert report["progress"] == {
        "total": 1,
        "done": 1,
        "running": 0,
        "todo": 0,
        "blocked": 0,
        "failed": 0,
        "abandoned": 0,
        "converged": True,
    }
    assert load_manifest(path).nodes[item_id].status == "done"
    assert remote.issues[item_id]["status"] == "done"
    assert "assignee_id" not in remote.issues[item_id]
    assert normalizations == [item_id]
    assert (
        remote.issue_gets,
        remote.attachment_downloads,
        remote.pr_observations,
    ) == (1, 0, 0)


def test_status_summary_preserves_deferred_verification_and_review_presence(
    tmp_path,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id,
        status="blocked",
        phase="review",
        review_verdict="reject",
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="blocked",
        ),
    })

    report = build_status_report(manifest, _store(remote), path)

    summary = report["needs_decision"]["failed_nodes"][0][
        "evidence_summary"]
    assert summary["has_verification"] is True
    assert summary["has_review"] is True
    assert summary["review_verdict"] == "reject"
    assert remote.issue_gets == 1
    assert remote.attachment_downloads == 0


def test_status_required_hydration_failure_produces_no_report_or_partial_write(
    tmp_path, monkeypatch,
):
    item_id = "node-a"
    issue, attachments = _issue(
        item_id,
        status="done",
        phase="authoring",
    )
    remote = _RemoteFixture({item_id: issue}, attachments)
    remote.fail_attachment_id = issue["metadata"]["verification_ref"][
        "attachment_id"]
    store = _store(remote)
    effects = []
    for method_name in (
        "normalize_confirmed_merge",
        "update_status",
        "assign_work_item",
        "clear_assignment",
    ):
        monkeypatch.setattr(
            store,
            method_name,
            lambda *args, _method=method_name, **kwargs: effects.append(
                (_method, args, kwargs)),
        )
    manifest, path = _manifest_path(tmp_path, {
        item_id: Node(
            id=item_id,
            worker="worker",
            reviewer="reviewer",
            work_item_id=item_id,
            status="blocked",
        ),
    })
    before_manifest = copy.deepcopy(manifest)
    before_file = Path(path).read_bytes()

    with pytest.raises(PlatformError, match="attachment read failed"):
        build_status_report(manifest, store, path)

    assert effects == []
    assert manifest == before_manifest
    assert Path(path).read_bytes() == before_file


def test_incident_confirmed_merge_recovers_manifest_and_platform_idempotently(
    tmp_path, monkeypatch,
):
    store = MockStore(EngineConfig(
        engine_type="mock",
        workspace_id="confirmed-merge-recovery",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))
    runtime = MockRuntime(store)
    item = store.create_work_item(
        "confirmed-merge-recovery",
        "node-a",
        "node-a",
        dag_key="node-a",
        worker="alice",
        reviewer="bob",
    )
    store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        artifacts={"pr_url": "https://github.com/acme/repo/pull/1"},
        verification={"commands": [{"cmd": "pytest -q", "exit_code": 0}]},
    )
    store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(item.id, "bob", "reviewer")
    runs_before = list(runtime.list_runs(item.id))
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a",
            worker="alice",
            reviewer="bob",
            work_item_id=item.id,
            status="in_progress",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
    })
    normalizations = []
    original_normalize = getattr(
        store, "normalize_confirmed_merge", None)

    def record_normalize(item_id):
        normalizations.append(item_id)
        assert original_normalize is not None
        return original_normalize(item_id)

    monkeypatch.setattr(
        store, "normalize_confirmed_merge", record_normalize, raising=False)

    assert loop.reconcile(store, manifest, path) is True

    recovered = store.get_work_item(item.id)
    assert manifest.nodes["node-a"].status == "done"
    assert recovered.status is WorkItemStatus.DONE
    assert recovered.platform_assignee_id is None
    assert runtime.list_runs(item.id) == runs_before
    assert normalizations == [item.id]

    assert loop.reconcile(store, manifest, path) is False
    assert normalizations == [item.id]
    assert runtime.list_runs(item.id) == runs_before


@pytest.mark.parametrize("outcome", ["failed", "unknown-after-commit"])
def test_confirmed_merge_recovery_write_fails_closed_without_partial_platform_state(
    tmp_path, monkeypatch, outcome,
):
    store = MockStore(EngineConfig(
        engine_type="mock",
        workspace_id=f"confirmed-merge-{outcome}",
        extra={"MOCK_AUTO_COMPLETE": "false"},
    ))
    runtime = MockRuntime(store)
    item = store.create_work_item(
        store.config.workspace_id,
        "node-a",
        "node-a",
        dag_key="node-a",
        worker="alice",
        reviewer="bob",
    )
    store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW)
    store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    store.assign_work_item(item.id, "bob", "reviewer")
    runs_before = list(runtime.list_runs(item.id))
    manifest, path = _manifest_path(tmp_path, {
        "node-a": Node(
            id="node-a",
            worker="alice",
            reviewer="bob",
            work_item_id=item.id,
            status="in_progress",
            merged=True,
            merged_at="2026-07-30T00:00:00Z",
        ),
    })
    before_file = Path(path).read_bytes()
    original_normalize = getattr(
        store, "normalize_confirmed_merge", None)

    def fail_normalize(item_id):
        if outcome == "unknown-after-commit":
            assert original_normalize is not None
            original_normalize(item_id)
        raise PlatformError("confirmed merge normalization outcome unknown")

    monkeypatch.setattr(
        store, "normalize_confirmed_merge", fail_normalize, raising=False)

    with pytest.raises(PlatformError, match="normalization outcome unknown"):
        loop.reconcile(store, manifest, path)

    current = store.get_work_item(item.id)
    platform_state = (current.status, current.platform_assignee_id)
    assert platform_state in {
        (WorkItemStatus.IN_REVIEW, "mock-agent-bob"),
        (WorkItemStatus.DONE, None),
    }
    assert manifest.nodes["node-a"].status == "in_progress"
    assert Path(path).read_bytes() == before_file
    assert runtime.list_runs(item.id) == runs_before


def test_unknown_control_fact_keeps_unconfirmed_done_on_fail_closed_hydration():
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
        ),
        projection,
    )
    store.hydrate_work_item_evidence(projection, plan)

    assert plan == frozenset(WorkItemPayload)
    assert remote.attachment_downloads == 4


def test_missing_attachment_digest_keeps_unconfirmed_done_on_fail_closed_hydration():
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
