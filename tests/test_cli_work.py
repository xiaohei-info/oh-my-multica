"""work show 9 种(kind × phase)组合快照 + submit 模板防漂移,加 submit 左移门 + 退出码。"""
from __future__ import annotations

import pytest
import yaml

from omac.cli.main import main
from omac.cli import exit_codes
from omac.core.manifest import Contract, Manifest, Node, save_manifest
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import ValidationError
from omac.pipeline import dispatch as dispatch_mod
from omac.pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    SUBMIT_PARAMS_BY_KIND_PHASE,
    build_show_output,
    submit_template_for,
)
from omac.pipeline.loop import tick

import pytest

from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.pipeline.dispatch import (
    SUBMIT_PARAM_SPECS,
    SUBMIT_PARAMS_BY_KIND_PHASE,
    build_show_output,
    submit_template_for,
)
from omac.cli.main import main
from omac.cli import exit_codes


def _store(auto_complete: str = "false"):
    config = EngineConfig(
        engine_type="mock", workspace_id="mock-workspace",
        extra={"MOCK_AUTO_COMPLETE": auto_complete,
              "MOCK_AUTO_COMPLETE_DELAY": "0"})
    return create_engine("mock", config).store


def _make_item(store, kind: TaskKind, phase: TaskPhase, dag_key: str = "a",
               with_contract: bool = False, with_deliverable: bool = False,
               with_verification: bool = False):
    item = store.create_work_item(
        "mock-workspace", f"title-{kind.value}", "desc",
        dag_key=dag_key, worker="alice", reviewer="bob",
        kind=kind)
    store.update_work_item_metadata(item.id, phase=phase)
    if with_contract:
        # 走真实 dispatch 路径:set_node_contract 下发 contract(§7.4),
        # 验证 work show 能读回完整上下文(回归 set_node_contract → work show 链路)。
        store.set_node_contract(item.id, {
            "objective": "实现 X",
            "acceptance": ["A 工作", "B 工作"],
            "non_goals": ["不做 Y"],
            "verification_commands": ["pytest -q"],
            "integration_gates": [],
            "pr_base": "feature/v1",
            "coverage_gate": 90,
        })
    if with_deliverable:
        store.update_work_item_metadata(
            item.id, phase=phase, deliverable="# 计划正文")
    if with_verification:
        store.update_work_item_metadata(
            item.id, phase=phase,
            verification={
                "commands": [{"cmd": "pytest -q", "exit_code": 0,
                              "summary": "ok"}],
                "pr_base": "feature/v1",
                "coverage": 92,
                "env_setup": ["docker compose up -d db"],
            })
    return store.get_work_item(item.id)


# 9 种组合(final-acceptance 仅 authoring)
COMBINATIONS = [
    (TaskKind.PLAN, TaskPhase.AUTHORING),
    (TaskKind.PLAN, TaskPhase.REVIEW),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING),
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW),
    (TaskKind.DEVELOP, TaskPhase.AUTHORING),
    (TaskKind.DEVELOP, TaskPhase.REVIEW),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING),
]


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_show_output_structure(kind, phase):
    """每种组合输出都包含四段:task/context/protocol/submit。"""
    store = _store()
    with_contract = (phase == TaskPhase.AUTHORING)
    item = _make_item(store, kind, phase, with_contract=with_contract,
                      with_deliverable=(phase == TaskPhase.REVIEW),
                      with_verification=(kind == TaskKind.DEVELOP
                                         and phase == TaskPhase.REVIEW))
    identity = f"worker:{item.worker}" if phase == TaskPhase.AUTHORING \
        else f"reviewer:{item.reviewer}"
    out = build_show_output(item, identity)

    assert "task" in out
    assert "context" in out
    assert "protocol" in out
    assert "submit" in out

    # 任务标识
    assert out["task"]["kind"] == kind.value
    assert out["task"]["phase"] == phase.value
    assert out["task"]["dag_key"] == "a"
    assert out["task"]["identity"] == identity

    # 协议非空
    assert out["protocol"].strip() != ""

    # submit 模板以 omac work submit <id> 开头
    assert out["submit"].startswith(f"omac work submit {item.id}")

    # authoring 阶段 context 含 contract
    if phase == TaskPhase.AUTHORING:
        assert "contract" in out["context"]

    # develop×review 阶段 context 含 env_setup 复跑清单
    if kind == TaskKind.DEVELOP and phase == TaskPhase.REVIEW:
        assert "env_setup" in out["context"]
        assert out["context"]["env_setup"] == ["docker compose up -d db"]


def test_authoring_show_includes_previous_review_report():
    """reject reset 后,下一轮 authoring 通过 work show 读取上轮评审上下文。"""
    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING, with_contract=True)
    report = {
        "verdict": "reject",
        "blockers": ["缺少积分体系的持久化方案"],
        "nits": ["补充排行榜刷新策略"],
    }
    store.update_work_item_metadata(
        item.id,
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_report=report,
        review_report_source="/tmp/omac-review-report.yaml",
    )
    store.reset_review(item.id)

    out = build_show_output(store.get_work_item(item.id), "worker:alice")

    assert out["context"]["previous_review"]["verdict"] == "reject"
    assert out["context"]["previous_review"]["report"] == report
    assert out["context"]["previous_review"]["report_ref"]["filename"] == "omac-review-report.yaml"


@pytest.mark.parametrize("kind,phase", COMBINATIONS, ids=[
    f"{k.value}-{p.value}" for k, p in COMBINATIONS])
def test_submit_template_matches_registered_params(kind, phase):
    """submit 模板使用的参数名必须与 SUBMIT_PARAM_SPECS 注册的一致(防漂移)。"""
    template = submit_template_for(kind, phase, "42")
    expected_params = SUBMIT_PARAMS_BY_KIND_PHASE[(kind, phase)]
    # 模板中每个 --xxx 都出现在注册表中
    for param in expected_params:
        assert param in template, f"模板缺少参数 {param}: {template}"
    # 模板中不应出现未注册的 -- 参数
    import re
    used_flags = re.findall(r"--\w+(?:-\w+)*", template)
    for flag in used_flags:
        assert flag in SUBMIT_PARAM_SPECS, \
            f"模板使用了未注册参数 {flag}: {template}"


def test_all_kind_phase_pairs_covered():
    """9 种组合全部在 SUBMIT_PARAMS_BY_KIND_PHASE 中有定义。"""
    assert set(SUBMIT_PARAMS_BY_KIND_PHASE.keys()) == set(COMBINATIONS)


def test_show_cli_json_output(tmp_path, monkeypatch, capsys):
    """CLI 入口:work show --output json 输出合法 JSON,exit 0。"""
    monkeypatch.chdir(tmp_path)
    # 写配置指向 mock 引擎
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)

    assert main(["work", "show", item.id, "--output", "json"]) == exit_codes.OK
    import json
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["task"]["kind"] == "develop"
    assert data["task"]["phase"] == "authoring"
    assert data["context"]["contract"]["objective"] == "实现 X"
    assert data["submit"].startswith(f"omac work submit {item.id}")


def test_show_cli_table_output(tmp_path, monkeypatch, capsys):
    """CLI 入口:work show 默认 markdown 输出,含相位视图各段。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.REVIEW,
                      with_deliverable=True)

    assert main(["work", "show", item.id]) == exit_codes.OK
    out = capsys.readouterr().out
    # markdown 段头(相位视图):任务头 / 现在做什么 / 完成后交付
    assert "# 任务" in out
    assert "## 现在做什么" in out
    assert "## 完成后交付" in out
    assert "plan" in out
    assert "review" in out


def test_show_identity_reflects_role_not_generic_worker(tmp_path, monkeypatch, capsys):
    """身份按角色如实标注:plan×authoring 是 planner,不再一律标 worker。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)
    assert main(["work", "show", item.id]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "planner" in out
    assert "worker:" not in out  # plan 的产出者不是 worker


def test_plan_authoring_action_not_role_mixed():
    """点5:plan×authoring 的「现在做什么」只讲 plan,不掺 acceptance 任务;深度指向 guide。"""
    store = _store()
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)
    out = build_show_output(item, "worker:alice")
    proto = out["protocol"]
    # 不再把 acceptance(验收文档)任务塞进 plan 的视图
    assert "验收文档" not in proto
    assert "acceptance" not in proto
    # 静态深度交给 guide(不再内联复制整段协议)
    assert "omac guide role planner" in proto


def test_review_show_surfaces_deliverable_and_env_setup(tmp_path, monkeypatch, capsys):
    """review 阶段 show 顶出只有此刻才存在的实例数据:评审对象(deliverable)+ env_setup。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.REVIEW,
                      with_deliverable=True, with_verification=True)
    assert main(["work", "show", item.id]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "评审对象" in out
    assert "docker compose up -d db" in out  # worker 的 env_setup 复跑清单
    assert "omac guide role reviewer" in out


def test_set_node_contract_visible_in_show():
    """回归:set_node_contract 下发的 contract 必须在 work show 中可见(真实 dispatch 路径)。

    这是被派发 agent 第一入口的关键链路:dispatch 侧调用 set_node_contract 下发契约,
    被派发 agent 调 work show 必须能读回完整 contract,否则拿到的是空上下文。
    """
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING,
                      with_contract=True)
    # 从 store 重新读取(模拟 agent 侧 get_work_item),确认 contract 已持久化
    got = store.get_work_item(item.id)
    assert got.contract is not None, (
        "set_node_contract 后 WorkItem.contract 必须非空,否则 work show 拿不到上下文")
    assert got.contract["objective"] == "实现 X"
    # 走 build_show_output 验证上下文完整
    out = build_show_output(got, f"worker:{got.worker}")
    assert out["context"]["contract"] is not None
    assert out["context"]["contract"]["acceptance"] == ["A 工作", "B 工作"]
    assert out["context"]["contract"]["pr_base"] == "feature/v1"




def test_develop_authoring_action_and_submit_cover_pr_flow():
    """develop x authoring:「现在做什么」点明推分支/开 PR/worker 自建;
    精确的 --pr-url 交付命令在 submit 段(不再把整条命令塞进协议文本)。"""
    store = _store()
    item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
    out = build_show_output(item, f"worker:{item.worker}")
    protocol = out["protocol"]
    # 动作点明 PR 三步的要害
    assert "推分支" in protocol or "git push" in protocol, protocol
    assert "PR" in protocol, protocol
    assert "自建" in protocol or "不代建" in protocol, protocol
    # 精确交付命令归 submit 段(相位视图:动作与命令分离)
    assert "--pr-url" in out["submit"]

def test_show_unknown_issue_id(tmp_path, monkeypatch, capsys):
    """issue_id 不存在时给出教学性报错,exit 5。"""
    monkeypatch.chdir(tmp_path)
    main(["config", "set", "engine", "mock"])
    main(["config", "set", "workspace", "mock-workspace"])
    capsys.readouterr()

    assert main(["work", "show", "99999"]) == exit_codes.VALIDATION
    err = capsys.readouterr().err
    assert "99999" in err



def _config(**extra):
    base = {"MOCK_AUTO_COMPLETE": "false"}
    base.update(extra)
    return EngineConfig(engine_type="mock", workspace_id="mock-workspace", extra=base)


def _engine(**extra):
    return create_engine("mock", _config(**extra))


CONTRACT = Contract(
    objective="do it",
    acceptance=["works"],
    non_goals=["no creep"],
    verification_commands=["pytest -q"],
    integration_gates=[{
        "name": "gate-1", "layer": "L1", "delivery_goal": "delivers",
        "source_of_truth": ["docs/d.md"], "covers": ["route"],
        "acceptance_refs": ["works"], "commands": ["pytest tests/int"],
        "required_metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
    }],
    pr_base="feature/v1",
    coverage_gate=90,
)


def _make_verification(pr_base="feature/v1", coverage=95):
    return {
        "commands": [{"cmd": "pytest -q", "exit_code": 0}],
        "integration_gates": [{
            "name": "gate-1",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100},
            "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"],
            "delivery_goal": "delivers",
        }],
        "env_setup": ["pip install -r requirements.txt", "docker compose up -d db"],
        "pr_base": pr_base,
        "coverage": coverage,
    }


def _make_review_report(integration_gates=True):
    report = {
        "review_goals": ["验收映射覆盖 contract.acceptance"],
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True,
        "acceptance_mapping": [{"acceptance": "works", "status": "pass"}],
        "blockers": [], "nits": [],
    }
    if integration_gates:
        report["integration_tests_rerun"] = True
        report["integration_gate_mapping"] = [{
            "gate": "gate-1", "status": "pass",
            "commands": [{"cmd": "pytest tests/int", "exit_code": 0}],
            "metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"],
            "source_of_truth": ["docs/d.md"], "delivery_goal": "delivers",
        }]
    return report


# ==================== 参数校验(直接调 dispatch.validate_params) ===========================

class TestParamValidation:

    def test_develop_authoring_missing_param(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.DEVELOP,
                dispatch_mod.TaskPhase.AUTHORING,
                {"pr_url": "https://x/pr/1"},  # 缺 verification_file
            )
        msg = str(exc.value)
        assert "verification-file" in msg
        assert "缺少" in msg

    def test_plan_authoring_extra_param(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.PLAN,
                dispatch_mod.TaskPhase.AUTHORING,
                {"plan_file": "p.md", "verdict": "pass"},  # verdict 多余
            )
        assert "多余" in str(exc.value) or "verdict" in str(exc.value)

    def test_develop_authoring_correct_passes(self):
        # 不应抛
        dispatch_mod.validate_params(
            dispatch_mod.TaskKind.DEVELOP,
            dispatch_mod.TaskPhase.AUTHORING,
            {"pr_url": "https://x/pr/1", "verification_file": "v.yaml"},
        )

    def test_final_acceptance_has_no_review(self):
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.validate_params(
                dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
                dispatch_mod.TaskPhase.REVIEW,
                {"verdict": "pass", "report_file": "r.yaml"},
            )
        assert "final-acceptance" in str(exc.value)

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            dispatch_mod._kind("not-a-kind")


# ==================== 每个 kind × phase 成功 + 内容校验打回 ====================

class TestSubmitPerKindPhase:

    # ---------- develop ----------

    def test_develop_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))

        result = dispatch_mod.submit(
            eng.store, item.id,
            pr_url="https://x/pr/1", verification_file=str(vfile),
        )
        assert result.kind == dispatch_mod.TaskKind.DEVELOP
        assert result.phase == dispatch_mod.TaskPhase.AUTHORING
        assert result.advanced_to == WorkItemStatus.DONE
        got = eng.store.get_work_item(item.id)
        assert got.artifacts == {"pr_url": "https://x/pr/1"}
        assert got.verification["pr_base"] == "feature/v1"
        assert got.verification_ref["filename"] == "omac-verification.yaml"
        assert got.status == WorkItemStatus.DONE

    def test_develop_authoring_content_rejected_atomic(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification(coverage=50)))  # 低于 gate

        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(
                eng.store, item.id,
                pr_url="https://x/pr/1", verification_file=str(vfile),
            )
        assert "gate" in str(exc.value).lower() or "coverage" in str(exc.value).lower()
        got = eng.store.get_work_item(item.id)
        assert got.artifacts is None
        assert got.verification is None
        assert got.status == WorkItemStatus.TODO

    # ---------- review ----------

    def test_develop_review_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        eng.store.set_node_contract(item.id, CONTRACT)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        result = dispatch_mod.submit(
            eng.store, item.id, verdict="pass", report_file=str(rfile),
        )
        got = eng.store.get_work_item(item.id)
        assert got.review_verdict == "pass"
        assert got.review_report["acceptance_mapping"][0]["acceptance"] == "works"
        assert got.review_report_ref["filename"] == "omac-review-report.yaml"

    def test_review_submit_cli_tells_reviewer_not_to_change_status(
            self, tmp_path, monkeypatch, capsys):
        """review submit 只提交 verdict,CLI 不应诱导 reviewer 手动保持 in_review。"""
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        store.set_node_contract(item.id, CONTRACT)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        rc = main(["work", "submit", item.id, "--verdict", "pass",
                   "--report-file", str(rfile)])

        assert rc == exit_codes.OK
        out = capsys.readouterr().out
        assert "状态推进: in_review" not in out
        assert "verdict 已提交: pass" in out
        assert "平台终态由 omac loop 收口" in out
        assert "不要手动修改 issue 状态" in out

    def test_review_reject_verdict_is_structured_verdict(self, tmp_path):
        """reviewer reject 必须能经 work submit 写入结构化 verdict/report。

        guide/help/run_task 都把 reject 作为合法评审结论;这里防止 submit
        左移校验把“不通过”挡在 metadata 外,导致编排永远等不到 review_verdict。
        """
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DEVELOP,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        item.phase = dispatch_mod.TaskPhase.REVIEW
        rfile = tmp_path / "report.yaml"
        report = _make_review_report()
        report["blockers"] = ["验收映射不满足"]
        report["acceptance_mapping"][0]["status"] = "fail"
        rfile.write_text(yaml.safe_dump(report))

        result = dispatch_mod.submit(
            eng.store, item.id, verdict="reject", report_file=str(rfile),
        )

        got = eng.store.get_work_item(item.id)
        assert result.advanced_to == WorkItemStatus.IN_REVIEW
        assert got.review_verdict == "reject"
        assert got.review_report["blockers"] == ["验收映射不满足"]
        assert got.review_report_ref["filename"] == "omac-review-report.yaml"

    def test_plan_review_without_deliverable_rejected(self, tmp_path):
        """review 相位没有评审对象时,不得允许 reviewer 写 verdict 掩盖半提交状态。"""
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice", reviewer="bob",
            kind=dispatch_mod.TaskKind.PLAN,
            initial_status=WorkItemStatus.IN_REVIEW,
        )
        eng.store.update_work_item_metadata(item.id, phase=dispatch_mod.TaskPhase.REVIEW)
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))

        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(
                eng.store, item.id, verdict="pass", report_file=str(rfile),
            )
        assert "评审对象缺失" in str(exc.value)

    # ---------- plan ----------

    def test_plan_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n\n## Summary\nsteps")
        result = dispatch_mod.submit(eng.store, item.id, plan_file=str(pfile))
        got = eng.store.get_work_item(item.id)
        assert got.deliverable.startswith("# Plan")
        assert got.status == WorkItemStatus.IN_REVIEW

    def test_plan_authoring_cli_tells_producer_to_stop(self, tmp_path, monkeypatch, capsys):
        """产出提交成功后的 CLI 文案不能诱导 planner 继续执行 reviewer 协议。"""
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan\n")

        rc = main(["work", "submit", item.id, "--plan-file", str(pfile)])
        assert rc == exit_codes.OK
        out = capsys.readouterr().out
        assert "产出阶段已结束" in out
        assert "不要提交 verdict" in out
        assert "等待 omac loop" in out

    def test_plan_authoring_empty_rejected(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("   \n")
        with pytest.raises(ValidationError):
            dispatch_mod.submit(eng.store, item.id, plan_file=str(pfile))
        assert eng.store.get_work_item(item.id).deliverable is None

    # ---------- acceptance ----------

    def test_acceptance_authoring_success(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.ACCEPTANCE,
        )
        afile = tmp_path / "acceptance.yaml"
        afile.write_text(yaml.safe_dump({
            "flows": [{"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"},
            ]}],
        }))
        dispatch_mod.submit(eng.store, item.id, acceptance_file=str(afile))
        got = eng.store.get_work_item(item.id)
        assert "flows" in got.deliverable
        assert got.status == WorkItemStatus.IN_REVIEW

    def test_acceptance_authoring_schema_rejected(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.ACCEPTANCE,
        )
        afile = tmp_path / "acceptance.yaml"
        afile.write_text(yaml.safe_dump({"flows": [{"id": "x"}]}), )  # 缺 name/actions
        with pytest.raises(ValidationError):
            dispatch_mod.submit(eng.store, item.id, acceptance_file=str(afile))

    # ---------- decompose ----------

    def test_decompose_authoring_success(self, tmp_path):
        eng = _engine()
        members = set(eng.store.list_members("mock-workspace"))
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DECOMPOSE,
        )
        mfile = tmp_path / "manifest.yaml"
        mfile.write_text(yaml.safe_dump({
            "meta": {},
            "nodes": [{"id": "b", "worker": sorted(members)[0],
                       "contract": {
                           "objective": "x", "acceptance": ["y"], "non_goals": ["z"],
                           "source_of_truth": ["docs/b.md"],
                           "verification_commands": ["pytest -q"],
                           "integration_gates": [{
                               "name": "g", "layer": "L1", "delivery_goal": "d",
                               "source_of_truth": ["s"], "covers": ["c"],
                               "acceptance_refs": ["y"], "commands": ["c1"],
                               "required_metrics": {}, "artifacts": [],
                           }],
                           "pr_base": "feature/v1", "coverage_gate": 90,
                       }}],
        }))
        dispatch_mod.submit(eng.store, item.id, manifest_file=str(mfile),
                            agent_pool=members)
        got = eng.store.get_work_item(item.id)
        assert got.deliverable is not None
        assert got.status == WorkItemStatus.IN_REVIEW

    def test_decompose_authoring_lint_rejected(self, tmp_path):
        eng = _engine()
        members = set(eng.store.list_members("mock-workspace"))
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.DECOMPOSE,
        )
        mfile = tmp_path / "manifest.yaml"
        mfile.write_text(yaml.safe_dump({
            "meta": {},
            # reviewer == worker 违反 lint
            "nodes": [{"id": "b", "worker": sorted(members)[0],
                       "reviewer": sorted(members)[0],
                       "contract": {
                           "objective": "x", "acceptance": ["y"], "non_goals": ["z"],
                           "verification_commands": ["pytest -q"],
                           "integration_gates": [{
                               "name": "g", "layer": "L1", "delivery_goal": "d",
                               "source_of_truth": ["s"], "covers": ["c"],
                               "acceptance_refs": ["y"], "commands": ["c1"],
                               "required_metrics": {}, "artifacts": [],
                           }],
                           "pr_base": "feature/v1", "coverage_gate": 90,
                       }}],
        }))
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(eng.store, item.id, manifest_file=str(mfile),
                                agent_pool=members)
        assert "lint" in str(exc.value) or "reviewer" in str(exc.value)

    # ---------- final-acceptance ----------

    def test_final_acceptance_authoring_success(self, tmp_path):
        eng = _engine()
        acceptance_doc = {"flows": [
            {"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"}]}]}
        contract = Contract(
            objective="accept", non_goals=["x"], acceptance=["login"],
            verification_commands=["pytest -q"],
            integration_gates=[{
                "name": "g", "layer": "L1", "delivery_goal": "d",
                "source_of_truth": ["s"], "covers": ["c"],
                "acceptance_refs": ["login"], "commands": ["c1"],
                "required_metrics": {}, "artifacts": [],
            }],
            pr_base="feature/v1", coverage_gate=90,
            acceptance_doc=acceptance_doc,
        )
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
        )
        eng.store.set_node_contract(item.id, contract)
        rfile = tmp_path / "results.yaml"
        rfile.write_text(yaml.safe_dump([{"id": "login", "status": "pass"}]))
        result = dispatch_mod.submit(eng.store, item.id,
                                     acceptance_results_file=str(rfile))
        assert result.advanced_to == WorkItemStatus.DONE

    def test_final_acceptance_authoring_missing_flow_rejected(self, tmp_path):
        eng = _engine()
        acceptance_doc = {"flows": [
            {"id": "login", "name": "登录", "actions": [
                {"step": "open", "how": "GET /login", "expected": "表单"}]}]}
        contract = Contract(
            objective="accept", non_goals=["x"], acceptance=["login"],
            verification_commands=["pytest -q"],
            integration_gates=[], pr_base="feature/v1", coverage_gate=90,
            acceptance_doc=acceptance_doc,
        )
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.FINAL_ACCEPTANCE,
        )
        eng.store.set_node_contract(item.id, contract)
        rfile = tmp_path / "results.yaml"
        rfile.write_text(yaml.safe_dump([]))  # 漏项 login
        with pytest.raises(ValidationError) as exc:
            dispatch_mod.submit(eng.store, item.id,
                                acceptance_results_file=str(rfile))
        assert "login" in str(exc.value)


# ==================== CLI 退出码映射(smoke) ===========================

class TestCliExitCodes:

    def test_submit_missing_engine_workspace_raises_generic(self, capsys):
        """CLI 入口层:缺配置时应以 exit 5(ValidationError) 干净退出,不崩溃。

        当前 main 走 resolve_engine_settings,缺 engine / workspace 时 raise
        ValidationError(§5.1:校验 → exit 5);omac.cli.main.main 捕获后映射为退出码,
        由 entry() 包装后才 sys.exit,因此断言返回值而非 SystemExit。
        """
        rc = main(["work", "submit", "1", "--plan-file", "p.md"])
        assert rc == exit_codes.VALIDATION
        assert "config.yaml" in capsys.readouterr().err


# ==================== mock e2e:submit → loop 收割必过 ===========================

class TestSubmitMissingCli:
    """CLI 层:e2e 派发 develop + work submit 缺 pr_url -> exit 5,报错精确。"""

    def test_develop_authoring_missing_pr_url_exits_five(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["config", "set", "engine", "mock"])
        main(["config", "set", "workspace", "mock-workspace"])
        capsys.readouterr()

        store = _store()
        item = _make_item(store, TaskKind.DEVELOP, TaskPhase.AUTHORING)
        # 故意只给 verification_file,缺 pr_url
        vfile = tmp_path / "v.yaml"
        vfile.write_text("commands: []")
        rc = main(["work", "submit", item.id, "--verification-file", str(vfile)])
        assert rc == exit_codes.VALIDATION, capsys.readouterr()
        err = capsys.readouterr().err
        assert "pr-url" in err, err


class TestSubmitLoopE2E:
    """验证 submit 左移校验与 loop 权威门 schema 同源,submit 过的证据,loop 必过。"""

    def test_develop_submit_then_loop_harvests(self, tmp_path):
        eng = _engine(MOCK_AUTO_COMPLETE="false")
        store = eng.store
        contract = CONTRACT
        members = sorted(store.list_members("mock-workspace"))
        a_worker = members[0]

        # 1. 建 manifest + 节点(无 reviewer → worker 完成后 loop 直接 done)
        node_a = Node(id="a", worker=a_worker, contract=contract, title="a")
        manifest = Manifest(meta={"workspace_id": "mock-workspace"},
                            nodes={"a": node_a})
        mpath = str(tmp_path / "omac.yaml")
        save_manifest(manifest, mpath)

        # 2. 模拟 loop dispatch 派发 a:建 work item + 落 contract + 标 IN_PROGRESS
        it_a = store.create_work_item("mock-workspace", "a", "d", dag_key="a",
                                      worker=a_worker)
        store.set_node_contract(it_a.id, contract)
        store.assign_work_item(it_a.id, a_worker, "worker")
        store.update_status(it_a.id, WorkItemStatus.IN_PROGRESS)
        manifest.nodes["a"].work_item_id = it_a.id
        save_manifest(manifest, mpath)

        # 3. worker 完成 → work submit(左移校验 schema 同源验证)
        vfile = tmp_path / "verification.yaml"
        vfile.write_text(yaml.safe_dump(_make_verification()))
        result = dispatch_mod.submit(
            store, it_a.id,
            pr_url="https://x/pr/1", verification_file=str(vfile),
        )
        assert result.advanced_to == WorkItemStatus.DONE
        assert store.get_work_item(it_a.id).status == WorkItemStatus.DONE

        # 4. loop tick 收割:证据门必过 → 节点 done
        result = tick(store, eng.runtime, manifest, mpath, max_parallel=4)
        assert manifest.nodes["a"].status == "done"
        assert "a" in result.done
        # schema 同源断言:权威的 validate_worker_evidence 对同一 verification 必须过
        from omac.core import evidence as evidence_mod
        item_after = store.get_work_item(it_a.id)
        assert evidence_mod.validate_worker_evidence(node_a, item_after) == []


# ==================== 评审员路由:phase/status 不一致时的正确路由 ============================

class TestPhaseResolution:
    """status 已是 IN_REVIEW 但 phase metadata 滞后为 AUTHORING 时,
    work submit 必须按 review 路由(由 loop/plan 流水线驱动 status)。
    """

    def test_plan_authoring_then_reviewer_submit_routed_as_review(self, tmp_path):
        import yaml
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        eng.store.set_node_contract(item.id, CONTRACT)
        # authoring submit
        pfile = tmp_path / "plan.md"
        pfile.write_text("# Plan")
        r1 = dispatch_mod.submit(eng.store, item.id, plan_file=str(pfile))
        assert r1.phase == dispatch_mod.TaskPhase.REVIEW
        assert eng.store.get_work_item(item.id).status == WorkItemStatus.IN_REVIEW

        # 模拟 loop 只改 status 未改 phase 的旧行为:把 phase 滞回 AUTHORING
        eng.store.update_work_item_metadata(item.id, phase=dispatch_mod.TaskPhase.AUTHORING)

        # reviewer submit 即便 phase metadata = AUTHORING,也应路由为 review
        rfile = tmp_path / "report.yaml"
        rfile.write_text(yaml.safe_dump(_make_review_report()))
        r2 = dispatch_mod.submit(eng.store, item.id, verdict="pass", report_file=str(rfile))
        assert r2.phase == dispatch_mod.TaskPhase.REVIEW
        got = eng.store.get_work_item(item.id)
        assert got.review_verdict == "pass"

    def test_authoring_rejected_leaves_metadata_atomic(self, tmp_path):
        eng = _engine()
        item = eng.store.create_work_item(
            "mock-workspace", "t", "d", dag_key="a", worker="alice",
            kind=dispatch_mod.TaskKind.PLAN,
        )
        pfile = tmp_path / "plan.md"
        pfile.write_text("   \n")  # empty
        with pytest.raises(ValidationError):
            dispatch_mod.submit(eng.store, item.id, plan_file=str(pfile))
        got = eng.store.get_work_item(item.id)
        assert got.deliverable is None
        assert got.status == WorkItemStatus.TODO
        assert got.phase == dispatch_mod.TaskPhase.AUTHORING
