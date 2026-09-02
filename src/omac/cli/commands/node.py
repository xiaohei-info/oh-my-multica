"""omac node — exit 20 之后的决策工具(重试是显式决策)。"""
from __future__ import annotations

import os
import secrets
from dataclasses import asdict, replace
from typing import Any

from .. import exit_codes
from ..output import add_output_flag, hint, print_json
from ...core.config import ENV_ENGINE, ENV_WORKSPACE, load_config, resolve_engine_settings
from ...core.manifest import (
    MISSING_CONSUMES, clear_confirmed_merge, load_manifest, save_manifest,
)
from ...core.graph import downstream_of
from ...core.taskmeta import (
    DECISION_REQUIRED_SCHEMA, REVIEW_NITS_ACCEPTANCE_SCHEMA,
    WORKER_HANDOFF_SCHEMA, WORKER_REWORK_FEEDBACK_SCHEMA,
    TaskKind, TaskPhase,
    WorkerHandoffIntent, exact_review_report_ref,
    review_nits_acceptance_is_valid,
)
from ...core.stage_recovery import (
    prepare_stage_recovery, stage_recovery_subject,
)
from ...engines import EngineConfig, create_engine
from ...engines.models import PullRequestState, WorkItemStatus
from ...errors import OmacError, ValidationError, WorkItemNotFoundError
from ...i18n import ui

NAME = "node"
SUMMARY = "exit 20 后的决策工具(show/retry/accept-nits/accept/abandon)"
DESCRIPTION = """异常处理闭环:dag run 以 exit 20 退出后,由调用者决策。

子命令:
  show     单节点完整证据链:contract、验证命令输出、评审 report(含评审目标)、
           env_setup、PR / 平台 issue 链接、回退计数
  retry    显式重置节点为 todo(可 --worker 换人),下次 dag run 生效。
           重试不会自动发生——这是设计原则(§2.4)
  accept-nits 接受当前 pass-with-nits 的非阻塞建议，恢复到 review/in_review，
           保留 Reviewer verdict/report；下一轮仍须观察真实 PR merge
  accept   人工接受已知风险,把节点标 done 后续跑
  abandon  放弃节点:标 abandoned,不硬依赖它的下游解锁

决策后重跑 `omac dag run`:已 done 节点复用,从决策后的状态继续推进。

abandoned 语义(§7.5):上游 abandoned 视同依赖已满足,下游可继续推进;
报告中会对经过 abandoned 上游的节点加注记。

硬约束:
  - 重试显式:节点 failed/blocked 后不会自动重试,必须经 `node retry` 显式决策;
    没有活跃的 `omac dag run` 时声称"持续监督中" = 假监督。
  - 失败隔离不可绕过:某节点 failed → 其下游自动 blocked,不会自动重置;
    只有 `node retry`(重置为 todo)、`node accept`(人工接受已知风险)或
    `node abandon`(放弃并解锁非硬依赖下游)能改变。
  - 防假收尾:汇报"完成"前必须核对 manifest,有非终态节点 + 无活跃 `dag run` = 未在监督,
    此时只有两条诚实路径:① 前台再跑 `dag run` 推进到终态;② 明确说"尚未收敛、当前未在监督"。
"""

# 回退计数:当前为占位(P4 评论线索落地后接入真实计数)。
_ROLLBACK_COUNT_PLACEHOLDER = 0


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    show = sub.add_parser("show", help="单节点完整证据链:contract + 证据")
    show.add_argument("manifest", help="manifest 文件路径")
    show.add_argument("node_key", help="节点 id(manifest.nodes[].id)")
    add_output_flag(show)

    retry = sub.add_parser("retry", help="显式重置节点为 todo(可换人)")
    retry.add_argument("manifest", help="manifest 文件路径")
    retry.add_argument("node_key", help="节点 id")
    retry.add_argument("--worker", help="改派给另一个 worker")
    retry.add_argument(
        "--stage", choices=("authoring", "review"), default="authoring",
        help="恢复阶段；默认 authoring，已有封存交付可显式恢复 review",
    )

    accept_nits = sub.add_parser(
        "accept-nits",
        help="接受 pass-with-nits 建议并恢复 review（不直接完成节点）",
    )
    accept_nits.add_argument("manifest", help="manifest 文件路径")
    accept_nits.add_argument("node_key", help="节点 id")

    accept = sub.add_parser("accept", help="人工接受已知风险,标记节点 done")
    accept.add_argument("manifest", help="manifest 文件路径")
    accept.add_argument("node_key", help="节点 id")

    abandon = sub.add_parser("abandon", help="放弃节点,解锁非硬依赖下游")
    abandon.add_argument("manifest", help="manifest 文件路径")
    abandon.add_argument("node_key", help="节点 id")


# ==================== helpers ====================

def _load_or_raise(path: str):
    if not os.path.exists(path):
        raise ValidationError(ui(
            f"Manifest file not found: {path}\n"
            "Create one with `omac plan create`, then run `omac node`.",
            f"manifest 文件不存在: {path}\n"
            f"提示:先生成 manifest(omac plan create),再运行 omac node。"))
    try:
        return load_manifest(path)
    except (OmacError, ValueError, KeyError) as e:
        raise ValidationError(ui(
            f"Could not parse manifest {path}: {e}",
            f"manifest 解析失败: {path}: {e}"))


def _require_node(manifest, key):
    if key not in manifest.nodes:
        avail = ", ".join(manifest.nodes) or ui("(none)", "(空)")
        raise ValidationError(ui(
            f"Node '{key}' is not in the manifest. Available nodes: {avail}",
            f"节点 '{key}' 不在 manifest 中。可用节点: {avail}"))
    return manifest.nodes[key]


def _contract_to_dict(contract):
    """contract → 可序列化 dict(对齐 save_manifest 的 dump 形状)。"""
    if contract is None:
        return None
    data = asdict(contract)
    if contract.consumes is MISSING_CONSUMES:
        data.pop("consumes", None)
    return data


def _build_engine(config: dict):
    """按 config + 环境变量装配引擎;失败返回 None(show 退化为 contract-only)。"""
    try:
        engine_type, workspace_id, project_id = resolve_engine_settings(config)
    except ValidationError:
        return None
    extra = {}
    for k, v in os.environ.items():
        if k.startswith("OMAC_") and k not in (ENV_ENGINE, ENV_WORKSPACE):
            extra[k] = v
    cfg = EngineConfig(
        engine_type=engine_type,
        workspace_id=workspace_id,
        project_id=project_id,
        extra=extra or {"MOCK_AUTO_COMPLETE": "false"},
    )
    return create_engine(engine_type, cfg)


def _work_item_status_to_str(status) -> str:
    if isinstance(status, WorkItemStatus):
        return status.value
    return str(status)


def _evidence_from_item(item) -> dict:
    """从 work item 提取证据链字段(store.get_work_item 的结果)。"""
    return {
        "work_item_id": item.id,
        "platform_status": _work_item_status_to_str(item.status),
        "artifacts": item.artifacts,
        "verification": item.verification,
        "review_verdict": item.review_verdict,
        "review_comment": item.review_comment,
        "review_report": item.review_report,
        "review_report_ref": getattr(item, "review_report_ref", None),
        "review_subject_digest": getattr(item, "review_subject_digest", None),
        "decision_required": getattr(item, "decision_required", None),
        "review_nits_acceptance": getattr(item, "review_nits_acceptance", None),
    }


# ==================== show ====================

def _cmd_show(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)

    engine = _build_engine(load_config())
    evidence: dict[str, Any] | None = None
    if node.work_item_id and engine is not None:
        try:
            item = engine.store.get_work_item(node.work_item_id)
            evidence = _evidence_from_item(item)
        except Exception as e:  # 平台读失败不阻断 show,降级为 contract-only
            hint(ui(
                f"Could not read work-item evidence (work_item_id={node.work_item_id}): {e}",
                f"读取工作单元证据失败(work_item_id={node.work_item_id}): {e}"))

    payload = {
        "node_key": node.id,
        "title": node.title,
        "status": node.status,
        "worker": node.worker,
        "reviewer": node.reviewer,
        "blocked_by": list(node.blocked_by),
        "work_item_id": node.work_item_id,
        "contract": _contract_to_dict(node.contract),
        "evidence": evidence,
        "rollback_count": _ROLLBACK_COUNT_PLACEHOLDER,
        "comments": ui(
            "P4 comment context is not available yet.",
            "P4(评论线索留待 P4 落地)"),
    }

    print_json(payload)
    hint(ui(
        "Evidence printed. Choose `omac node accept-nits|retry|accept|abandon`, then rerun `omac dag run`.",
        "证据链已输出。决策:omac node accept-nits|retry|accept|abandon 后 omac dag run 续跑生效。"))
    return exit_codes.OK


# ==================== retry ====================

def _validate_worker(manifest, node, new_worker: str, config: dict, engine) -> str:
    """校验 --worker 在 config.roles.workers 或 agent 池内。"""
    roles_workers = config.get("roles", {}).get("workers") if isinstance(config.get("roles"), dict) else None
    pool = set(roles_workers) if isinstance(roles_workers, list) and roles_workers else set()
    if not pool and engine is not None:
        # 用引擎解析后的有效 workspace_id(兼顾 config.yaml / env / 命令行),
        # 避免仅 env 设 OMAC_WORKSPACE_ID 时传入空串导致 list_members 返回空池。
        effective_ws = getattr(getattr(engine.store, "config", None), "workspace_id", None)
        if not effective_ws:
            effective_ws = config.get("workspace")
        if not effective_ws:
            raise ValidationError(ui(
                "Cannot determine the workspace for agent-pool validation. Set workspace in "
                "config.yaml, OMAC_WORKSPACE_ID, or --workspace.",
                "无法确定 workspace 以校验 agent 池 —— 三种给法任选:config.yaml 的 workspace 字段 / "
                "环境变量 OMAC_WORKSPACE_ID / 命令行 --workspace"))
        try:
            pool = set(engine.store.list_members(effective_ws))
        except Exception:
            pool = set()

    # 校验集合不可得时,退化为「与现有 worker 同名即放行 + 非空」,
    # 避免无配置环境把 retry 卡死。
    if not new_worker:
        raise ValidationError(ui("--worker cannot be empty", "--worker 不能为空"))
    if pool and new_worker not in pool:
        raise ValidationError(ui(
            f"Worker '{new_worker}' is not dispatchable. Available: {', '.join(sorted(pool))}\n"
            "Add it to roles.workers in config.yaml or verify workspace membership.",
            f"worker '{new_worker}' 不在可派发池内。可选: {', '.join(sorted(pool))}\n"
            f"提示:在 config.yaml 的 roles.workers 增补,或确认 agent 池成员。"))
    return new_worker


def _recover_delayed_reviewer_submission(
    manifest, node_key: str, node, engine, current, manifest_path: str,
) -> bool:
    """Bind a delayed-visible Reviewer Run without replaying either role."""
    from ...pipeline.loop import (
        _classify_reviewer_recovery_decision,
        _delayed_reviewer_recovery_marker_error,
        _formal_dispatch_target,
        _observe_direct_run_attempt,
    )

    decision_class = _classify_reviewer_recovery_decision(
        manifest_path, node_key, current)
    retry = f"omac node retry {manifest_path} {node.id} --stage review"

    def unsafe(detail: str) -> ValidationError:
        return ValidationError(ui(
            "Cannot recover the submitted Reviewer result because its Run "
            f"causality is not unique: {detail}. The existing decision and "
            f"review report were preserved; no Agent Run was started. Inspect "
            f"the issue Runs, then retry with `{retry}`.",
            "无法恢复已提交的 Reviewer 结果，因为 Run 因果关系不唯一："
            f"{detail}。现有 decision 与 review report 已保留，且没有启动 "
            f"Agent Run。请检查 issue Runs 后重试 `{retry}`。",
        ))

    if decision_class == "other":
        raise unsafe(
            "the persisted recovery decision does not identify this review")
    if decision_class == "explicit-review-retry":
        return False
    if decision_class != "canonical-baseline-unavailable":
        return False

    has_submitted_review = (
        current.review_verdict in {"pass", "pass-with-nits", "reject"}
        and isinstance(current.review_report, dict)
        and current.review_report
        and isinstance(current.review_report_ref, dict)
        and current.review_report_ref
    )
    decision = (
        current.decision_required
        if isinstance(current.decision_required, dict) else {}
    )
    dispatch_unresolved = (
        decision.get("reason_code") == "reviewer-run-dispatch-unresolved"
    )
    # A submitted review always needs its causal Run proven before replay. An
    # unresolved continuation dispatch may additionally own a delayed Run that
    # became visible after the observation window; adopting it below avoids a
    # duplicate Reviewer dispatch. Every other unsubmitted shape stays an
    # ordinary stage reset.
    if not has_submitted_review and not dispatch_unresolved:
        return False

    baseline = current.reviewer_run_baseline
    reviewer_id = engine.store.resolve_agent_id(node.reviewer)
    if not engine.runtime.capabilities.stable_direct_run_identity:
        raise unsafe("stable direct Run identity is unavailable")

    # Reuse the controller's direct-Run matcher. It proves direct kind, target
    # agent, strict cutoff, baseline exclusion, usable time, and uniqueness.
    marker_error = _delayed_reviewer_recovery_marker_error(
        manifest, manifest_path, node_key, current,
        node.reviewer, reviewer_id,
        require_target=False,
    )
    if marker_error is not None:
        raise unsafe(marker_error)

    runs = engine.runtime.list_runs(current.id)
    observed = _observe_direct_run_attempt(
        runs,
        reviewer_id,
        baseline_direct_run_ids=baseline.baseline_direct_run_ids,
        cutoff_created_at=baseline.cutoff_created_at,
        target_run_id=baseline.target_run_id,
        attempt=baseline.attempt,
    )
    _target, target_error = _formal_dispatch_target(runs, observed)
    if target_error is not None:
        if has_submitted_review:
            raise unsafe(target_error)
        # The unresolved continuation never materialized as a provable Run;
        # there is nothing to preserve, so the ordinary review-stage reset in
        # the caller redispatches the Reviewer exactly once.
        return False

    engine.store.update_work_item_metadata(
        current.id,
        reviewer_run_baseline=replace(
            baseline, target_run_id=observed.target_run_id),
        phase=TaskPhase.REVIEW,
    )
    engine.store.update_status(current.id, WorkItemStatus.IN_REVIEW)
    return True


def _operator_retry_feedback(current, prior_handoff):
    """Carry bounded actionable context into a new authoring generation."""
    def clip(value: str, limit: int = 256) -> str:
        encoded = value.encode("utf-8")
        return encoded[:limit].decode("utf-8", errors="ignore")

    feedback = {"schema": WORKER_REWORK_FEEDBACK_SCHEMA}
    verdict = current.review_verdict
    if verdict not in {"reject", "pass-with-nits"} and prior_handoff is not None:
        verdict = prior_handoff.source_review_verdict
    if verdict in {"reject", "pass-with-nits"}:
        feedback["verdict"] = verdict

    report_ref = current.review_report_ref
    prior_feedback = (
        prior_handoff.source_review_feedback
        if prior_handoff is not None
        and isinstance(prior_handoff.source_review_feedback, dict)
        else {}
    )
    if not isinstance(report_ref, dict) or not report_ref:
        report_ref = prior_feedback.get("report_ref")
    if exact_review_report_ref(report_ref):
        feedback["report_ref"] = dict(report_ref)

    ledger_ref = getattr(current, "review_ledger_ref", None)
    if not isinstance(ledger_ref, dict) or not ledger_ref:
        ledger_ref = prior_feedback.get("ledger_ref")
    if exact_review_report_ref(ledger_ref):
        feedback["ledger_ref"] = dict(ledger_ref)

    report = current.review_report
    blockers = []
    if isinstance(report, dict):
        for raw in report.get("blockers", []) or []:
            if not isinstance(raw, dict):
                continue
            compact = {}
            for field in ("root_cause_key", "summary", "required_fix"):
                value = raw.get(field)
                if isinstance(value, str) and value.strip():
                    compact[field] = clip(value)
            if compact:
                blockers.append(compact)
    if not blockers:
        prior_blockers = prior_feedback.get("blockers")
        if isinstance(prior_blockers, list):
            blockers = []
            for blocker in prior_blockers[:4]:
                if not isinstance(blocker, dict):
                    continue
                compact = {
                    field: clip(value)
                    for field, value in blocker.items()
                    if field in {"root_cause_key", "summary", "required_fix"}
                    and isinstance(value, str) and value.strip()
                }
                if compact:
                    blockers.append(compact)
    if blockers:
        feedback["blockers"] = blockers[:4]

    comment = current.review_comment
    if not comment:
        comment = prior_feedback.get("comment")
    if isinstance(comment, str) and comment.strip():
        feedback["comment"] = clip(comment)
    return feedback if len(feedback) > 1 else None


def _cmd_retry(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)
    stage = args.stage

    if stage == "review" and args.worker:
        raise ValidationError(ui(
            "--worker cannot be combined with --stage review",
            "--worker 不能与 --stage review 同时使用"))
    if stage == "review" and not node.reviewer:
        raise ValidationError(ui(
            f"Node {node.id} has no reviewer to resume",
            f"节点 {node.id} 没有可恢复的 reviewer"))

    config = load_config()
    engine = _build_engine(config)

    # Persist the active-set hint before any retry recovery metadata write.
    # The platform facts remain authoritative; this only keeps the node
    # observable after a restart with an unknown write result. Do this before
    # a requested worker replacement so a failed platform recovery cannot
    # commit that business-field change.
    if node.work_item_id and engine is not None:
        node.recovery_marker = True
        save_manifest(manifest, args.manifest)

    if args.worker:
        new_worker = _validate_worker(manifest, node, args.worker, config, engine)
        node.worker = new_worker
        # 改派同步写 manifest;work_item_id 保留(转派发生在下次 dispatch)。

    # 显式 retry 的 authoring + todo 意图必须同时写到平台。否则 review 阶段
    # 被阻塞后重试时，下一次虽然会重新指派 worker，work show / work submit
    # 仍会按 reviewer 协议解释旧 phase，worker 无法正式交付。
    # 平台先写、本地后写：平台失败时不留下两边分叉的半完成 retry。
    if node.work_item_id and engine is not None:
        try:
            current = engine.store.get_work_item(node.work_item_id)
            delayed_review_recovered = (
                stage == "review"
                and _recover_delayed_reviewer_submission(
                    manifest, args.node_key, node, engine, current,
                    args.manifest)
            )
            handoff = None
            prior_handoff = current.worker_handoff
            has_delivery = bool(current.artifacts or current.verification)
            prior_rework = bool(
                prior_handoff is not None
                and (
                    prior_handoff.source_review_verdict in {
                        "reject", "pass-with-nits",
                    }
                    or prior_handoff.baseline_pr_head_sha
                )
            )
            if (
                stage == "authoring"
                and node.reviewer
                and (
                    current.bounces.review > 0
                    or current.review_verdict == "reject"
                    or prior_rework
                )
                and has_delivery
            ):
                from ...pipeline.loop import _bounded_direct_run_baseline

                source_subject = (
                    current.review_subject_digest
                    or stage_recovery_subject(node, current)
                )
                baseline_direct_run_ids, baseline_cutoff_created_at = (
                    _bounded_direct_run_baseline(
                        engine.runtime.list_runs(current.id))
                )
                source_review_verdict = (
                    current.review_verdict
                    if current.review_verdict in {"reject", "pass-with-nits"}
                    else (
                        prior_handoff.source_review_verdict
                        if prior_rework else None
                    )
                )
                identity_head = getattr(
                    current.delivery_identity, "pr_head_sha", None)
                artifact_head = (
                    str(current.artifacts.get("head_sha") or "")
                    if isinstance(current.artifacts, dict) else ""
                )
                baseline_pr_head_sha = (
                    identity_head
                    or artifact_head
                    or (
                        prior_handoff.baseline_pr_head_sha
                        if prior_handoff is not None else None
                    )
                    or None
                )
                handoff = WorkerHandoffIntent(
                    schema=WORKER_HANDOFF_SCHEMA,
                    state="pending",
                    target_worker=node.worker,
                    gate="operator-retry",
                    source_review_feedback=_operator_retry_feedback(
                        current, prior_handoff),
                    source_review_subject_digest=source_subject,
                    source_review_round=max(1, current.bounces.review),
                    source_review_verdict=source_review_verdict,
                    target_review_bounce=max(1, current.bounces.review),
                    generation=f"handoff-{secrets.token_hex(8)}",
                    target_agent_id=engine.store.resolve_agent_id(node.worker),
                    baseline_direct_run_ids=baseline_direct_run_ids,
                    baseline_cutoff_created_at=baseline_cutoff_created_at,
                    baseline_verification_attachment_id=str(
                        (current.verification_ref or {}).get("attachment_id") or ""
                    ) or None,
                    baseline_pr_head_sha=baseline_pr_head_sha,
                    target_worker_bounce=current.bounces.worker,
                )
            if not delayed_review_recovered:
                # 复用 DAG stage recovery 原语；清除旧 reviewer 判定并恢复
                # 指定阶段，同时保留 PR、verification 与历史附件。
                prepare_stage_recovery(node, engine.store, stage)
                if handoff is not None:
                    engine.store.update_work_item_metadata(
                        node.work_item_id, worker_handoff=handoff)
            refreshed = engine.store.get_work_item(node.work_item_id)
            node.recovery_marker = bool(
                refreshed.worker_handoff
                or refreshed.reviewer_run_baseline
                or refreshed.decision_required
            )
        except WorkItemNotFoundError:
            # mock 的跨进程恢复没有持久化 store；陈旧 work_item_id 与 reconcile
            # 的“平台工单不存在”语义相同。retry 仍保留 ID 以兼容输出契约，
            # 下一次 dag run 再由 reconcile 清空并重新建单。
            node.recovery_marker = False

    # work_item_id 保留(同一 issue 续用)。
    node.status = "in_review" if stage == "review" else "todo"
    clear_confirmed_merge(node)
    save_manifest(manifest, args.manifest)

    print_json({
        "node_key": node.id,
        "status": node.status,
        "stage": stage,
        "worker": node.worker,
        "work_item_id": node.work_item_id,
    })
    hint(ui(
        f"Node {node.id} resumed at {stage}. Run `omac dag run "
        f"{args.manifest}` to continue.",
        f"节点 {node.id} 已恢复到 {stage}。运行 `omac dag run "
        f"{args.manifest}` 续跑生效。"))
    return exit_codes.OK


# ==================== accept-nits ====================


def _accept_nits_decision_matches(decision, item, node_key: str) -> bool:
    if not isinstance(decision, dict):
        return False
    return bool(
        decision.get("schema") == DECISION_REQUIRED_SCHEMA
        and decision.get("reason_code") == "review-nits-acceptance-required"
        and decision.get("kind") == TaskKind.DEVELOP.value
        and decision.get("phase") == TaskPhase.REVIEW.value
        and decision.get("gate") == "review-nits"
        and decision.get("resume_issue_id") == item.id
        and decision.get("node_id") == node_key
        and decision.get("review_subject_digest") == item.review_subject_digest
        and decision.get("review_report_ref") == item.review_report_ref
        and decision.get("verdict") == "pass-with-nits"
    )


def _cmd_accept_nits(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)
    if not node.work_item_id:
        raise ValidationError(ui(
            "A node without a work item cannot accept review nits. Run `omac dag run` first.",
            "没有 work item 的节点不能接受 review nits；请先运行 `omac dag run`。"))

    engine = _build_engine(load_config())
    if engine is None:
        raise ValidationError(ui(
            "The node has a work_item_id, but engine configuration cannot be resolved. "
            "Set OMAC_ENGINE and OMAC_WORKSPACE_ID or configure .omac/config.yaml first.",
            "节点有 work_item_id,但无法解析引擎配置；请先配置 OMAC_ENGINE/OMAC_WORKSPACE_ID "
            "或 .omac/config.yaml。"))

    item = engine.store.get_work_item(node.work_item_id)
    if item.kind != TaskKind.DEVELOP or item.phase != TaskPhase.REVIEW:
        raise ValidationError(ui(
            "accept-nits is only valid for a develop WorkItem in review phase.",
            "accept-nits 仅适用于 develop 类型且处于 review 阶段的 WorkItem。"))
    if item.review_verdict != "pass-with-nits":
        raise ValidationError(ui(
            "The current WorkItem does not have review verdict pass-with-nits; "
            "the Reviewer fact was not changed. Use `omac node retry` only after inspecting "
            "the current review state.",
            "当前 WorkItem 没有 pass-with-nits verdict，未修改 Reviewer 事实；请先检查当前 review 状态，"
            "再决定是否执行 `omac node retry`。"))
    if item.status not in {WorkItemStatus.BLOCKED, WorkItemStatus.IN_REVIEW}:
        raise ValidationError(ui(
            "The WorkItem is not in the blocked/in_review acceptance state; refusing to change it.",
            "WorkItem 不处于 blocked/in_review 的接受状态；拒绝修改。"))
    if not isinstance(item.review_report, dict) or not item.review_report:
        raise ValidationError(ui(
            "pass-with-nits requires the complete persisted review report; no state was changed.",
            "pass-with-nits 必须有完整且已持久化的 review report；未修改状态。"))
    if not exact_review_report_ref(item.review_report_ref):
        raise ValidationError(ui(
            "The sealed review report reference is missing or invalid; refusing acceptance.",
            "sealed review report ref 缺失或无效；拒绝接受。"))

    # Reuse the pipeline's authoritative delivery/subject checks. The CLI only
    # adapts arguments; all platform facts still come from Store/Runtime.
    from ...pipeline.loop import (
        _control_matches_delivery_identity,
        _review_subject_for_current_delivery,
    )

    expected_subject = _review_subject_for_current_delivery(
        manifest, args.node_key, item)
    if item.review_subject_digest != expected_subject:
        raise ValidationError(ui(
            "The pass-with-nits verdict is bound to a stale review subject; "
            "run `omac node retry` to start a fresh delivery.",
            "pass-with-nits verdict 绑定的 review subject 已过期；请运行 "
            "`omac node retry` 开始新的交付。"))
    if not _control_matches_delivery_identity(item):
        raise ValidationError(ui(
            "The current delivery is not controller-sealed; refusing acceptance. "
            "Run `omac node retry` after inspecting the delivery facts.",
            "当前交付没有通过 Controller sealed 校验；拒绝接受。请检查交付事实后运行 "
            "`omac node retry`。"))

    marker = {
        "schema": REVIEW_NITS_ACCEPTANCE_SCHEMA,
        "review_subject_digest": item.review_subject_digest,
        "review_report_ref": dict(item.review_report_ref),
        "verdict": "pass-with-nits",
    }
    existing_marker = item.review_nits_acceptance
    if existing_marker not in (None, {}) and (
        not review_nits_acceptance_is_valid(existing_marker)
        or existing_marker != marker
    ):
        raise ValidationError(ui(
            "The existing review-nits acceptance marker does not match the current sealed review; "
            "refusing to overwrite it. Run `omac node retry` for a fresh decision.",
            "已有 review-nits acceptance marker 与当前 sealed review 不匹配；拒绝覆盖。请运行 "
            "`omac node retry` 重新决策。"))

    decision = item.decision_required
    if decision not in (None, {}):
        if not _accept_nits_decision_matches(decision, item, args.node_key):
            raise ValidationError(ui(
                "The existing decision_required does not identify this exact pass-with-nits review; "
                "no state was changed.",
                "现有 decision_required 没有绑定当前 pass-with-nits review；未修改状态。"))

    def ensure_no_active_direct_run() -> None:
        active = [
            run.id for run in engine.runtime.list_runs(item.id)
            if run.kind == "direct" and run.active
        ]
        if active:
            raise ValidationError(ui(
                "An active direct Run still exists ("
                f"{', '.join(active)}); wait for it to finish before rerunning "
                f"`omac node accept-nits {args.manifest} {args.node_key}`.",
                "仍有 active direct Run（"
                f"{', '.join(active)}）；请等待其结束后重新运行 "
                f"`omac node accept-nits {args.manifest} {args.node_key}`。"))

    # Write the marker before clearing decision_required. Each write is
    # read-after-write verified, so a restart can safely resume this sequence.
    if existing_marker in (None, {}):
        ensure_no_active_direct_run()
        updated = engine.store.update_work_item_metadata(
            item.id, review_nits_acceptance=marker)
        if updated.review_nits_acceptance != marker:
            raise ValidationError(ui(
                "The operator acceptance marker was not persisted; refusing to clear decision_required.",
                "operator acceptance marker 未成功持久化；拒绝清除 decision_required。"))
        item = updated

    if item.decision_required not in (None, {}):
        ensure_no_active_direct_run()
        updated = engine.store.update_work_item_metadata(
            item.id, decision_required={})
        if updated.decision_required not in (None, {}):
            raise ValidationError(ui(
                "decision_required was not cleared; the WorkItem remains blocked.",
                "decision_required 未清除；WorkItem 仍保持 blocked。"))
        item = updated

    ensure_no_active_direct_run()
    if item.status != WorkItemStatus.IN_REVIEW:
        engine.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    final = engine.store.get_work_item(item.id)
    if not (
        final.status == WorkItemStatus.IN_REVIEW
        and final.phase == TaskPhase.REVIEW
        and final.review_verdict == "pass-with-nits"
        and final.review_nits_acceptance == marker
        and final.decision_required in (None, {})
    ):
        raise ValidationError(ui(
            "The WorkItem did not converge to review/in_review with its acceptance marker; "
            "no merge or Reviewer fact was assumed.",
            "WorkItem 未收敛到带 acceptance marker 的 review/in_review；未假设 merge 成功，Reviewer 事实未修改。"))

    node.status = "in_review"
    node.recovery_marker = False
    save_manifest(manifest, args.manifest)
    print_json({
        "node_key": node.id,
        "status": "in_review",
        "stage": "review",
        "work_item_id": node.work_item_id,
        "review_subject_digest": marker["review_subject_digest"],
    })
    hint(ui(
        f"Accepted pass-with-nits for node {node.id}; no Worker was dispatched. "
        f"Run `omac dag run {args.manifest}` to observe merge.",
        f"节点 {node.id} 的 pass-with-nits 已接受；未派发 Worker。运行 `omac dag run "
        f"{args.manifest}` 继续观察 merge。"))
    return exit_codes.OK


# ==================== accept ====================

def _cmd_accept(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)

    if not node.work_item_id:
        raise ValidationError(ui(
            "A node without a work item cannot be accepted as done. "
            "Use `omac node abandon` for an explicit abandonment.",
            "没有工作单元的节点不能 accept 为 done；如需放弃请显式使用 `omac node abandon`。"))

    engine = _build_engine(load_config())
    if engine is None:
        raise ValidationError(ui(
            "The node has a work_item_id, but engine configuration cannot be resolved. "
            "Set OMAC_ENGINE and OMAC_WORKSPACE_ID or configure .omac/config.yaml first.",
            "节点有 work_item_id,但无法解析引擎配置；为避免 manifest 与平台状态分裂,请先配置 OMAC_ENGINE/OMAC_WORKSPACE_ID 或 .omac/config.yaml"))
    item = engine.store.get_work_item(node.work_item_id)
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    pr_url = artifacts.get("pr_url") or artifacts.get("pr")
    if item.kind == TaskKind.DEVELOP:
        if not pr_url:
            raise ValidationError(ui(
                "This develop node has no PR, so it cannot be accepted as done. "
                "Use `omac node abandon` for an explicit abandonment.",
                "该 develop 节点没有 PR，不能 accept 为 done；如需放弃请显式使用 "
                "`omac node abandon`。"))
        observation = engine.store.observe_pull_request(pr_url)
        state = observation.state
        if not isinstance(state, PullRequestState):
            try:
                state = PullRequestState(str(state).lower())
            except ValueError:
                state = PullRequestState.UNKNOWN
        if state != PullRequestState.MERGED or not observation.merged_at:
            raise ValidationError(ui(
                "This develop node has a PR without confirmed merge; use `omac node abandon` "
                "for an explicit abandonment, or wait for remote merge confirmation.",
                "该 develop 节点的 PR 尚未确认合入；如需放弃请显式使用 `omac node abandon`，"
                "否则等待远端确认合入。"))
        node.merged = True
        node.merged_at = observation.merged_at
    engine.store.update_work_item_metadata(
        node.work_item_id,
        decision_required={},
    )
    engine.store.update_status(node.work_item_id, WorkItemStatus.DONE)

    node.status = "done"
    save_manifest(manifest, args.manifest)

    print_json({
        "node_key": node.id,
        "status": "done",
        "work_item_id": node.work_item_id,
    })
    hint(ui(
        f"Node {node.id} accepted and marked done. Run `omac dag run {args.manifest}` to continue.",
        f"节点 {node.id} 已接受建议项并标记 done。运行 `omac dag run {args.manifest}` 续跑生效。"))
    return exit_codes.OK


# ==================== abandon ====================

def _cmd_abandon(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)

    node.status = "abandoned"
    save_manifest(manifest, args.manifest)

    # 计算受影响下游(传递依赖),报告中对经过 abandoned 上游的节点加注记。
    issues = {k: {"status": n.status, "blocked_by": list(n.blocked_by)}
              for k, n in manifest.nodes.items()}
    affected = sorted(downstream_of(issues, {node.id}))

    print_json({
        "node_key": node.id,
        "status": "abandoned",
        "affected_downstream": affected,
    })
    hint(ui(
        f"Node {node.id} abandoned. Its dependency is treated as satisfied and downstream "
        f"work may continue.\nRun `omac dag run {args.manifest}` to continue.",
        f"节点 {node.id} 已 abandon:上游视同依赖已满足,下游可继续推进。\n"
        f"运行 `omac dag run {args.manifest}` 续跑生效。"))
    return exit_codes.OK


def run(args) -> int:
    if args.action == "show":
        return _cmd_show(args)
    if args.action == "retry":
        return _cmd_retry(args)
    if args.action == "accept-nits":
        return _cmd_accept_nits(args)
    if args.action == "accept":
        return _cmd_accept(args)
    if args.action == "abandon":
        return _cmd_abandon(args)
    raise ValidationError(ui(
        f"Unknown node subcommand: {args.action}",
        f"未知 node 子命令: {args.action}"))
