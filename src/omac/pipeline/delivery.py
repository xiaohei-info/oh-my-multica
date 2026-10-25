"""delivery — CI 监控与 ci_check 有界回退引擎(docs/design/README.md §7.3)。

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

    reviewer pass ─► merging ─ merge.command ─► observe remote PR
                                                    │
                         MERGED + mergedAt ─────────┴──► done(已合入)
                         OPEN / queue pending ─────────► merging
                         CLOSED_UNMERGED ──────────────► 有界转回 worker
                         UNKNOWN / temporary read error ─► merging (observe later)

纪律(§12.4):CI 走模板命令；merge/observe 只经 WorkItemStore 由引擎适配器执行，
pipeline 不直接 shell out 平台 CLI；
CI 在显式配置 ``config.ci.check_command`` 或检测到 ``.github/workflows``
时启用;否则跳过。merge 默认使用 ``gh pr merge``;显式 ``config.merge.command``
可覆盖。
考试点:退出码契约不可破(§5.1),
术语 §10.2 用「进行中节点」「就绪节点」,禁止 harvest/在飞 等硬译行话。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import DEFAULT_RETRY, get_ci_config, get_merge_config
from ..core.retry_budget import consumed_bounces
from ..core.workitem import (
    PullRequestCheckResult,
    PullRequestObservation,
    PullRequestState,
    WorkItemStatus,
)
from ..engines.codehost import CodeHost
from .decide_merge import (
    AfterMergeRequest, MergeAfterRequest, MergeObserved, MergePrecheck, MergePrecheckObservation, MergeStep,
    auth_error_durable, auth_error_remote, block_no_pull_request,
    block_unproven, decide_after_merge_request, decide_merge_observed, decide_merge_precheck,
    merge_bounce_attempt, plan_blocked_timed_out_open, plan_bounce_pending, plan_observed_step,
    plan_request_intent, plan_request_sent, plan_resume_bounce, plan_settle, tail_of,
)
from ..engines.runtime import AgentRuntime
from ..core.manifest import Manifest
from ..errors import AuthError, PlatformError
from ..i18n import ui
from .apply import ManifestState, Ports, apply
from .decide_ci import plan_ci_result, plan_ci_start
from .decide_effects import RequestMerge

# ── manifest 侧细分态 ↔ 平台 WorkItemStatus 映射表 ──────────────────────────
# ci_check 是 manifest 侧细分态;平台侧仍映射到 in_progress,保证平台状态机不被
# 细分态污染(docs/design/README.md §7.3)。映射表写清并测试;未知状态报错即教学。
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


_tail = tail_of


def _ci_result(result: PullRequestCheckResult) -> CIResult:
    return CIResult(
        passed=result.succeeded,
        timed_out=result.timed_out,
        exit_code=result.exit_code,
        output=result.output,
        summary=_tail(result.output) or ui("(no output)", "(无输出)"),
    )


def block_unproven_merge_request(
    node, item, store, manifest_path: str | None, node_key: str, detail: str,
) -> str:
    """保留不确定的 merge marker，要求人工核实后显式 retry。"""
    plan = block_unproven(node_key, item.id, manifest_path, detail, save=False)
    apply(plan, Ports(store=store), _LoneNode(node))
    return plan.outcome


class _LoneNode:
    """State sink for plans that only mutate one node the caller already holds (the caller saves)."""

    def __init__(self, node):
        self.node = node

    def set_node(self, node_key: str, fields: dict) -> None:
        for name, value in fields.items():
            setattr(self.node, name, value)

    def save(self) -> None:
        raise RuntimeError("a plan applied to a lone node must not save the manifest")


def block_merge_auth_error(
    node, item, store, manifest: Manifest, manifest_path: str, node_key: str,
) -> str:
    """认证/授权失败必须 fail closed，且不能篡改既有 merge 恢复事实。"""
    ports, state = Ports(store=store), ManifestState(manifest, manifest_path)
    # 先写 manifest：即使凭证失效导致 issue 写回也失败，重启后仍不会重放 merge。
    apply(auth_error_durable(node_key), ports, state)
    try:
        apply(auth_error_remote(node_key, item.id, manifest_path), ports, state)
    except (AuthError, PlatformError):
        # 本地 manifest 已是 durable fail-closed 事实；无有效凭证时不能依赖
        # 远端 comment/status 写回才能阻止重复外部副作用。
        pass
    return "blocked"


# ── worker 证据过门后的 CI 门推进 ─────────────────────────────────────────────

def advance_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    code_host: CodeHost,
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
    manifest Node 不持计数。决策是 ``decide_ci`` 的纯函数,这里只观察并 ``apply``。
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

    ports, state = Ports(store=store, runtime=runtime), ManifestState(manifest, None)
    start = plan_ci_start(node_key=node_key, item_id=item_id, pr_url=pr_url)
    apply(start, ports, state)
    if start.outcome:
        return start.outcome

    result = _ci_result(code_host.check_pull_request(
        pr_url, ci["check_command"],
        max(1, int(ci.get("timeout_minutes", 30))) * 60,
    ))
    plan = plan_ci_result(
        node_key=node_key, item_id=item_id, worker=node.worker, passed=result.passed,
        label=result.label, summary=result.summary, bounces=item.bounces.ci,
        limit=retry_limits.get("ci", DEFAULT_RETRY["ci"]))
    apply(plan, ports, state)
    return plan.outcome


# ── P4.2 自动 merge 与冲突回退 ──────────────────────────────────────────────

def run_merge_delivery(
    config: dict,
    manifest: Manifest,
    node_key: str,
    store: object,
    runtime: AgentRuntime,
    code_host: CodeHost,
    retry_limits: dict,
    manifest_path: str | None = None,
) -> str:
    """PR 合并闭环：命令只请求合并，只有远端观察确认 MERGED 才能通过。

    观察 → 纯决策(``decide_merge``) → ``apply`` 一步；`REQUEST` 在 intent 落盘之后才发出唯一一次请求。
    """
    if not manifest_path:
        raise ValueError(
            "manifest_path is required for restart-safe merge delivery")

    node = manifest.nodes[node_key]
    item_id = node.work_item_id
    merge = get_merge_config(config)
    run = _MergeRun(node, node_key, item_id, store.get_work_item(item_id), manifest, manifest_path,
                    Ports(store=store, runtime=runtime, code_host=code_host), retry_limits)
    pr_url = ""
    if isinstance(run.item.artifacts, dict):
        pr_url = run.item.artifacts.get("pr_url") or run.item.artifacts.get("pr") or ""
    precheck = decide_merge_precheck(MergePrecheckObservation(
        request_state=node.merge_request_state, has_pull_request=bool(pr_url)))
    if precheck is MergePrecheck.RESUME_BOUNCE:
        return run.bounce(merge_bounce_attempt(node.merge_request_state))
    if precheck is MergePrecheck.BLOCK_NO_PULL_REQUEST:
        return run.apply(block_no_pull_request(node_key, item_id))
    if precheck is MergePrecheck.BLOCK_INVALID_MARKER:
        return run.apply(block_unproven(
            node_key, item_id, manifest_path,
            f"Merge request state {node.merge_request_state!r} is invalid."))

    try:
        observation = _observe_pull_request(code_host, pr_url)
    except AuthError:
        return run.auth_blocked()
    step = decide_merge_observed(MergeObserved(
        request_state=node.merge_request_state, pr_state=_observation_state(observation)))
    if step is MergeStep.SETTLE:
        return run.settle(observation)
    if step is not MergeStep.REQUEST:
        return run.apply(plan_observed_step(
            step, node_key=node_key, item_id=item_id, manifest_path=manifest_path))
    return _request_merge(run, merge, pr_url)


def _request_merge(run: "_MergeRun", merge: dict, pr_url: str) -> str:
    """Persist the intent first, send exactly one request, then observe and decide again."""
    intent = plan_request_intent(run.node_key, run.item_id)
    sent = RequestMerge(pr_url, merge["command"], max(1, int(merge.get("timeout_minutes", 30))) * 60)
    result = apply(list(intent.effects) + [sent], run.ports, run.state)[-1]
    timed_out = bool(getattr(result, "timed_out", False))
    run.apply(plan_request_sent(run.node_key, timed_out=timed_out))
    try:
        observation = _observe_pull_request(run.ports.code_host, pr_url)
    except AuthError:
        return run.auth_blocked()
    state = _observation_state(observation)
    after = decide_after_merge_request(AfterMergeRequest(
        timed_out=timed_out, succeeded=bool(result.succeeded), pr_state=state))
    if after is MergeAfterRequest.SETTLE:
        return run.settle(observation, command_output=result.output if state not in {
            PullRequestState.MERGED, PullRequestState.PENDING} else "")
    if after is MergeAfterRequest.BLOCK_TIMED_OUT_OPEN:
        return run.apply(plan_blocked_timed_out_open(run.node_key, run.item_id, run.manifest_path))
    if after is MergeAfterRequest.PENDING:
        return "pending"
    label = ui(
        "Merge command timed out" if result.timed_out else
        f"Merge command failed (exit code {result.exit_code})",
        "merge 命令超时" if result.timed_out else
        f"merge 命令失败(退出码 {result.exit_code})")
    return run.bounce_or_block(label=label, output=result.output)


@dataclass
class _MergeRun:
    """One node's merge delivery in flight: what the steps share, and the apply/settle/bounce helpers."""

    node: object
    node_key: str
    item_id: str
    item: object
    manifest: Manifest
    manifest_path: str
    ports: Ports
    retry_limits: dict

    @property
    def state(self) -> ManifestState:
        return ManifestState(self.manifest, self.manifest_path)

    def apply(self, plan) -> str | None:
        apply(plan, self.ports, self.state)
        return plan.outcome

    def auth_blocked(self) -> str:
        return block_merge_auth_error(
            self.node, self.item, self.ports.store, self.manifest, self.manifest_path, self.node_key)

    def settle(self, observation, *, command_output: str = "") -> str:
        plan, closed_unmerged = plan_settle(
            _observation_state(observation), merged_at=getattr(observation, "merged_at", None),
            node_key=self.node_key, item_id=self.item_id)
        self.apply(plan)
        if closed_unmerged:
            return self.bounce_or_block(
                label=ui("PR closed without merge", "PR 已关闭但未合入"), output=command_output)
        return plan.outcome

    def bounce_or_block(self, *, label: str, output: str) -> str:
        """merge 失败后的有界「回到 worker」回退(CI 路径的对称实现)：先把绝对回退轮次落盘。"""
        attempt = self.item.bounces.merge + 1
        self.apply(plan_bounce_pending(self.node_key, attempt))
        return self.bounce(attempt, label=label, output=output)

    def bounce(self, attempt: int, *, label: str | None = None, output: str = "") -> str:
        """幂等补齐 durable merge bounce；完成前始终保留 bounce_pending marker。"""
        consumed = consumed_bounces(
            self.manifest, self.node.id, self.item, "merge", absolute_count=attempt)
        return self.apply(plan_resume_bounce(
            node_key=self.node_key, item_id=self.item_id, worker=self.node.worker, attempt=attempt,
            current=self.item.bounces.merge, label=label, output=output,
            merge_limit=self.retry_limits.get("merge", DEFAULT_RETRY["merge"]), consumed_attempt=consumed))


def _observation_state(observation) -> PullRequestState:
    state = getattr(observation, "state", PullRequestState.UNKNOWN)
    if isinstance(state, PullRequestState):
        return state
    try:
        return PullRequestState(str(state).lower())
    except ValueError:
        return PullRequestState.UNKNOWN


def _observe_pull_request(code_host, pr_url: str) -> PullRequestObservation:
    """把临时远端读失败建模为 UNKNOWN，而不是业务 merge 失败。"""
    try:
        return code_host.observe_pull_request(pr_url)
    except PlatformError as exc:
        return PullRequestObservation(
            PullRequestState.UNKNOWN,
            detail=ui(
                f"temporary PR observation failure: {exc}",
                f"临时 PR 观察失败: {exc}"),
        )
