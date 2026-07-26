"""omac bridge — Multica 桥接:人工计划门、父工单投影与外部 merge 证据。

最薄桥接层(切片 5):只组合 WorkItemStore/AgentRuntime 与 core 校验器,
不重建调度器。dry-run/status 是纯观测(零写入);submit-* 是仅有的摄入
路径(PlanReturn 校验器与外部 merge 证据)。
"""
from __future__ import annotations

import json
import os

import yaml

from .. import exit_codes
from ..output import add_output_flag, hint, print_json, print_table
from ...bridge.multica import (
    partition_ready_by_plan_gate,
    project_parent,
    submit_external_merge_evidence,
    submit_plan_return,
    validate_machine_isolation,
)
from ...core import graph
from ...core.config import (
    DEFAULTS, load_config, resolve_delivery, resolve_engine_settings,
    resolve_machine,
)
from ...core.manifest import load_manifest
from ...engines import create_engine
from ...engines.models import EngineConfig
from ...errors import NeedsDecision, ValidationError
from ...i18n import ui
from .dag import _config_path_for_manifest

NAME = "bridge"
SUMMARY = "Multica 桥接:人工计划门、投影与外部 merge 证据(dry-run/status/submit-*)"
DESCRIPTION = """Multica 桥接层(最薄组合,不重建调度器)。

子命令:
  dry-run               评估一次派发决策而不做任何写入:计划门挡哪些节点、
                        机器隔离是否成立、父工单投影。exit 0 观测 /
                        5 机器隔离违规。
  status                父工单五阶段投影(Intake/Plan/Build/Verify/Done),
                        不需要可见工作流 label;blocked 是异常态,不是阶段。
                        退出码恒 0(观测,不是判定)。
  submit-plan-return    PlanReturn 校验器入口(唯一解锁人工计划门的路径)。
                        批准形式:
                          PlanReturn path=/absolute/path/to/plan.md
                          PlanReturn artifact=https://artifactd.example/...
                          PlanReturn host=artemis path=/absolute/path.md sha256=<digest>
                        畸形输入 exit 5;缺失/不可读/变动/hash 不匹配或未配置
                        安全 fetch 时 exit 20,stdout 出结构化报告(含可复制的
                        repair 行)。CLI 不内嵌任何 fetch/shell —— artifact/host
                        形式需要桥接 API 注入窄 fetch 适配器。
  submit-merge-evidence 外部 merge 权威证据摄入(external merge 模式):
                        只接受绑定已批准 pr_url + tip_sha 的证据,stale/wrong/
                        畸形一律 exit 5 且不落盘。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    dry_run = sub.add_parser(
        "dry-run", help="评估派发决策,零写入(计划门/机器隔离/投影)")
    dry_run.add_argument("manifest", help="manifest 文件路径")
    add_output_flag(dry_run)

    status = sub.add_parser("status", help="父工单五阶段投影(退出码恒 0)")
    status.add_argument("manifest", help="manifest 文件路径")
    add_output_flag(status)

    plan_return = sub.add_parser(
        "submit-plan-return", help="PlanReturn 校验器入口(解锁人工计划门)")
    plan_return.add_argument("manifest", help="manifest 文件路径")
    text_group = plan_return.add_mutually_exclusive_group(required=True)
    text_group.add_argument("--text", help="单行 PlanReturn 文本")
    text_group.add_argument("--text-file", help="含单行 PlanReturn 的文件路径")
    add_output_flag(plan_return)

    merge_evidence = sub.add_parser(
        "submit-merge-evidence", help="外部 merge 权威证据摄入")
    merge_evidence.add_argument("manifest", help="manifest 文件路径(定位项目配置)")
    merge_evidence.add_argument("--issue", required=True,
                                help="平台 issue / work item ID")
    merge_evidence.add_argument("--evidence-file", required=True,
                                help="merge 证据文件(JSON 或 YAML)")
    add_output_flag(merge_evidence)


def _load_manifest_checked(path: str):
    if not os.path.exists(path):
        raise ValidationError(ui(
            f"Manifest file not found: {path}\n"
            "  Generate it with `omac plan create --name <name>` or check the path.",
            f"manifest 文件不存在: {path}\n"
            f"  用 omac plan create --name <name> 生成,或检查路径"))
    return load_manifest(path)


def _snapshot_of(manifest) -> dict:
    return {
        key: {"status": node.status, "blocked_by": list(node.blocked_by)}
        for key, node in manifest.nodes.items()
    }


def dry_run(args) -> int:
    """评估派发决策,零写入。机器隔离违规 → exit 5(校验失败)。"""
    config = load_config(_config_path_for_manifest(args.manifest))
    manifest = _load_manifest_checked(args.manifest)

    isolation_ok = True
    isolation_errors = []
    try:
        validate_machine_isolation(config, manifest)
    except ValidationError as exc:
        isolation_ok = False
        isolation_errors = [line for line in str(exc).splitlines() if line.strip()]

    ready = graph.ready_nodes(_snapshot_of(manifest))
    dispatchable, gated = partition_ready_by_plan_gate(manifest, ready)
    projection = project_parent(manifest)

    payload = {
        "manifest": args.manifest,
        "delivery": resolve_delivery(config),
        "machine": resolve_machine(config),
        "machine_isolation": {"ok": isolation_ok, "errors": isolation_errors},
        "plan_gate": projection["plan_gate"],
        "ready_nodes": ready,
        "would_dispatch": dispatchable,
        "held_by_plan_gate": gated,
        "projection": projection,
    }
    if args.output == "json":
        print_json(payload)
    else:
        print_table(
            ("KEY", "STATUS", "DISPATCH"),
            [(key, manifest.nodes[key].status,
              "held-by-plan-gate" if key in gated else "ready")
             for key in ready] or [("-", "-", "no ready nodes")])
        print(ui(
            f"Plan gate: {'unlocked' if projection['plan_gate']['unlocked'] else 'locked'}"
            f"  Machine isolation: {'ok' if isolation_ok else 'VIOLATION'}"
            f"  Parent stage: {projection['stage']}",
            f"计划门: {'已解锁' if projection['plan_gate']['unlocked'] else '锁定'}"
            f"  机器隔离: {'正常' if isolation_ok else '违规'}"
            f"  父阶段: {projection['stage']}"))
        if not isolation_ok:
            for line in isolation_errors:
                print(f"  - {line}")
    if not isolation_ok:
        hint(ui(
            "Fix meta.source/meta.namespace, then rerun `omac bridge dry-run`.",
            "修正 meta.source/meta.namespace 后重跑 `omac bridge dry-run`"))
        return exit_codes.VALIDATION
    return exit_codes.OK


def status(args) -> int:
    """父工单投影,不推进;退出码恒 0。"""
    manifest = _load_manifest_checked(args.manifest)
    projection = project_parent(manifest)
    if args.output == "json":
        print_json({"manifest": args.manifest, "projection": projection})
        return exit_codes.OK
    print(ui(f"Parent stage: {projection['stage']}",
             f"父阶段: {projection['stage']}"))
    if projection["source"]:
        print(ui(
            f"Source: {projection['source'].get('project')}/"
            f"{projection['source'].get('issue')}",
            f"来源: {projection['source'].get('project')}/"
            f"{projection['source'].get('issue')}"))
    if projection["blocked"]:
        print(ui(
            f"Exception states (not stages): {', '.join(projection['blocked'])}",
            f"异常态(不是阶段): {', '.join(projection['blocked'])}"))
    print_table(
        ("KEY", "STAGE"),
        [(key, stage or "exception")
         for key, stage in projection["node_stages"].items()])
    return exit_codes.OK


def _read_plan_return_text(args) -> str:
    if args.text is not None:
        return args.text
    try:
        with open(args.text_file, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        raise ValidationError(ui(
            f"File not found: {args.text_file}", f"文件不存在: {args.text_file}"))
    except OSError as exc:
        raise ValidationError(ui(
            f"Could not read file {args.text_file}: {exc}",
            f"无法读取文件 {args.text_file}: {exc}"))


def submit_plan_return_cmd(args) -> int:
    """PlanReturn 校验器入口:成功写入 meta.plan_snapshot 并解锁人工计划门。"""
    config = load_config(_config_path_for_manifest(args.manifest))
    manifest = _load_manifest_checked(args.manifest)
    text = _read_plan_return_text(args)
    try:
        snapshot = submit_plan_return(
            manifest, args.manifest, text, config=config)
    except NeedsDecision as exc:
        # 结构化报告走 stdout(与 dag run exit 20 同一约定),异常继续抛给
        # main 统一转 exit 20。
        if args.output == "json":
            print_json(exc.report)
        raise
    if args.output == "json":
        print_json({
            "sha256": snapshot.sha256,
            "size": snapshot.size,
            "snapshot_path": snapshot.snapshot_path,
            "source": snapshot.source,
        })
    else:
        print(ui(
            f"Plan snapshot recorded: {snapshot.sha256}",
            f"计划快照已记录: {snapshot.sha256}"))
        print(ui(
            f"Immutable snapshot: {snapshot.snapshot_path}",
            f"不可变快照: {snapshot.snapshot_path}"))
    hint(ui(
        "Human plan gate unlocked. Next: `omac dag run <manifest>`.",
        "人工计划门已解锁。下一步:`omac dag run <manifest>`"))
    return exit_codes.OK


def _parse_evidence_file(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise ValidationError(ui(
            f"File not found: {path}", f"文件不存在: {path}"))
    except OSError as exc:
        raise ValidationError(ui(
            f"Could not read file {path}: {exc}", f"无法读取文件 {path}: {exc}"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(ui(
            f"{path} is neither valid JSON nor valid YAML: {exc}",
            f"{path} 既不是合法 JSON 也不是合法 YAML: {exc}"))


def submit_merge_evidence_cmd(args) -> int:
    """外部 merge 权威证据摄入(绑定已批准 pr_url + tip,stale 一律拒绝)。"""
    config = load_config(_config_path_for_manifest(args.manifest))
    engine_type, workspace_id, project_id = resolve_engine_settings(config)
    poll_interval = config.get("defaults", {}).get(
        "poll_interval", DEFAULTS["poll_interval"])
    engine = create_engine(engine_type, EngineConfig(
        engine_type=engine_type,
        workspace_id=workspace_id,
        project_id=project_id,
        polling_interval=poll_interval,
    ))
    evidence = _parse_evidence_file(args.evidence_file)
    submit_external_merge_evidence(engine.store, args.issue, evidence)
    if args.output == "json":
        print_json({"ok": True, "issue": args.issue})
    else:
        print(ui(
            f"External merge evidence recorded for {args.issue}; "
            "the next tick validates and advances.",
            f"外部 merge 证据已记录到 {args.issue};下一个 tick 校验并推进。"))
    return exit_codes.OK


def run(args) -> int:
    if args.action == "dry-run":
        return dry_run(args)
    if args.action == "status":
        return status(args)
    if args.action == "submit-plan-return":
        return submit_plan_return_cmd(args)
    if args.action == "submit-merge-evidence":
        return submit_merge_evidence_cmd(args)
    raise ValidationError(ui(
        f"Unknown bridge action: {args.action}",
        f"未知的 bridge 子命令: {args.action}"))
