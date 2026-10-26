"""work submit 的参数校验、左移门注册表与缺配置退出码(不依赖平台);需要真实平台的 show/submit/block 见 tests/it/test_it_cli_work_show.py 与 tests/it/test_it_work_submit.py;
事实投影见 tests/test_work_show.py。"""
from __future__ import annotations

import json

import pytest
import yaml

from _support.workitems import COMBINATIONS
from omac.cli import exit_codes
from omac.cli.main import main
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.workitem import WorkItemPayload
from omac.errors import ValidationError
from omac.pipeline import dispatch as dispatch_mod
from omac.pipeline.dispatch import SUBMIT_PARAM_SPECS, SUBMIT_PARAMS_BY_KIND_PHASE, submit_template_for


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
    """全部受支持组合都在 SUBMIT_PARAMS_BY_KIND_PHASE 中有定义。"""
    assert set(SUBMIT_PARAMS_BY_KIND_PHASE.keys()) == set(COMBINATIONS)


def test_work_resolve_store_preserves_workspace_slug_from_config(tmp_path, monkeypatch):
    from omac.cli.commands import work as work_cmd

    """work show 渲染上游 issue 链需要 workspace_slug 才能生成 mention 链接。"""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".omac"
    cfg_dir.mkdir()
    with open(cfg_dir / "config.yaml", "w") as f:
        yaml.safe_dump({
            "engine": "multica",
            "workspace": "ws-1",
            "project": "p1",
            "workspace_slug": "acme-team",
        }, f)

    store = work_cmd._resolve_store()

    assert store.config.extra["workspace_slug"] == "acme-team"


@pytest.mark.parametrize(("kind", "phase", "expected"), [
    (TaskKind.DEVELOP, TaskPhase.AUTHORING, {WorkItemPayload.CONTRACT}),
    (TaskKind.DEVELOP, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.PLAN, TaskPhase.AUTHORING, set()),
    (TaskKind.PLAN, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.PROJECT_RULES,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING, set()),
    (TaskKind.ACCEPTANCE, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING, {WorkItemPayload.CONTRACT}),   # an incremental decomposition is linted against its base manifest
    (TaskKind.DECOMPOSE, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.AMENDMENT, TaskPhase.AUTHORING, set()),
    (TaskKind.AMENDMENT, TaskPhase.REVIEW, {
        WorkItemPayload.CONTRACT,
        WorkItemPayload.DELIVERABLE,
        WorkItemPayload.REVIEW_OBLIGATIONS,
        WorkItemPayload.REVIEW_LEDGER,
    }),
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING, {
        WorkItemPayload.CONTRACT,
    }),
])
def test_submit_hydration_plan_is_explicit_by_kind_and_phase(
    kind, phase, expected,
):
    assert dispatch_mod.submit_hydration_plan(kind, phase) == frozenset(expected)


def test_submit_hydration_registry_covers_every_supported_spec():
    supported = {
        (kind, phase)
        for kind, phases in dispatch_mod.SPECS.items()
        for phase in phases
    }

    assert set(dispatch_mod.SUBMIT_HYDRATION_BY_KIND_PHASE) == supported


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
        assert "Missing parameters" in msg

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
        err = json.loads(capsys.readouterr().err)
        assert err["ok"] is False
        assert err["action"] == "submit"
        assert "config.yaml" in err["error"]["message"]
