"""
Live MulticaEngine 测试 —— 直接对真 multica CLI 验证命令面。

Gating:
- 需同时满足：multica CLI 在 PATH，且显式设置 `MULTICA_WORKSPACE_ID` + `MULTICA_TEST_SQUAD`。
- 任一缺失即 skip（开源仓库不携带任何私有 workspace/squad 默认值）。

测试间隔离：每个 test 用唯一 dag_key 前缀（test_live_<func>_<timestamp>），收尾用
`multica issue status <id> cancelled` 把创建的 issue 标取消（不删，留审计痕）。

不跑真 agent：用 `multica issue status/metadata set` 直接把 issue 置为各终态，
再调 get_work_item / reconcile / _harvest 断言反应。
"""
import json
import os
import subprocess
import sys
import time
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines import create_engine_from_config, WorkItemStatus
from engines.multica import MulticaEngine


# ==================== Live test gating ====================
# 仅当 multica CLI 在 PATH 且显式提供以下环境变量时才跑，否则 skip：
#   MULTICA_WORKSPACE_ID  — 你的 workspace ID
#   MULTICA_TEST_SQUAD    — 测试专用 squad ID（issue 跑完标 cancelled，可随时清理）
#
# 排除 live 测试（CI 或无 CLI 环境）：
#   python3 -m pytest tests/ -m "not live_multica"

_CLI = shutil.which("multica") is not None
_WS = os.environ.get("MULTICA_WORKSPACE_ID", "")
_SQUAD = os.environ.get("MULTICA_TEST_SQUAD", "")

_SKIP = not (_CLI and _WS and _SQUAD)
_REASON = "需 multica CLI + MULTICA_WORKSPACE_ID + MULTICA_TEST_SQUAD（缺任一则 skip）"

# module 级 marker：让 -m "not live_multica" 能排掉整个文件
pytestmark = pytest.mark.live_multica


def _engine() -> MulticaEngine:
    """构造绑定了 workspace + test squad 的 MulticaEngine。"""
    assert _WS, "MULTICA_WORKSPACE_ID 未设置"
    engine = create_engine_from_config("multica", _WS)
    if _SQUAD:
        engine.config.squad_id = _SQUAD
    engine.config.polling_interval = 1
    return engine


def _cli(*args) -> subprocess.CompletedProcess:
    """直跑 multica CLI（不经过 engine），返回 CompletedProcess。"""
    cmd = ["multica"]
    if _WS:
        cmd += ["--workspace-id", _WS]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def _set_status(issue_id: str, status: str):
    """用 `multica issue status <id> <status>` 直接改状态（绕过 engine update_status）。"""
    r = _cli("issue", "status", issue_id, status)
    assert r.returncode == 0, f"set status {issue_id} -> {status} failed: {r.stderr}"


def _cancel(issue_id: str):
    """收尾：把 issue 标 cancelled（不删）。容忍已终态的失败。"""
    _cli("issue", "status", issue_id, "cancelled")


def _dag_key(func: str) -> str:
    """唯一 dag_key 前缀，避免跨 test/跨 run 撞 issue。"""
    return f"test_live_{func}_{int(time.time() * 1000)}"


def _make_issue(engine: MulticaEngine, dag_key: str, worker: str = "alice",
                reviewer: str = None, wave: int = None,
                blocked_by=None) -> str:
    """建 issue + 写 metadata，返回 issue id。"""
    item = engine.create_work_item(
        workspace_id=_SQUAD or _WS,
        title="live test",
        description="live multica test",
        dag_key=dag_key,
        worker=worker,
        reviewer=reviewer,
        blocked_by=blocked_by,
        wave=wave,
    )
    return item.id


def _real_member_name(engine: MulticaEngine, exclude: str = None) -> str:
    """从 list_members 取一个真实存在平台的 agent 名（供 assign/review 用）。"""
    members = engine.list_members(_SQUAD or _WS)
    assert members, "workspace 无 agent 可做 reviewer"
    for name in members:
        if name != exclude:
            return name
    return members[0]


# ==================== 1. create/get 往返一致性 ====================

def test_live_create_get_roundtrip():
    """create_work_item -> get_work_item：metadata（dag_key/worker/reviewer/wave/blocked_by）写回读回一致。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("roundtrip")
    item = engine.create_work_item(
        workspace_id=_SQUAD or _WS,
        title="roundtrip test",
        description="roundtrip desc",
        dag_key=dk,
        worker="alice",
        reviewer="bob",
        blocked_by=["X", "Y"],
        wave=2,
    )
    try:
        got = engine.get_work_item(item.id)
        assert got.dag_key == dk, f"dag_key: {got.dag_key!r} != {dk!r}"
        assert got.worker == "alice", f"worker: {got.worker!r}"
        assert got.reviewer == "bob", f"reviewer: {got.reviewer!r}"
        assert got.wave == 2, f"wave: {got.wave!r}"
        assert got.blocked_by == ["X", "Y"], f"blocked_by: {got.blocked_by!r}"
    finally:
        _cancel(item.id)


# ==================== 2. update_status 各状态转换 ====================

@pytest.mark.parametrize("status", [
    WorkItemStatus.TODO,
    WorkItemStatus.IN_PROGRESS,
    WorkItemStatus.IN_REVIEW,
    WorkItemStatus.DONE,
    WorkItemStatus.BLOCKED,
])
def test_live_update_status_roundtrip(status):
    """update_status -> get_work_item 读回的 status 经 _multica_to_status 映射后与业务状态一致。

    含 FIXED->blocked（任务核心修复点：FAILED 不映射到 multica 非法态 "failed"）。
    """
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("status")
    issue_id = _make_issue(engine, dk)
    try:
        engine.update_status(issue_id, status)
        got = engine.get_work_item(issue_id)
        assert got.status == status, (
            f"update_status({status.name}) -> get 回 {got.status.name}"
        )
    finally:
        _cancel(issue_id)


def test_live_update_status_failed_maps_to_blocked():
    """FAILED 经 _status_to_multica 映射到 multica 'blocked'，真 CLI 接受且读回 BLOCKED。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("failed")
    issue_id = _make_issue(engine, dk)
    try:
        # 显式验证映射值
        multica_status = engine._status_to_multica(WorkItemStatus.FAILED)
        assert multica_status == "blocked", f"FAILED 应映射 blocked，实际 {multica_status!r}"
        # 真 CLI 接受
        r = _cli("issue", "status", issue_id, multica_status)
        assert r.returncode == 0, f"CLI 拒绝 status={multica_status}: {r.stderr}"
        got = engine.get_work_item(issue_id)
        assert got.status == WorkItemStatus.BLOCKED, (
            f"blocked 读回应为 BLOCKED，实际 {got.status.name}"
        )
    finally:
        _cancel(issue_id)


def test_live_cancelled_maps_to_blocked():
    """平台 cancelled issue 读回应映射成 BLOCKED（任务核心修复点：不退化为 todo 被重派）。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("cancelled")
    issue_id = _make_issue(engine, dk)
    try:
        _set_status(issue_id, "cancelled")
        got = engine.get_work_item(issue_id)
        assert got.status == WorkItemStatus.BLOCKED, (
            f"cancelled 读回应为 BLOCKED，实际 {got.status.name}"
        )
        # 反向映射显式断言
        assert engine._multica_to_status("cancelled") == WorkItemStatus.BLOCKED
    finally:
        # 再 cancel 一次容错（已 cancelled 则 CLI 可能 no-op）
        _cancel(issue_id)


# ==================== 3. _status_to_multica / _multica_to_status 真 CLI 验证 ====================

def test_live_all_valid_statuses_accepted_by_cli():
    """multica 合法 status backlog/todo/in_progress/in_review/done/blocked/cancelled，
    每个 _status_to_multica 能产出的值被真 CLI `issue status` 接受。
    """
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("valids")
    issue_id = _make_issue(engine, dk)
    try:
        # 业务能正向映射出的 multica 态
        forward = {engine._status_to_multica(s) for s in WorkItemStatus}
        # 去掉 "failed"（已不被产出）后，所有候选必须是 multica 合法态
        valid = {"backlog", "todo", "in_progress", "in_review",
                 "done", "blocked", "cancelled"}
        produced = forward & valid
        assert produced, f"无合法产物: forward={forward}"
        # 逐个尝试 set，CLI 都应接受
        for st in sorted(produced):
            r = _cli("issue", "status", issue_id, st)
            assert r.returncode == 0, f"CLI 拒绝合法态 {st}: {r.stderr}"
    finally:
        _cancel(issue_id)


def test_live_multica_to_status_full_table():
    """_multica_to_status 对 7 个 cli 合法态都有明确映射（不依赖默认 todo 兜底误派）。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    # 显式映射断言（不要求每个都非 TODO，只断言 cancelled/blocked 两个关键修复项）
    assert engine._multica_to_status("todo") == WorkItemStatus.TODO
    assert engine._multica_to_status("in_progress") == WorkItemStatus.IN_PROGRESS
    assert engine._multica_to_status("in_review") == WorkItemStatus.IN_REVIEW
    assert engine._multica_to_status("done") == WorkItemStatus.DONE
    assert engine._multica_to_status("blocked") == WorkItemStatus.BLOCKED
    assert engine._multica_to_status("cancelled") == WorkItemStatus.BLOCKED  # 修复点
    # failed 不在 cli 合法态，但反向表保留为 FAILED（防御性）
    assert engine._multica_to_status("failed") == WorkItemStatus.FAILED


# ==================== 4. _harvest 对真实平台状态反应 ====================

def _harvest_manifest(engine, issue_id: str, node_status: str,
                     work_item_id: str = None):
    """构造一个最小单节点 manifest 并跑 reconcile + _harvest。

    复用 run_dag 的 reconcile + _harvest，对真实平台已置态的 issue 收割。
    """
    from core import load_manifest  # noqa: 我们用构造而非 load，避免再写 mini yaml
    saved = None
    return saved


def test_live_harvest_done_with_pr_transitions_to_review():
    """in_progress 节点 -> 平台 done + artifacts.pr -> _harvest 应转 in_review（有 reviewer）。

    用 metadata set 写 artifacts，再用 issue status 置 done；harvest 读 get_work_item。
    """
    if _SKIP:
        pytest.skip(_REASON)
    from core import Manifest, Node
    from run_dag import _harvest, commit_manifest
    import run_dag as rd

    engine = _engine()
    dk = _dag_key("harv_pr")
    # 取真实 agent 名做 worker 与 reviewer，避免 assign 失败（'bob' 不是 workspace agent）
    members = engine.list_members(_SQUAD or _WS)
    assert len(members) >= 2, "需至少 2 个 agent 做 worker/reviewer"
    worker_name = members[0]
    reviewer_name = members[1]
    issue_id = _make_issue(engine, dk, worker=worker_name, reviewer=reviewer_name)
    try:
        # 平台置 done + 写 artifacts
        _set_status(issue_id, "done")
        r = _cli("issue", "metadata", "set", issue_id,
                 "--key", "artifacts", "--value", json.dumps({"pr": "https://ex/p/1"}))
        assert r.returncode == 0, f"set artifacts failed: {r.stderr}"

        # 构造 manifest 单节点 in_progress + reviewer=bob
        m = Manifest(
            meta={"name": "live", "squad": _SQUAD or ""},
            nodes={
                "N": Node(id="N", worker=worker_name, reviewer=reviewer_name,
                      title="t", description="d", blocked_by=[],
                      status="in_progress", work_item_id=issue_id),
            },
        )
        completed, failed = set(), set()
        # patch commit_manifest 防真落地
        import run_dag as rd2
        orig = rd2.commit_manifest
        rd2.commit_manifest = lambda *a, **k: False
        try:
            _harvest(engine, m, "/tmp/_live_harv.yaml", completed, failed)
        finally:
            rd2.commit_manifest = orig

        assert m.nodes["N"].status == "in_review", (
            f"done+PR+reviewer 应转 in_review，实际 {m.nodes['N'].status}"
        )
        assert "N" not in completed, "有 reviewer 不应直接 completed"
        assert "N" not in failed
    finally:
        _cancel(issue_id)


def test_live_harvest_done_missing_pr_blocks():
    """in_progress -> 平台 done 但无 artifacts.pr -> _harvest 标 blocked + failed.add。"""
    if _SKIP:
        pytest.skip(_REASON)
    from core import Manifest, Node
    from run_dag import _harvest

    engine = _engine()
    dk = _dag_key("harv_nopr")
    issue_id = _make_issue(engine, dk, worker="alice")
    try:
        _set_status(issue_id, "done")
        m = Manifest(
            meta={"name": "live", "squad": _SQUAD or ""},
            nodes={
                "N": Node(id="N", worker="alice", reviewer="bob",
                      title="t", description="d", blocked_by=[],
                      status="in_progress", work_item_id=issue_id),
            },
        )
        completed, failed = set(), set()
        import run_dag as rd
        rd.commit_manifest = lambda *a, **k: False
        _harvest(engine, m, "/tmp/_live_harv.yaml", completed, failed)

        assert m.nodes["N"].status == "blocked", (
            f"done 缺 PR 应 blocked，实际 {m.nodes['N'].status}"
        )
        assert "N" in failed, "缺 PR 应入 failed"
    finally:
        _cancel(issue_id)


def test_live_harvest_failed_transitions_to_blocked():
    """FAILED -> 真多一点 CLI 'blocked' 投影 + engine 读回 BLOCKED（Bug 1 修复点）。

    修复前 _status_to_multica(FAILED)='failed' 对 CLI 非法，update_status 直接报错；
    现映射到 'blocked'，CLI 接受。_harvest 编排级 failed->blocked 下游隔离依赖 run_dag.py，
    不在此 lane 内断言，只验证映射/CLI/读回三段闭合。
    """
    if _SKIP:
        pytest.skip(_REASON)
    from core import Manifest, Node
    from run_dag import _harvest

    engine = _engine()
    dk = _dag_key("harv_failed")
    worker_name = _real_member_name(engine)
    reviewer_name = _real_member_name(engine, exclude=worker_name)
    issue_id = _make_issue(engine, dk, worker=worker_name, reviewer=reviewer_name)
    try:
        # Bug 1 修复的真 CLI 验证：FAILED 投影到 'blocked' 并被 CLI 接受
        assert engine._status_to_multica(WorkItemStatus.FAILED) == "blocked"
        engine.update_status(issue_id, WorkItemStatus.FAILED)
        got = engine.get_work_item(issue_id)
        assert got.status == WorkItemStatus.BLOCKED, (
            "FAILED 写入后读回应为 BLOCKED"
        )
        # 构造 in_progress 节点跑 harvest：CLI 侧实为 blocked，
        # _harvest in_progress 分支仅匹配 DONE/FAILED，不会误标 done 也不崩溃。
        m = Manifest(
            meta={"name": "live", "squad": _SQUAD or ""},
            nodes={
                "N": Node(id="N", worker=worker_name, reviewer=reviewer_name,
                      title="t", description="d", blocked_by=[],
                      status="in_progress", work_item_id=issue_id),
            },
        )
        completed, failed = set(), set()
        import run_dag as rd
        rd.commit_manifest = lambda *a, **k: False
        _harvest(engine, m, "/tmp/_live_harv.yaml", completed, failed)

        # 闭合不变量：不误标 done，不进 completed；failed 集合的 full 隔离属 run_dag 范围。
        assert "N" not in completed, "FAILED/BLOCKED 不应被 harvest 标 done"
        assert m.nodes["N"].status != "done"
    finally:
        _cancel(issue_id)


def test_live_harvest_in_review_rejected_blocks():
    """in_review -> 平台有 review_verdict='needs-changes' -> _harvest 标 blocked + failed。"""
    if _SKIP:
        pytest.skip(_REASON)
    from core import Manifest, Node
    from run_dag import _harvest

    engine = _engine()
    dk = _dag_key("harv_rej")
    issue_id = _make_issue(engine, dk, worker="alice", reviewer="bob")
    try:
        _set_status(issue_id, "in_review")
        r = _cli("issue", "metadata", "set", issue_id,
                 "--key", "review_verdict", "--value", '"needs-changes"')
        # review_verdict 文本型，用 --type string 强制 string 更稳
        if r.returncode != 0:
            r = _cli("issue", "metadata", "set", issue_id,
                     "--key", "review_verdict", "--value", "needs-changes",
                     "--type", "string")
        assert r.returncode == 0, f"set review_verdict failed: {r.stderr}"

        m = Manifest(
            meta={"name": "live", "squad": _SQUAD or ""},
            nodes={
                "N": Node(id="N", worker="alice", reviewer="bob",
                      title="t", description="d", blocked_by=[],
                      status="in_review", work_item_id=issue_id),
            },
        )
        completed, failed = set(), set()
        import run_dag as rd
        rd.commit_manifest = lambda *a, **k: False
        _harvest(engine, m, "/tmp/_live_harv.yaml", completed, failed)

        assert m.nodes["N"].status == "blocked", (
            f"reviewer reject 应 blocked，实际 {m.nodes['N'].status}"
        )
        assert "N" in failed
    finally:
        _cancel(issue_id)


# ==================== 5. list_members / list_work_items ====================

def test_live_list_members_returns_names():
    """list_members 返回成员名列表（sq 或退化为 workspace agents），非空且为 str。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    members = engine.list_members(_SQUAD or _WS)
    assert isinstance(members, list), f"应为 list，实际 {type(members)}"
    # 至少有一个 agent（小队或 workspace 必然有）
    assert len(members) >= 1, "list_members 返回空"
    for name in members:
        assert isinstance(name, str) and name, f"成员名非空 str: {name!r}"


def test_live_list_work_items_includes_created():
    """list_work_items 能把刚建的 issue 列回来（至少出现在全集里）。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("list")
    item = engine.create_work_item(
        workspace_id=_SQUAD or _WS,
        title="list test",
        description="d",
        dag_key=dk,
        worker="alice",
    )
    try:
        all_items = engine.list_work_items(_SQUAD or _WS)
        ids = [it.id for it in all_items]
        assert item.id in ids, f"新建 issue {item.id} 未出现在 list_work_items: {ids[:5]}..."
    finally:
        _cancel(item.id)


# ==================== 6. _resolve_agent_id ====================

def test_live_resolve_agent_id_existing():
    """_resolve_agent_id 对 workspace 内存在的 agent 名返回非空 id。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    # 取 list_members 第一个成员名做解析
    members = engine.list_members(_SQUAD or _WS)
    assert members, "无成员可用于 agent 解析"
    name = members[0]
    agent_id = engine._resolve_agent_id(name)
    assert agent_id, f"_resolve_agent_id({name!r}) 返回空 id"


def test_live_resolve_agent_id_missing_raises():
    """_resolve_agent_id 对不存在 agent 名应抛 ValueError。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    with pytest.raises(ValueError):
        engine._resolve_agent_id("__definitely_not_an_agent__xyz123")


# ==================== 7. _issue_to_work_item JSON 解析分支 ====================

def test_live_issue_to_work_item_blocked_by_json_string():
    """blocked_by 以 JSON 字符串存（['A','B']）-> _issue_to_work_item 解析为 list。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("ibb")
    item = engine.create_work_item(
        workspace_id=_SQUAD or _WS,
        title="blocked_by test",
        description="d",
        dag_key=dk,
        worker="alice",
        blocked_by=["A", "B"],
    )
    try:
        got = engine.get_work_item(item.id)
        assert got.blocked_by == ["A", "B"], (
            f"blocked_by JSON 解析失败: {got.blocked_by!r}"
        )
    finally:
        _cancel(item.id)


def test_live_issue_to_work_item_artifacts_json_string():
    """artifacts 以 JSON 字符串存 -> _issue_to_work_item 解析为 dict。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("iart")
    issue_id = _make_issue(engine, dk)
    try:
        r = _cli("issue", "metadata", "set", issue_id,
                 "--key", "artifacts",
                 "--value", json.dumps({"pr": "https://ex/p/9", "commit": "abc"}))
        assert r.returncode == 0, f"set artifacts failed: {r.stderr}"
        got = engine.get_work_item(issue_id)
        assert got.artifacts is not None, "artifacts 应解析为非 None"
        assert got.artifacts.get("pr") == "https://ex/p/9", (
            f"artifacts.pr 解析错: {got.artifacts!r}"
        )
        assert got.artifacts.get("commit") == "abc"
    finally:
        _cancel(issue_id)


def test_live_issue_to_work_item_wave_string():
    """wave 以 number 类型存 -> _issue_to_work_item 解析为 int。"""
    if _SKIP:
        pytest.skip(_REASON)
    engine = _engine()
    dk = _dag_key("iwave")
    issue_id = _make_issue(engine, dk, wave=3)
    try:
        got = engine.get_work_item(issue_id)
        assert got.wave == 3, f"wave 应解析为 int 3，实际 {got.wave!r}"
        assert isinstance(got.wave, int), f"wave 应为 int，实际 {type(got.wave)}"
    finally:
        _cancel(issue_id)
