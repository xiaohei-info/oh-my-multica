"""派发内容(dispatch.render_issue_body / render_review_rollout_comment)
与 loop 集成。

验收标准:
- render_issue_body 是 Human-first 内容，顶部只有一个可执行 JSON bootstrap
- issue 类型→角色映射稳定；字段缺失时省略，不引用不存在的 contract
- 可选字段(pr_base/non_goals/scope_paths)缺省时相应段落省略
- render_review_rollout_comment 覆盖 pass / pass-with-nits / reject:
  阶段说明 + 定位 + reject 时含 review_goals/blockers/nits
- mock e2e:关闭 auto-complete,手动扮演 worker(review 提交)→ reviewer(pass)
  驱动一个节点走完 develop→review→done,证明 Agent-first 闭环在接口层成立
"""
import os
import tempfile
from types import SimpleNamespace

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

@pytest.fixture(autouse=True)
def _default_gh_merge_succeeds_in_dispatch_tests(monkeypatch):
    import subprocess

    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if isinstance(command, str) and command.startswith("gh pr merge "):
            class Proc:
                returncode = 0
                stdout = "merged"
                stderr = ""

            return Proc()
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("omac.pipeline.delivery.subprocess.run", fake_run)


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
        quality={
            "required_outcomes": [{
                "id": "login-mobile", "source_ref": "acceptance#login.mobile",
            }],
            "business_tests": [{
                "id": "login-business", "outcome_refs": ["login-mobile"],
                "command": "pytest tests/int_login", "level": "integration",
                "real_dependencies": ["postgres"], "must_fail_on_base": True,
            }],
            "runtime_data_policy": "real-or-error",
        },
        pr_base="feature/v1",
        coverage_gate=90,
    )


def _tmp_manifest_path(manifest):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="omac_test_")
    os.close(fd)
    save_manifest(manifest, path)
    return path


def test_final_acceptance_body_reads_mapping_contract_and_repositories():
    node = SimpleNamespace(
        id="fa",
        title="最终验收 · Demo · 第 1 轮",
        description="按验收文档逐项走查。",
        reviewer=None,
    )
    contract = {
        "acceptance_doc": {"flows": []},
        "acceptance": ["ACC-001"],
        "pr_base": "main",
        "repo_urls": ["git@github.com:owner/demo.git"],
    }

    body = render_issue_body(
        node,
        contract,
        TaskKind.FINAL_ACCEPTANCE,
        "fa-1",
        engine_env={
            "OMAC_ENGINE": "multica",
            "OMAC_WORKSPACE_ID": "ws-1",
            "OMAC_PROJECT_ID": "project-1",
        },
    )

    assert "## 完成标准\n- ACC-001" in body
    assert "- PR 基线: `main`" in body
    assert "## 目标仓库" in body
    assert "git@github.com:owner/demo.git" in body


# ==================== render_issue_body 快照 ====================

class TestRenderIssueBody:

    def test_issue_body_is_human_first_with_one_agent_entry(self):
        n = Node(id="a", worker="alice", title="Add login", reviewer="bob",
                 contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ISSUE-9")
        assert "ISSUE-9" in body
        assert "Agent 入口" in body
        assert "omac work show ISSUE-9 --output json" in body
        assert "omac work submit ISSUE-9" not in body
        assert "omac guide role worker" not in body
        assert "## 任务摘要" in body
        assert "类型: 开发实现" in body
        assert "执行角色: 开发执行者" in body
        assert "## 完成标准" in body
        assert "硬约束" not in body

    def test_issue_body_supports_english_project_language(self):
        n = Node(id="a", worker="alice", title="Add login", reviewer="bob")

        body = render_issue_body(
            n, None, TaskKind.DEVELOP, "ISSUE-9", language="en")

        assert "Agent entry" in body
        assert "## Task summary" in body
        assert "Execution role" in body
        assert "任务摘要" not in body

    def test_briefing_lists_render_as_nested_markdown(self):
        n = Node(id="a", worker="alice", title="Add login", reviewer="bob",
                 contract=_full_contract())

        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ISSUE-9")

        assert "- 依据: `docs/login.md`" in body
        assert "## 完成标准\n- 移动端可登录\n- token 10 分钟过期" in body
        assert "source_of_truth" not in body
        assert "acceptance:" not in body

    def test_bootstrap_command_is_copy_pasteable(self):
        """Agent 入口嵌入真实 id,且只引导读取权威 JSON 上下文。"""
        n = Node(id="n", worker="alice", contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "REAL-100")
        assert "omac work show REAL-100 --output json" in body
        assert "omac work submit REAL-100" not in body
        assert "<id>" not in body and "<issue>" not in body

    def test_develop_body_requires_issue_key_in_github_pr(self):
        """Multica PR 自动关联靠 issue key,worker body 必须把该约束前置。"""
        n = Node(id="n", worker="alice", contract=_full_contract())
        body = render_issue_body(
            n, n.contract, TaskKind.DEVELOP, "uuid-1", issue_key="AITEAM-762")
        assert "AITEAM-762" in body
        assert "PR 关联标识" in body

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
        assert f"{prefix} omac work show REAL-100 --output json" in body
        assert f"{prefix} omac work submit REAL-100" not in body

    def test_source_refs_render_human_links_without_agent_commands(self):
        n = Node(id="n", worker="alice", contract=_full_contract())
        env = {
            "OMAC_ENGINE": "multica",
            "OMAC_WORKSPACE_ID": "ws-1",
            "OMAC_PROJECT_ID": "proj-1",
        }

        body = render_issue_body(
            n, n.contract, TaskKind.DEVELOP, "REAL-100",
            source_refs=[
                {"label": "设计方案", "issue_id": "plan-1",
                 "url": "https://multica.ai/workspaces/ws-1/issues/plan-1"},
                {"label": "验收文档", "issue_id": "acc-1"},
            ],
            engine_env=env,
        )

        assert "## 上游 issue（防跑偏）" in body
        assert "- 设计方案: [plan-1](https://multica.ai/workspaces/ws-1/issues/plan-1)" in body
        assert "- 验收文档: `acc-1`" in body
        assert "omac work show plan-1" not in body

    def test_source_refs_generate_multica_links_from_engine_env(self):
        n = Node(id="n", worker="alice", contract=_full_contract())
        env = {
            "OMAC_ENGINE": "multica",
            "OMAC_WORKSPACE_ID": "ws-1",
            "OMAC_WORKSPACE_SLUG": "guantik-aiteam",
        }

        body = render_issue_body(
            n, n.contract, TaskKind.DEVELOP, "REAL-100",
            source_refs=[{"label": "设计方案", "issue_id": "plan-1"}],
            engine_env=env,
        )

        assert "- 设计方案: [plan-1](mention://issue/plan-1)" in body

    def test_kind_role_mapping_is_human_readable_without_guide_dump(self):
        """issue 保留角色/类型供 Human 识别,不复制 Agent guide。"""
        n = Node(id="n", worker="alice", title="t",
                 contract=Contract(objective="o", acceptance=["a"]))
        # 全部五种 kind 都有确定映射
        for kind in TaskKind:
            body = render_issue_body(n, n.contract, kind, "ID")
            role = KIND_ROLE[kind]
            label = KIND_LABEL[kind]
            assert role in body, f"{kind} 缺角色 {role}"
            assert "omac guide" not in body
            assert label in body, f"{kind} 缺标签 {label}"

    def test_missing_contract_fields_omit_briefing_lines(self):
        """contract 字段缺失时,简报省略该行,绝不渲染指向虚空的「见 contract.X」死占位。

        plan/acceptance/decompose 天生无 contract(payload 只有 source_of_truth),
        「见 contract.objective」是误导——真实需求在「上游产物」段。字段不存在就不印那行。"""
        n = Node(id="n", worker="alice", title="t")  # contract=None
        body = render_issue_body(n, None, TaskKind.DEVELOP, "ID")
        # 不得出现任何指向不存在 contract 的死占位
        assert "见 contract." not in body
        assert "- 目标:" not in body
        assert "- 依据:" not in body
        assert "## 完成标准" not in body
        assert "# t" in body
        assert "## 任务摘要" in body and "omac work show ID --output json" in body

    def test_plan_task_briefing_has_no_dead_contract_placeholder(self):
        """plan 任务(contract=None)的简报不得出现「见 contract.X」——它引用的东西根本不存在。"""
        n = Node(id="n", worker="alice", title="贪吃蛇手游 计划")
        body = render_issue_body(n, None, TaskKind.PLAN, "ID")
        assert "见 contract" not in body
        assert "# 贪吃蛇手游 计划" in body

    def test_bootstrap_orders_work_show_first(self):
        """实例事实优先:issue 只给 work show,不要求先读静态 guide。"""
        n = Node(id="n", worker="alice", title="计划")
        body = render_issue_body(n, None, TaskKind.PLAN, "ID")
        assert "omac work show ID --output json" in body
        assert "omac guide" not in body
        assert "omac work submit" not in body

    def test_issue_body_does_not_duplicate_agent_protocol(self):
        n = Node(id="n", worker="alice", title="计划")
        body = render_issue_body(n, None, TaskKind.PLAN, "ID")
        assert "guide 是软上下文" not in body
        assert "左移校验" not in body
        assert "不信任何自述" not in body

    def test_worker_platform_rules_live_outside_human_issue(self):
        n = Node(id="n", worker="alice", title="开发")
        body = render_issue_body(n, None, TaskKind.DEVELOP, "ID")
        assert "multica issue status" not in body
        assert "multica issue assign" not in body
        assert "multica issue rerun" not in body
        assert "multica issue cancel-task" not in body

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
        assert "## 非目标" not in body
        assert "reviewer（" not in body

    def test_scope_paths_rendered_as_primary_scope_when_present(self):
        """scope_paths 是主要归属范围,必要配套文件可按 contract 扩展。"""
        c = Contract(objective="o", acceptance=["a"], scope_paths=["src/auth/**"])
        n = Node(id="n", worker="alice", title="t", contract=c)
        body = render_issue_body(n, c, TaskKind.DEVELOP, "ID")
        assert "src/auth/**" in body
        assert "主要代码归属范围" in body
        # 无 scope_paths:不渲染该约束(新项目可留空,直接放行)
        c2 = Contract(objective="o", acceptance=["a"])
        n2 = Node(id="n", worker="alice", title="t", contract=c2)
        assert "主要代码归属范围" not in render_issue_body(n2, c2, TaskKind.DEVELOP, "ID")

    def test_optional_fields_render_when_present(self):
        n = Node(id="n", worker="alice", title="t", reviewer="bob",
                 contract=_full_contract())
        body = render_issue_body(n, n.contract, TaskKind.DEVELOP, "ID")
        assert "PR 基线: `feature/v1`" in body
        assert "不接第三方 OAuth" in body
        assert "改动分支覆盖率: `≥ 90%`" in body
        assert "reviewer（bob）" not in body

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
        assert f"omac work show {item.id} --output json" in item.description
        assert "## Task summary" in item.description
        assert "硬约束" not in item.description

    def test_retry_existing_issue_refreshes_body_without_recreating_issue(self):
        """node retry 复用旧 issue 时同步最新 scope 文案,避免旧硬白名单继续阻塞。"""
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        contract = _full_contract()
        contract.scope_paths = ["src/auth/**"]
        manifest = Manifest(meta={"workspace_id": "ws"}, nodes={
            "a": Node(id="a", worker="alice", title="Add login",
                      reviewer="bob", contract=contract),
        })
        path = _tmp_manifest_path(manifest)

        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        item_id = manifest.nodes["a"].work_item_id
        first_body = eng.store.get_work_item(item_id).description
        assert "package.json" not in first_body

        manifest.nodes["a"].contract.scope_paths.append("package.json")
        manifest.nodes["a"].status = "todo"
        eng.store.update_status(item_id, WorkItemStatus.BLOCKED)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)

        refreshed = eng.store.get_work_item(item_id)
        assert manifest.nodes["a"].work_item_id == item_id
        assert "package.json" in refreshed.description
        assert "Primary code ownership" in refreshed.description
        assert refreshed.status == WorkItemStatus.IN_PROGRESS

    def test_worker_submit_assigns_reviewer_without_handoff_comment(self):
        """worker 完成交付后靠 assign + metadata 交接,不发评论触发第二次 run。"""
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
        assert eng.store.get_work_item(item_id).reviewer == "bob"
        comments = eng.store.get_comments(item_id)
        assert not any("阶段变更" in c and "reviewer" in c for c in comments)

    def test_e2e_worker_to_reviewer_to_done_manual_submit(self):
        """零 skill 闭环:关闭自动完成,手动扮演 worker+reviewer 走完 develop→review→done。"""
        # 关闭 auto-complete,由我们手动写入结构化证据(模拟 work submit)
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        contract = Contract(
            objective="登录", source_of_truth=["docs/login.md#flow"],
            acceptance=["可登录"], non_goals=["不修改相邻认证流程"],
            verification_commands=["pytest -q"],
            integration_gates=[{
                "name": "login-gate", "layer": "L1",
                "delivery_goal": "端到端登录",
                "source_of_truth": ["docs/login.md"], "covers": ["route"],
                "acceptance_refs": ["可登录"], "commands": ["pytest -q"],
                "required_metrics": {"route_coverage": 100},
                "artifacts": ["coverage.xml"],
            }],
            quality={
                "required_outcomes": [{
                    "id": "login", "source_ref": "acceptance#login.action",
                }],
                "business_tests": [{
                    "id": "login-business", "outcome_refs": ["login"],
                    "command": "pytest -q", "level": "integration",
                    "real_dependencies": ["none"], "must_fail_on_base": True,
                }],
                "runtime_data_policy": "real-or-error",
            },
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
        assert f"omac work show {item_id} --output json" in item.description

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
                "quality": {
                    "delivered_revision": "head-sha",
                    "outcome_mapping": [{
                        "outcome": "login", "implementation": ["src/login.py"],
                        "tests": ["tests/test_login.py"],
                    }],
                    "regression_proof": [{
                        "test_id": "login-business",
                        "base_ref": "base-sha", "base_exit_code": 1,
                        "head_ref": "head-sha", "head_exit_code": 0,
                    }],
                    "runtime_fallbacks": [], "known_gaps": [],
                    "evidence_origin": "real",
                },
            },
        )
        eng.store.update_status(item_id, WorkItemStatus.DONE)
        tick(eng.store, eng.runtime, manifest, path, max_parallel=4)
        assert eng.store.get_work_item(item_id).status == WorkItemStatus.IN_REVIEW

        # reviewer 接手靠 assign + metadata,正常路径不发阶段变更评论。
        handoff = eng.store.get_work_item(item_id)
        assert handoff.reviewer == "bob"
        assert not any("阶段变更" in c for c in eng.store.get_comments(item_id))

        # 3) 手动扮演 reviewer:提交 pass verdict 与结构化报告
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict="pass",
            review_report={
                "reviewed_revision": "head-sha",
                "review_goals": ["验收映射全覆盖"],
                "diff_reviewed": True,
                "tests_rerun": True,
                "integration_tests_rerun": True,
                "coverage_checked": True,
                "review_scope": {
                    "changed_files": ["src/login.py", "tests/test_login.py"],
                    "all_changed_files_reviewed": True,
                    "all_outcomes_reviewed": True,
                    "all_business_tests_rerun": True,
                    "runtime_fallback_audit_completed": True,
                },
                "findings": [],
                "outcome_mapping": [{"outcome": "login", "status": "pass"}],
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
