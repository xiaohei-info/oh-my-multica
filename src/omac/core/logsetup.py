"""进度事件流:structlog 配置 + 格式解析 + 事件名常量。

两条正交输出线(见输出分层设计):
- 数据线(stdout):命令 payload / 查询结果 / 末尾汇总表 —— 归 cli/output.py。
- 进度线(stderr):loop 生命周期事件 —— 本模块。进度永不污染 stdout。

一批事件、两个渲染器:默认 ConsoleRenderer(给人),--json-logs/OMAC_LOG_FORMAT=json
切 JSONRenderer(给上层机器/CI 解析,扁平 {"event":...,字段})。核心只写
`log.info(EVT_DISPATCH, id=..., worker=...)`,渲染在边缘决定。
"""
from __future__ import annotations

import json
import os
import sys

import structlog

TEXT = "text"
JSON = "json"
LOG_FORMATS = (TEXT, JSON)

# ── 事件名常量(机器契约:上层 LLM/CI 依赖这些名字解析事件流)──
EVT_DISPATCH = "dispatch"                # 节点/任务派单给 worker
EVT_REVIEW_DISPATCH = "review_dispatch"  # 同一 issue 转派 reviewer
EVT_VERDICT = "verdict"                  # 评审判决(pass/reject)
EVT_REVISION = "revision"                # 回退重做(gate: worker/ci/review/guard)
EVT_NODE_DONE = "node_done"              # 节点/任务完成
EVT_NODE_FAILED = "node_failed"          # 节点/任务失败或受阻
EVT_HUMAN_GATE_WAIT = "human_gate_wait"  # confirm 门:干等人挪 issue(否则看着像卡死)
EVT_CASCADE_BLOCKED = "cascade_blocked"  # 失败连坐:下游被标 blocked
EVT_UNBLOCK = "unblock"                  # 上游修复:blocked 下游解封回 todo
EVT_CONVERGED = "converged"              # loop 出口:全部 done
EVT_NEEDS_DECISION = "needs_decision"    # loop 出口:有失败/受阻,需决策
EVT_CONFIG_SYNCED = "config_synced"      # 派单前 config 自动同步到 main


def resolve_log_format(cli: str | None = None) -> str:
    """格式来源优先级:--log-format flag > OMAC_LOG_FORMAT 环境变量 > 默认 text。

    未知值一律回落 text —— 宁可给人看,也不吐半吊子。
    """
    if cli in LOG_FORMATS:
        return cli
    env = os.environ.get("OMAC_LOG_FORMAT", "").strip().lower()
    if env in LOG_FORMATS:
        return env
    return TEXT


def configure_logging(fmt: str) -> None:
    """一次性配置 structlog:事件走 stderr,按 fmt 选人类/机器渲染器。

    pytest capsys 在每次捕获/sys.stderr 切换成新的 StringIO;结构log 的
    PrintLoggerFactory(file=sys.stderr) 求值一次后闭包会冻结那个 StringIO,下个测试就打到
    已关闭的流上 → "I/O on closed file"。

    解法:自定义工厂,每次 emit 时回落 sys.stderr(不缓存老对象)。配合
    cache_logger_on_first_use=False,保证结构log 拿到的是此刻真实 stderr
    (生产=终端,测试=capsys 捕获流),永远不会跨测试泄漏。
    """
    fmt = fmt if fmt in LOG_FORMATS else TEXT
    if fmt == JSON:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    class _StderrFactory:
        """每次 emit 回落 sys.stderr,不缓存老对象(兼容 pytest capsys)。"""

        def __call__(self, *args, **kwargs):
            return structlog._output.PrintLogger(file=sys.stderr)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        logger_factory=_StderrFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None):
    """取 logger。若尚未 configure(库/测试直接调 pipeline,未过 CLI 入口),
    惰性落一个 stderr 默认 —— structlog 原生默认打 stdout,会污染数据线。
    """
    if not structlog.is_configured():
        configure_logging(resolve_log_format())
    return structlog.get_logger(name)
