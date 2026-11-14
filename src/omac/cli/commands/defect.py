"""omac defect — the operator's side of upstream-defect feedback (list / report / grant / resolve)."""
from __future__ import annotations

import os

import yaml

from .. import exit_codes
from ..output import add_output_flag, hint, print_json
from ..queued_writes import add_queue_flags, run_write
from ...core.manifest import load_manifest
from ...errors import ValidationError
from ...i18n import ui
from ...pipeline.defects import describe, grant_budget, report_defects, resolve_defect

NAME = "defect"
SUMMARY = "上游缺陷回流:查看账本(list)、人工上报(report)、补充自动修复预算(grant)、登记 OMAC 之外已合并的修复(resolve)"
DESCRIPTION = """上游缺陷回流的人工入口。Worker / Reviewer 发现"设计已写明、上游漏做"时由 `omac work block`
上报;`dag run` 在两轮 tick 之间校验、指纹去重、扣预算,再用系统 amendment 插入修复节点并让报告者等它。

子命令:
  list    只读:指纹账本(状态 queued/applying/fixing/fixed/recurred/needs-owner)、预算用量、待路由报告
  report  人工上报:--before <节点> 之前必须先修的缺陷(YAML,与 worker-blocker 的 defects 同形,repro 可省略);
          校验通过后排队,下次 `omac dag run` 路由
  grant   补充自动修复次数并让被预算停止的报告重新进入路由:--reporter <节点> 补报告者自己的配额,
          --global 提高全局上限 max_auto_fixes;停止报告的 budget.layers 说明该用哪一个
  resolve 登记 OMAC 之外已合并的修复:--pr <url> 把该指纹标为 fixed(之后不会再为它插修复节点),并打印收尾命令

预算两层(任一层用完即停):defects.per_reporter(每报告者)、defects.max_auto_fixes(全局)、defects.per_suspected(同一疑似节点)、defects.auto_fix=off 关闭自动修复。
修复节点自己再上报缺陷、同一缺陷在修复合并后复发、预算用尽,都会停在 exit 20,报告里给出可复制的放行命令。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    listing = sub.add_parser("list", help=ui("Read-only: the defect ledger and pending reports", "只读:缺陷账本与待路由报告"))
    listing.add_argument("manifest", help=ui("Manifest file path", "manifest 文件路径"))
    listing._help_key = "defect-list"
    add_output_flag(listing, default="json")

    report = sub.add_parser("report", help=ui("Queue a defect report: --before must wait for the fix", "人工上报一份缺陷:--before 节点之前必须先修"))
    report.add_argument("manifest", help=ui("Manifest file path", "manifest 文件路径"))
    report.add_argument("--before", required=True, help=ui("Node that must wait until the fix is merged (todo or blocked)", "必须等修复合并后才能继续的节点 id(todo 或 blocked)"))
    report.add_argument("--report-file", required=True, help=ui("Defect YAML: a defects list (or the list itself)", "缺陷 YAML:defects 列表(或直接是列表)"))

    grant = sub.add_parser("grant", help=ui("Grant a reporter more automatic fixes and re-route its stopped report", "补充报告者的自动修复次数并重新路由其报告"))
    grant.add_argument("manifest", help=ui("Manifest file path", "manifest 文件路径"))
    scope = grant.add_mutually_exclusive_group(required=True)
    scope.add_argument("--reporter", help=ui("Grant this reporter node more fixes (its own allowance is spent)", "给该报告者节点补充次数(它自己的配额用完)"))
    scope.add_argument("--global", dest="global_scope", action="store_true",
                       help=ui("Raise the DAG-wide allowance defects.max_auto_fixes (the whole DAG's budget is spent)", "提高全局上限 defects.max_auto_fixes(整个 DAG 的预算用完)"))
    grant.add_argument("--count", type=int, default=1, help=ui("How many fixes to add (default 1)", "补充次数(默认 1)"))

    resolve = sub.add_parser("resolve", help=ui("Record a fix merged outside OMAC: the fingerprint becomes fixed", "登记 OMAC 之外已合并的修复:该指纹标为 fixed"))
    resolve.add_argument("manifest", help=ui("Manifest file path", "manifest 文件路径"))
    resolve.add_argument("fingerprint", help=ui("Defect fingerprint (see `omac defect list`)", "缺陷指纹(见 `omac defect list`)"))
    resolve.add_argument("--pr", required=True, help=ui("URL of the merged fix PR", "已合并修复 PR 的 URL"))

    for writer in (report, grant, resolve):
        add_queue_flags(writer)


def _manifest(path):
    if not os.path.exists(path):
        raise ValidationError(ui(f"Manifest file not found: {path}", f"manifest 文件不存在: {path}"))
    return load_manifest(path)


def _read_defects(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(ui(f"Could not read {path}: {exc}", f"无法读取 {path}: {exc}")) from exc
    return data.get("defects") if isinstance(data, dict) else data


def apply_locked(args) -> int:
    """Apply a write action. The caller holds the manifest write lock (a direct command or the runner's tick boundary)."""
    manifest = _manifest(args.manifest)
    if args.action == "report":
        result = report_defects(manifest, args.manifest, args.before, _read_defects(args.report_file))
    elif args.action == "resolve":
        result = resolve_defect(manifest, args.manifest, args.fingerprint, args.pr)
    else:
        if args.count < 1:
            raise ValidationError(ui("--count must be at least 1", "--count 至少为 1"))
        result = grant_budget(manifest, args.manifest, args.reporter, args.count, global_scope=getattr(args, "global_scope", False))
    print_json(result)
    hint(ui(f"Run `omac dag run {args.manifest}` to route it.", f"运行 `omac dag run {args.manifest}` 使其生效。"))
    return exit_codes.OK


def run(args) -> int:
    if args.action == "list":
        print_json(describe(_manifest(args.manifest)))
        return exit_codes.OK
    return run_write(args, "defect", apply_locked)
