"""omac node — exit 20 之后的决策工具(重试是显式决策)。"""
from __future__ import annotations

import os
import secrets
from dataclasses import asdict
from typing import Any

from .. import exit_codes
from ..output import add_output_flag, hint, print_json
from ...core.config import ENV_ENGINE, ENV_WORKSPACE, load_config, resolve_engine_settings
from ...core.manifest import (
    MISSING_CONSUMES, clear_confirmed_merge, load_manifest, save_manifest,
)
from ...core.graph import downstream_of
from ...core.taskmeta import WORKER_HANDOFF_SCHEMA, TaskKind, WorkerHandoffIntent
from ...core.stage_recovery import prepare_stage_recovery, stage_recovery_subject
from ...engines import EngineConfig, create_engine
from ...engines.models import PullRequestState, WorkItemStatus
from ...errors import OmacError, ValidationError, WorkItemNotFoundError
from ...i18n import ui

NAME = "node"
SUMMARY = "exit 20 后的决策工具(show/retry/accept/abandon)"
DESCRIPTION = """异常处理闭环:dag run 以 exit 20 退出后,由调用者决策。

子命令:
  show     单节点完整证据链:contract、验证命令输出、评审 report(含评审目标)、
           env_setup、PR / 平台 issue 链接、回退计数
  retry    显式重置节点为 todo(可 --worker 换人),下次 dag run 生效。
           重试不会自动发生——这是设计原则(§2.4)
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
        "Evidence printed. Choose `omac node retry|accept|abandon`, then rerun `omac dag run`.",
        "证据链已输出。决策:omac node retry|accept|abandon 后 omac dag run 续跑生效。"))
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


def _cmd_retry(args) -> int:
    manifest = _load_or_raise(args.manifest)
    node = _require_node(manifest, args.node_key)

    config = load_config()
    engine = _build_engine(config)

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
            handoff = None
            has_delivery = bool(current.artifacts or current.verification)
            if node.reviewer and current.bounces.review > 0 and has_delivery:
                source_subject = (
                    current.review_subject_digest
                    or stage_recovery_subject(node, current)
                )
                handoff = WorkerHandoffIntent(
                    schema=WORKER_HANDOFF_SCHEMA,
                    state="pending",
                    target_worker=node.worker,
                    gate="operator-retry",
                    source_review_subject_digest=source_subject,
                    source_review_round=max(1, current.bounces.review),
                    target_review_bounce=max(1, current.bounces.review),
                    generation=f"handoff-{secrets.token_hex(8)}",
                    target_agent_id=engine.store.resolve_agent_id(node.worker),
                    baseline_direct_run_ids=tuple(sorted(
                        run.id for run in engine.runtime.list_runs(current.id)
                        if run.kind == "direct"
                    )),
                    baseline_verification_attachment_id=str(
                        (current.verification_ref or {}).get("attachment_id") or ""
                    ) or None,
                    target_worker_bounce=current.bounces.worker,
                )
            # 复用 DAG stage recovery 原语；清除旧 reviewer 判定并恢复
            # authoring/todo，同时保留 PR、verification 与历史附件。
            prepare_stage_recovery(node, engine.store, "authoring")
            if handoff is not None:
                engine.store.update_work_item_metadata(
                    node.work_item_id, worker_handoff=handoff)
        except WorkItemNotFoundError:
            # mock 的跨进程恢复没有持久化 store；陈旧 work_item_id 与 reconcile
            # 的“平台工单不存在”语义相同。retry 仍保留 ID 以兼容输出契约，
            # 下一次 dag run 再由 reconcile 清空并重新建单。
            pass

    # 重置为 todo;work_item_id 保留(同一 issue 续用)。
    node.status = "todo"
    clear_confirmed_merge(node)
    save_manifest(manifest, args.manifest)

    print_json({
        "node_key": node.id,
        "status": "todo",
        "worker": node.worker,
        "work_item_id": node.work_item_id,
    })
    hint(ui(
        f"Node {node.id} reset to todo. Run `omac dag run {args.manifest}` to continue.",
        f"节点 {node.id} 已重置为 todo。运行 `omac dag run {args.manifest}` 续跑生效。"))
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
    if args.action == "accept":
        return _cmd_accept(args)
    if args.action == "abandon":
        return _cmd_abandon(args)
    raise ValidationError(ui(
        f"Unknown node subcommand: {args.action}",
        f"未知 node 子命令: {args.action}"))
