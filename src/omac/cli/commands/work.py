"""omac work — 被派发 agent 的统一执行接口(5 类 issue × 产出/评审阶段)。"""
from __future__ import annotations

import sys

from ._stub import not_implemented
from ...core import config as config_mod
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import ValidationError
from ...pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    SubmitResult,
    build_show_output,
    submit,
)
from .. import exit_codes
from ..output import add_output_flag, print_json

NAME = "work"
SUMMARY = "统一执行接口(5 类 issue × 产出/评审阶段)"
DESCRIPTION = """被派活的 agent 永远只需要两个命令。

issue 的范围是一个完整阶段:产出、评审、回退往返都在同一条 issue 时间线上;
当前阶段与承担者由 issue metadata + assignee 表达,交接 = 转派(assign)。

  show     按(issue 类型 × 当前阶段 × 你的身份)输出任务上下文与执行协议
  submit   按同一维度校验并提交交付物(左移校验:缺什么当场打回,exit 5)

issue 类型与交付参数:
  plan              产出: --plan-file           review: --verdict --report-file
  acceptance        产出: --acceptance-file      review: 同上
  decompose         产出: --manifest-file        review: 同上
  develop           产出: --pr-url --verification-file(env 依赖时须含 env_setup)
                                                 review: 同上(report 必含评审目标)
  final-acceptance  产出: --acceptance-results-file(逐项 pass/fail,无 review 阶段)

硬约束:
  - 唯一写入口:交付物只经 `work submit` 写入,禁止手搓 metadata set(会冒出 dotted key /
    prose / JSON 三套口径,引擎读不到就误判 blocked、甚至失败隔离整条 DAG)。
  - 证据门:submit 时左移校验,verification 必须覆盖 contract.verification_commands 与
    contract.integration_gates,缺什么当场 exit 5 并精确告知;不写入、不转状态。
  - 改动分支覆盖硬门槛(缺省 90%):`diff-cover` 退出码非 0 = 不达标,不得转 in_review;
    不接受"先合后补"。
  - 收活铁律(reviewer):先 `git diff` 看真实改动,再独立复跑测试,绝不只凭 worker 自述;
    改动分支覆盖 < gate 阈值 = Blocker。
  - 只读共享态:reviewer 用 `git diff`/`git show` 审阅,绝不在共享主工作树 reset/checkout/merge。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    show = sub.add_parser("show", help="取任务上下文与该类型×阶段的执行协议")
    show.add_argument("issue_id")
    add_output_flag(show)

    submit = sub.add_parser("submit", help="提交交付物(左移校验)")
    submit.add_argument("issue_id")
    # submit 参数由 dispatch 单一事实源注册,与 show 模板共享防漂移
    for flag, kwargs in SUBMIT_PARAM_SPECS.items():
        submit.add_argument(flag, **kwargs)


def _resolve_store():
    """按 config < env < 命令行 解析引擎配置,返回 Store 实例。"""
    cfg = config_mod.load_config()
    engine_type, workspace_id, project_id = config_mod.resolve_engine_settings(cfg)
    config = EngineConfig(
        engine_type=engine_type, workspace_id=workspace_id, project_id=project_id)
    return create_engine(engine_type, config).store


def _identity_for(item) -> str:
    """按 phase 判定当前 assignee 的身份(authoring=worker, review=reviewer)。"""
    if item.phase.value == "review":
        return f"reviewer:{item.reviewer}"
    return f"worker:{item.worker}"


def _render_table(output: dict) -> None:
    """table 给人:分段可读文本。"""
    task = output["task"]
    print("=== 任务标识 ===")
    for key in ("kind", "phase", "dag_key", "issue_id", "title",
                "worker", "reviewer", "identity"):
        if task.get(key) is not None:
            print(f"  {key}: {task[key]}")

    print("\n=== 完整上下文 ===")
    ctx = output["context"]
    contract = ctx.get("contract")
    if contract is not None:
        print("  contract:")
        for k, v in contract.items():
            print(f"    {k}: {v}")
    if "deliverable" in ctx:
        print(f"  deliverable: {ctx['deliverable']}")
    env_setup = ctx.get("env_setup")
    if env_setup:
        print("  env_setup(复跑清单):")
        for step in env_setup:
            print(f"    - {step}")

    print("\n=== 执行协议 ===")
    print(output["protocol"])

    print("\n=== submit 模板 ===")
    print(f"  {output['submit']}")



def _submit(args) -> int:
    """work submit 入口:调 dispatch 左移门,ValidationError → exit 5。"""
    try:
        item = _get_item(args.issue_id)
    except ValidationError:
        raise
    result = submit(
        _resolve_store_for(item),
        args.issue_id,
        plan_file=args.plan_file,
        acceptance_file=args.acceptance_file,
        manifest_file=args.manifest_file,
        pr_url=args.pr_url,
        verification_file=args.verification_file,
        verdict=args.verdict,
        report_file=args.report_file,
        acceptance_results_file=args.acceptance_results_file,
    )
    target = (
        result.advanced_to.value
        if hasattr(result.advanced_to, "value")
        else result.advanced_to
    )
    print(
        f"交付物已提交 —— {result.kind.value} × {result.phase.value}\n"
        f"deliverable: {result.deliverable_key}\n"
        f"状态推进: {target}",
    )
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

    if args.output == "json":
        print_json(output)
    else:
        _render_table(output)
    return exit_codes.OK


def run(args) -> int:
    if args.action == "show":
        return _run_show(args)
    if args.action == "submit":
        try:
            return _submit(args)
        except ValidationError as e:
            print(str(e), file=sys.stderr)
            return exit_codes.VALIDATION
    return not_implemented(f"work {args.action}", "P2")
