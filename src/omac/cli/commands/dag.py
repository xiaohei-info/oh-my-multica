"""omac dag — 确定性 loop 执行。"""
from __future__ import annotations
from typing import Optional

import argparse
import os

from .. import exit_codes
from ..output import add_output_flag, print_json, print_table
from ...core.config import ENV_ENGINE, ENV_WORKSPACE, load_config, resolve_engine_settings, DEFAULTS, CONFIG_PATH
from ...core.manifest import load_manifest
from ...core.gitsync import ensure_config_synced
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import NeedsDecision, ValidationError
from ...pipeline.loop import tick
from ...pipeline.acceptance import (
    acceptance_doc_path, run_acceptance_loop,
)
from ...pipeline.report import build_status_report, render_table

NAME = "dag"
SUMMARY = "确定性 loop 执行(run/status/tick)"
DESCRIPTION = """确定性编排循环:sync(回收结果)→ decide(就绪节点)→ dispatch(派发)。

子命令:
  run      前台循环直到收敛或需决策。exit 0 = 全部节点 done 且总控验收 pass;
           exit 20 = 无法继续推进,stdout 输出结构化报告(失败节点、证据摘要、
           受阻下游、可执行的下一步动作命令)。
           循环幂等:任意中断后重跑即续跑,done 节点复用。
           节点生命周期:todo → in_progress → ci_check* → in_review → merging* → done
           (* 由 config 的 ci/merge 决定;三类回退一律转回 worker,各有界 ≤3 次)
  status   随时查看快照(reconcile + 各节点状态),不推进;退出码恒 0
  tick     单轮推进后立即退出:exit 0 收敛 / 10 推进中 / 20 需决策(调试用)

有界运行:--max-rounds N / --max-minutes N(给不想长阻塞的 agent 调用者分段跑)
进度事件(走 stderr,不污染 stdout 数据线):默认人类文本,--json-logs /
--log-format json 出 JSON-lines 供上层机器/CI 解析(也可设 OMAC_LOG_FORMAT 环境变量)。
事件清单:dispatch / review_dispatch / verdict / revision(gate:worker|ci|review|guard)
/ node_done / node_failed / human_gate_wait / cascade_blocked / unblock / converged
/ needs_decision。

硬约束:
  - 前台阻塞监督铁律:run 是前台进程,必须在本轮跑到它返回才算"在监督";
    禁止放后台、禁止寄望"未来某轮再看"、禁止在无活跃 run 时声称"持续监督中"。
  - 重试显式:节点不会自动重试,必须经 `omac node retry` 显式决策。
  - 失败隔离:某节点 failed → 其下游自动 blocked,不再派发;不可绕过。
  - 不自动 merge:合并是外部门控,引擎只推进到 done,不替你合入。
  - manifest 唯一口径:全局状态只在 manifest + 平台,不依赖 checkpoint / event log。
"""


def _add_log_flags(parser):
    """允许 dag 子动作尾随日志 flag,同时不覆盖顶层同名 flag。"""
    parser.add_argument(
        "--log-format", choices=("text", "json"), default=argparse.SUPPRESS,
        help="进度事件格式:text 给人看(默认)/ json 给机器/CI 解析")
    parser.add_argument(
        "--json-logs", dest="log_format", action="store_const", const="json",
        default=argparse.SUPPRESS,
        help="--log-format json 的简写:进度事件出 JSON-lines(stderr)")


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    run_p = sub.add_parser("run", help="前台 loop 直到收敛或 exit 20")
    run_p.add_argument("manifest", help="manifest 文件路径")
    run_p.add_argument("--engine", help="引擎类型覆盖(缺省读 config/env)")
    run_p.add_argument("--workspace", help="workspace 覆盖(缺省读 config/env)")
    run_p.add_argument("--max-parallel", type=int, help="并发上限覆盖")
    run_p.add_argument("--max-rounds", type=int, help="最多跑 N 轮后退出(分段跑)")
    run_p.add_argument("--max-minutes", type=int, help="最多跑 N 分钟后退出(分段跑)")
    add_output_flag(run_p)
    _add_log_flags(run_p)

    status = sub.add_parser("status", help="查看快照,不推进(退出码恒 0)")
    status.add_argument("manifest", help="manifest 文件路径")
    status.add_argument("--engine", help="引擎类型覆盖(缺省读 config/env)")
    status.add_argument("--workspace", help="workspace 覆盖(缺省读 config/env)")
    add_output_flag(status)
    _add_log_flags(status)

    tick = sub.add_parser("tick", help="单轮推进后退出(exit 0/10/20)")
    tick.add_argument("manifest", help="manifest 文件路径")
    tick.add_argument("--engine", help="引擎类型覆盖(缺省读 config/env)")
    tick.add_argument("--workspace", help="workspace 覆盖(缺省读 config/env)")
    add_output_flag(tick)
    _add_log_flags(tick)


def _assemble_engine(args):
    """config.yaml < OMAC_* env < --engine/--workspace → Engine。

    返回 (engine, engine_config)。报错即教学(§2)。
    """
    config = load_config(CONFIG_PATH)
    engine_type, workspace_id, project_id = resolve_engine_settings(
        config, engine=getattr(args, "engine", None),
        workspace=getattr(args, "workspace", None),
        project=getattr(args, "project", None))

    poll_interval = config.get("defaults", {}).get(
        "poll_interval", DEFAULTS["poll_interval"])
    # OMAC_* 与 MOCK_* env vars 透传给 EngineConfig.extra(对齐 node.py/init_cmd 模式);
    # 让 MOCK_AUTO_COMPLETE / MOCK_AUTO_COMPLETE_DELAY 等 mock 配置可被 e2e 测试携带。
    extra = {
        k: v for k, v in os.environ.items()
        if (k.startswith('OMAC_') or k.startswith('MOCK_'))
        and k not in (ENV_ENGINE, ENV_WORKSPACE)
    } or None
    engine_config = EngineConfig(
        engine_type=engine_type,
        workspace_id=workspace_id,
        project_id=project_id,
        polling_interval=poll_interval,
        extra=extra,
    )
    engine = create_engine(engine_type, engine_config)

    # 测试钩子:OMAC_MOCK_FAIL_KEYS 注入失败节点(逗号分隔 dag_key)。
    # mock 引擎下可驱动失败场景;真实引擎忽略(set_fail_keys 不存在)。
    fail_keys = os.environ.get("OMAC_MOCK_FAIL_KEYS")
    if fail_keys and hasattr(engine.store, "set_fail_keys"):
        engine.store.set_fail_keys({k.strip() for k in fail_keys.split(",") if k.strip()})

    # 测试钩子:OMAC_MOCK_ACCEPTED / OMAC_MOCK_INCREMENTS 驱动总控验收行为(JSON).
    # OMAC_MOCK_ACCEPTED={"final-acceptance-r1":[{"id":"f1","status":"pass"}]}
    # OMAC_MOCK_INCREMENTS={"decompose-r1":{"nodes":[{"id":"fix-f1","worker":"alice","blocked_by":["b"]}]}}
    import json as _json
    accepted = os.environ.get("OMAC_MOCK_ACCEPTED")
    increments = os.environ.get("OMAC_MOCK_INCREMENTS")
    if (accepted or increments) and hasattr(engine.store, "set_acceptance_behaviors"):
        acc = _json.loads(accepted) if accepted else {}
        inc_raw = _json.loads(increments) if increments else {}
        inc = {}
        for dk, payload in inc_raw.items():
            from omac.core.manifest import Manifest, Node
            nodes = {n["id"]: Node(id=n["id"], worker=n["worker"],
                                     blocked_by=list(n.get("blocked_by", [])))
                     for n in payload.get("nodes", [])}
            inc[dk] = Manifest(meta=payload.get("meta", {}), nodes=nodes)
        engine.store.set_acceptance_behaviors(acc, inc)

    return engine, engine_config


def _default_max_parallel(args) -> int:
    override = getattr(args, "max_parallel", None)
    if override is not None:
        return override
    return load_config(CONFIG_PATH).get("defaults", {}).get(
        "max_parallel", DEFAULTS["max_parallel"])


def status(args) -> int:
    """reconcile + 快照,不推进;退出码恒 0(设计文档 §7.3)。"""
    if not os.path.exists(args.manifest):
        raise ValidationError(
            f"manifest 文件不存在: {args.manifest}\n"
            f"  用 omac plan create --name <name> 生成,或检查路径")
    engine, _ = _assemble_engine(args)
    config = load_config(CONFIG_PATH)
    manifest = load_manifest(args.manifest)
    report = build_status_report(manifest, engine.store, args.manifest)

    if args.output == "json":
        print_json(report)
    else:
        import sys
        sys.stdout.write(render_table(report))
    return exit_codes.OK


def _emit(result, manifest, args) -> None:
    """stdout 出数据:--output json 打完整 payload,否则 table 推进进度。

    run/tick 输出 loop 推进结果,schema 与 status 解耦(轻量、面向推进过程)。
    """
    payload = {
        "state": result.state,
        "done": result.done,
        "running": result.running,
        "failed": result.failed,
        "dispatched": result.dispatched,
    }
    payload["report"] = result.report or None

    if args.output == "json":
        print_json(payload)
        return

    headers = ("KEY", "STATUS", "WORKER", "WORK_ITEM_ID")
    rows = [
        (key, n.status, n.worker or "-", n.work_item_id or "-")
        for key, n in manifest.nodes.items()
    ]
    print_table(headers, rows)


def _maybe_acceptance(args, engine, config, manifest) -> Optional[int]:
    """内层 loop 收敛后:如有验收文档且未禁用,跑总控验收外层循环。

    返回退出码(0 全 pass / 20 耗尽仍 fail)或 None(无验收/禁用,由调用方 exit 0)。
    """
    manifest_path = args.manifest
    doc_path = acceptance_doc_path(manifest_path)
    no_acceptance = getattr(args, "no_acceptance", False)
    if no_acceptance:
        return None
    if not os.path.exists(doc_path):
        return None

    from ...core.acceptance import load_acceptance_doc_file as _load_doc
    try:
        doc = _load_doc(doc_path)
    except (ValueError, OSError) as exc:
        raise ValidationError(f"验收文档解析失败: {exc}")

    import time as _time
    outcome = run_acceptance_loop(
        engine, manifest, manifest_path, doc, config, no_acceptance=False,
        poll=lambda: _time.sleep(config.get("defaults", {}).get("poll_interval", 0)),
    )
    return outcome.exit_code


def _loop_or_single(args, single_round: bool) -> int:
    """共享核心:single_round=True → tick 一次退出;否则跑到收敛/需决策。"""
    if not os.path.exists(args.manifest):
        raise ValidationError(
            f"manifest 文件不存在: {args.manifest}\n"
            f"  用 omac plan create --name <name> 生成,或检查路径")

    import time as _time

    engine, _ = _assemble_engine(args)
    # 派单前:真实引擎下自动把 config 同步到 main,否则隔离区 agent clone 后读不到。
    ensure_config_synced(CONFIG_PATH, branch="main",
                         engine_type=engine.store.config.engine_type)
    config = load_config(CONFIG_PATH)
    manifest = load_manifest(args.manifest)
    max_parallel = _default_max_parallel(args)
    max_rounds = getattr(args, "max_rounds", None)
    max_minutes = getattr(args, "max_minutes", None)

    start = _time.monotonic()
    rounds = 0
    last_result = None

    while True:
        last_result = tick(
            engine.store, engine.runtime, manifest, args.manifest,
            max_parallel=max_parallel, config=config)
        rounds += 1

        if last_result.state == "converged":
            acceptance_exit = _maybe_acceptance(
                args, engine, config, manifest)
            if acceptance_exit is not None:
                # 验收外层循环可能已并入 fix 节点并收敛;重新 tick 一次
                # (幂等:全部 done 时不派发)拿到最新 done 列表再 emit,
                # 否则 emit 反映的是验收前的 3 节点,用户看不到增量节点。
                last_result = tick(
                    engine.store, engine.runtime, manifest, args.manifest,
                    max_parallel=max_parallel, config=config)
                _emit(last_result, manifest, args)
                return acceptance_exit
            _emit(last_result, manifest, args)
            return exit_codes.OK

        if last_result.state == "needs_decision":
            _emit(last_result, manifest, args)
            raise NeedsDecision(
                f"需调用者决策:节点 {last_result.failed} 失败/受阻,重跑/重试/abandon 后继续。",
                report=last_result.report,
            )

        if single_round:
            _emit(last_result, manifest, args)
            return exit_codes.IN_PROGRESS

        if max_rounds is not None and rounds >= max_rounds:
            _emit(last_result, manifest, args)
            return exit_codes.IN_PROGRESS
        if max_minutes is not None and (_time.monotonic() - start) >= max_minutes * 60:
            _emit(last_result, manifest, args)
            return exit_codes.IN_PROGRESS


def run(args) -> int:
    """前台 loop:sync → decide → dispatch,直到收敛或需决策(设计文档 §7.3)。

    幂等:全部状态在 manifest + 平台,任意中断重跑即续跑,done 节点复用。
    有界:--max-rounds / --max-minutes 支持分段跑。
    """
    if args.action == "status":
        return status(args)
    if args.action == "tick":
        return _loop_or_single(args, single_round=True)
    return _loop_or_single(args, single_round=False)
