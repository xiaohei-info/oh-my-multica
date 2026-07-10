"""任务类型×阶段模型 — 让每条 issue 自描述(设计文档 §7.4)。

issue 的范围是一个完整阶段:产出、评审、回退往返都发生在同一条 issue
时间线上。当前阶段(phase)与承担者由 issue metadata + assignee 表达,
交接 = 转派(assign)。本模块集中定义:

- 任务类型 kind(5 种):plan / acceptance / decompose / develop / final-acceptance
- 阶段 phase(产出 / 评审):authoring / review
- 回退计数 bounces:ci_bounce / review_bounce / merge_bounce(每类有界,缺省 3)
- 通用交付物 metadata key / ref key(按 kind 承载 plan/acceptance/manifest 等交付)

本模块只管 schema 与读写映射,不做状态机推进——phase 流转与回退递增由
pipeline 经 WorkItemStore.update_work_item_metadata 写入,Store 只存取。
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# ==================== 枚举常量 ====================

class TaskKind(Enum):
    """issue 类型。review 是各类型内的阶段,不是独立 issue(§7.4)。"""
    PLAN = "plan"
    ACCEPTANCE = "acceptance"
    DECOMPOSE = "decompose"
    DEVELOP = "develop"
    FINAL_ACCEPTANCE = "final-acceptance"


class TaskPhase(Enum):
    """issue 当前阶段(同一条 issue 内的产出/评审两段)。"""
    AUTHORING = "authoring"
    REVIEW = "review"


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
# 旧 inline 交付物 key + 新引用 key。真实平台优先用 *_ref 承载 comment/attachment
# 引用,避免长正文或嵌套 JSON 塞进 metadata;读侧仍向后兼容旧 inline key。
DELIVERABLE_KEY = "deliverable"
DELIVERABLE_REF_KEY = "deliverable_ref"
CONTRACT_REF_KEY = "contract_ref"
VERIFICATION_REF_KEY = "verification_ref"
REVIEW_REPORT_REF_KEY = "review_report_ref"
DECISION_REQUIRED_KEY = "decision_required"
SOURCE_REFS_KEY = "source_refs"

# run_task 交付 dict 的 key(按 kind 承载交付正文)——单一来源,tasks/plan/mock 共用。
# decompose 正文是 manifest(≠ kind.value),故不能用 kind.value 直接推。
DELIVERY_CONTENT_KEY = {
    TaskKind.PLAN: "plan",
    TaskKind.ACCEPTANCE: "acceptance",
    TaskKind.DECOMPOSE: "manifest",
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
