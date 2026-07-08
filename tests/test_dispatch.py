"""P2.5 派发模板(dispatch.render_issue_body / render_review_rollout_comment)
与 loop 集成。

验收标准:
- render_issue_body 三段齐全 + 命令可直接复制执行(嵌入真实 issue id)
- issue 类型→角色→guide topic 同源映射;字段缺失有人可读占位;
  可选字段(pr_base/reviewer/non_goals)缺省时相应段落省略
- render_review_rollout_comment 覆盖 pass / pass-with-nits / reject:
  阶段说明 + 定位 + reject 时含 review_goals/blockers/nits
- mock e2e:关闭 auto-complete,手动扮演 worker(review 提交)→ reviewer(pass)
  驱动一个节点走完 develop→review→done,证明零 skill 闭环在接口层成立
"""
import os
import tempfile

import pytest

from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.core.taskmeta import TaskKind
from omac.engines import create_engine
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.pipeline.dispatch import (
    KIND_GUIDE,
    KIND_LABEL,
    KIND_ROLE,
    render_issue_body,
    render_review_rollout_comment,
)
from omac.pipeline.loop import tick


# ==================== helpers ====================

def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="ws", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


def _full_contract():
    return Contract(
        objective="实现登录",
        source_of_truth=["docs/login.md"],
        acceptance=["移动端可登录", "token 10 分钟过期"],
        non_goals=["不接第三方 OAuth", "不改 DB schema"],
        verification_commands=["pytest tests/login -q"],
        integration_gates=[{
            "name": "login-gate",
            "layer": "L1",
            "delivery_goal": "移动端登录端到端",
            "source_of_truth": ["docs/login.md"],
            "covers": ["route"],
            "acceptance_refs": ["移动端可登录"],
            "commands": ["pytest tests/int_login"],
            "required_metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
        }],
        pr_base="feature/v1",
        coverage_gate=90,
    )


def _tmp_manifest_path(manifest):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="omac_test_")
    os.close(fd)
    save_manifest(manifest, path)
    return path


# ==================== render_issue_body 快照 ====================

class TestRenderIssueBody:

    def test_three_paragraphs_present(self):
        n = Node(id="a", worker="alice", title="Add login", reviewer="bob",
                 contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ISSUE-9")
        # 三条命令/briefing/硬约束
        assert "ISSUE-9" in body
        assert "omac work show ISSUE-9" in body
        assert "omac work submit ISSUE-9" in body
        assert "简报" in body
        assert "硬约束" in body
        # bootstrap 指引 guide(worker)
        assert "omac guide worker" in body

    def test_bootstrap_commands_are_copy_pasteable(self):
        """work show/submit 命令里嵌入真实 id(不含通用占位),可直接复制执行。"""
        n = Node(id="n", worker="alice", contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "REAL-100")
        assert "omac work show REAL-100" in body
        assert "omac work submit REAL-100" in body
        assert "<id>" not in body and "<issue>" not in body

    def test_bootstrap_can_include_engine_env_for_no_checkout_runtime(self):
        """隔离 runtime 尚未 checkout repo 时也能直接跑 omac:命令内带 engine/workspace/project。"""
        n = Node(id="n", worker="alice", contract=_full_contract())
        env = {
            "OMAC_ENGINE": "multica",
            "OMAC_WORKSPACE_ID": "ws-1",
            "OMAC_PROJECT_ID": "proj-1",
        }
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "REAL-100", engine_env=env)
        prefix = "OMAC_ENGINE=multica OMAC_WORKSPACE_ID=ws-1 OMAC_PROJECT_ID=proj-1"
        assert f"{prefix} omac work show REAL-100" in body
        assert f"{prefix} omac work submit REAL-100" in body

    def test_kind_role_and_guide_mapping(self):
        """每种 issue 类型映射到对应角色与 guide topic(同源、不复制)。"""
        n = Node(id="n", worker="alice", title="t",
                 contract=Contract(objective="o", acceptance=["a"]))
        # 全部五种 kind 都有确定映射
        for kind in TaskKind:
            body = render_issue_body(n, n.contract, kind, "ID")
            role = KIND_ROLE[kind]
            topic = KIND_GUIDE[kind]
            label = KIND_LABEL[kind]
            assert role in body, f"{kind} 缺角色 {role}"
            assert f"omac guide {topic}" in body, f"{kind} 缺 guide {topic}"
            assert label in body, f"{kind} 缺标签 {label}"

    def test_missing_contract_fields_omit_briefing_lines(self):
        """contract 字段缺失时,简报省略该行,绝不渲染指向虚空的「见 contract.X」死占位。

        plan/acceptance/decompose 天生无 contract(payload 只有 source_of_truth),
        「见 contract.objective」是误导——真实需求在「上游产物」段。字段不存在就不印那行。"""
        n = Node(id="n", worker="alice", title="t")  # contract=None
        body = render_issue_body(n, None, TaskKind.DEVELOP, "ID")
        # 不得出现任何指向不存在 contract 的死占位
        assert "见 contract." not in body
        # 缺字段的行整条省略(只剩 title)
        assert "- objective:" not in body
        assert "- source_of_truth:" not in body
        assert "- acceptance:" not in body
        # title 与三段骨架仍在
        assert "- title: t" in body
        assert "简报" in body and "硬约束" in body and "omac work show ID" in body

    def test_plan_task_briefing_has_no_dead_contract_placeholder(self):
        """plan 任务(contract=None)的简报不得出现「见 contract.X」——它引用的东西根本不存在。"""
        n = Node(id="n", worker="alice", title="贪吃蛇手游 计划")
        body = render_issue_body(n, None, TaskKind.PLAN, "ID")
        assert "见 contract" not in body
        assert "- title: 贪吃蛇手游 计划" in body

    def test_bootstrap_orders_guide_first_no_contract_lie(self):
        """点2:bootstrap 把 guide 抬为第 1 必看(先懂流程再取实例),
        且不谎称「你的 contract 全量」——plan 天生无 contract。"""
        n = Node(id="n", worker="alice", title="计划")
        body = render_issue_body(n, None, TaskKind.PLAN, "ID")
        # guide 指引出现在 work show 之前
        assert body.index("omac guide workflow") < body.index("omac work show ID")
        # 三条入口命令仍在(重排,不删)
        assert "omac work show ID" in body
        assert "omac work submit ID" in body
        # 删掉「contract 全量」这句对 plan 而言的谎
        assert "contract 全量" not in body

    def test_contract_summary_none_returns_fallback(self):
        """_contract_summary 在 contract=None 时应直接返回 fallback,作为占位的根。"""
        from omac.pipeline.dispatch import _contract_summary
        assert _contract_summary(None, "objective", "fallback-obj") == "fallback-obj"
        assert _contract_summary(None, "acceptance", ["x"]) == ["x"]

    def test_optional_fields_omit_when_absent(self):
        """pr_base / reviewer / non_goals 缺省时对应段落不出现。"""
        c = Contract(objective="o", acceptance=["a"])  # 无 pr_base/non_goals
        n = Node(id="n", worker="alice", title="t", contract=c)  # 无 reviewer
        body = render_issue_body(n, c, TaskKind.DEVELOP, "ID")
        assert "pr_base" not in body
        assert "non_goals 是红线" not in body
        assert "reviewer（" not in body

    def test_scope_paths_rendered_as_boundary_when_present(self):
        """contract.scope_paths 有值→渲染为代码范围红线;无值→不渲染(可选字段)。"""
        c = Contract(objective="o", acceptance=["a"], scope_paths=["src/auth/**"])
        n = Node(id="n", worker="alice", title="t", contract=c)
        body = render_issue_body(n, c, TaskKind.DEVELOP, "ID")
        assert "src/auth/**" in body
        assert "代码范围" in body
        # 无 scope_paths:不渲染该约束(新项目可留空,直接放行)
        c2 = Contract(objective="o", acceptance=["a"])
        n2 = Node(id="n", worker="alice", title="t", contract=c2)
        assert "代码范围" not in render_issue_body(n2, c2, TaskKind.DEVELOP, "ID")

    def test_optional_fields_render_when_present(self):
        n = Node(id="n", worker="alice", title="t", reviewer="bob",
                 contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ID")
        assert "pr_base=feature/v1" in body
        assert "不接第三方 OAuth" in body
        assert "coverage_gate=90" in body
        assert "reviewer（bob）" in body

    def test_node_description_renders_as_task_detail(self):
        """node.description 非空时进 body 的「任务详情」段(worker 上下文来源);
        无 contract 节点尤其依赖它承载任务。"""
        n = Node(id="n", worker="alice", title="t",
                 description="新增 hello_omac.txt,内容 omac smoke ok,开 PR 到 main")
        body = render_issue_body(n, None, TaskKind.DEVELOP, "ID")
        assert "## 任务详情" in body
        assert "新增 hello_omac.txt" in body

    def test_empty_description_omits_task_detail(self):
        """description 为空则不渲染「任务详情」段(向后兼容既有派发)。"""
        n = Node(id="n", worker="alice", title="t", contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ID")
        assert "## 任务详情" not in body


# ==================== render_review_rollout_comment ====================

class TestRenderReviewRolloutComment:

    def test_handoff_none_posts_reader_phase(self):
        n = Node(id="n", worker="alice", reviewer="bob",
                 contract=Contract(objective="o", acceptance=["a"]))
        c = render_review_rollout_comment(n, n.contract, None, item_id="ITEM-1")
        assert "reviewer" in c and "ITEM-1" in c
        assert "omac work submit ITEM-1" in c

    def test_pass_notes_no_blockers(self):
        n = Node(id="n", worker="alice", reviewer="bob",
                 contract=Contract(objective="o", acceptance=["a"]))
        c = render_review_rollout_comment(
            n, n.contract, "pass",
            report={"review_goals": ["全覆盖"], "blockers": [], "nits": []},
            item_id="ITEM-1")
        assert "pass" in c and "ITEM-1" in c and "reviewer" in c

    def test_pass_with_nits_lists_nits(self):
        n = Node(id="n", worker="alice", reviewer="bob",
                 contract=Contract(objective="o", acceptance=["a"]))
        c = render_review_rollout_comment(
            n, n.contract, "pass-with-nits",
            report={"review_goals": ["g"], "blockers": [], "nits": ["命名修正"]},
            item_id="ITEM-1")
        assert "命名修正" in c

    def test_reject_carry_goals_blockers_nits_and_location(self):
        n = Node(id="n", worker="alice", reviewer="bob",
                 contract=Contract(objective="o", acceptance=["a"]))
        report = {
            "review_goals": ["验收映射全覆盖", "改动分支覆盖≥90"],
            "blockers": ["token 过期用例未覆盖"],
            "nits": ["命名修正"],
        }
        c = render_review_rollout_comment(
            n, n.contract, "reject", report=report, item_id="ITEM-1")
        assert "token 过期用例未覆盖" in c
        assert "验收映射全覆盖" in c
        assert "命名修正" in c
        assert "ITEM-1" in c  # 定位评审对象
        assert "朝评审目标修" in c or "目标修" in c


# ==================== loop 集成:e2e worker→reviewer→done ====================

class TestDispatchLoopIntegration:

    def test_dispatch_backfills_real_issue_id_into_body(self):
        """_dispatch 创建工单后回填真实 id,issue body 即含可直接执行的命令。"""
        eng = _engine()
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={
            "a": Node(id="a", worker="alice", title="Add login",
                     reviewer="bob", contract=_full_contract()),
        })
        path = _tmp_manifest_path(manifest)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item = eng.store.get_work_item(manifest.nodes["a"].work_item_id)
        assert item.id in item.description
        assert f"omac work show {item.id}" in item.description
        assert "硬约束" in item.description

    def test_worker_submit_triggers_reviewer_handoff_comment(self):
        """worker 完成交付(loop 回收)后往 issue 派发 rollout 评论。"""
        eng = _engine()
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob",
                     contract=_full_contract()),
        })
        path = _tmp_manifest_path(manifest)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item_id = manifest.nodes["a"].work_item_id
        eng.store.update_status(item_id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert eng.store.get_work_item(item_id).status == WorkItemStatus.IN_REVIEW
        comments = eng.store.get_comments(item_id)
        assert any("reviewer" in c for c in comments)

    def test_e2e_worker_to_reviewer_to_done_manual_submit(self):
        """零 skill 闭环:关闭自动完成,手动扮演 worker+reviewer 走完 develop→review→done。"""
        # 关闭 auto-complete,由我们手动写入结构化证据(模拟 work submit)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        contract = Contract(
            objective="登录", acceptance=["可登录"],
            verification_commands=["pytest -q"],
            integration_gates=[{
                "name": "login-gate", "layer": "L1",
                "delivery_goal": "端到端登录",
                "source_of_truth": ["docs/login.md"], "covers": ["route"],
                "acceptance_refs": ["可登录"], "commands": ["pytest -q"],
                "required_metrics": {"route_coverage": 100},
                "artifacts": ["coverage.xml"],
            }],
            pr_base="feature/v1", coverage_gate=90,
        )
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={
            "a": Node(id="a", worker="alice", reviewer="bob", contract=contract),
        })
        path = _tmp_manifest_path(manifest)

        # 1) 派发 → 工单进入 in_progress,body 含真实 id
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item_id = manifest.nodes["a"].work_item_id
        item = eng.store.get_work_item(item_id)
        assert item.status == WorkItemStatus.IN_PROGRESS
        assert f"omac work show {item_id}" in item.description

        # 2) 手动扮演 worker:写入可通过证据门的 verification
        eng.store.update_work_item_metadata(
            item_id,
            artifacts={"pr_url": "https://mock.example.com/pr/1"},
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0,
                              "summary": "pass"}],
                "integration_gates": [{
                    "name": "login-gate",
                    "commands": [{"cmd": "pytest -q", "exit_code": 0,
                                  "summary": "pass"}],
                    "metrics": {"route_coverage": 100},
                    "artifacts": ["coverage.xml"],
                    "source_of_truth": ["docs/login.md"],
                    "delivery_goal": "端到端登录",
                }],
                "pr_base": "feature/v1",
                "coverage": 95,
                "env_setup": ["Mock env: login-gate"],
            },
        )
        eng.store.update_status(item_id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert eng.store.get_work_item(item_id).status == WorkItemStatus.IN_REVIEW

        # reviewer 接手评论应含评审目标(空报告时给默认目标)
        reviewer_comments = [c for c in eng.store.get_comments(item_id)
                             if "reviewer" in c]
        assert reviewer_comments

        # 3) 手动扮演 reviewer:提交 pass verdict 与结构化报告
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict="pass",
            review_report={
                "review_goals": ["验收映射全覆盖"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "integration_tests_rerun": True,
                "coverage_checked": True,
                "acceptance_mapping": [
                    {"acceptance": "可登录", "evidence": "rerun pass",
                     "status": "pass"},
                ],
                "integration_gate_mapping": [{
                    "gate": "login-gate", "status": "pass",
                    "evidence": "rerun pass",
                    "commands": [{"cmd": "pytest -q", "exit_code": 0,
                                  "summary": "pass"}],
                    "metrics": {"route_coverage": 100},
                    "artifacts": ["coverage.xml"],
                    "source_of_truth": ["docs/login.md"],
                    "delivery_goal": "端到端登录",
                }],
                "blockers": [],
                "nits": [],
            },
        )
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        item = eng.store.get_work_item(item_id)
        assert item.status == WorkItemStatus.DONE
        assert manifest.nodes["a"].status == "done"
