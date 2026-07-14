"""omac plan — 设计方案 + DAG 拆解流水线(全程内置 review 阶段)。"""
from __future__ import annotations

import os

from ...core import config as config_mod
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import ValidationError
from ...i18n import resolve_language, ui
from .. import exit_codes
from ..output import hint
from ...pipeline.plan import PlanContext, plan_create, plan_dag_key_from_id, plan_resume

def resolve_review_rounds(cfg: dict | None = None) -> int:
    """plan 流水线评审修订轮次上界,与 dag run 节点评审共用 config.retry.review。

    设计文档 §7.2:每个 LLM 环节的修订循环有界(评审轮次读 config.retry.review,缺省 ≤3),
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
             --doc <设计方案文档>  跳过 planner 设计环节,直接进验收文档 + 拆解
             --goal <需求>        把需求注入 planner,由它据此生成设计方案(无 --doc 时;
                              二者互斥,--doc 优先)
           设计方案定稿后 planner 产出验收文档(业务流程 → 用户视角端到端可执行
           验收动作),再由 orchestrator 拆解为 manifest DAG。
           issue 的范围 = 一个完整阶段:产出 → 评审 → 回退修订都在同一条
           issue 上,评审 = 该 issue 转派 reviewer。
           开关:--no-review 跳过全部 review 阶段;--no-acceptance 跳过验收文档。

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
    create.add_argument("--doc", help="已有设计方案文档路径(给了就跳过 planner 设计环节)")
    create.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    create.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")
    create.add_argument(
        "--no-confirm", action="store_true",
        help="跳过设计/验收的人机确认门(无人值守入口用;默认开启,需人工把 issue 流转到 DONE 放行)")

    confirm = sub.add_parser(
        "confirm", help="人机门手动放行:把待确认的设计/验收 issue 流转到 DONE")
    confirm.add_argument("--name", help="方案名(plan create --name 用的同一名字;兼容入口,重名时请用唯一 ID)")
    confirm.add_argument("--dag-key", help="精确定位某个阶段 issue,如 plan-p-xxxx / acceptance-p-xxxx")
    confirm.add_argument("--plan-id", help="精确定位一条 plan 流水线,如 p-xxxx")
    confirm.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    confirm.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")

    resume = sub.add_parser("resume", help="按唯一 plan_id/dag_key 衔接已创建的 plan 流水线")
    resume.add_argument("--dag-key", help="plan create 创建的阶段 dag_key,如 plan-p-xxxx")
    resume.add_argument("--plan-id", help="plan 流水线唯一 ID,如 p-xxxx")
    resume.add_argument("--name", help="manifest 名;缺省从设计方案 issue 标题反推")
    resume.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    resume.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")
    resume.add_argument(
        "--no-confirm", action="store_true",
        help="跳过设计/验收的人机确认门(默认开启,需人工把 issue 流转到 DONE 放行)")
    resume.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    resume.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")


def _resolve_engine(args):
    """按 config.yaml < 环境变量 < 命令行 解析引擎配置;缺失时报错即教学。"""
    cfg = config_mod.load_config()
    engine_type, workspace_id, project_id = config_mod.resolve_engine_settings(
        cfg, engine=getattr(args, "engine", None), workspace=getattr(args, "workspace", None),
        project=getattr(args, "project", None))
    extra = dict(cfg.get("engine_extra") or {})
    extra.update(getattr(args, "engine_extra", None) or {})
    return create_engine(
        engine_type,
        EngineConfig(engine_type=engine_type, workspace_id=workspace_id,
                     project_id=project_id, extra=extra))


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
            "or provide an existing design with `--doc <path>`.",
            "workflow.goal_required=true:无 --doc 时必须提供需求。"
            "请使用 `omac plan create --name <name> --goal <需求>` "
            "或 `--goal-file <path>`;已有设计方案则用 `--doc <path>`。"))

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
        plan_key = plan_dag_key_from_id(args.plan_id)
        plan_id = plan_key[len("plan-"):]
        return (
            f"plan_id={plan_id}",
            {f"plan-{plan_id}", f"acceptance-{plan_id}"},
        )
    return f"name={args.name}", None


def _confirm(args) -> int:
    """omac plan confirm:人机门手动放行(方案3,防自动识别失效)。

    找 <name> 下停在人机门的产出 issue(IN_REVIEW + phase=REVIEW + 尚未指派
    reviewer),流转到 DONE。omac 的编排轮询识别到 DONE 后翻回评审流程。
    """
    from ...core.taskmeta import TaskKind, TaskPhase
    from ...engines.models import WorkItemStatus

    engine = _resolve_engine(args)
    workspace_id = engine.store.config.workspace_id
    label, dag_keys = _selector(args)

    # 待确认 = 设计/验收产出停在人机门:IN_REVIEW + phase=REVIEW + 尚未指派 reviewer。
    # 新入口按 dag_key 精确匹配;--name 仅保留兼容,重名时要求调用方换唯一 ID。
    waiting = [
        it for it in engine.store.list_work_items(workspace_id)
        if it.kind in (TaskKind.PLAN, TaskKind.ACCEPTANCE)
        and it.status == WorkItemStatus.IN_REVIEW
        and it.phase == TaskPhase.REVIEW
        and not it.reviewer
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
    engine.store.mark_done(it.id)
    print(ui(
        f"Confirmed: {it.title} (issue {it.id}) → DONE. OMAC will continue the review flow.",
        f"已确认:{it.title}(issue {it.id})→ DONE,omac 将继续评审流程"))
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
    )


def run(args) -> int:
    if args.action == "create":
        return _create(args)
    if args.action == "confirm":
        return _confirm(args)
    if args.action == "resume":
        return _resume(args)
    raise ValidationError(ui(
        f"Unknown plan subcommand: {args.action}",
        f"未知 plan 子命令:{args.action}"))
