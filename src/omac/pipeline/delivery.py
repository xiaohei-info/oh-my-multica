"""delivery — 交付阶段状态机:CI 监控 + 有界回退(设计文档 §7.3/§7.4)。

本模块承载 loop 收割顺序中「worker 证据过门」之后的交付环节:

    worker 证据过门 → ci_check* →(绿)in_review 转派 reviewer
                      │
                      └ CI 失败 / 评审 reject → 转回 worker(有界 ≤3,耗尽 → blocked)

`*` 标注的环节由 config 的 ci/merge 决定是否启用;未配置则整体跳过,
退化为现行为(P4.1 的回归保证)。

三类回退(CI 失败 / 评审 reject / merge 冲突)共用同一套「评论 + 转派 worker +
唤醒 + 计数 + 封顶」机制,只是计数器不同(ci_bounce / review_bounce /
merge_bounce)。本 issue(P4.1)实现 ci_bounce 与 review_bounce;merge_bounce
留待 P4.2。

纪律(§12.4):本层只调 engines 的 WorkItemStore/AgentRuntime 接口,
CI/merge 走模板命令(subprocess),绝不直接 shell out 平台 CLI。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ..core.config import get_ci_config
from ..engines.models import WorkItemStatus
from ..engines.runtime import AgentRuntime
from ..engines.store import WorkItemStore
from ..core.manifest import Manifest

# 评审通过集合(与 core/evidence.REVIEW_APPROVE 一致,本地复制避免循环依赖)
REVIEW_APPROVE = {"pass", "pass-with-nits"}

DEFAULT_MAX_BOUNCES = 3
DEFAULT_TIMEOUT_MINUTES = 30

# ── manifest 侧状态 ↔ 平台 WorkItemStatus 映射表 ──────────────────────────
# ci_check / merging 是 manifest 侧细分态:平台侧仍映射到 in_progress / in_review,
# 保证平台状态机不被细分态污染(设计文档 §7.3)。映射表写清并测试。
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
        raise ValueError(
            f"未知的 manifest 节点状态 {manifest_status!r} —— 合法值: "
            f"{sorted(VALID_MANIFEST_STATUSES)}"
        )
    return MANIFEST_TO_PLATFORM_STATUS[manifest_status]


# ── CI 检查执行 ────────────────────────────────────────────────────────────

@dataclass
class CIResult:
    """CI 检查结论。退出码即结论(0=绿,非0=失败);timeout_minutes 内未返回视为超时。"""
    passed: bool
    timed_out: bool
    exit_code: int | None
    output: str          # 命令完整输出(stdout+stderr)
    summary: str         # 输出尾部(回退评论里贴给 worker,指明修什么)

    @property
    def label(self) -> str:
        if self.timed_out:
            return "CI 检查超时"
        return f"CI 检查失败(退出码 {self.exit_code})"


def _tail(text: str, n: int = 2000) -> str:
    """输出尾部:回退评论只贴尾部,够 worker 定位问题又不刷屏。"""
    text = text.rstrip()
    if len(text) <= n:
        return text
    return "..." + text[-n:]


def run_ci_check(check_command: str, pr_url: str,
                 timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES) -> CIResult:
    """执行 ci.check_command 模板命令,{pr_url} 占位替换为实际 PR 地址。

    命令自身负责轮询远端 CI 直到出结果(退出码即结论);本函数用 timeout_minutes
    作为外层安全阀,超时即判定失败并带回退。
    """
    cmd = check_command.replace("{pr_url}", pr_url)
    timeout = max(1, int(timeout_minutes)) * 60
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = ""
        for stream in (exc.stdout, exc.stderr):
            if isinstance(stream, bytes):
                out += stream.decode("utf-8", errors="replace")
            elif isinstance(stream, str):
                out += stream
        return CIResult(
            passed=False, timed_out=True, exit_code=None,
            output=out, summary=_tail(out) or "(无输出)",
        )
    output = (proc.stdout or "") + (proc.stderr or "")
    return CIResult(
        passed=proc.returncode == 0, timed_out=False,
        exit_code=proc.returncode, output=output, summary=_tail(output) or "(无输出)",
    )


# ── 回退机制(三类共用) ──────────────────────────────────────────────────

def _bounce_back(
    manifest: Manifest, node_key: str, store: WorkItemStore, runtime: AgentRuntime,
    *, kind: str, comment: str, max_bounces: int = DEFAULT_MAX_BOUNCES,
) -> str:
    """评论回 issue + 转回 worker + 唤醒 + 计数 + 封顶。

    kind: 'ci' | 'review' | 'merge'(对应 ci_bounce / review_bounce / merge_bounce)。
    返回新 manifest 状态:'in_progress'(已转回 worker)或 'blocked'(回退耗尽)。
    封顶后节点标 blocked,平台同步 BLOCKED,不再转派。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    attr = f"{kind}_bounce"
    next_count = int(getattr(node, attr, 0)) + 1
    setattr(node, attr, next_count)

    store.add_comment(item_id, comment)

    if next_count >= max_bounces:
        node.status = "blocked"
        store.update_status(item_id, to_platform_status("blocked"))
        return "blocked"

    node.status = "in_progress"
    store.update_status(item_id, to_platform_status("in_progress"))
    store.assign_work_item(item_id, node.worker, "worker")
    runtime.wake(item_id, node.worker, "worker")
    return "in_progress"


# ── 交付推进:worker 证据过门 → ci_check → in_review ─────────────────────

def _transition_to_review(manifest: Manifest, node_key: str,
                          store: WorkItemStore, runtime: AgentRuntime) -> str:
    """CI 绿(或未配置)→ in_review,转派 reviewer 并唤醒。无 reviewer 则只标态。"""
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    node.status = "in_review"
    store.update_status(item_id, to_platform_status("in_review"))
    if node.reviewer:
        store.assign_work_item(item_id, node.reviewer, "reviewer")
        runtime.wake(item_id, node.reviewer, "reviewer")
    return "in_review"


def advance_delivery(
    config: dict, manifest: Manifest, node_key: str,
    store: WorkItemStore, runtime: AgentRuntime,
    *, max_bounces: int = DEFAULT_MAX_BOUNCES,
) -> str:
    """worker 证据已过门后推进交付环节。

    收割顺序(§7.3):worker 证据过门 → ci_check →(绿)in_review 转派 reviewer。
    - 未配置 ci → 整体跳过,直接 in_review(现行为,回归保证)。
    - CI 绿 → in_review 转派 reviewer。
    - CI 失败/超时 → 评论失败摘要(输出尾部)+ 转回 worker + ci_bounce+1;
      ≥max_bounces → blocked。
    返回新 manifest 状态:'in_review' | 'in_progress'(已回退) | 'blocked'。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    item = store.get_work_item(item_id)

    ci = get_ci_config(config)
    if ci is None:
        return _transition_to_review(manifest, node_key, store, runtime)

    pr_url = ""
    if isinstance(item.artifacts, dict):
        pr_url = item.artifacts.get("pr_url") or item.artifacts.get("pr") or ""
    if not pr_url:
        # CI 已配置但 worker 未提交 pr_url —— 证据门本应挡住,此处防御性阻断并教化。
        store.add_comment(
            item_id,
            "⚠️ CI 已配置(check_command)但 worker 未提交 pr_url,无法运行 CI 检查。\n"
            "请用 `omac work submit <id> --pr-url <url> --verification-file <ev.yaml>` "
            "补交 PR 地址后重新提交。",
        )
        node.status = "blocked"
        store.update_status(item_id, to_platform_status("blocked"))
        return "blocked"

    # 进入 ci_check(manifest 侧细分态,平台仍 in_progress)
    node.status = "ci_check"
    store.update_status(item_id, to_platform_status("ci_check"))

    result = run_ci_check(
        ci["check_command"], pr_url,
        ci.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES),
    )
    if result.passed:
        return _transition_to_review(manifest, node_key, store, runtime)

    comment = (
        f"⚠️ {result.label} —— CI 未通过,转回 worker 修复后重新提交。\n\n"
        f"--- 命令输出尾部 ---\n{result.summary}"
    )
    return _bounce_back(
        manifest, node_key, store, runtime,
        kind="ci", comment=comment, max_bounces=max_bounces,
    )


# ── 评审结果回收:reject 有界回退 ─────────────────────────────────────────

def handle_review_result(
    config: dict, manifest: Manifest, node_key: str,
    store: WorkItemStore, runtime: AgentRuntime,
    *, max_bounces: int = DEFAULT_MAX_BOUNCES,
) -> str:
    """回收 reviewer verdict。

    - pass / pass-with-nits → 留在 in_review(自动 merge 是 P4.2 的职责)。
    - reject → 评审意见 + 评审目标落 issue → 转回 worker + review_bounce+1;
      ≥max_bounces → blocked。
    返回新 manifest 状态:'in_review'(通过) | 'in_progress'(已回退) | 'blocked'。
    """
    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    item = store.get_work_item(item_id)
    verdict = item.review_verdict

    if verdict in REVIEW_APPROVE:
        node.status = "in_review"
        return "in_review"

    report = item.review_report if isinstance(item.review_report, dict) else {}
    review_goals = report.get("review_goals")
    parts = ["⚠️ 评审 reject —— 转回 worker 按评审目标修复后重新提交。"]
    parts.append("")
    parts.append("评审意见:")
    parts.append(item.review_comment or "(reviewer 未留意见)")
    if review_goals:
        parts.append("")
        parts.append("评审目标:")
        parts.append(str(review_goals))
    comment = "\n".join(parts)
    return _bounce_back(
        manifest, node_key, store, runtime,
        kind="review", comment=comment, max_bounces=max_bounces,
    )
