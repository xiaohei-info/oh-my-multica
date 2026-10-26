"""What the Store persists on a real Multica issue: task kind/phase/bounces, control records, and bodies kept as attachments.

The Store is written and read back through the port; the issue as the platform holds it is read through the CLI.
"""
import itertools
import json

import pytest

from _support.taskmeta_samples import (
    delivery_identity, review_nits_handoff_payload, reviewer_run_baseline, worker_handoff_intent,
)
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase


def raw_metadata(stack, item_id):
    """The issue's metadata as the platform holds it (structured values are stored as JSON text)."""
    metadata = json.loads(stack.multica("issue", "get", item_id, "--output", "json"))["metadata"]
    return {k: json.loads(v) if isinstance(v, str) and v[:1] in "{[" else v for k, v in metadata.items()}


def comments(stack, item_id):
    rows = json.loads(stack.multica("issue", "comment", "list", item_id, "--output", "json") or "[]")
    return rows.get("comments", rows) if isinstance(rows, dict) else rows


_keys = itertools.count(1)   # an active issue per DAG key: every item of the session needs its own


def create(store, stack, **kwargs):
    return store.create_work_item(stack.workspace_id, "t", "d", dag_key=f"meta-{next(_keys)}", worker="alice", **kwargs)


def test_kind_phase_and_bounces_default_on_create_and_roundtrip_on_update(store, stack):
    plan = create(store, stack, kind=TaskKind.PLAN)
    fresh = store.get_work_item(plan.id)
    assert (fresh.kind, fresh.phase, fresh.bounces, fresh.deliverable) == (TaskKind.PLAN, TaskPhase.AUTHORING, Bounces(), None)
    assert raw_metadata(stack, plan.id)["kind"] == "plan"
    assert store.get_work_item(create(store, stack).id).kind is TaskKind.DEVELOP   # no kind given: the default

    store.update_work_item_metadata(plan.id, phase=TaskPhase.REVIEW)
    store.update_work_item_metadata(plan.id, ci_bounce=1, review_bounce=2, merge_bounce=3, deliverable="manifest: ...")
    got = store.get_work_item(plan.id)
    assert (got.phase, got.bounces, got.deliverable) == (TaskPhase.REVIEW, Bounces(ci=1, review=2, merge=3), "manifest: ...")
    store.update_work_item_metadata(plan.id, review_bounce=got.bounces.review + 1)   # read, add one, write back
    assert store.get_work_item(plan.id).bounces.review == 3

    metadata = raw_metadata(stack, plan.id)
    assert [metadata[k] for k in ("kind", "phase", "ci_bounce", "review_bounce", "merge_bounce")] == ["plan", "review", 1, 3, 3]


def test_an_issue_created_without_omac_metadata_reads_as_a_develop_task(store, stack):
    legacy = json.loads(stack.multica("issue", "create", "--title", "old", "--output", "json"))
    got = store.get_work_item(legacy["id"])
    assert (got.kind, got.phase, got.bounces) == (TaskKind.DEVELOP, TaskPhase.AUTHORING, Bounces())
    store.cancel_work_item(legacy["id"])


@pytest.mark.parametrize("field,value,clears", [
    ("review_continuation", {"schema": "omac.review-continuation/v1", "stage": "decompose", "mode": "producer-rework",
                            "authorized_through_round": 9, "decision_count": 1, "reason": "operator decision"}, False),
    ("review_nits_acceptance", {"schema": "omac.review-nits-acceptance/v1", "review_subject_digest": "subject-1",
                               "review_report_ref": {"attachment_id": "att-1", "sha256": "a" * 64}, "verdict": "pass-with-nits"}, True),
    ("reviewer_run_baseline", reviewer_run_baseline(), True),
    ("worker_handoff", worker_handoff_intent(), True),
    ("delivery_identity", delivery_identity(), True),
])
def test_a_control_record_roundtrips_and_an_empty_value_clears_it(store, stack, field, value, clears):
    item = create(store, stack)

    store.update_work_item_metadata(item.id, **{field: value})
    assert getattr(store.get_work_item(item.id), field) == value
    if clears:
        store.update_work_item_metadata(item.id, **{field: {}})
        assert getattr(store.get_work_item(item.id), field) is None
        assert not raw_metadata(stack, item.id).get(field)


def test_a_worker_handoff_carries_the_review_feedback_it_was_made_from(store, stack):
    item = create(store, stack)
    payload = review_nits_handoff_payload()
    store.update_work_item_metadata(item.id, worker_handoff=payload)

    persisted = store.get_work_item(item.id).worker_handoff
    assert persisted.source_review_feedback == payload["source_review_feedback"]
    assert persisted.is_complete() and persisted.is_causally_bound()


@pytest.mark.parametrize("damage", [
    {"source_review_feedback": None}, {"source_review_verdict": "reject"}, {"nits": []}, {"report_ref": None},
    {"report_ref": {"attachment_id": "", "sha256": "a" * 64}}, {"report_ref": {"attachment_id": "review-1", "sha256": "not-a-sha"}},
    {"unknown": True},
], ids=["no-feedback", "verdict-mismatch", "empty-nits", "no-report-ref", "blank-attachment", "bad-sha", "unknown-field"])
def test_a_damaged_review_feedback_is_stored_but_never_reads_as_a_usable_handoff(store, stack, damage):
    payload = review_nits_handoff_payload(**{k: v for k, v in damage.items() if k not in ("source_review_feedback", "source_review_verdict")})
    payload.update({k: v for k, v in damage.items() if k in ("source_review_feedback", "source_review_verdict")})
    item = create(store, stack)

    store.update_work_item_metadata(item.id, worker_handoff=payload)

    persisted = store.get_work_item(item.id).worker_handoff
    assert persisted is not None and not persisted.is_complete() and not persisted.is_causally_bound()


def test_a_deliverable_is_kept_as_an_attachment_and_referenced_from_metadata(store, stack):
    item = create(store, stack, kind=TaskKind.ACCEPTANCE)
    store.update_work_item_metadata(item.id, deliverable="acceptance: ...")

    metadata = raw_metadata(stack, item.id)
    ref = metadata["deliverable_ref"]
    posted = comments(stack, item.id)
    assert "deliverable" not in metadata and ref["sha256"] and ref["comment_id"] == posted[0]["id"]
    assert posted[0]["attachments"][0]["id"] == ref["attachment_id"]
    assert "<!-- omac-deliverable-begin -->" not in posted[0]["content"]


@pytest.mark.parametrize("field,body,prefix,payload", [
    ("review_report", "review_goals:\n- g\nblockers: []\nnits:\n- nit\n", "omac-review-report-",
     {"review_goals": ["g"], "blockers": [], "nits": ["nit"]}),
    ("verification", "commands:\n- cmd: pytest\n  exit_code: 0\n", "omac-verification-", {"commands": [{"cmd": "pytest", "exit_code": 0}]}),
])
def test_review_reports_and_verification_travel_only_as_attachments(store, stack, field, body, prefix, payload):
    item = create(store, stack, kind=TaskKind.DEVELOP)
    store.update_work_item_metadata(item.id, **{field: payload, f"{field}_source": body})

    assert getattr(store.get_work_item(item.id), field) == payload
    metadata = raw_metadata(stack, item.id)
    attachment = comments(stack, item.id)[0]["attachments"][0]
    assert field not in metadata and metadata[f"{field}_ref"]["sha256"]
    assert metadata[f"{field}_ref"]["attachment_id"] == attachment["id"] and attachment["filename"].startswith(prefix)


def test_a_payload_is_read_back_from_its_attachment_as_yaml_or_json_and_not_from_legacy_inline_metadata(store, stack):
    item = create(store, stack, kind=TaskKind.DEVELOP)
    store.update_work_item_metadata(
        item.id, verification={"inline": "legacy"}, verification_source="commands:\n- cmd: pytest\n  exit_code: 0\n",
        review_report={"inline": "legacy"}, review_report_source=json.dumps({"review_goals": ["json"], "blockers": [], "nits": []}))
    for key in ("verification", "review_report"):   # an older OMAC left the body inline as well
        stack.multica("issue", "metadata", "set", item.id, "--key", key, "--value", json.dumps({"inline": "legacy"}))

    got = store.get_work_item(item.id)

    assert got.verification == {"commands": [{"cmd": "pytest", "exit_code": 0}]}
    assert got.review_report == {"review_goals": ["json"], "blockers": [], "nits": []}


def test_a_transient_failure_while_downloading_a_payload_is_retried(store, stack, monkeypatch):
    from _support.it_proxy import Proxy, Rule
    from omac.engines import create_engine
    from omac.engines.models import EngineConfig

    item = create(store, stack, kind=TaskKind.DEVELOP)
    store.update_work_item_metadata(item.id, verification={"commands": [{"cmd": "pytest", "exit_code": 0}]},
                                    verification_source="commands:\n- cmd: pytest\n  exit_code: 0\n")
    reader = create_engine("multica", EngineConfig(engine_type="multica", workspace_id=stack.workspace_id)).store   # no cached bodies
    proxy = Proxy(stack.url).start()
    proxy.rules.append(Rule("GET", r"/attachments/[^/]+/signed-download", "fail_before", nth=1))   # the server answers 502 once
    monkeypatch.setenv("MULTICA_SERVER_URL", proxy.url)
    try:
        got = reader.get_work_item(item.id)
    finally:
        monkeypatch.setenv("MULTICA_SERVER_URL", stack.url)
        proxy.stop()

    assert got.verification == {"commands": [{"cmd": "pytest", "exit_code": 0}]}
    downloads = [row for row in proxy.log if "/signed-download" in row[1]]
    assert [row[2] for row in downloads][:2] == [502, 200]
