"""任务类型×阶段模型:kind/phase/bounces 写后读回 + 旧 issue 向后兼容。

验收(AITEAM-308):
- mock 写后读回 kind/phase/bounces
- multica(subprocess mock)写后读回 kind/phase/bounces
- 未带 kind 的旧 issue 读回缺省 develop(兼容)
"""
import json
import pathlib
import subprocess
from unittest.mock import patch

import pytest

from omac.core import taskmeta
from omac.core.taskmeta import Bounces, TaskKind, TaskPhase
from omac.engines.metadata_policy import assert_metadata_write_allowed
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


def test_make_dag_key_is_single_generation_rule():
    assert taskmeta.make_dag_key(TaskKind.PLAN, scope="Demo Feature") == "plan-demo-feature"
    assert taskmeta.make_dag_key(TaskKind.FINAL_ACCEPTANCE, scope="r1") == "final-acceptance-r1"
    assert taskmeta.make_dag_key(TaskKind.PLAN, title="中文计划").startswith("plan-task")
    a = taskmeta.make_dag_key(TaskKind.PLAN, title="Same", unique=True)
    b = taskmeta.make_dag_key(TaskKind.PLAN, title="Same", unique=True)
    assert a.startswith("plan-same-")
    assert b.startswith("plan-same-")
    assert a != b
    assert taskmeta.make_plan_id().startswith("p-")


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
        self.comments = {}        # id -> [comment dict]
        self._next = 1
        self._next_attachment = 1
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
                            if isinstance(v, str) and v[:1] in ("{", "["):
                                try:
                                    v = json.loads(v)
                                except json.JSONDecodeError:
                                    pass
                            kv[k] = v
                    self.metadata.setdefault(issue_id, {}).update(kv)
            elif action == "comment":
                caction = args[2]
                if caction == "add":
                    issue_id = args[3]
                    content = ""
                    attachments = []
                    it = iter(args[4:])
                    for tok in it:
                        if tok == "--content-file":
                            content = pathlib.Path(next(it)).read_text(encoding="utf-8")
                        elif tok == "--attachment":
                            path = next(it)
                            attachment_path = pathlib.Path(path)
                            attachment_id = f"att-{self._next_attachment}"
                            self._next_attachment += 1
                            attachments.append({
                                "id": attachment_id,
                                "filename": attachment_path.name,
                                "content": attachment_path.read_text(encoding="utf-8"),
                            })
                        elif tok == "--output":
                            next(it)
                    comment = {
                        "id": f"c-{len(self.comments.get(issue_id, [])) + 1}",
                        "content": content,
                        "attachments": attachments,
                    }
                    self.comments.setdefault(issue_id, []).append(comment)
                    r.stdout = json.dumps(comment)
                elif caction == "list":
                    issue_id = args[3]
                    r.stdout = json.dumps(self.comments.get(issue_id, []))
        elif sub == "attachment":
            action = args[1]
            if action == "download":
                attachment_id = args[2]
                output_dir = pathlib.Path(".")
                it = iter(args[3:])
                for tok in it:
                    if tok in ("-o", "--output-dir"):
                        output_dir = pathlib.Path(next(it))
                for comments in self.comments.values():
                    for comment in comments:
                        for attachment in comment.get("attachments", []):
                            if attachment.get("id") == attachment_id:
                                output_dir.mkdir(parents=True, exist_ok=True)
                                path = output_dir / attachment["filename"]
                                path.write_text(attachment["content"], encoding="utf-8")
                                r.stdout = str(path)
                                return r
                r.returncode = 1
                r.stderr = f"attachment not found: {attachment_id}"
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
    assert "deliverable" not in md
    ref = md["deliverable_ref"]
    assert ref["sha256"]
    assert ref["comment_id"] == "c-1"
    assert ref["attachment_id"] == "att-1"
    assert fake.comments[item.id][0]["attachments"]
    assert "<!-- omac-deliverable-begin -->" not in fake.comments[item.id][0]["content"]


def test_multica_review_report_uses_ref_not_nested_json_string():
    """review_report 只经附件 ref 交接,不再写入 metadata 正文。"""
    store = _multica_store()
    fake = _FakeMulticaProc()
    report = {"review_goals": ["g"], "blockers": [], "nits": ["nit"]}
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.PLAN)
        store.update_work_item_metadata(
            item.id,
            review_report=report,
            review_report_source="review_goals:\n- g\nblockers: []\nnits:\n- nit\n",
        )
        got = store.get_work_item(item.id)
    assert got.review_report == report
    md = fake.metadata[item.id]
    assert "review_report" not in md
    assert md["review_report_ref"]["sha256"]
    assert md["review_report_ref"]["attachment_id"] == "att-1"
    assert fake.comments[item.id][0]["attachments"][0]["filename"].startswith("omac-review-report-")


def test_multica_verification_file_is_attached_and_referenced():
    """verification 只经附件 ref 交接,不再写入 metadata 正文。"""
    store = _multica_store()
    fake = _FakeMulticaProc()
    verification = {"commands": [{"cmd": "pytest", "exit_code": 0}]}
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.DEVELOP)
        store.update_work_item_metadata(
            item.id,
            verification=verification,
            verification_source="commands:\n- cmd: pytest\n  exit_code: 0\n",
        )
        got = store.get_work_item(item.id)
    assert got.verification == verification
    md = fake.metadata[item.id]
    assert "verification" not in md
    assert md["verification_ref"]["sha256"]
    assert md["verification_ref"]["attachment_id"] == "att-1"
    assert fake.comments[item.id][0]["attachments"][0]["filename"].startswith("omac-verification-")


def test_multica_payload_refs_parse_yaml_and_json():
    """新 issue 优先从 *_ref 附件读回 YAML/JSON;旧 inline 只作为 fallback。"""
    store = _multica_store()
    fake = _FakeMulticaProc()
    with patch("subprocess.run", side_effect=fake.run):
        item = store.create_work_item("ws", "t", "d", dag_key="a", worker="alice", kind=TaskKind.DEVELOP)
        store.update_work_item_metadata(
            item.id,
            verification={"inline": "legacy"},
            verification_source="commands:\n- cmd: pytest\n  exit_code: 0\n",
            review_report={"inline": "legacy"},
            review_report_source=json.dumps({"review_goals": ["json"], "blockers": [], "nits": []}),
        )
        md = fake.metadata[item.id]
        md["verification"] = {"inline": "legacy"}
        md["review_report"] = {"inline": "legacy"}
        got = store.get_work_item(item.id)

    assert got.verification == {"commands": [{"cmd": "pytest", "exit_code": 0}]}
    assert got.review_report == {"review_goals": ["json"], "blockers": [], "nits": []}


def test_metadata_policy_rejects_inline_prose_fields():
    with pytest.raises(ValueError):
        assert_metadata_write_allowed("review_report", {"summary": "long reviewer prose"})
    with pytest.raises(ValueError):
        assert_metadata_write_allowed("verification", {"commands": []})
    with pytest.raises(ValueError):
        assert_metadata_write_allowed(
            "decision_required",
            {"verdict": "pass-with-nits", "nits": [{"issue": "rename", "fix": "use x"}]},
        )


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
