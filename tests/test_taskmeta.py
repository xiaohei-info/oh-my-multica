"""任务类型×阶段模型:kind/phase/bounces 写后读回 + 旧 issue 向后兼容。

验收(AITEAM-308):
- mock 写后读回 kind/phase/bounces
- multica(subprocess mock)写后读回 kind/phase/bounces
- 未带 kind 的旧 issue 读回缺省 develop(兼容)
"""
import json
import subprocess
from unittest.mock import patch

import pytest

from omac.core import taskmeta
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.engines.mock import MockStore
from omac.engines.multica import MulticaStore


def _mock_config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


# ==================== schema 单元 ====================

def test_parse_kind_defaults_develop_on_missing():
    assert taskmeta.parse_kind(None) == TaskKind.DEVELOP
    assert taskmeta.parse_kind("") == TaskKind.DEVELOP
    assert taskmeta.parse_kind("garbage") == TaskKind.DEVELOP  # 容错,不抛


def test_parse_phase_and_bounce_tolerant():
    assert taskmeta.parse_phase(None) == TaskPhase.AUTHORING
    assert taskmeta.parse_phase("review") == TaskPhase.REVIEW
    b = taskmeta.parse_bounces({"ci_bounce": "2", "review_bounce": "x", "merge_bounce": None})
    assert b == Bounces(ci=2, review=0, merge=0)


# ==================== Mock 写后读回 ====================

def test_mock_create_carries_kind():
    store = MockStore(_mock_config())
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.PLAN)
    got = store.get_work_item(item.id)
    assert got.kind == TaskKind.PLAN
    assert got.phase == TaskPhase.AUTHORING  # 缺省
    assert got.bounces == Bounces()
    assert got.deliverable is None


def test_mock_create_default_kind_is_develop():
    store = MockStore(_mock_config())
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    assert store.get_work_item(item.id).kind == TaskKind.DEVELOP


def test_mock_update_phase_and_bounces_roundtrip():
    store = MockStore(_mock_config())
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.DECOMPOSE)
    # pipeline 推进 phase + 累加回退计数(绝对值写回语义)
    store.update_work_item_metadata(item.id, phase=TaskPhase.REVIEW)
    store.update_work_item_metadata(item.id, ci_bounce=1, review_bounce=2, merge_bounce=3,
                                    deliverable="manifest: ...")
    got = store.get_work_item(item.id)
    assert got.phase == TaskPhase.REVIEW
    assert got.bounces == Bounces(ci=1, review=2, merge=3)
    assert got.deliverable == "manifest: ..."


def test_mock_bounce_increment_pattern():
    """pipeline 读当前→+1→写回的标准递增模式可用。"""
    store = MockStore(_mock_config())
    item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
    cur = store.get_work_item(item.id).bounces.review
    store.update_work_item_metadata(item.id, review_bounce=cur + 1)
    assert store.get_work_item(item.id).bounces.review == 1


# ==================== Multica(subprocess mock)写后读回 ====================

class _FakeMulticaProc:
    """模拟 multica CLI:维护一个内存 issue store,mirror metadata 语义。

    覆盖的子命令:issue create / get / metadata set / agent list。
    """

    def __init__(self):
        self.issues = {}          # id -> issue dict(含 metadata)
        self.metadata = {}        # id -> {key: value}
        self._next = 1
        self.calls = []           # 记录调用,供断言

    def run(self, cmd, capture_output=True, text=True):
        self.calls.append(cmd)
        # cmd = ["multica", --workspace-id?, ws?, <subcommand...>]
        # 跳过全局 flag 段,定位子命令
        args = cmd
        if args and args[0] == "multica":
            args = args[1:]
        if args and args[0] == "--workspace-id":
            args = args[2:]
        sub = args[0] if args else ""

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()

        if sub == "issue":
            action = args[1]
            if action == "create":
                issue_id = f"iss-{self._next}"
                self._next += 1
                issue = {"id": issue_id, "title": "", "description": "", "status": "todo", "metadata": {}}
                # 解析 --title / --status
                it = iter(args[2:])
                for tok in it:
                    if tok == "--title":
                        issue["title"] = next(it)
                    elif tok == "--status":
                        issue["status"] = next(it)
                    elif tok == "--description-file":
                        next(it)
                    elif tok == "--output":
                        next(it)
                self.issues[issue_id] = issue
                self.metadata[issue_id] = {}
                r.stdout = json.dumps(issue)
            elif action == "get":
                issue_id = args[2]
                issue = dict(self.issues[issue_id])
                issue["metadata"] = dict(self.metadata.get(issue_id, {}))
                r.stdout = json.dumps(issue)
            elif action == "metadata":
                maction = args[2]
                issue_id = args[3]
                if maction == "set":
                    # --key k --value v
                    kv = {}
                    it = iter(args[4:])
                    for tok in it:
                        if tok == "--key":
                            k = next(it)
                        elif tok == "--value":
                            v = next(it)
                            kv[k] = v
                    self.metadata.setdefault(issue_id, {}).update(kv)
            elif action == "comment":
                pass  # add_comment 走 --content-file,本测试不触发
        elif sub == "agent":
            r.stdout = json.dumps([])  # list_members 不在本测试路径

        return r


def _multica_store():
    cfg = EngineConfig(engine_type="multica", workspace_id="ws", extra={})
    store = MulticaStore(cfg)
    return store


def test_multica_create_and_get_kind_roundtrip():
    store = _multica_store()
    fake = _FakeMulticaProc()
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.PLAN)
        got = store.get_work_item(item.id)
    assert got.kind == TaskKind.PLAN
    assert got.phase == TaskPhase.AUTHORING  # 未写 → 缺省
    assert got.bounces == Bounces()          # 未写 → 缺省


def test_multica_update_phase_and_bounces_roundtrip():
    store = _multica_store()
    fake = _FakeMulticaProc()
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.ACCEPTANCE)
        store.update_work_item_metadata(
            item.id, phase=TaskPhase.REVIEW, ci_bounce=1, review_bounce=2,
            merge_bounce=3, deliverable="acceptance: ...")
        got = store.get_work_item(item.id)
    assert got.kind == TaskKind.ACCEPTANCE
    assert got.phase == TaskPhase.REVIEW
    assert got.bounces == Bounces(ci=1, review=2, merge=3)
    assert got.deliverable == "acceptance: ..."
    # metadata 真的落到了 issue 上
    md = fake.metadata[item.id]
    assert md["kind"] == "acceptance"
    assert md["phase"] == "review"
    assert md["ci_bounce"] == "1"
    assert md["review_bounce"] == "2"
    assert md["merge_bounce"] == "3"
    assert md["deliverable"] == "acceptance: ..."


def test_multica_old_issue_without_kind_reads_develop():
    """旧 issue metadata 无 kind 字段 → 读回缺省 develop(向后兼容)。"""
    store = _multica_store()
    fake = _FakeMulticaProc()
    with patch("subprocess.run", side_effect=fake.run):
        # 手动塞一条无 kind 的旧 issue
        old_id = "iss-legacy"
        fake.issues[old_id] = {
            "id": old_id, "title": "old", "description": "",
            "status": "in_progress", "metadata": {"dag_key": "x"},
        }
        fake.metadata[old_id] = {"dag_key": "x"}
        got = store.get_work_item(old_id)
    assert got.kind == TaskKind.DEVELOP
    assert got.phase == TaskPhase.AUTHORING
    assert got.bounces == Bounces()


def test_multica_default_kind_when_create_omitted():
    store = _multica_store()
    fake = _FakeMulticaProc()
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice")
        got = store.get_work_item(item.id)
    assert got.kind == TaskKind.DEVELOP
    assert fake.metadata[item.id]["kind"] == "develop"
