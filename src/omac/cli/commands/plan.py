"""omac plan — 设计方案 + DAG 拆解流水线(全程内置 review 阶段)。"""
from __future__ import annotations

import os

from ...core import config as config_mod
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import ValidationError
from .. import exit_codes
from ..output import hint
from ...pipeline.plan import PlanContext, plan_create

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
        "confirm", help="人机门手动放行:把 <name> 待确认的设计/验收 issue 流转到 DONE")
    confirm.add_argument("--name", required=True, help="方案名(plan create --name 用的同一名字)")
    confirm.add_argument("--engine", help="引擎类型,缺省按 config.yaml / 环境变量")
    confirm.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量")


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
        raise ValidationError("--goal 与 --goal-file 互斥,二选一")
    if goal:
        return goal
    if goal_file:
        if not os.path.exists(goal_file):
            raise ValidationError(f"--goal-file 不存在: {goal_file}")
        with open(goal_file, encoding="utf-8") as f:
            return f.read()
    return None


def _create(args) -> int:
    """mac plan create:装配 PlanContext + 调 plan_create 编排三阶段。"""
    cfg = config_mod.load_config()
    engine = _resolve_engine(args)
    workspace_id = engine.store.config.workspace_id
    roles = cfg.get("roles") or {}

    workers = roles.get("workers") or []
    if isinstance(workers, str):
        workers = [workers]
    reviewers = roles.get("reviewers") or []
    if isinstance(reviewers, str):
        reviewers = [reviewers]
    planner = roles.get("planner") or (workers[0] if workers else None)
    orchestrator = roles.get("orchestrator") or planner

    if not planner:
        raise ValidationError(
            "缺少 planner 角色 —— 请 `omac config set roles.planner <agent>`,"
            "或设置 roles.workers(取首位作为 planner)")

    goal_text = _resolve_goal(args)
    doc_path = getattr(args, "doc", None)
    if goal_text and doc_path:
        hint("同时给了 --doc 与 --goal:--doc 会跳过 planner 设计环节,--goal 被忽略")

    members = set(engine.store.list_members(workspace_id))
    ctx = PlanContext(
        engine=engine,
        workspace_id=workspace_id,
        planner=planner,
        orchestrator=orchestrator,
        reviewers=reviewers,
        max_revisions=resolve_review_rounds(cfg),
        no_review=args.no_review,
        no_acceptance=args.no_acceptance,
        members=members,
        confirm=not args.no_confirm,
    )
    return plan_create(ctx, args.name, doc_path=doc_path, goal_text=goal_text)


def _confirm(args) -> int:
    """omac plan confirm:人机门手动放行(方案3,防自动识别失效)。

    找 <name> 下停在人机门的产出 issue(IN_REVIEW + phase=REVIEW + 尚未指派
    reviewer),流转到 DONE。omac 的编排轮询识别到 DONE 后翻回评审流程。
    """
    from ...core.taskmeta import TaskKind, TaskPhase
    from ...engines.models import WorkItemStatus

    engine = _resolve_engine(args)
    workspace_id = engine.store.config.workspace_id
    name = args.name

    # 待确认 = 设计/验收产出停在人机门:IN_REVIEW + phase=REVIEW + 尚未指派 reviewer。
    # 标题含 [DAG:...] 前缀,故按 name 子串匹配(create 用 `{name} 设计方案/验收文档`)。
    waiting = [
        it for it in engine.store.list_work_items(workspace_id)
        if it.kind in (TaskKind.PLAN, TaskKind.ACCEPTANCE)
        and it.status == WorkItemStatus.IN_REVIEW
        and it.phase == TaskPhase.REVIEW
        and not it.reviewer
        and name in (it.title or "")
    ]
    if not waiting:
        raise ValidationError(
            f"未找到 {name} 待确认的设计/验收 issue —— "
            "可能已确认、尚未产出,或 --name 不匹配。")

    it = waiting[0]
    engine.store.mark_done(it.id)
    print(f"已确认:{it.title}(issue {it.id})→ DONE,omac 将继续评审流程")
    return exit_codes.OK


def run(args) -> int:
    if args.action == "create":
        return _create(args)
    if args.action == "confirm":
        return _confirm(args)
    raise ValidationError(f"未知 plan 子命令:{args.action}")
