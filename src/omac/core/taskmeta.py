"""任务类型×阶段模型 — 让每条 issue 自描述(设计文档 §7.4)。

issue 的范围是一个完整阶段:产出、评审、回退往返都发生在同一条 issue
时间线上。当前阶段(phase)与承担者由 issue metadata + assignee 表达,
交接 = 转派(assign)。本模块集中定义:

- 任务类型 kind:plan / acceptance / decompose / amendment / develop / final-acceptance
- 阶段 phase(产出 / 评审):authoring / review
- 回退计数 bounces:ci_bounce / review_bounce / merge_bounce(每类有界,缺省 3)
- 通用交付物 metadata key / ref key(按 kind 承载 plan/acceptance/manifest 等交付)

本模块只管 schema 与读写映射,不做状态机推进——phase 流转与回退递增由
pipeline 经 WorkItemStore.update_work_item_metadata 写入,Store 只存取。
"""
from __future__ import annotations

import re
import secrets
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple


# ==================== 枚举常量 ====================

class TaskKind(Enum):
    """issue 类型。review 是各类型内的阶段,不是独立 issue(§7.4)。"""
    PLAN = "plan"
    ACCEPTANCE = "acceptance"
    DECOMPOSE = "decompose"
    AMENDMENT = "amendment"
    DEVELOP = "develop"
    FINAL_ACCEPTANCE = "final-acceptance"


class TaskPhase(Enum):
    """issue 当前阶段(同一条 issue 内的产出、评审、人工确认)。"""
    AUTHORING = "authoring"
    REVIEW = "review"
    CONFIRMATION = "confirmation"


DEFAULT_KIND = TaskKind.DEVELOP
DEFAULT_PHASE = TaskPhase.AUTHORING

# 回退有界上限(设计文档 §7.3:缺省 3 次,耗尽 → blocked)
DEFAULT_MAX_BOUNCES = 3


def slug(value: str) -> str:
    """dag_key 片段归一化:只保留 ASCII 小写字母/数字,空值回退 task。"""
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "task"


def make_dag_key(
    kind: TaskKind,
    *,
    scope: Optional[str] = None,
    title: Optional[str] = None,
    unique: bool = False,
) -> str:
    """统一 dag_key 生成规则:<kind>-<scope/title>[-随机后缀]。"""
    base = f"{kind.value}-{slug(scope if scope is not None else title or 'task')}"
    return f"{base}-{secrets.token_hex(4)}" if unique else base


def make_plan_id() -> str:
    """plan create 的机器实例 ID;不从 --name 派生,避免中文/重名冲突。"""
    return f"p-{secrets.token_hex(4)}"


# ==================== metadata key 约定 ====================
# 全部 ASCII snake_case,与 multica issue metadata 一致(§12.3)。

KIND_KEY = "kind"
PHASE_KEY = "phase"
CI_BOUNCE_KEY = "ci_bounce"
REVIEW_BOUNCE_KEY = "review_bounce"
MERGE_BOUNCE_KEY = "merge_bounce"
WORKER_BOUNCE_KEY = "worker_bounce"
BOUNCE_BASELINE_KEY = "bounce_baseline"
# 旧 inline 交付物 key + 新引用 key。真实平台优先用 *_ref 承载 comment/attachment
# 引用,避免长正文或嵌套 JSON 塞进 metadata;读侧仍向后兼容旧 inline key。
DELIVERABLE_KEY = "deliverable"
DELIVERABLE_REF_KEY = "deliverable_ref"
PROJECT_RULES_KEY = "project_rules"
PROJECT_RULES_REF_KEY = "project_rules_ref"
CONTRACT_REF_KEY = "contract_ref"
VERIFICATION_REF_KEY = "verification_ref"
REVIEW_REPORT_REF_KEY = "review_report_ref"
REVIEW_SUBJECT_DIGEST_KEY = "review_subject_digest"
REVIEW_OBLIGATIONS_KEY = "review_obligations"
REVIEW_OBLIGATIONS_REF_KEY = "review_obligations_ref"
REVIEW_LEDGER_REF_KEY = "review_ledger_ref"
REVIEW_GENERATION_KEY = "review_generation"
REVIEW_LEDGER_GENERATION_KEY = "review_ledger_generation"
MACHINE_FEEDBACK_REF_KEY = "machine_feedback_ref"
REVIEW_CONTINUATION_KEY = "review_continuation"
REVIEW_NITS_ACCEPTANCE_KEY = "review_nits_acceptance"
REVIEW_NITS_ACCEPTANCE_SCHEMA = "omac.review-nits-acceptance/v1"
REVIEWER_RUN_BASELINE_KEY = "reviewer_run_baseline"
REVIEWER_RUN_BASELINE_SCHEMA = "omac.reviewer-run-baseline/v1"
WORKER_HANDOFF_KEY = "worker_handoff"
WORKER_HANDOFF_SCHEMA = "omac.worker-handoff/v1"
DELIVERY_IDENTITY_KEY = "delivery_identity"
DELIVERY_IDENTITY_SCHEMA = "omac.delivery-identity/v1"
DECISION_REQUIRED_KEY = "decision_required"
DECISION_REQUIRED_SCHEMA = "omac.decision-required/v1"
AMENDMENT_ATTEMPT_KEY = "amendment_attempt"
SOURCE_REFS_KEY = "source_refs"

# run_task 交付 dict 的 key(按 kind 承载交付正文)——单一来源,tasks/plan/mock 共用。
# decompose 正文是 manifest(≠ kind.value),故不能用 kind.value 直接推。
DELIVERY_CONTENT_KEY = {
    TaskKind.PLAN: "plan",
    TaskKind.ACCEPTANCE: "acceptance",
    TaskKind.DECOMPOSE: "manifest",
    TaskKind.AMENDMENT: "amendment",
    TaskKind.FINAL_ACCEPTANCE: "acceptance_results",
}


@dataclass
class Bounces:
    """回退计数:worker 未交付 / CI 失败 / 评审 reject / merge 冲突。"""
    worker: int = 0
    ci: int = 0
    review: int = 0
    merge: int = 0

    def as_dict(self) -> dict:
        return {
            WORKER_BOUNCE_KEY: self.worker,
            CI_BOUNCE_KEY: self.ci,
            REVIEW_BOUNCE_KEY: self.review,
            MERGE_BOUNCE_KEY: self.merge,
        }

    def total(self) -> int:
        return self.worker + self.ci + self.review + self.merge


def current_review_ledger(item: Any) -> Optional[dict[str, Any]]:
    """Return only the review ledger bound to the current control generation."""
    ledger = getattr(item, "review_ledger", None)
    current = getattr(item, "review_generation", None)
    ledger_generation = getattr(item, "review_ledger_generation", None)
    if current in (None, "") and ledger_generation in (None, ""):
        return ledger
    if current and current == ledger_generation:
        return ledger
    return None


@dataclass(frozen=True)
class WorkerHandoffIntent:
    """持久化的 review→worker 交接意图；只引用源评审，不复制完整报告。"""

    schema: Optional[str] = None
    state: Optional[str] = None
    target_worker: Optional[str] = None
    gate: Optional[str] = None
    source_review_subject_digest: Optional[str] = None
    source_review_round: Optional[int] = None
    source_review_verdict: Optional[str] = None
    source_review_feedback: Optional[dict[str, Any]] = None
    target_review_bounce: Optional[int] = None
    generation: Optional[str] = None
    target_agent_id: Optional[str] = None
    baseline_direct_run_ids: Tuple[str, ...] = ()
    # 基线封顶后消费端的时间戳门控:该时间之前创建的 Run 视为基线内。
    # 仅在基线被封顶时写入;None 保持纯 ID 成员判断的旧语义。
    baseline_cutoff_created_at: Optional[str] = None
    baseline_verification_attachment_id: Optional[str] = None
    target_run_id: Optional[str] = None
    target_worker_bounce: Optional[int] = None
    terminal_observed_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "state": self.state,
            "target_worker": self.target_worker,
            "gate": self.gate,
            "source_review_subject_digest": self.source_review_subject_digest,
            "source_review_round": self.source_review_round,
            "source_review_verdict": self.source_review_verdict,
            "source_review_feedback": deepcopy(self.source_review_feedback),
            "target_review_bounce": self.target_review_bounce,
            "generation": self.generation,
            "target_agent_id": self.target_agent_id,
            "baseline_direct_run_ids": list(self.baseline_direct_run_ids),
            "baseline_cutoff_created_at": self.baseline_cutoff_created_at,
            "baseline_verification_attachment_id": (
                self.baseline_verification_attachment_id
            ),
            "target_run_id": self.target_run_id,
            "target_worker_bounce": self.target_worker_bounce,
            "terminal_observed_at": self.terminal_observed_at,
        }

    def is_complete(self) -> bool:
        review_bounce_is_int = (
            isinstance(self.target_review_bounce, int)
            and not isinstance(self.target_review_bounce, bool)
        )
        if self.gate == "explicit-dispatch":
            review_bounce_valid = (
                review_bounce_is_int and self.target_review_bounce >= 0
            )
        elif self.gate in {"review", "review-nits", "operator-retry"}:
            review_bounce_valid = (
                review_bounce_is_int and self.target_review_bounce > 0
            )
        else:
            review_bounce_valid = False
        feedback_valid = self.source_review_feedback is None
        if self.gate == "review-nits":
            feedback_valid = bool(
                self.source_review_verdict == "pass-with-nits"
                and review_nits_feedback_is_complete(
                    self.source_review_feedback)
            )
        return bool(
            self.schema == WORKER_HANDOFF_SCHEMA
            and self.state == "pending"
            and self.target_worker
            and review_bounce_valid
            and self.source_review_subject_digest
            and isinstance(self.source_review_round, int)
            and not isinstance(self.source_review_round, bool)
            and self.source_review_round > 0
            and feedback_valid
            and (
                self.target_worker_bounce is None
                or (
                    isinstance(self.target_worker_bounce, int)
                    and not isinstance(self.target_worker_bounce, bool)
                    and self.target_worker_bounce >= 0
                )
            )
        )

    def is_causally_bound(self) -> bool:
        return bool(
            self.is_complete()
            and self.generation
            and self.target_agent_id
            and all(
                isinstance(run_id, str) and run_id
                for run_id in self.baseline_direct_run_ids
            )
        )


@dataclass(frozen=True)
class ReviewerRunBaseline:
    """当前 review subject 的 direct Run 因果边界，不承载评审状态。"""

    schema: Optional[str] = None
    subject_digest: Optional[str] = None
    target_reviewer: Optional[str] = None
    target_agent_id: Optional[str] = None
    cutoff_created_at: Optional[str] = None
    generation: Optional[str] = None
    attempt: int = 1
    baseline_direct_run_ids: Tuple[str, ...] = ()
    target_run_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "subject_digest": self.subject_digest,
            "target_reviewer": self.target_reviewer,
            "target_agent_id": self.target_agent_id,
            "cutoff_created_at": self.cutoff_created_at,
            "generation": self.generation,
            "attempt": self.attempt,
            "baseline_direct_run_ids": list(self.baseline_direct_run_ids),
            "target_run_id": self.target_run_id,
        }

    def is_causally_bound(self) -> bool:
        return bool(
            self.schema == REVIEWER_RUN_BASELINE_SCHEMA
            and self.subject_digest
            and self.target_reviewer
            and self.target_agent_id
            and self.cutoff_created_at
            and self.generation
            and isinstance(self.attempt, int)
            and not isinstance(self.attempt, bool)
            and self.attempt > 0
            and all(
                isinstance(run_id, str) and run_id
                for run_id in self.baseline_direct_run_ids
            )
        )


@dataclass(frozen=True)
class DeliveryIdentity:
    """Controller 依据平台事实封装的持久交付因果身份。"""

    schema: Optional[str] = None
    handoff_generation: Optional[str] = None
    worker: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    pr_url: Optional[str] = None
    pr_head_sha: Optional[str] = None
    verification_sha256: Optional[str] = None
    verification_attachment_id: Optional[str] = None
    verification_comment_id: Optional[str] = None
    verification_uploader_id: Optional[str] = None
    verification_uploader_type: Optional[str] = None
    verification_task_id: Optional[str] = None
    verification_created_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "handoff_generation": self.handoff_generation,
            "worker": self.worker,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "pr_url": self.pr_url,
            "pr_head_sha": self.pr_head_sha,
            "verification_sha256": self.verification_sha256,
            "verification_attachment_id": self.verification_attachment_id,
            "verification_comment_id": self.verification_comment_id,
            "verification_uploader_id": self.verification_uploader_id,
            "verification_uploader_type": self.verification_uploader_type,
            "verification_task_id": self.verification_task_id,
            "verification_created_at": self.verification_created_at,
        }

    def is_complete(self) -> bool:
        attachment_actor_is_bound = bool(
            self.verification_task_id
            or (
                self.verification_uploader_type == "agent"
                and self.verification_uploader_id
                and self.verification_created_at
            )
        )
        return bool(
            self.schema == DELIVERY_IDENTITY_SCHEMA
            and self.handoff_generation
            and self.worker
            and self.agent_id
            and self.run_id
            and self.pr_url
            and self.pr_head_sha
            and self.verification_sha256
            and self.verification_attachment_id
            and self.verification_comment_id
            and attachment_actor_is_bound
        )


# ==================== 解析(容错:旧数据缺字段走缺省) ====================

def parse_kind(value: Any) -> TaskKind:
    """旧 issue 未带 kind → 缺省 develop(向后兼容)。无法识别也走缺省,不抛。"""
    if isinstance(value, TaskKind):
        return value
    if value is None or value == "":
        return DEFAULT_KIND
    try:
        return TaskKind(str(value))
    except ValueError:
        return DEFAULT_KIND


def parse_phase(value: Any) -> TaskPhase:
    if isinstance(value, TaskPhase):
        return value
    if value is None or value == "":
        return DEFAULT_PHASE
    try:
        return TaskPhase(str(value))
    except ValueError:
        return DEFAULT_PHASE


def parse_bounce(value: Any) -> int:
    """回退计数容错解析:非数/负数/空 → 0。"""
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_bounces(metadata: dict) -> Bounces:
    """从 metadata dict 解出回退计数。"""
    return Bounces(
        worker=parse_bounce(metadata.get(WORKER_BOUNCE_KEY)),
        ci=parse_bounce(metadata.get(CI_BOUNCE_KEY)),
        review=parse_bounce(metadata.get(REVIEW_BOUNCE_KEY)),
        merge=parse_bounce(metadata.get(MERGE_BOUNCE_KEY)),
    )


def parse_worker_handoff(value: Any) -> Optional[WorkerHandoffIntent]:
    """空值表示无 intent；非空畸形值保留为不完整 typed intent 供调用方失效。"""
    if value in (None, "", {}):
        return None
    if isinstance(value, WorkerHandoffIntent):
        return value
    if not isinstance(value, dict):
        return WorkerHandoffIntent()

    def text_field(key: str) -> Optional[str]:
        field = value.get(key)
        return field if isinstance(field, str) and field else None

    def int_field(key: str) -> Optional[int]:
        field = value.get(key)
        return field if isinstance(field, int) and not isinstance(field, bool) else None

    return WorkerHandoffIntent(
        schema=text_field("schema"),
        state=text_field("state"),
        target_worker=text_field("target_worker"),
        gate=text_field("gate"),
        source_review_subject_digest=text_field(
            "source_review_subject_digest"),
        source_review_round=int_field("source_review_round"),
        source_review_verdict=text_field("source_review_verdict"),
        source_review_feedback=(
            deepcopy(value["source_review_feedback"])
            if isinstance(value.get("source_review_feedback"), dict)
            and value["source_review_feedback"]
            else None
        ),
        target_review_bounce=int_field("target_review_bounce"),
        generation=text_field("generation"),
        target_agent_id=text_field("target_agent_id"),
        baseline_direct_run_ids=tuple(
            run_id for run_id in value.get("baseline_direct_run_ids", [])
            if isinstance(run_id, str) and run_id
        ) if isinstance(value.get("baseline_direct_run_ids", []), list) else (),
        baseline_cutoff_created_at=text_field("baseline_cutoff_created_at"),
        baseline_verification_attachment_id=text_field(
            "baseline_verification_attachment_id"),
        target_run_id=text_field("target_run_id"),
        target_worker_bounce=int_field("target_worker_bounce"),
        terminal_observed_at=text_field("terminal_observed_at"),
    )


_REVIEW_FEEDBACK_FIELDS = frozenset({"verdict", "nits", "report_ref"})
_REVIEW_REPORT_REF_FIELDS = frozenset({
    "comment_id", "attachment_id", "sha256", "bytes", "filename",
})


def exact_review_report_ref(value: Any) -> bool:
    """Return whether a review report ref is downloadable and integrity-bound."""
    if not isinstance(value, dict) or not value:
        return False
    if set(value) - _REVIEW_REPORT_REF_FIELDS:
        return False
    attachment_id = value.get("attachment_id")
    sha256 = value.get("sha256")
    if not isinstance(attachment_id, str) or not attachment_id.strip():
        return False
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        return False
    for key in ("comment_id", "filename"):
        field = value.get(key)
        if field is not None and (not isinstance(field, str) or not field.strip()):
            return False
    size = value.get("bytes")
    return size is None or (
        isinstance(size, int) and not isinstance(size, bool) and size >= 0
    )


def review_nits_feedback_is_complete(value: Any) -> bool:
    """Validate the compact source feedback owned by one review-nits handoff."""
    if not isinstance(value, dict) or set(value) - _REVIEW_FEEDBACK_FIELDS:
        return False
    if value.get("verdict") != "pass-with-nits":
        return False
    nits = value.get("nits")
    if not isinstance(nits, list) or not nits:
        return False
    if any(not isinstance(nit, str) or not nit.strip() for nit in nits):
        return False
    return exact_review_report_ref(value.get("report_ref"))


_REVIEW_NITS_ACCEPTANCE_FIELDS = frozenset({
    "schema", "review_subject_digest", "review_report_ref", "verdict",
})


def review_nits_acceptance_is_valid(value: Any) -> bool:
    """Validate the bounded operator marker for an accepted nits verdict."""
    if not isinstance(value, dict) or set(value) != _REVIEW_NITS_ACCEPTANCE_FIELDS:
        return False
    return bool(
        value.get("schema") == REVIEW_NITS_ACCEPTANCE_SCHEMA
        and isinstance(value.get("review_subject_digest"), str)
        and bool(value["review_subject_digest"])
        and value.get("verdict") == "pass-with-nits"
        and exact_review_report_ref(value.get("review_report_ref"))
    )


def parse_reviewer_run_baseline(value: Any) -> Optional[ReviewerRunBaseline]:
    if value in (None, "", {}):
        return None
    if isinstance(value, ReviewerRunBaseline):
        return value
    if not isinstance(value, dict):
        return ReviewerRunBaseline()

    def text_field(key: str) -> Optional[str]:
        field = value.get(key)
        return field if isinstance(field, str) and field else None

    baseline = value.get("baseline_direct_run_ids", [])
    return ReviewerRunBaseline(
        schema=text_field("schema"),
        subject_digest=text_field("subject_digest"),
        target_reviewer=text_field("target_reviewer"),
        target_agent_id=text_field("target_agent_id"),
        cutoff_created_at=text_field("cutoff_created_at"),
        generation=text_field("generation"),
        attempt=(
            value.get("attempt")
            if isinstance(value.get("attempt"), int)
            and not isinstance(value.get("attempt"), bool)
            else 1
        ),
        baseline_direct_run_ids=tuple(
            run_id for run_id in baseline
            if isinstance(run_id, str) and run_id
        ) if isinstance(baseline, list) else (),
        target_run_id=text_field("target_run_id"),
    )


def parse_delivery_identity(value: Any) -> Optional[DeliveryIdentity]:
    if value in (None, "", {}):
        return None
    if isinstance(value, DeliveryIdentity):
        return value
    if not isinstance(value, dict):
        return DeliveryIdentity()

    def text_field(key: str) -> Optional[str]:
        field = value.get(key)
        return field if isinstance(field, str) and field else None

    return DeliveryIdentity(
        schema=text_field("schema"),
        handoff_generation=text_field("handoff_generation"),
        worker=text_field("worker"),
        agent_id=text_field("agent_id"),
        run_id=text_field("run_id"),
        pr_url=text_field("pr_url"),
        pr_head_sha=text_field("pr_head_sha"),
        verification_sha256=text_field("verification_sha256"),
        verification_attachment_id=text_field("verification_attachment_id"),
        verification_comment_id=text_field("verification_comment_id"),
        verification_uploader_id=text_field("verification_uploader_id"),
        verification_uploader_type=text_field("verification_uploader_type"),
        verification_task_id=text_field("verification_task_id"),
        verification_created_at=text_field("verification_created_at"),
    )
