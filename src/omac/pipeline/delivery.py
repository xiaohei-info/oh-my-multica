"""delivery — CI 监控与 ci_check 有界回退引擎(设计文档 §7.3)。

本模块承载 loop 收割顺序「worker 证据过门 → ci_check →(绿)in_review」中
worker 证据过门之后、进评审之前的 CI 门:

    worker 证据过门 ─► ci_check ─ 绿 ──► in_review 转派 reviewer
                              │
                              └ 失败/超时 ──► 有界转回 worker(ci_bounce+1,
                                               ≥ 上界 → blocked)

评审 reject 的回退(P4 任务 §7.3)由 ``loop.collect_results`` 内联实现
(读/写 ``WorkItem.bounces.review``),本模块不重复——避免与主线状态机分歧。

回退计数统一存放在平台侧 ``WorkItem.bounces`` 的 ``.ci`` 字段,
由 ``WorkItemStore.update_work_item_metadata(ci_bounce=...)`` 写入,Store 只存取;
manifest 的 ``Node`` 不携带回退计数(单一事实源)。上界由 ``config.retry.ci``
(缺省 3)经 ``resolve_retry`` 解析后注入 ``loop.tick`` 的 ``retry_limits``。

本模块还承载 reviewer pass 之后的 P4.2 自动 merge 门(§7.3):

    reviewer pass ─► merging ─ merge.command ─ 成功 ──► done(已合入)
                                         │
                                         └ 冲突/失败 ──► 有界转回 worker
                                                        (merge_bounce+1,
                                                         ≥ 上界 → blocked)

纪律(§12.4):CI / merge 走模板命令(subprocess),绝不直接 shell out 平台 CLI;
CI 在显式配置 ``config.ci.check_command`` 或检测到 ``.github/workflows``
时启用;否则跳过。merge 默认使用 ``gh pr merge``;显式 ``config.merge.command``
可覆盖。
考试点:退出码契约不可破(§5.1),
术语 §10.2 用「进行中节点」「就绪节点」,禁止 harvest/在飞 等硬译行话。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import time

from ..core.config import DEFAULT_RETRY, get_ci_config, get_merge_config
from ..engines.models import WorkItemStatus
from ..engines.runtime import AgentRuntime
from ..core.manifest import Manifest
from ..i18n import ui

# ── manifest 侧细分态 ↔ 平台 WorkItemStatus 映射表 ──────────────────────────
# ci_check 是 manifest 侧细分态;平台侧仍映射到 in_progress,保证平台状态机不被
# 细分态污染(设计文档 §7.3)。映射表写清并测试;未知状态报错即教学。
MANIFEST_TO_PLATFORM_STATUS: dict[str, WorkItemStatus] = {
    "todo": WorkItemStatus.TODO,
    "in_progress": WorkItemStatus.IN_PROGRESS,
    "ci_check": WorkItemStatus.IN_PROGRESS,
    "in_review": WorkItemStatus.IN_REVIEW,
    "merging": WorkItemStatus.IN_REVIEW,
    "done": WorkItemStatus.DONE,
    "blocked": WorkItemStatus.BLOCKED,
}

VALID_MANIFEST_STATUSES = set(MANIFEST_TO_PLATFORM_STATUS)


def to_platform_status(manifest_status: str) -> WorkItemStatus:
    """manifest 侧状态 → 平台 WorkItemStatus。未知状态报错即教学。"""
    if manifest_status not in MANIFEST_TO_PLATFORM_STATUS:
        raise ValueError(ui(
            f"Unknown manifest node status {manifest_status!r}. Valid values: "
            f"{sorted(VALID_MANIFEST_STATUSES)}",
            f"未知的 manifest 节点状态 {manifest_status!r} —— 合法值: "
            f"{sorted(VALID_MANIFEST_STATUSES)}"))
    return MANIFEST_TO_PLATFORM_STATUS[manifest_status]


# ── CI 检查执行 ──────────────────────────────────────────────────────────────

@dataclass
class CIResult:
    """CI 检查结论。退出码即结论(0 = 绿,非 0 = 失败);
    timeout_minutes 内未返回视为超时,外层安全阀兜底。"""

    passed: bool
    timed_out: bool
    exit_code: int | None
    output: str        # 命令完整输出(stdout + stderr)
    summary: str       # 输出尾部;回退评论里贴给 worker 定位

    @property
    def label(self) -> str:
        if self.timed_out:
            return ui("CI check timed out", "CI 检查超时")
        return ui(
            f"CI check failed (exit code {self.exit_code})",
            f"CI 检查失败(退出码 {self.exit_code})")


def _tail(text: str, n: int = 2000) -> str:
    """输出尾部:回退评论只贴尾部,够 worker 定位问题又不刷屏。短输出整段返回。"""
    text = text.rstrip()
    if len(text) <= n:
        return text
    return "..." + text[-n:]


def run_ci_check(check_command: str, pr_url: str, timeout_minutes: int = 30) -> CIResult:
    """执行 ``ci.check_command`` 模板命令,把 ``{pr_url}`` 占位替换为实际 PR 地址。

    命令本身负责轮询远端 CI 直到出结果(退出码即结论);本函数用 ``timeout_minutes``
    作为外层安全阀,超时即判定失败并带回退。
    """
    cmd = check_command.replace("{pr_url}", pr_url)
    timeout = max(1, int(timeout_minutes)) * 60
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = ""
        for stream in (exc.stdout, exc.stderr):
            if isinstance(stream, bytes):
                out += stream.decode("utf-8", errors="replace")
            elif isinstance(stream, str):
                out += stream
        return CIResult(passed=False, timed_out=True, exit_code=None,
                        output=out, summary=_tail(out) or ui("(no output)", "(无输出)"))
    output = (proc.stdout or "") + (proc.stderr or "")
    return CIResult(passed=proc.returncode == 0, timed_out=False,
                    exit_code=proc.returncode, output=output,
                    summary=_tail(output) or ui("(no output)", "(无输出)"))


# ── worker 证据过门后的 CI 门推进 ─────────────────────────────────────────────

def advance_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    retry_limits: dict,
    project_root: str = ".",
) -> str:
    """worker 证据已过门后推进 CI 门(§7.3)。

    - 未配置 ci 且未检测到 GitHub workflow → 环节整体跳过,返回 ``'pass'``。
    - 配置了 ci 或检测到 GitHub workflow → 进入 ``ci_check``(manifest 细分态,平台仍 in_progress),执行
      ``ci.check_command``:
        * 绿 → 回到 ``in_progress``,返回 ``'pass'``
        * 失败/超时 → 失败摘要(命令输出尾部) add_comment + 转回 worker +
          wake + ``ci_bounce``+1;
          - ≥ ``retry_limits['ci']`` → blocked,返回 ``'blocked'``
          - 否则返回 ``'bounce'``(节点已置回 in_progress,loop 本 tick 不动它)。

    回退计数(读/写)经平台 ``WorkItem.bounces.ci``(单一事实源,Store 只存取);
    manifest Node 不持计数。
    返回:'pass'(继续) | 'bounce'(已转回 worker,上界未到) | 'blocked'(上界耗尽)。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    ci = get_ci_config(config, root=project_root)
    if ci is None:
        return "pass"

    item = store.get_work_item(item_id)
    pr_url = ""
    if isinstance(item.artifacts, dict):
        pr_url = item.artifacts.get("pr_url") or item.artifacts.get("pr") or ""

    if not pr_url:
        # CI 已配置但 worker 未提交 pr_url —— 证据门本应挡住,此处防御性阻断并教化。
        store.add_comment(item_id, ui(
            "⚠️ CI is configured, but the worker did not submit pr_url, so the CI check cannot run.\n"
            "Resubmit with `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>`.",
            "⚠️ CI 已配置(check_command)但 worker 未提交 pr_url,无法运行 CI 检查。\\n"
            "请用 `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>` "
            "补交 PR 地址后重新提交。"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return "blocked"

    # 进入 ci_check(manifest 细分态;平台仍 in_progress)
    node.status = "ci_check"
    store.update_status(item_id, to_platform_status("ci_check"))

    result = run_ci_check(
        ci["check_command"], pr_url, ci.get("timeout_minutes", 30))
    if result.passed:
        node.status = "in_progress"
        store.update_status(item_id, to_platform_status("in_progress"))
        return "pass"

    # CI 失败/超时:先贴失败摘要(让 worker 知道修什么)。
    comment = ui(
        f"⚠️ {result.label}. Returning to the worker for repair and resubmission.\n\n"
        f"--- Command output tail ---\n{result.summary}",
        f"⚠️ {result.label} —— CI 未通过,转回 worker 修复后重新提交。\\n\\n"
        f"--- 命令输出尾部 ---\\n{result.summary}")
    store.add_comment(item_id, comment)

    # 回退计数读/写经平台 WorkItem.bounces.ci(单一事实源,Store 只存取)。
    cur_bounce = item.bounces.ci
    next_bounce = cur_bounce + 1
    store.update_work_item_metadata(item_id, ci_bounce=next_bounce)

    ci_limit = retry_limits.get("ci", DEFAULT_RETRY["ci"])
    if ci_limit == 0 or next_bounce >= ci_limit:
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return "blocked"

    # 有界转回 worker:改回 in_progress,重新派发 worker 并唤醒,让它修后重走 ci→review。
    node.status = "in_progress"
    store.update_status(item_id, to_platform_status("in_progress"))
    store.assign_work_item(item_id, node.worker, "worker")
    runtime.wake(item_id, node.worker, "worker")
    return "bounce"


# ── P4.2 自动 merge 与冲突回退 ──────────────────────────────────────────────

def run_merge_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    retry_limits: dict,
) -> str:
    """reviewer pass 后、进 done 之前的自动 merge 门(§7.3)。

    - 未配置 merge → 默认执行 ``gh pr merge {pr_url} --squash --delete-branch``。
    - 配置了 merge 但节点无 pr_url → 防御性 blocked + 报错即教学,返回 ``'blocked'``。
    - 配置了 merge → 进入 ``merging``(manifest 细分态,平台仍 in_review),执行
      ``merge.command``:
        * 成功 → 回到 ``in_progress`` 语义即「已合入」;manifest ``Node`` 记录
          ``merged: true`` / ``merged_at``;返回 ``'pass'``(loop 随即 ``done``)。
        * 冲突/失败 → 失败摘要(命令输出尾部) add_comment + reset_review + 转回
          worker + wake + ``merge_bounce``+1;
          - ≥ ``retry_limits['merge']`` → blocked,返回 ``'blocked'``
          - 否则返回 ``'bounce'``(节点已置回 in_progress,loop 本 tick 不动它)。

    回退计数(读/写)经平台 ``WorkItem.bounces.merge``(单一事实源,Store 只存取);
    manifest Node 不持计数。上界由 ``config.retry.merge``(缺省 3)经
    ``resolve_retry`` 解析后注入 ``loop.tick`` 的 ``retry_limits``。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    merge = get_merge_config(config)

    item = store.get_work_item(item_id)
    pr_url = ""
    if isinstance(item.artifacts, dict):
        pr_url = item.artifacts.get("pr_url") or item.artifacts.get("pr") or ""

    if not pr_url:
        # merge 已配置但 reviewer pass 后无 pr_url —— 防御性阻断并教化。
        store.add_comment(item_id, ui(
            "⚠️ Merge is configured, but the node has no pr_url, so automatic merge cannot run.\n"
            "Confirm the worker submitted a PR URL with `omac work submit <id> --pr-url <url> ...`.",
            "⚠️ merge 已配置(command)但节点无 pr_url,无法执行自动合并。\n"
            "请确认 worker 已用 `omac work submit <id> --pr-url <url> ...` 提交 PR 地址。"))
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return "blocked"

    # 进入 merging(manifest 细分态;平台仍 in_review)
    node.status = "merging"
    store.update_status(item_id, to_platform_status("merging"))

    command = merge["command"].replace("{pr_url}", pr_url)
    timeout = max(1, int(merge.get("timeout_minutes", 30))) * 60
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = ""
        for stream in (exc.stdout, exc.stderr):
            if isinstance(stream, bytes):
                out += stream.decode("utf-8", errors="replace")
            elif isinstance(stream, str):
                out += stream
        return _bounce_or_block_merge(
            node, item, store, runtime, retry_limits,
            failed=True, label=ui("Merge command timed out", "merge 命令超时"), output=out)

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        # 合入成功 → done = 已合入集成分支
        node.status = "in_progress"
        node.merged = True
        node.merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        store.update_status(item_id, to_platform_status("in_progress"))
        return "pass"

    # merge 冲突/失败
    return _bounce_or_block_merge(
        node, item, store, runtime, retry_limits,
        failed=True, label=ui(
            f"Merge command failed (exit code {proc.returncode})",
            f"merge 命令失败(退出码 {proc.returncode})"),
        output=output)


def _bounce_or_block_merge(node, item, store, runtime, retry_limits, *,
                           failed: bool, label: str, output: str) -> str:
    """merge 失败后的有界「回到 worker」回退(CI 路径的对称实现)。

    复用与 CI 回退相同的单一事实源与封顶语义:
    - 失败摘要(输出尾部)评论回 issue 让 worker 知道解什么;
    - reset_review 使旧评审结论失效,强制重走完整 ci→review→merge 链;
    - 读/写 WorkItem.bounces.merge,封顶即 blocked,否则转回 worker + wake。
    """
    item_id = node.work_item_id
    tail = _tail(output) or ui("(no output)", "(无输出)")
    store.add_comment(item_id, ui(
        f"⚠️ {label}. Returning to the worker to resolve the merge and rerun ci→review→merge.\n\n"
        f"--- Command output tail ---\n{tail}",
        f"⚠️ {label} —— merge 冲突,转回 worker 解决后重新走 ci→review→merge。\n\n"
        f"--- 命令输出尾部 ---\n{tail}"))

    cur_bounce = item.bounces.merge
    next_bounce = cur_bounce + 1
    store.update_work_item_metadata(item_id, merge_bounce=next_bounce)

    merge_limit = retry_limits.get("merge", DEFAULT_RETRY["merge"])
    if merge_limit == 0 or next_bounce >= merge_limit:
        node.status = "blocked"
        store.update_status(item_id, WorkItemStatus.BLOCKED)
        return "blocked"

    # 有界转回 worker:改回 in_progress,清除旧评审判定,重新派发 worker 并唤醒。
    # reset_review 确保旧 verdict 不会在新一轮被复用——强制 reviewer 重新 pass,
    # 否则新 PR 会在旧 verdict 下被自动 merge,绕过 reviewer gate。
    node.status = "in_progress"
    store.update_status(item_id, to_platform_status("in_progress"))
    store.reset_review(item_id)
    store.assign_work_item(item_id, node.worker, "worker")
    runtime.wake(item_id, node.worker, "worker")
    return "bounce"
