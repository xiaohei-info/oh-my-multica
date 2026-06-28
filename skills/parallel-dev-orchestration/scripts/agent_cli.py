#!/usr/bin/env python3
"""agent_cli —— executor 端确定性入口（worker/reviewer 的「机床」）。

与 orchestrator 端 run_dag.py 对称：run_dag 管编排侧的确定性读写，agent_cli 管执行侧。
两者跑的是**同一份** scripts/（engines/ + core.evidence）——两个 skill 的 scripts 目录逐字一致，
以 orchestration 为权威源、整体复制到 executor（见 sync_to_executor.sh + test_skill_scripts_parity）。
这样 executor 自带完整引擎层、自给自足，不依赖运行时另一个 skill 是否在场（Multica 按 agent 隔离物化 skill）。

为什么需要它：让 worker/reviewer 不再手敲 `multica issue metadata set`。手敲会冒出
dotted key（artifacts.pr_url）/ prose / JSON 三套口径，runner 读不到就误判 blocked、
甚至失败隔离整条 DAG。agent_cli 独占写路径 → 只产唯一 JSON 口径，并在提交前用
runner 同一套 validator 自校验，证据不全当场拒绝，而不是甩给 harvest。

子命令：
  read-task     <id>                      读回归一化任务配置（worker/reviewer/契约）
  read-evidence <id>                      读回上游 artifacts/verification（reviewer 用）
  submit-worker <id> --pr-url ... ...     组装→自校验→写 artifacts/verification→转 in_review
  submit-review <id> --verdict ... ...    组装→自校验→写 verdict/report→转 done/blocked
  block         <id> --reason ...         标准化失败隔离（评论 + 转 blocked）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # 与 run_dag.py 一致：引擎层在本 skill 自带的 scripts/ 内

from core.evidence import validate_worker_evidence, validate_review_evidence
from core.manifest import _load_contract
from engines import create_engine_from_env, WorkItemStatus

REVIEW_APPROVE = ("pass", "pass-with-nits")


# ==================== 证据组装（纯函数）====================

def parse_command_spec(spec):
    """`cmd::exit_code::summary` → {cmd, exit_code[, summary]}。

    只给 cmd 时 exit_code 默认 0；summary 可省略。统一口径，杜绝 worker 自由格式。
    """
    parts = spec.split("::")
    out = {"cmd": parts[0]}
    out["exit_code"] = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
    if len(parts) > 2 and parts[2]:
        out["summary"] = parts[2]
    return out


def build_artifacts(pr_url, branch=None, commit=None):
    artifacts = {"pr_url": pr_url}
    if branch:
        artifacts["branch"] = branch
    if commit:
        artifacts["commit"] = commit
    return artifacts


def build_verification(commands, coverage=None, pr_base=None, integration_gates=None):
    verification = {
        "commands": [parse_command_spec(c) if isinstance(c, str) else c for c in (commands or [])]
    }
    if integration_gates is not None:
        verification["integration_gates"] = integration_gates
    if pr_base is not None:
        verification["pr_base"] = pr_base
    if coverage is not None:
        verification["coverage"] = coverage
    return verification


# ==================== 自校验（复用 runner 同一套 validator）====================

def _node_like(contract_dict):
    """validate_* 读 node.contract（Contract 对象）。把读回的 contract dict 还原成
    Contract，包成 node-like。契约缺失（未下发）则只做最小校验（pr_url）。"""
    contract = _load_contract(contract_dict) if contract_dict else None
    return type("_Node", (), {"contract": contract})()


def worker_gate_errors(item):
    return validate_worker_evidence(_node_like(getattr(item, "contract", None)), item)


def review_gate_errors(item):
    return validate_review_evidence(_node_like(getattr(item, "contract", None)), item)


# ==================== 命令（与引擎交互）====================

def submit_worker(engine, item_id, *, pr_url, branch=None, commit=None,
                  commands=None, coverage=None, pr_base=None, integration_gates=None):
    """组装 worker 证据 → 自校验 → 通过才写入并标 done。返回 gate 错误列表（空=成功）。

    标 done（不是 in_review）：交给 runner harvest 收割——有 reviewer 则由 runner 指派
    reviewer 并转 in_review，无 reviewer 直接收口 done。worker 不自行指派 reviewer。
    runner 会用 manifest 权威 contract 复跑同一套 validator，本地自校验只是提交前预拦截。
    """
    item = engine.get_work_item(item_id)
    item.artifacts = build_artifacts(pr_url, branch, commit)
    item.verification = build_verification(commands, coverage, pr_base, integration_gates)

    errors = worker_gate_errors(item)
    if errors:
        return errors  # 提交前拦截：拒绝写入、不转状态

    engine.update_work_item_metadata(item_id, artifacts=item.artifacts, verification=item.verification)
    engine.update_status(item_id, WorkItemStatus.DONE)
    return []


def submit_review(engine, item_id, *, verdict, report=None):
    """写 reviewer 判决。pass/pass-with-nits 先过证据门再转 done；blocked 直接隔离。

    返回 gate 错误列表（空=成功）。"""
    item = engine.get_work_item(item_id)
    item.review_verdict = verdict
    item.review_report = report

    if verdict in REVIEW_APPROVE:
        errors = review_gate_errors(item)
        if errors:
            return errors  # 报告不全：拒绝放行
        engine.update_work_item_metadata(item_id, review_verdict=verdict, review_report=report)
        engine.update_status(item_id, WorkItemStatus.DONE)
        return []

    # blocked / needs-changes：无需证据门，记录判决并失败隔离回流给编排器
    engine.update_work_item_metadata(item_id, review_verdict=verdict, review_report=report)
    engine.update_status(item_id, WorkItemStatus.BLOCKED)
    return []


def block_item(engine, item_id, reason):
    """worker/reviewer 失败：坦诚标 blocked + 评论原因，回流给编排器。"""
    engine.add_comment(item_id, f"❌ Blocked\n\n{reason}")
    engine.update_status(item_id, WorkItemStatus.BLOCKED)


def read_task(engine, item_id):
    item = engine.get_work_item(item_id)
    return {
        "id": item.id,
        "status": item.status.value,
        "dag_key": item.dag_key,
        "worker": item.worker,
        "reviewer": item.reviewer,
        "blocked_by": item.blocked_by,
        "has_contract": item.contract is not None,
        "contract": item.contract,
    }


def read_evidence(engine, item_id):
    item = engine.get_work_item(item_id)
    return {
        "artifacts": item.artifacts,
        "verification": item.verification,
        "review_verdict": item.review_verdict,
        "review_report": item.review_report,
    }


# ==================== CLI ====================

def _load_json_arg(value, flag):
    """--xxx-file 读 JSON 文件；为空返回 None。"""
    if not value:
        return None
    try:
        with open(value) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"错误: 读取 {flag} 失败: {e}", file=sys.stderr)
        sys.exit(2)


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _emit_gate_result(action, errors):
    """统一收口：有 gate 错误 → 打印缺项并以非零退出（让调用方/agent 当场看到拦截）。"""
    if errors:
        print(f"✗ {action} 被证据门拦截，未写入：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ {action} 成功")


def main(argv=None):
    parser = argparse.ArgumentParser(description="executor 端确定性入口（worker/reviewer）")
    parser.add_argument("--engine", help="引擎类型（默认从 .env / 环境变量读取）")
    parser.add_argument("--workspace", help="工作空间 ID（默认从环境变量读取）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("read-task", help="读回归一化任务配置")
    p.add_argument("item_id")

    p = sub.add_parser("read-evidence", help="读回上游 artifacts/verification")
    p.add_argument("item_id")

    p = sub.add_parser("submit-worker", help="组装→自校验→写证据→转 in_review")
    p.add_argument("item_id")
    p.add_argument("--pr-url", required=True)
    p.add_argument("--branch")
    p.add_argument("--commit")
    p.add_argument("--command", action="append", dest="commands",
                   help="格式 'cmd::exit_code::summary'，可重复")
    p.add_argument("--coverage", type=float)
    p.add_argument("--pr-base")
    p.add_argument("--integration-gates-file", help="集成门证据 JSON 文件")

    p = sub.add_parser("submit-review", help="组装→自校验→写判决→转 done/blocked")
    p.add_argument("item_id")
    p.add_argument("--verdict", required=True,
                   choices=["pass", "pass-with-nits", "blocked", "needs-changes"])
    p.add_argument("--report-file", help="review_report JSON 文件")

    p = sub.add_parser("block", help="标准化失败隔离")
    p.add_argument("item_id")
    p.add_argument("--reason", required=True)

    args = parser.parse_args(argv)
    engine = create_engine_from_env(engine_type=args.engine, workspace_id=args.workspace)

    if args.command == "read-task":
        _print(read_task(engine, args.item_id))
    elif args.command == "read-evidence":
        _print(read_evidence(engine, args.item_id))
    elif args.command == "submit-worker":
        gates = _load_json_arg(args.integration_gates_file, "--integration-gates-file")
        errors = submit_worker(
            engine, args.item_id, pr_url=args.pr_url, branch=args.branch, commit=args.commit,
            commands=args.commands, coverage=args.coverage, pr_base=args.pr_base,
            integration_gates=gates,
        )
        _emit_gate_result("submit-worker", errors)
    elif args.command == "submit-review":
        report = _load_json_arg(args.report_file, "--report-file")
        errors = submit_review(engine, args.item_id, verdict=args.verdict, report=report)
        _emit_gate_result(f"submit-review ({args.verdict})", errors)
    elif args.command == "block":
        block_item(engine, args.item_id, args.reason)
        print("✓ block 成功")


if __name__ == "__main__":
    main()
