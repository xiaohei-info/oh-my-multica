"""omac plan — 设计方案 + DAG 拆解流水线(全程内置 review 阶段)。"""
from __future__ import annotations

import os

from ...core import config as config_mod
from ...engines import engine_from_config
from ...errors import NeedsDecision, ValidationError
from ...i18n import resolve_language, ui
from .. import exit_codes
from ..output import hint
from ...pipeline.decide_stop import Release, render_release
from ...pipeline.plan import (
    PlanContext, plan_accept_nits, plan_continue_review, plan_create, plan_resume,
)
from ...core.taskmeta import plan_id_of
from ..output import print_json
from ...pipeline.apply import Ports, apply
from ...pipeline.decide_effects import UpdateStatus

def resolve_review_rounds(cfg: dict | None = None) -> int:
    """plan 流水线评审修订轮次上界,与 dag run 节点评审共用 config.retry.review。

    docs/design/README.md §7.2:每个 LLM 环节的修订循环有界(评审轮次读 config.retry.review,缺省 ≤3),
    耗尽则 exit 20 移交调用者。此处统一从 config.retry 读取,消除第二处硬编码。
    """
    cfg = cfg if cfg is not None else config_mod.load_config()
    retry = config_mod.resolve_retry(cfg)
    return int(retry["review"])


NAME = "plan"
SUMMARY = "设计方案 + DAG 拆解流水线(全程内置 review 阶段)"
DESCRIPTION = """设计方案与 DAG 拆解流水线。

子命令:
  create   两种模式一条流水线:
             --doc <设计文档文件或目录>  跳过 planner 设计环节,直接进验收文档 + 拆解
             --goal <需求>        把需求注入 planner,由它据此生成设计方案(无 --doc 时;
                              二者互斥,--doc 优先)
           设计方案定稿后 planner 产出验收文档(业务流程 → 用户视角端到端可执行
           验收动作),再由 orchestrator 拆解为 manifest DAG。
           issue 的范围 = 一个完整阶段:产出 → 评审 → 回退修订都在同一条
           issue 上,评审 = 该 issue 转派 reviewer。
           开关:--no-review 跳过全部 review 阶段;--no-acceptance 跳过验收文档。

  resume   按 --plan-id 续跑；流水线形态（--doc/--goal、是否评审/验收/确认）记录在来源 issue 上，无需再带参数。
           --doc 只在要显式重发来源时给（内容变化才会替换）。
  confirm   人机确认门（默认开启）：评审通过后阶段以 exit 20 停下，报告里的 next_action 就是确认命令。
           plan confirm --dag-key <阶段> 放行；加 --reject --reason <文本> 把理由交给作者返工。之后运行 plan resume。
  continue-review  人工授权某个已停止的 stage 再来一轮 review（也用于拒绝 pass-with-nits 并退回作者）。
  accept-nits      接受某个 stage 的 pass-with-nits 建议项，之后 plan resume 完成该阶段。

manifest 门禁与摘要属于 DAG 层:
  omac dag check <manifest>
  omac dag show <manifest>
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    create = sub.add_parser("create", help="启动设计方案→验收文档→DAG 拆解流水线")
    create.add_argument("--name", required=True, help="manifest 名(落盘 .omac/<name>.yaml)")
    create.add_argument("--goal", help="需求(一句话或多行);无 --doc 时注入 planner,据此生成设计方案")
    create.add_argument("--goal-file", help="需求文档路径(与 --goal 互斥)")
    create.add_argument(
        "--doc",
        help="已有设计文档文件或目录路径(给了就跳过 planner 设计环节)")
    create.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    create.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")
    create.add_argument(
        "--no-confirm", action="store_true",
        help="跳过设计/验收的人机确认门(无人值守入口用;默认开启,门前以 exit 20 停止,由 plan confirm 放行或 --reject 退回)")

    confirm = sub.add_parser(
        "confirm", help="人机门:确认待确认的设计/验收 issue(流转到 DONE),或用 --reject 附理由退回作者返工")
    confirm.add_argument("--name", help="方案名(plan create --name 用的同一名字;兼容入口,重名时请用唯一 ID)")
    confirm.add_argument("--dag-key", help="精确定位某个阶段 issue,如 plan-p-xxxx / acceptance-p-xxxx")
    confirm.add_argument("--plan-id", help="精确定位一条 plan 流水线,如 p-xxxx")
    confirm.add_argument(
        "--reject", action="store_true",
        help="不确认,把该阶段退回作者返工;必须同时给 --reason(理由作为作者返工输入)")
    confirm.add_argument("--reason", help="--reject 的理由(最多 1024 UTF-8 bytes);作者据此返工")
    confirm.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    confirm.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")

    resume = sub.add_parser("resume", help="按唯一 plan_id/dag_key 衔接已创建的 plan 流水线")
    resume.add_argument("--dag-key", help="plan create 创建的阶段 dag_key,如 plan-p-xxxx")
    resume.add_argument("--plan-id", help="plan 流水线唯一 ID,如 p-xxxx")
    resume.add_argument("--name", help="manifest 名;缺省从设计方案 issue 标题反推")
    resume.add_argument(
        "--doc",
        help="显式重发设计来源（文件或目录；内容与已发布的不同才替换）。不给则沿用来源 issue 上已发布的设计文本")
    resume.add_argument(
        "--restart-active", action="store_true",
        help="显式取消当前阶段仍活跃的 Agent run，刷新同一 issue 后重新派发")
    resume.add_argument(
        "--acceptance-issue-id",
        help="恢复时精确指定已有 acceptance issue，绕过项目级 issue 列表查询")
    resume.add_argument(
        "--decompose-issue-id",
        help="精确指定已创建但尚未派发的 decompose issue；用于旧巨型正文无法读取时先原地压缩")
    resume.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    resume.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")
    resume.add_argument(
        "--no-confirm", action="store_true",
        help="跳过设计/验收的人机确认门(默认开启,门前以 exit 20 停止,由 plan confirm 放行或 --reject 退回)")
    resume.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    resume.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")

    continue_review = sub.add_parser(
        "continue-review",
        help="人工授权同一 plan stage 额外一轮 review，不修改项目 config")
    selector = continue_review.add_mutually_exclusive_group(required=True)
    selector.add_argument("--dag-key", help="精确 stage DAG key,如 decompose-p-xxxx")
    selector.add_argument("--plan-id", help="plan 流水线唯一 ID,如 p-xxxx")
    continue_review.add_argument(
        "--stage", choices=("plan", "acceptance", "decompose"),
        help="使用 --plan-id 时必填；指定需要继续的 stage")
    continue_review.add_argument(
        "--issue-id", help="精确指定 stage issue，绕过项目级列表查询")
    continue_review.add_argument(
        "--reason", help="记录本次人工授权原因；不写入项目文件")
    continue_review.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    continue_review.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")

    accept_nits = sub.add_parser(
        "accept-nits",
        help="接受某个 plan stage 的 pass-with-nits 建议项（不重审、不返工），之后 plan resume 完成该阶段")
    nits_selector = accept_nits.add_mutually_exclusive_group(required=True)
    nits_selector.add_argument("--dag-key", help="精确 stage DAG key,如 acceptance-p-xxxx")
    nits_selector.add_argument("--plan-id", help="plan 流水线唯一 ID,如 p-xxxx")
    accept_nits._help_key = "plan-accept-nits"
    accept_nits.add_argument(
        "--stage", choices=("plan", "acceptance", "decompose"),
        help="使用 --plan-id 时必填；指定要接受 nits 的 stage")
    accept_nits.add_argument("--issue-id", help="精确指定 stage issue，绕过项目级列表查询")
    accept_nits.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    accept_nits.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")


def _resolve_engine(args):
    """按 config.yaml < 环境变量 < 命令行 解析引擎配置;缺失时报错即教学。"""
    return engine_from_config(
        config_mod.load_config(), engine=getattr(args, "engine", None),
        workspace=getattr(args, "workspace", None), project=getattr(args, "project", None),
        engine_extra=getattr(args, "engine_extra", None))


def _resolve_goal(args) -> str | None:
    """解析需求输入:--goal 直给 / --goal-file 读文件,二者互斥。缺省 None。"""
    goal = getattr(args, "goal", None)
    goal_file = getattr(args, "goal_file", None)
    if goal and goal_file:
        raise ValidationError(ui(
            "--goal and --goal-file are mutually exclusive",
            "--goal 与 --goal-file 互斥,二选一"))
    if goal:
        return goal
    if goal_file:
        if not os.path.exists(goal_file):
            raise ValidationError(ui(
                f"--goal-file not found: {goal_file}",
                f"--goal-file 不存在: {goal_file}"))
        with open(goal_file, encoding="utf-8") as f:
            return f.read()
    return None


def _build_context(cfg: dict, engine, args) -> PlanContext:
    workspace_id = engine.store.config.workspace_id
    roles = cfg.get("roles") or {}
    workflow = config_mod.resolve_workflow(cfg)

    workers = roles.get("workers") or []
    if isinstance(workers, str):
        workers = [workers]
    reviewers = roles.get("reviewers") or []
    if isinstance(reviewers, str):
        reviewers = [reviewers]
    planner = roles.get("planner") or (workers[0] if workers else None)
    orchestrator = roles.get("orchestrator") or planner

    if not planner:
        raise ValidationError(ui(
            "Planner role is missing. Run `omac config set roles.planner <agent>`, "
            "or configure roles.workers so the first worker can be used.",
            "缺少 planner 角色 —— 请 `omac config set roles.planner <agent>`,"
            "或设置 roles.workers(取首位作为 planner)"))

    members = set(engine.store.list_members(workspace_id))
    plan = config_mod.resolve_plan(cfg)
    return PlanContext(
        engine=engine,
        workspace_id=workspace_id,
        planner=planner,
        orchestrator=orchestrator,
        reviewers=reviewers,
        max_revisions=resolve_review_rounds(cfg),
        no_review=(not workflow["review"]) or args.no_review,
        no_acceptance=(not workflow["acceptance_doc"]) or args.no_acceptance,
        members=members,
        confirm=workflow["human_in_loop"] and not args.no_confirm,
        language=resolve_language(cfg),
        profile=plan["profile"],
        light_max_nodes=plan["light_max_nodes"],
        light_max_business_actions=plan["light_max_business_actions"],
        invalid_evidence_restarts=config_mod.resolve_invalid_evidence_restarts(cfg),
    )


def _create(args) -> int:
    """mac plan create:装配 PlanContext + 调 plan_create 编排三阶段。"""
    cfg = config_mod.load_config()
    engine = _resolve_engine(args)

    goal_text = _resolve_goal(args)
    doc_path = getattr(args, "doc", None)
    if goal_text and doc_path:
        hint(ui(
            "Both --doc and --goal were provided. --doc skips planner authoring, so --goal is ignored.",
            "同时给了 --doc 与 --goal:--doc 会跳过 planner 设计环节,--goal 被忽略"))
    if not doc_path and not goal_text and config_mod.resolve_workflow(cfg)["goal_required"]:
        raise ValidationError(ui(
            "workflow.goal_required=true requires a request when --doc is absent. "
            "Use `omac plan create --name <name> --goal <request>`, `--goal-file <path>`, "
            "or provide existing design documents with `--doc <file-or-directory>`.",
            "workflow.goal_required=true:无 --doc 时必须提供需求。"
            "请使用 `omac plan create --name <name> --goal <需求>` "
            "或 `--goal-file <path>`;已有设计文档则用 `--doc <文件或目录>`。"))

    ctx = _build_context(cfg, engine, args)
    return plan_create(ctx, args.name, doc_path=doc_path, goal_text=goal_text)


def _selector(args) -> tuple[str, set[str] | None]:
    """返回用户选择器标签与可匹配 dag_key 集合;None 表示走 legacy name 子串。"""
    provided = [v for v in (args.name, args.dag_key, args.plan_id) if v]
    if not provided:
        raise ValidationError(ui(
            "plan confirm requires one of --dag-key, --plan-id, or --name",
            "plan confirm 需要 --dag-key / --plan-id / --name 之一"))
    if args.dag_key:
        return f"dag_key={args.dag_key}", {args.dag_key}
    if args.plan_id:
        plan_id = plan_id_of(args.plan_id)
        return f"plan_id={plan_id}", {f"plan-{plan_id}", f"acceptance-{plan_id}"}
    return f"name={args.name}", None


def _confirm(args) -> int:
    """omac plan confirm:人机门。确认 = 流转到 DONE;--reject --reason = 退回作者返工。

    门上的 issue 是 Reviewer 已通过后进入 confirmation 阶段的那条(IN_REVIEW + phase=CONFIRMATION)。
    两种放行都经 decide_stop.decide_release;之后运行 plan resume(plan create/resume 在门前以 exit 20 停下)。
    """
    from ...core.taskmeta import TaskKind, TaskPhase
    from ...core.workitem import WorkItemStatus
    from ...pipeline.stage_release import release_stage

    engine = _resolve_engine(args)
    workspace_id = engine.store.config.workspace_id
    label, dag_keys = _selector(args)
    if args.reason and not args.reject:
        raise ValidationError(ui(
            "--reason belongs to --reject; to confirm, run the command without it",
            "--reason 只与 --reject 搭配；确认请不带它运行"))
    if len((args.reason or "").encode("utf-8")) > 1024:
        raise ValidationError(ui("--reason must be at most 1024 UTF-8 bytes", "--reason 最多 1024 UTF-8 bytes"))

    # 待确认 = Reviewer 已通过后进入的显式 confirmation 阶段。
    # 新入口按 dag_key 精确匹配;--name 仅保留兼容,重名时要求调用方换唯一 ID。
    waiting = [
        it for it in engine.store.list_work_items(workspace_id)
        if it.kind in (TaskKind.PLAN, TaskKind.ACCEPTANCE)
        and it.status == WorkItemStatus.IN_REVIEW
        and it.phase == TaskPhase.CONFIRMATION
        and (
            (dag_keys is not None and it.dag_key in dag_keys)
            or (dag_keys is None and args.name in (it.title or ""))
        )
    ]
    if not waiting:
        raise ValidationError(ui(
            f"No pending design or acceptance issue matched {label}. It may already be "
            "confirmed, not yet authored, or selected incorrectly.",
            f"未找到 {label} 待确认的设计/验收 issue —— "
            "可能已确认、尚未产出,或 selector 不匹配。"))
    if len(waiting) > 1:
        raise ValidationError(ui(
            f"{label} matched multiple pending issues. Use --dag-key plan-p-xxxx or "
            "acceptance-p-xxxx for an exact selection.",
            f"{label} 匹配到多条待确认 issue —— name 可能重复;"
            "请改用 --dag-key plan-p-xxxx / acceptance-p-xxxx 精确定位。"))

    it = waiting[0]
    release = Release.REJECT_CONFIRMATION if args.reject else Release.CONFIRM
    released = release_stage(
        engine, it, release, configured_limit=resolve_review_rounds(config_mod.load_config()),
        reason=args.reason or "")
    next_action = render_release(Release.RESUME, it.dag_key)
    print_json({"ok": True, "item_id": it.id, "dag_key": it.dag_key, "stage": it.kind.value,
                "mode": released["mode"], "next_action": next_action})
    hint(ui(
        (f"Returned {it.title} (issue {it.id}) to its author with your reason." if args.reject
         else f"Confirmed: {it.title} (issue {it.id}) → DONE.")
        + f" Nothing continues by itself: run `{next_action}`.",
        (f"已把 {it.title}(issue {it.id})连同理由退回作者。" if args.reject
         else f"已确认:{it.title}(issue {it.id})→ DONE。")
        + f"不会自行继续：请运行 `{next_action}`。"))
    return exit_codes.OK


def _resume(args) -> int:
    cfg = config_mod.load_config()
    engine = _resolve_engine(args)
    ctx = _build_context(cfg, engine, args)
    return plan_resume(
        ctx,
        dag_key=args.dag_key,
        plan_id=args.plan_id,
        name=args.name,
        doc_path=args.doc,
        restart_active=args.restart_active,
        cli_flags={"no_review": args.no_review, "no_acceptance": args.no_acceptance,
                   "no_confirm": args.no_confirm},
        acceptance_issue_id=args.acceptance_issue_id,
        decompose_issue_id=args.decompose_issue_id,
    )


def _continue_review(args) -> int:
    cfg = config_mod.load_config()
    engine = _resolve_engine(args)
    result = plan_continue_review(
        engine,
        resolve_review_rounds(cfg),
        dag_key=args.dag_key,
        plan_id=args.plan_id,
        stage=args.stage,
        issue_id=args.issue_id,
        reason=args.reason,
    )
    print_json({"ok": True, **result})
    hint(ui(
        f"Review continuation authorized through round "
        f"{result['authorized_through_round']}. Run `{result['next_action']}`.",
        f"已授权 review 至 round {result['authorized_through_round']}。"
        f"运行 `{result['next_action']}` 继续。"))
    return exit_codes.OK


def _accept_nits(args) -> int:
    cfg = config_mod.load_config()
    engine = _resolve_engine(args)
    result = plan_accept_nits(
        engine, resolve_review_rounds(cfg), dag_key=args.dag_key, plan_id=args.plan_id, stage=args.stage,
        issue_id=args.issue_id)
    print_json({"ok": True, **result})
    hint(ui(
        f"Accepted the pass-with-nits review of {result['dag_key']}; no Agent was dispatched. "
        f"Run `{result['next_action']}` to finish the stage.",
        f"已接受 {result['dag_key']} 的 pass-with-nits；未派发 Agent。运行 `{result['next_action']}` 完成该阶段。"))
    return exit_codes.OK


def _dispatch(args) -> int:
    if args.action == "create":
        return _create(args)
    if args.action == "confirm":
        return _confirm(args)
    if args.action == "resume":
        return _resume(args)
    if args.action == "continue-review":
        return _continue_review(args)
    if args.action == "accept-nits":
        return _accept_nits(args)
    raise ValidationError(ui(
        f"Unknown plan subcommand: {args.action}",
        f"未知 plan 子命令:{args.action}"))


def run(args) -> int:
    try:
        return _dispatch(args)
    except NeedsDecision as exc:
        if exc.report is not None:
            print_json(exc.report)      # the structured report the exit-20 contract promises; next_action is a runnable command
        raise
