"""任务类型×阶段模型的纯单元:解析容错、dag_key 生成、worker handoff 的完整性判定、metadata 写入策略。

写后读回(kind/phase/bounces 与各控制记录经 Store 持久化)在 Docker Multica 上验证:tests/it/test_it_store_metadata.py。
"""
import pytest

from omac.core import taskmeta
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase, WorkerHandoffIntent
from omac.engines.metadata_policy import assert_metadata_write_allowed
from _support.taskmeta_samples import review_nits_handoff_payload, worker_handoff_intent


# ==================== schema 单元 ====================

def test_parse_kind_defaults_develop_on_missing():
    assert taskmeta.parse_kind(None) == TaskKind.DEVELOP
    assert taskmeta.parse_kind("") == TaskKind.DEVELOP
    assert taskmeta.parse_kind("garbage") == TaskKind.DEVELOP  # 容错,不抛


def test_parse_phase_and_bounce_tolerant():
    assert taskmeta.parse_phase(None) == TaskPhase.AUTHORING
    assert taskmeta.parse_phase("review") == TaskPhase.REVIEW
    b = taskmeta.parse_bounces({"ci_bounce": "2", "review_bounce": "x", "merge_bounce": None})
    assert b == Bounces(ci=2, review=0, merge=0)


def test_make_dag_key_is_single_generation_rule():
    assert taskmeta.make_dag_key(TaskKind.PLAN, scope="Demo Feature") == "plan-demo-feature"
    assert taskmeta.make_dag_key(TaskKind.FINAL_ACCEPTANCE, scope="r1") == "final-acceptance-r1"
    assert taskmeta.make_dag_key(TaskKind.PLAN, title="中文计划").startswith("plan-task")
    a = taskmeta.make_dag_key(TaskKind.PLAN, title="Same", unique=True)
    b = taskmeta.make_dag_key(TaskKind.PLAN, title="Same", unique=True)
    assert a.startswith("plan-same-")
    assert b.startswith("plan-same-")
    assert a != b
    assert taskmeta.make_plan_id().startswith("p-")

def test_worker_handoff_allows_zero_review_bounce_for_explicit_dispatch():
    intent = worker_handoff_intent()
    intent = WorkerHandoffIntent(**{
        **intent.as_dict(),
        "gate": "explicit-dispatch",
        "target_review_bounce": 0,
        "target_worker_bounce": 0,
    })

    assert intent.is_causally_bound()


@pytest.mark.parametrize(
    "gate", ["review", "review-nits", "operator-retry", "unknown-gate"],
)
def test_worker_handoff_rejects_zero_review_bounce_for_other_gates(gate):
    intent = worker_handoff_intent()
    intent = WorkerHandoffIntent(**{
        **intent.as_dict(),
        "gate": gate,
        "target_review_bounce": 0,
    })

    assert not intent.is_complete()
    assert not intent.is_causally_bound()


def test_worker_handoff_rejects_unknown_gate_with_positive_bounce():
    intent = worker_handoff_intent()
    intent = WorkerHandoffIntent(**{
        **intent.as_dict(),
        "gate": "unknown-gate",
    })

    assert not intent.is_complete()
    assert not intent.is_causally_bound()


@pytest.mark.parametrize("gate", ["review", "operator-retry"])
def test_worker_handoff_keeps_positive_bounce_compatibility(gate):
    intent = worker_handoff_intent()
    intent = WorkerHandoffIntent(**{**intent.as_dict(), "gate": gate})

    assert intent.is_causally_bound()


def test_worker_handoff_round_trips_baseline_pr_head_sha():
    intent = worker_handoff_intent()

    parsed = taskmeta.parse_worker_handoff(intent.as_dict())

    assert parsed == intent
    assert parsed.baseline_pr_head_sha == "head-old"

    legacy = dict(intent.as_dict())
    legacy.pop("baseline_pr_head_sha")
    assert taskmeta.parse_worker_handoff(legacy).baseline_pr_head_sha is None


def test_worker_handoff_round_trips_baseline_cutoff_created_at():
    """gap #13:封顶基线的时间戳门控字段必须可持久化并容错解析。"""
    base = worker_handoff_intent()
    intent = WorkerHandoffIntent(**{
        **base.as_dict(),
        "baseline_direct_run_ids": tuple(base.baseline_direct_run_ids),
        "baseline_cutoff_created_at": "2026-02-01T00:39:00Z",
    })

    parsed = taskmeta.parse_worker_handoff(intent.as_dict())
    assert parsed == intent
    assert parsed.baseline_cutoff_created_at == "2026-02-01T00:39:00Z"

    # 旧数据无该字段 → 缺省 None,保持纯成员判断语义。
    legacy = dict(intent.as_dict())
    legacy.pop("baseline_cutoff_created_at")
    assert taskmeta.parse_worker_handoff(legacy).baseline_cutoff_created_at is None
    # 非字符串值 → 容错为 None。
    legacy["baseline_cutoff_created_at"] = 123
    assert taskmeta.parse_worker_handoff(legacy).baseline_cutoff_created_at is None


def test_worker_handoff_accepts_complete_review_nits_feedback():
    intent = taskmeta.parse_worker_handoff(review_nits_handoff_payload())

    assert intent is not None
    assert intent.is_complete()
    assert intent.is_causally_bound()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {**review_nits_handoff_payload(), "source_review_feedback": None},
            id="missing-feedback",
        ),
        pytest.param(
            {**review_nits_handoff_payload(), "source_review_verdict": "reject"},
            id="source-verdict-mismatch",
        ),
        pytest.param(
            review_nits_handoff_payload(verdict="reject"),
            id="feedback-verdict-mismatch",
        ),
        pytest.param(
            review_nits_handoff_payload(nits=[]),
            id="empty-nits",
        ),
        pytest.param(
            review_nits_handoff_payload(nits=["ok", ""]),
            id="blank-nit",
        ),
        pytest.param(
            review_nits_handoff_payload(nits=["ok", 1]),
            id="non-string-nit",
        ),
        pytest.param(
            review_nits_handoff_payload(extra="unknown"),
            id="unknown-feedback-field",
        ),
        pytest.param(
            review_nits_handoff_payload(report_ref=None),
            id="missing-report-ref",
        ),
        pytest.param(
            review_nits_handoff_payload(report_ref={
                "attachment_id": "",
                "sha256": "a" * 64,
            }),
            id="missing-report-attachment",
        ),
        pytest.param(
            review_nits_handoff_payload(report_ref={"attachment_id": "review-1"}),
            id="partial-report-ref",
        ),
        pytest.param(
            review_nits_handoff_payload(report_ref={
                "attachment_id": "review-1",
                "sha256": "not-a-sha",
            }),
            id="malformed-report-ref",
        ),
    ],
)
def test_worker_handoff_rejects_incomplete_review_nits_feedback(payload):
    intent = taskmeta.parse_worker_handoff(payload)

    assert intent is not None
    assert not intent.is_complete()
    assert not intent.is_causally_bound()


def test_metadata_policy_rejects_inline_prose_fields():
    with pytest.raises(ValueError):
        assert_metadata_write_allowed("contract", {"objective": "long contract prose"})
    with pytest.raises(ValueError):
        assert_metadata_write_allowed("review_report", {"summary": "long reviewer prose"})
    with pytest.raises(ValueError):
        assert_metadata_write_allowed("verification", {"commands": []})
    with pytest.raises(ValueError):
        assert_metadata_write_allowed(
            "decision_required",
            {"verdict": "pass-with-nits", "nits": [{"issue": "rename", "fix": "use x"}]},
        )


def test_metadata_policy_rejects_values_above_multica_limit():
    with pytest.raises(ValueError, match="8192 bytes"):
        assert_metadata_write_allowed("review_comment", "x" * 8193)


