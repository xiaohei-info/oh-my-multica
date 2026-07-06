"""omac plan — 计划制定 + DAG 拆解流水线(全程内置 review 阶段)。"""
from __future__ import annotations

import os
import time

from ...core import config as config_mod
from ...core.graph import node_waves
from ...core.lint import lint
from ...core.manifest import load_manifest
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import ValidationError
from ...pipeline.review import run_review
from .. import exit_codes
from ..output import add_output_flag, hint, print_json, print_table
from ._stub import not_implemented
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
SUMMARY = "计划制定 + DAG 拆解流水线(全程内置 review 阶段)"
DESCRIPTION = """计划制定与 DAG 拆解流水线。

子命令:
  create   两种模式一条流水线:
             --doc <设计文档>  跳过 planner 制定计划环节,直接进验收文档 + 拆解
             (无 --doc)      planner 从零制定计划,评审通过后继续
           计划定稿后 planner 产出验收文档(业务流程 → 用户视角端到端可执行
           验收动作),再由 orchestrator 拆解为 manifest DAG。
           issue 的范围 = 一个完整阶段:产出 → 评审 → 回退修订都在同一条
           issue 上,评审 = 该 issue 转派 reviewer。
           开关:--no-review 跳过全部 review 阶段;--no-acceptance 跳过验收文档。
  check    调用者自拆 manifest 的门禁:lint 机器门 + (配置了 reviewers 且未
           --no-review)manifest review 阶段(经 P3.1 共享的 Store/Runtime 原语)。
           退出码:0 通过 / 5 lint 失败(附完整错误清单) / 20 review 拒绝。
  show     查看已注册 manifest 的摘要:meta、节点统计、按 wave/依赖的简要拓扑、
           契约覆盖率(多少节点有 contract/验收锚定);支持 --output json。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    create = sub.add_parser("create", help="启动计划→验收文档→拆解流水线")
    create.add_argument("--name", required=True, help="manifest 名(落盘 .orchestrator/<name>.yaml)")
    create.add_argument("--doc", help="已有设计/计划文档路径(给了就跳过 planner 制定环节)")
    create.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    create.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")

    check = sub.add_parser("check", help="lint + review 一份现成 manifest(调用者自拆模式)")
    check.add_argument("manifest", help="manifest 文件路径")
    check.add_argument("--no-review", action="store_true", help="跳过 manifest review 阶段(仅 lint)")
    check.add_argument("--engine", help="引擎类型(multica|mock),缺省按 config.yaml / 环境变量 OMAC_ENGINE")
    check.add_argument("--workspace", help="工作空间 id,缺省按 config.yaml / 环境变量 OMAC_WORKSPACE_ID")
    add_output_flag(check)

    show = sub.add_parser("show", help="查看 manifest 摘要")
    show.add_argument("manifest", help="manifest 文件路径")
    add_output_flag(show)


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


def _check(args) -> int:
    path = args.manifest
    if not os.path.exists(path):
        raise ValidationError(f"manifest 文件不存在: {path} —— 请确认路径或先 `omac plan create`")

    manifest = load_manifest(path)
    name = manifest.meta.get("name") or os.path.basename(path)

    engine = _resolve_engine(args)
    pool = set(engine.store.list_members(engine.store.config.workspace_id))
    errs = lint(manifest, pool)

    if errs:
        if args.output == "json":
            print_json({"ok": False, "errors": errs})
        else:
            print(f"lint 失败({len(errs)} 项):", flush=True)
            for e in errs:
                print(f"  - {e}")
            hint("修订后重跑 `omac plan check <file>` 重新过门")
        return exit_codes.VALIDATION

    reviewers = (config_mod.load_config().get("roles") or {}).get("reviewers") or []
    reviewed = False
    if reviewers and not args.no_review:
        reviewer = reviewers[0]
        run_review(
            engine, engine.store.config.workspace_id,
            title=f"[plan-check] {name}",
            body=_review_body(manifest, path),
            reviewer=reviewer,
            poll=lambda: time.sleep(1),
        )
        reviewed = True

    if args.output == "json":
        print_json({
            "ok": True,
            "lint_errors": 0,
            "review": "pass" if reviewed else "skipped",
        })
    else:
        print(f"lint 通过({len(manifest.nodes)} 节点)")
        if reviewed:
            print(f"review 通过(reviewer={reviewers[0]})")
        else:
            hint("未配置 reviewers 或已 --no-review,跳过 manifest review 阶段")
        hint("下一步:`omac dag run` 开始执行")
    return exit_codes.OK


def _review_body(manifest, path: str) -> str:
    """渲染 manifest review 的 issue body(三段式 §7.4 的简报变体)。"""
    import yaml
    return (
        "你被分配了一件 manifest review 任务(必须经 omac 交互):\n"
        f"  1. 审查 manifest 文件:{path}\n"
        f"  2. 审查通过后 omac work submit <id> --verdict pass --report-file ..."
        "  拒绝请用 --verdict reject,附 blockers。\n"
        "遇到不明确的地方:运行 omac guide reviewer 查阅 reviewer 角色说明。\n\n"
        "## 简报\n"
        f"- 节点数:{len(manifest.nodes)}\n"
        f"- meta:{manifest.meta}\n\n"
        "## 硬约束\n"
        " reviewer 独立复跑验证命令与 manifest 门(lint/contract/成员池),不信自述。"
        "\n 契约与 DAG 门禁不过即 reject。\n"
        + yaml.dump(
            {"manifest": manifest.meta,
             "nodes": {k: {"worker": n.worker, "blocked_by": n.blocked_by}
                       for k, n in manifest.nodes.items()}},
            default_flow_style=False, allow_unicode=True, sort_keys=False)
    )


def _show(args) -> int:
    path = args.manifest
    if not os.path.exists(path):
        raise ValidationError(f"manifest 文件不存在: {path} —— 请确认路径")

    manifest = load_manifest(path)
    nodes = manifest.nodes
    total = len(nodes)
    with_contract = sum(1 for n in nodes.values() if n.contract and n.contract.acceptance)
    waves = node_waves(nodes)

    by_wave: dict[int, list[str]] = {}
    for key, wave in sorted(waves.items(), key=lambda kv: (kv[1], kv[0])):
        by_wave.setdefault(wave, []).append(key)

    by_status: dict[str, int] = {}
    for n in nodes.values():
        by_status[n.status] = by_status.get(n.status, 0) + 1

    edges = [[b, key] for key, n in nodes.items()
             for b in n.blocked_by if b in nodes]

    if args.output == "json":
        print_json({
            "meta": manifest.meta,
            "nodes": {
                "total": total,
                "with_contract": with_contract,
                "contract_coverage": f"{with_contract}/{total}",
                "by_status": by_status,
            },
            "topology": {
                "waves": {str(w): ks for w, ks in by_wave.items()},
                "edges": edges,
            },
        })
        return exit_codes.OK

    print(f"manifest: {os.path.basename(path)}")
    print(f"节点:{total}  契约覆盖:{with_contract}/{total}  状态:{', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}")
    print_table(["wave", "nodes"], [(str(w), ", ".join(ks)) for w, ks in sorted(by_wave.items())])
    dep_rows = [(b, "->", k) for b, k in edges]
    if dep_rows:
        print_table(["from", "", "to"], dep_rows)
    else:
        hint("无依赖边(全部为根节点)")
    return exit_codes.OK


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
    )
    return plan_create(ctx, args.name, doc_path=getattr(args, "doc", None))


def run(args) -> int:
    if args.action == "create":
        return _create(args)
    if args.action == "check":
        return _check(args)
    if args.action == "show":
        return _show(args)
    return not_implemented(f"plan {args.action}", "P3")
