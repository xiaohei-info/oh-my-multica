"""omac work — 被派发 agent 的统一执行接口(5 类 issue × 产出/评审阶段)。"""
from __future__ import annotations

import os
import sys

from ._stub import not_implemented
from ...core import config as config_mod
from ...engines import create_engine
from ...engines.models import EngineConfig, WorkItemStatus
from ...errors import OmacError, ValidationError
from ...pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    build_show_output,
    render_source_refs_section,
    submit,
)
from .. import exit_codes
from ..output import add_output_flag, print_json

NAME = "work"
SUMMARY = "Agent 执行接口:读取实例事实并提交结构化交付"
DESCRIPTION = """Agent 处理 omac 任务时使用的唯一执行接口。

先运行 show 读取当前实例事实、权威顺序、角色/产物 guide 和精确 submit 命令;
完成后只通过 submit 交付。show 与 submit 默认输出 JSON,成功与错误都可被 Agent 稳定解析。

  show     输出任务事实包(task/context/protocol/authority/guide_refs/submit)
  submit   校验并提交交付物,返回 submitted_phase/next_phase/advanced_to

issue 类型与交付参数:
  plan              产出: --plan-file           review: --verdict --report-file
  acceptance        产出: --acceptance-file      review: 同上
  decompose         产出: --manifest-file        review: 同上
  develop           产出: --pr-url --verification-file(env 依赖时须含 env_setup)
                                                 review: 同上(report 必含评审目标)
  final-acceptance  产出: --acceptance-results-file(逐项 pass/fail,无 review 阶段)

具体执行规则不要从 help 猜测,以 `work show` 返回的实例事实和 guide_refs 为准。
"""


def register(parser):
    parser._parse_error_renderer = _render_parse_error
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    show = sub.add_parser("show", help="给 Agent 返回当前任务的完整实例事实包(默认 JSON)")
    show._work_action = "show"
    show._parse_error_renderer = _render_parse_error
    show.add_argument("issue_id")
    add_output_flag(show, default="json")

    submit = sub.add_parser("submit", help="给 Agent 提交交付物并返回结构化结果(默认 JSON)")
    submit._work_action = "submit"
    submit._parse_error_renderer = _render_parse_error
    submit.add_argument("issue_id")
    add_output_flag(submit, default="json")
    # submit 参数由 dispatch 单一事实源注册,与 show 模板共享防漂移
    for flag, kwargs in SUBMIT_PARAM_SPECS.items():
        submit.add_argument(flag, **kwargs)


def _render_parse_error(parser, message: str, namespace) -> bool:
    """argparse 失败发生在 run() 之前,在这里闭合 work 的 JSON 错误契约。"""
    if getattr(namespace, "output", "json") == "table":
        return False
    print_json({
        "ok": False,
        "action": getattr(parser, "_work_action", None),
        "issue_id": getattr(namespace, "issue_id", None),
        "error": {
            "type": "ArgumentError",
            "message": message,
            "exit_code": exit_codes.GENERIC,
        },
        "help": parser.format_help(),
    }, stream=sys.stderr)
    return True


def _resolve_store():
    """按 config < env < 命令行 解析引擎配置,返回 Store 实例。"""
    cfg = config_mod.load_config()
    engine_type, workspace_id, project_id = config_mod.resolve_engine_settings(cfg)
    extra = dict(cfg.get("engine_extra") or {})
    if cfg.get("workspace_slug"):
        extra["workspace_slug"] = cfg["workspace_slug"]
    extra.update({
        k: v for k, v in os.environ.items()
        if k.startswith("OMAC_") or k.startswith("MOCK_")
    })
    config = EngineConfig(
        engine_type=engine_type, workspace_id=workspace_id,
        project_id=project_id, extra=extra)
    return create_engine(engine_type, config).store


def _identity_for(item) -> str:
    """按 phase × kind 如实标注身份:review=reviewer;authoring 用角色本名
    (planner/orchestrator/worker/acceptor),不再一律标 worker。"""
    if item.phase.value == "review" or item.status == WorkItemStatus.IN_REVIEW:
        return f"reviewer:{item.reviewer}"
    from ...pipeline.dispatch import KIND_ROLE
    return f"{KIND_ROLE.get(item.kind, 'worker')}:{item.worker}"


def _render_kv(label: str, value) -> None:
    """列表逐条、标量单行,统一缩进 markdown 子项。"""
    if isinstance(value, list):
        if not value:
            return
        print(f"- {label}:")
        for v in value:
            print(f"  - {v}")
    else:
        print(f"- {label}: {value}")


def _render_table(output: dict) -> None:
    """markdown 相位视图:任务头 / 上下文(相位特定)/ 现在做什么 / 完成后交付。

    - authoring:有 contract 才列(develop),无则指回 issue 正文
    - review:顶出只有此刻才存在的实例数据(评审对象 deliverable + env_setup 复跑清单)
    """
    task = output["task"]
    ctx = output["context"]
    is_review = task["phase"] == "review"

    print(f"# 任务 · {task['kind']} · {task['phase']}")
    print()
    print(f"- issue: {task['issue_id']}")
    print(f"- 标题: {task['title']}")
    print(f"- 状态: {task['status']}")
    if task.get("issue_key"):
        print(f"- issue_key: {task['issue_key']}")
    print(f"- 身份: {task['identity']}")
    if task.get("dag_key"):
        print(f"- dag_key: {task['dag_key']}")
    if task.get("wave") is not None:
        print(f"- wave: {task['wave']}")
    if task.get("blocked_by"):
        _render_kv("blocked_by", task["blocked_by"])
    if task.get("bounces"):
        print(f"- 回退计数: {task['bounces']}")
    if is_review and task.get("worker"):
        print(f"- 产出者: {task['worker']}")

    if ctx.get("issue_description"):
        print("\n## Issue 上下文")
        print(ctx["issue_description"])

    source_issues = ctx.get("source_issues")
    if source_issues:
        print()
        print(render_source_refs_section(
            source_issues,
            engine_env=output.get("engine_env"),
        ))

    contract = ctx.get("contract")
    if is_review:
        print("\n## 评审对象")
        if ctx.get("deliverable") is not None:
            print(f"- deliverable: {ctx['deliverable']}")
        for key in ("deliverable_ref", "artifacts", "verification", "verification_ref"):
            if ctx.get(key) is not None:
                _render_kv(key, ctx[key])
        env_setup = ctx.get("env_setup")
        if env_setup:
            print("- 复跑清单(env_setup):")
            for step in env_setup:
                print(f"  - {step}")
        if contract:
            print("\n## 评审依据(contract)")
            for k, v in contract.items():
                _render_kv(k, v)
    else:
        if contract:
            print("\n## 你的 contract(全量)")
            for k, v in contract.items():
                _render_kv(k, v)
        else:
            print("\n> 任务详情与需求见本 issue 正文（briefing / 上游产物 段)。")
        previous_review = ctx.get("previous_review")
        if previous_review:
            print("\n## 上轮评审")
            for k, v in previous_review.items():
                _render_kv(k, v)

    print("\n## 现在做什么")
    print(output["protocol"])

    print("\n## 权威顺序")
    for index, source in enumerate(output.get("authority", []), start=1):
        print(f"{index}. {source}")

    print("\n## 需要读取的 Guide")
    for command in output.get("guide_refs", []):
        print(f"- `{command}`")

    print("\n## 完成后交付")
    print(f"    {output['submit']}")



def _submit(args) -> int:
    """work submit 入口:调 dispatch 左移门,ValidationError → exit 5。"""
    try:
        item = _get_item(args.issue_id)
    except ValidationError:
        raise
    store = _resolve_store_for(item)
    agent_pool = set(store.list_members(store.config.workspace_id))
    result = submit(
        store,
        args.issue_id,
        plan_file=args.plan_file,
        acceptance_file=args.acceptance_file,
        manifest_file=args.manifest_file,
        pr_url=args.pr_url,
        verification_file=args.verification_file,
        verdict=args.verdict,
        report_file=args.report_file,
        acceptance_results_file=args.acceptance_results_file,
        agent_pool=agent_pool,
    )
    target = (
        result.advanced_to.value
        if hasattr(result.advanced_to, "value")
        else result.advanced_to
    )
    next_phase = (
        result.next_phase.value
        if hasattr(result.next_phase, "value")
        else result.next_phase
    )
    message = (
        f"交付物已提交 —— {result.kind.value} × {result.phase.value}\n"
        f"deliverable: {result.deliverable_key}"
    )
    if result.phase.value == "review":
        message += (
            f"\nverdict 已提交: {args.verdict}"
            "\n平台终态由 omac loop 收口；不要手动修改 issue 状态/assignee。"
        )
    else:
        message += f"\n状态推进: {target}"
    if getattr(result, "message", None):
        message += f"\n{result.message}"
    if getattr(args, "output", "json") == "json":
        payload = {
            "ok": True,
            "issue_id": args.issue_id,
            "kind": result.kind.value,
            "submitted_phase": result.phase.value,
            "next_phase": next_phase,
            "deliverable_key": result.deliverable_key,
            "advanced_to": target,
            "message": result.message,
        }
        if result.phase.value == "review":
            payload["verdict"] = args.verdict
        print_json(payload)
    else:
        print(message)
    return exit_codes.OK


def _get_item(issue_id: str):
    store = _resolve_store()
    try:
        return store.get_work_item(issue_id)
    except Exception as e:
        raise ValidationError(f"无法读取 work item '{issue_id}' —— {e}")


def _resolve_store_for(item) -> object:
    """submit 与 show 共用同一引擎/工作空间上下文,保证读写同源。"""
    return _resolve_store()


def _run_show(args) -> int:
    store = _resolve_store()
    try:
        item = store.get_work_item(args.issue_id)
    except Exception as e:
        raise ValidationError(f"无法读取 work item '{args.issue_id}' —— {e}")

    identity = _identity_for(item)
    output = build_show_output(item, identity)
    output["engine_env"] = _store_env(store)

    if getattr(args, "output", "json") == "json":
        print_json(output)
    else:
        _render_table(output)
    return exit_codes.OK


def _store_env(store) -> dict:
    config = store.config
    env = {
        "OMAC_ENGINE": config.engine_type,
        "OMAC_WORKSPACE_ID": config.workspace_id,
    }
    if config.project_id:
        env["OMAC_PROJECT_ID"] = config.project_id
    workspace_slug = (config.extra or {}).get("workspace_slug") or (config.extra or {}).get("OMAC_WORKSPACE_SLUG")
    if workspace_slug:
        env["OMAC_WORKSPACE_SLUG"] = workspace_slug
    return env


def _render_error(args, error: OmacError) -> int:
    """work 是 Agent-first 接口；JSON 模式下错误也保持结构化。"""
    if getattr(args, "output", "json") == "json":
        print_json({
            "ok": False,
            "action": args.action,
            "issue_id": getattr(args, "issue_id", None),
            "error": {
                "type": error.__class__.__name__,
                "message": str(error),
                "exit_code": error.exit_code,
            },
        }, stream=sys.stderr)
    else:
        print(f"Error: {error}", file=sys.stderr)
    return error.exit_code


def run(args) -> int:
    try:
        if args.action == "show":
            return _run_show(args)
        if args.action == "submit":
            return _submit(args)
        return not_implemented(f"work {args.action}", "P2")
    except OmacError as error:
        return _render_error(args, error)
