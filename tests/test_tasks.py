"""P3.1:run_task 确定性原语 —— 派任务→等终态→取交付→有界修订循环。

验收标准:
- mock:一次过 / reject 2 次后过 / 耗尽 NeedsDecision 三条路径单测
- 全程同一 issue id(不新建评审 issue)
- issue body 取自 dispatch.render_issue_body(Human-first + 单一 Agent 入口模板)
"""
from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest
import yaml

import omac.pipeline.tasks as tasks_module
from omac.core.acceptance import load_acceptance_doc
from omac.core.amendment import (
    build_reviewed_amendment,
    historical_work_item_evidence_digest,
)
from omac.core.manifest import Contract, Manifest, Node
from omac.core.review_convergence import REVIEW_PROTOCOL_VERSION, open_blockers
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.core.restart import RestartState, deliverable_identity
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import AgentRunObservation, EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision
from omac.pipeline.dispatch import build_show_output
from omac.pipeline.tasks import AuthoringTaskSpec, create_authoring_task, run_task


def _engine(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    return create_engine("mock", EngineConfig(engine_type="mock", workspace_id="ws", extra=base))


def _payload(**over):
    base = {
        "title": "feature-x",
        "contract": Contract(
            objective="实现 feature-x",
            acceptance=["端到端可走通"],
            non_goals=["不碰其他模块"],
        ),
    }
    base.update(over)
    return base


def _poll():
    """测试用 no-op poll(配合 MOCK_AUTO_COMPLETE_DELAY=0 立即收敛)。"""
    pass


def _review_report(verdict="pass", item=None):
    report = {
        "review_goals": ["完整复核当前交付"],
        "diff_reviewed": True,
        "tests_rerun": True,
        "coverage_checked": True,
        "full_review_completed": True,
        "acceptance_mapping": [
            {
                "acceptance": "端到端可走通",
                "evidence": "独立复核通过",
                "status": "fail" if verdict == "reject" else "pass",
            }
        ],
        "blockers": ["仍有 blocker"] if verdict == "reject" else [],
    }
    obligations = list(getattr(item, "review_obligations", None) or [])
    if not obligations:
        return report
    failed_id = "dimension:structure" if verdict == "reject" else None
    report.update({
        "review_protocol": REVIEW_PROTOCOL_VERSION,
        "obligation_results": [
            {
                "obligation_id": obligation["obligation_id"],
                "status": (
                    "fail" if obligation["obligation_id"] == failed_id
                    else "pass"),
                "evidence": "独立覆盖当前 obligation",
            }
            for obligation in obligations
        ],
        "prior_blocker_results": [
            {
                "blocker_id": blocker["blocker_id"],
                "status": "fixed",
                "evidence": "历史 blocker 回归通过",
            }
            for blocker in open_blockers(getattr(item, "review_ledger", None))
        ],
        "blockers": ([{
            "root_cause_key": "test-review-blocker",
            "obligation_id": failed_id,
            "classification": "new",
            "summary": "仍有 blocker",
            "evidence": "测试发现 blocker",
            "required_fix": "完成返工",
        }] if failed_id else []),
    })
    return report


def test_produced_requires_review_phase_and_review_status_to_agree():
    """远端 phase 写后读延迟时，旧 review phase 不能跳过 producer 返工。"""
    stale = SimpleNamespace(
        phase=TaskPhase.REVIEW,
        status=WorkItemStatus.IN_PROGRESS,
    )
    submitted = SimpleNamespace(
        phase=TaskPhase.REVIEW,
        status=WorkItemStatus.IN_REVIEW,
    )

    assert tasks_module._produced(stale) is False
    assert tasks_module._produced(submitted) is True


def test_machine_gate_externalizes_large_feedback_for_author(monkeypatch):
    MockStore.reset()
    eng = _engine()
    errors = [
        f"node node-{index}: deterministic failure with enough detail for repair"
        for index in range(240)
    ]
    monkeypatch.setattr(
        tasks_module, "run_review_preflight", lambda _item: list(errors))

    with pytest.raises(NeedsDecision):
        run_task(
            eng,
            TaskKind.DECOMPOSE,
            _payload(title="large machine feedback"),
            "alice",
            reviewers=["bob"],
            max_revisions=1,
            poll=_poll,
        )

    item = eng.store.list_work_items("ws")[0]
    assert len((item.review_comment or "").encode("utf-8")) < 8192
    assert f"omac work show {item.id} --output json" in item.review_comment
    assert "context.machine_feedback" in item.review_comment
    assert item.machine_feedback == {
        "schema": "omac.machine-feedback/v1",
        "gate": "machine-gate",
        "error_count": len(errors),
        "errors": errors,
    }
    assert item.machine_feedback_ref["bytes"] > 8192
    assert item.machine_feedback_ref["sha256"]

    output = build_show_output(item, "orchestrator:alice")
    assert output["context"]["machine_feedback"] == item.machine_feedback
    assert output["context"]["machine_feedback_ref"] == item.machine_feedback_ref


def test_machine_feedback_clears_across_review_lifecycle():
    MockStore.reset()
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.DECOMPOSE,
        title="lifecycle",
        dag_key="decompose-lifecycle",
        assignee="alice",
    ))
    feedback = {
        "schema": "omac.machine-feedback/v1",
        "gate": "machine-gate",
        "error_count": 1,
        "errors": ["fix the local target"],
    }

    eng.store.update_work_item_metadata(
        item.id, machine_feedback=feedback, review_comment="bounded summary")
    eng.store.reset_review(item.id)
    reset = eng.store.get_work_item(item.id)
    assert reset.machine_feedback is None
    assert reset.machine_feedback_ref is None

    eng.store.update_work_item_metadata(
        item.id, machine_feedback=feedback, review_comment="bounded summary")
    prepared = eng.store.prepare_review_cycle(item.id, "subject-v2")
    assert prepared.machine_feedback is None
    assert prepared.machine_feedback_ref is None


def test_create_authoring_task_renders_body_contract_and_source_refs():
    eng = _engine()
    project = eng.store.create_project(
        "ws", "demo", repo_urls=["git@github.com:owner/demo.git"])
    eng.store.config.project_id = project.id
    spec = AuthoringTaskSpec(
        kind=TaskKind.FINAL_ACCEPTANCE,
        title="最终验收 · Demo · 第 1 轮",
        dag_key="final-acceptance-p-demo-r1",
        assignee="alice",
        description="按 ACC-001 逐项走查。",
        contract={
            "acceptance_doc": {"flows": []},
            "acceptance": ["ACC-001"],
            "pr_base": "main",
            "repo_urls": ["git@github.com:owner/demo.git"],
        },
        source_refs=[{"label": "最终开发交付", "issue_id": "closeout-1"}],
    )

    item = create_authoring_task(eng, spec)

    assert "OMAC_ENGINE=mock OMAC_WORKSPACE_ID=ws" in item.description
    assert f"omac work show {item.id}" in item.description
    assert "PR base: `main`" in item.description
    assert "git@github.com:owner/demo.git" in item.description
    assert item.contract["acceptance_doc"] == {"flows": []}
    assert item.source_refs == [
        {"label": "最终开发交付", "issue_id": "closeout-1"}
    ]


def test_run_task_delegates_new_issue_creation_to_shared_primitive(monkeypatch):
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    original = tasks_module.create_authoring_task
    calls = []

    def tracking_create_authoring_task(engine, spec):
        calls.append(spec)
        return original(engine, spec)

    monkeypatch.setattr(tasks_module, "create_authoring_task", tracking_create_authoring_task)

    result = run_task(eng, TaskKind.PLAN, _payload(), "alice", poll=_poll)

    assert result["verdict"] == "pass"
    assert len(calls) == 1
    assert calls[0].kind == TaskKind.PLAN
    assert calls[0].assignee == "alice"


def test_poll_is_required():
    """poll 是必填关键字参数,不传应抛 TypeError。"""
    eng = _engine()
    with pytest.raises(TypeError):
        run_task(eng, TaskKind.PLAN, _payload(), "alice")


def test_one_pass_no_reviewers():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice", poll=_poll)
    assert res["item_id"]
    assert res["delivery"] == {"plan": "计划正文"}
    assert res["rounds"] == 0
    assert res["verdict"] == "pass"
    assert res["kind"] == "plan"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    # 全程只建了一条 issue
    assert len(eng.store.list_work_items("ws")) == 1
    # Human-first body 只保留一个 Agent work-show 入口。
    assert f"omac work show {res['item_id']}" in item.description
    assert f"omac work submit {res['item_id']}" not in item.description


def test_run_task_auto_generates_unique_dag_key_when_missing():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    first = run_task(eng, TaskKind.PLAN, _payload(title="重复计划"), "alice", poll=_poll)
    second = run_task(eng, TaskKind.PLAN, _payload(title="重复计划"), "alice", poll=_poll)

    first_key = eng.store.get_work_item(first["item_id"]).dag_key
    second_key = eng.store.get_work_item(second["item_id"]).dag_key
    assert first_key.startswith("plan-")
    assert second_key.startswith("plan-")
    assert first_key != "plan"
    assert second_key != "plan"
    assert first_key != second_key


def test_run_task_consumes_real_submit_deliverable():
    """真实 submit 路径:producer 经 dispatch.submit → IN_REVIEW + deliverable(正文),
    run_task 应取到 deliverable 并跑完评审到 done(而非依赖 mock 的 artifacts 捷径)。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文-真实路径"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice", reviewers=["bob"], poll=_poll)
    assert res["delivery"]["plan"] == "计划正文-真实路径"
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    # 真实路径:交付正文落 deliverable(不是 artifacts 捷径)
    assert item.deliverable == "计划正文-真实路径"


def test_run_task_renders_source_refs_in_body():
    """Human issue 只展示上游链接,Agent 命令由 work show 返回。"""
    eng = _engine()
    MockStore.set_kind_delivery("acceptance", {"acceptance": "验收正文"})
    res = run_task(eng, TaskKind.ACCEPTANCE, _payload(), "alice",
                   source_refs=["7", "8"], poll=_poll)
    item = eng.store.get_work_item(res["item_id"])

    assert "## Upstream issues (stay on target)" in item.description
    assert "- `#7`" in item.description
    assert "- `#8`" in item.description
    assert "omac work show 7" not in item.description
    assert "omac work show 8" not in item.description
    assert "#7(omac work show 7 查看)" not in item.description


def test_run_task_renders_single_agent_bootstrap_as_code_block():
    """issue 只提供一个 JSON work-show 入口,不复制 guide/submit 协议。"""
    eng = _engine()
    MockStore.set_kind_delivery("decompose", {"manifest": "nodes: []"})
    res = run_task(eng, TaskKind.DECOMPOSE, _payload(), "alice", poll=_poll)
    item = eng.store.get_work_item(res["item_id"])

    assert (
        f"```bash\nOMAC_ENGINE=mock OMAC_WORKSPACE_ID=ws "
        f"omac work show {res['item_id']} --output json\n```"
    ) in item.description
    assert "omac guide role orchestrator" not in item.description
    assert "omac work submit" not in item.description


def test_run_task_renders_markdown_source_of_truth_as_collapsible_markdown():
    """上游 Markdown 保持原生渲染,不用外层代码块包住整份文档。"""
    eng = _engine()
    MockStore.set_kind_delivery("acceptance", {"acceptance": "验收正文"})
    upstream_plan = "# 设计方案\n\n```ts\nexport const ok = true\n```\n\n## 下一节"

    res = run_task(
        eng,
        TaskKind.ACCEPTANCE,
        _payload(source_of_truth={"plan": upstream_plan}),
        "alice",
        poll=_poll,
    )

    item = eng.store.get_work_item(res["item_id"])
    assert "### plan" in item.description
    assert "<details>" in item.description
    assert "<details open>" not in item.description
    assert "<summary>View upstream artifact: plan</summary>" in item.description
    assert "# 设计方案" in item.description
    assert "```ts\nexport const ok = true\n```" in item.description
    assert "## 下一节" in item.description
    assert "### plan\n````" not in item.description


def test_large_issue_backed_source_is_externalized_from_issue_body():
    """大型上游交付保留在源 issue 附件中，下游正文只放可校验读取入口。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    upstream = eng.store.create_work_item(
        "ws", "acceptance", "acceptance", dag_key="acceptance-p1",
        worker="alice", kind=TaskKind.ACCEPTANCE)
    large_acceptance = "schema: omac.acceptance/v1\n" + "x" * (70 * 1024)
    eng.store.update_work_item_metadata(
        upstream.id, deliverable=large_acceptance,
        phase=TaskPhase.CONFIRMATION)
    eng.store.update_status(upstream.id, WorkItemStatus.DONE)

    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.DECOMPOSE,
        title="decompose",
        dag_key="decompose-p1",
        assignee="bob",
        source_refs=[{"label": "acceptance", "issue_id": upstream.id}],
        source_of_truth={"acceptance": large_acceptance},
    ))

    assert large_acceptance not in item.description
    assert "Large upstream artifact omitted from the issue body" in item.description
    assert (
        f"omac work read {item.id} --source acceptance "
        "--output-file /tmp/omac-acceptance.yaml"
    ) in item.description
    assert item.source_refs[0]["content_externalized"] is True
    assert item.source_refs[0]["content_bytes"] == len(
        large_acceptance.encode("utf-8"))
    assert item.source_refs[0]["content_sha256"]


def test_resume_refreshes_unstarted_authoring_issue_in_place():
    """正文回填失败后的 resume 必须刷新原 issue，不能创建第二条。"""
    eng = _engine()
    item = eng.store.create_work_item(
        "ws", "decompose", "decompose", dag_key="decompose-p1",
        worker="bob", kind=TaskKind.DECOMPOSE)

    result = run_task(
        eng,
        TaskKind.DECOMPOSE,
        _payload(
            title="decompose",
            source_of_truth={"acceptance": "small acceptance"},
        ),
        "bob",
        source_refs=[{"label": "acceptance", "issue_id": "acceptance-1"}],
        poll=_poll,
        resume_item_id=item.id,
    )

    refreshed = eng.store.get_work_item(item.id)
    assert result["item_id"] == item.id
    assert len(eng.store.list_work_items("ws")) == 1
    assert "small acceptance" in refreshed.description
    assert refreshed.source_refs == [
        {"label": "acceptance", "issue_id": "acceptance-1"}
    ]


def test_resume_snapshot_shrinks_unreadable_issue_before_full_get():
    """巨型旧正文不可读时，先用 list 快照覆盖紧凑正文，再完整读取。"""
    eng = _engine()
    base_store = eng.store
    snapshot = base_store.create_work_item(
        "ws", "decompose", "oversized old body", dag_key="decompose-p1",
        worker="bob", kind=TaskKind.DECOMPOSE)

    class _UnreadableUntilRefreshed:
        def __init__(self, delegate):
            self.delegate = delegate
            self.config = delegate.config
            self.refreshed = False

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def get_work_item(self, item_id):
            if not self.refreshed:
                raise RuntimeError("old issue body is too large to read")
            return self.delegate.get_work_item(item_id)

        def update_work_item_metadata(self, item_id, **metadata):
            if metadata.get("description") is not None:
                self.refreshed = True
            return self.delegate.update_work_item_metadata(item_id, **metadata)

    eng.store = _UnreadableUntilRefreshed(base_store)

    result = run_task(
        eng,
        TaskKind.DECOMPOSE,
        _payload(
            title="decompose",
            source_of_truth={"acceptance": "small acceptance"},
        ),
        "bob",
        source_refs=[{"label": "acceptance", "issue_id": "acceptance-1"}],
        poll=_poll,
        resume_item_id=snapshot.id,
        resume_item_snapshot=snapshot,
    )

    assert result["item_id"] == snapshot.id
    assert eng.store.refreshed is True


def test_run_task_handoff_to_reviewer_does_not_post_trigger_comment():
    """正常转派 reviewer 只靠 assign + metadata 交接,不发评论触发第二次 run。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], poll=_poll)
    comments = eng.store.get_comments(res["item_id"])
    assert not any("阶段变更" in c and "omac work submit" in c for c in comments)


def test_run_task_pass_with_nits_re_reviews_changed_worker_followup():
    """pass-with-nits 后若交付变化，最终交付必须重新经过 reviewer。"""
    eng = _engine()
    MockStore.set_kind_delivery_sequence(
        "plan", [{"plan": "计划正文-v1"}, {"plan": "计划正文-v2"}])
    MockStore.set_review_verdict_sequence(["pass-with-nits", "pass"])

    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], poll=_poll)

    item = eng.store.get_work_item(res["item_id"])
    assert res["verdict"] == "pass"
    assert res["rounds"] == 2
    assert res["delivery"]["plan"] == "计划正文-v2"
    assert item.status == WorkItemStatus.DONE
    assert item.bounces.review == 1
    assert item.decision_required is None
    assert eng.store.get_comments(item.id) == []


def test_run_task_resume_confirmation_re_reviews_stale_subject():
    """confirmation 只能复用仍绑定当前交付的 verdict。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="feature-x",
        dag_key="plan-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="计划正文-v2",
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass-with-nits",
        review_bounce=1,
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = "stale-v1-review-subject"
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    def submit_fresh_review():
        current = eng.store.get_work_item(item.id)
        if current.phase == TaskPhase.REVIEW and current.review_verdict is None:
            eng.store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report=_review_report(item=current))

    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=submit_fresh_review,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 2
    assert result["delivery"]["plan"] == "计划正文-v2"


def test_run_task_resume_confirmation_consumes_subject_after_prior_bounces():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="feature-x",
        dag_key="plan-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="计划正文-v3",
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
        review_report=_review_report(),
        review_bounce=2,
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.PLAN, current, 3)
    current.agent_run_failed = True
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=lambda: pytest.fail("current confirmation must not redispatch"),
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 3
    assert eng.store.assign_log == []


def test_run_task_resume_confirmation_rejects_invalid_stored_review_evidence():
    """旧 CLI 遗留的无效 report 不得越过 Reviewer 证据门进入人工确认。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.ACCEPTANCE,
        title="acceptance document",
        dag_key="acceptance-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="当前验收正文",
        phase=TaskPhase.CONFIRMATION,
        review_verdict="pass",
        review_report={
            key: value
            for key, value in _review_report().items()
            if key != "full_review_completed"
        },
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.ACCEPTANCE, current, 1)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    def submit_valid_review_then_confirm():
        current = eng.store.get_work_item(item.id)
        if current.phase == TaskPhase.REVIEW and current.review_verdict is None:
            eng.store.update_work_item_metadata(
                item.id,
                review_verdict="pass",
                review_report=_review_report(item=current),
            )
            return
        if current.phase == TaskPhase.CONFIRMATION:
            if not current.review_report.get("full_review_completed"):
                pytest.fail("invalid stored review evidence reached human confirmation")
            eng.store.update_status(item.id, WorkItemStatus.DONE)

    result = run_task(
        eng,
        TaskKind.ACCEPTANCE,
        _payload(title="acceptance document"),
        "alice",
        reviewers=["bob"],
        confirm=True,
        poll=submit_valid_review_then_confirm,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert [entry[2] for entry in eng.store.assign_log] == ["reviewer"]
    assert eng.store.get_work_item(item.id).review_report[
        "full_review_completed"
    ] is True
    assert eng.store.get_work_item(item.id).review_comment == ""


def test_run_task_resume_done_pass_with_nits_is_terminal():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="feature-x",
        dag_key="plan-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="计划正文-v2",
        phase=TaskPhase.REVIEW,
        review_verdict="pass-with-nits",
    )
    eng.store.update_status(item.id, WorkItemStatus.DONE)

    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=lambda: pytest.fail("completed pass-with-nits item must not be polled"),
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass-with-nits"
    assert result["rounds"] == 0
    assert result["delivery"]["plan"] == "计划正文-v2"
    assert eng.store.get_work_item(item.id).status == WorkItemStatus.DONE


def test_run_task_reject_handoff_uses_metadata_not_comment():
    """reject 转回产出者只更新 metadata/status/assignee,不再用评论触发交接。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    MockStore.set_review_rejects(1)
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], max_revisions=3, poll=_poll)

    assert res["verdict"] == "pass"
    assert res["rounds"] == 2
    assert eng.store.get_comments(res["item_id"]) == []


def test_run_task_ignores_blank_review_verdict_while_waiting():
    """空 review_verdict 是 reset 后的未决态,不能当成 reject 触发返工。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = eng.store.create_work_item(
        "ws", "feature-x", "feature-x", dag_key="plan-p1",
        worker="alice", kind=TaskKind.PLAN)
    eng.store.update_work_item_metadata(
        item.id, deliverable="计划正文", phase=TaskPhase.REVIEW,
        review_verdict="")
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    calls = {"n": 0}

    def poll_until_valid_verdict():
        calls["n"] += 1
        if calls["n"] == 1:
            eng.store.update_work_item_metadata(
                item.id, review_verdict="pass",
                review_report=_review_report(item=eng.store.get_work_item(item.id)))
        if calls["n"] > 3:
            raise TimeoutError("blank verdict was treated as terminal")

    res = run_task(
        eng, TaskKind.PLAN, _payload(), "alice",
        reviewers=["bob"], poll=poll_until_valid_verdict,
        resume_item_id=item.id,
    )

    assert res["verdict"] == "pass"
    assert "verdict=reject" not in "\n".join(eng.store.get_comments(item.id))


def test_human_gate_blocks_until_confirmed():
    """Reviewer 通过后才进入 confirmation；无人确认时保持等待。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    calls = {"n": 0}

    def bounded_poll():
        calls["n"] += 1
        if calls["n"] > 5:
            raise TimeoutError("人机门未通过")

    with pytest.raises(TimeoutError):
        run_task(eng, TaskKind.PLAN, _payload(), "alice",
                 reviewers=["bob"], confirm=True, poll=bounded_poll)
    item = eng.store.list_work_items("ws")[0]
    assert item.review_verdict == "pass"
    assert item.reviewer is None
    assert item.phase.value == "confirmation"


def test_human_gate_passes_when_confirmed_to_done():
    """confirm=True:Reviewer 通过后，人工确认才把 issue 流转到 DONE。"""
    eng = _engine()
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], confirm=True, poll=_poll)
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    assert item.review_verdict == "pass"
    assert item.phase.value == "confirmation"


def test_human_gate_persists_confirmation_after_clearing_assignment(monkeypatch):
    """unassign 可能触发平台状态写；confirmation 必须作为最后一次阶段写入。"""
    eng = _engine()
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    events = []
    original_update = eng.store.update_work_item_metadata
    original_clear = eng.store.clear_assignment

    def tracking_update(item_id, **kwargs):
        if kwargs.get("phase") == TaskPhase.CONFIRMATION:
            events.append("phase")
        return original_update(item_id, **kwargs)

    def tracking_clear(item_id):
        events.append("clear")
        return original_clear(item_id)

    monkeypatch.setattr(eng.store, "update_work_item_metadata", tracking_update)
    monkeypatch.setattr(eng.store, "clear_assignment", tracking_clear)

    run_task(
        eng, TaskKind.PLAN, _payload(), "alice",
        reviewers=["bob"], confirm=True, poll=_poll)

    assert events[-2:] == ["clear", "phase"]


def test_human_gate_no_reviewers_stops_at_human_done():
    """confirm=True 且无 reviewer:人工确认(DONE)即终态,不再另跑评审。"""
    eng = _engine()
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   confirm=True, poll=_poll)
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE


def test_no_confirm_skips_human_gate():
    """confirm=False(默认):不等人工 DONE,产出后直接进评审。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], confirm=False, poll=_poll)
    assert res["verdict"] == "pass"


def test_reject_twice_then_pass():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    MockStore.set_review_rejects(2)
    res = run_task(
        eng, TaskKind.PLAN, _payload(), "alice",
        reviewers=["bob"], max_revisions=3, poll=_poll)
    assert res["delivery"] == {"plan": "计划正文"}
    assert res["rounds"] == 3  # 2 次 reject + 1 次 pass
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE
    assert item.bounces.review == 2
    # 全程同一 issue id,未新建评审 issue
    assert len(eng.store.list_work_items("ws")) == 1
    assert item.id == res["item_id"]
    # reject 返工不再通过评论交接,避免评论本身再次触发 agent run。
    assert eng.store.get_comments(res["item_id"]) == []


def test_amendment_review_budget_exhaustion_projects_decision_and_resumes():
    eng = _engine()
    MockStore.set_kind_delivery("amendment", {"amendment": "schema: omac.dag-amendment/v1"})
    MockStore.set_review_rejects(99)  # 永远 reject
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.AMENDMENT, _payload(), "alice",
                 reviewers=["bob"], max_revisions=3, poll=_poll)
    report = exc.value.report
    assert report["rounds"] == 3
    assert report["last_opinion"]
    assert report["item_id"]
    assert report["kind"] == "amendment"
    assert report["phase"] == "review"
    assert report["gate"] == "review"
    assert report["resume_issue_id"] == report["item_id"]
    # 全程同一 issue id
    assert len(eng.store.list_work_items("ws")) == 1
    item = eng.store.list_work_items("ws")[0]
    assert report["item_id"] == item.id
    assert item.status == WorkItemStatus.BLOCKED
    assert item.phase == TaskPhase.REVIEW
    assert item.decision_required == {
        "schema": "omac.decision-required/v1",
        "reason_code": "review-budget-exhausted",
        "kind": "amendment",
        "phase": "review",
        "gate": "review",
        "rounds": 3,
        "resume_issue_id": item.id,
        "review_ledger_ref": item.review_ledger_ref,
    }
    shown = build_show_output(item, "orchestrator:alice")
    assert shown["context"]["decision_required"] == item.decision_required

    MockStore.set_review_rejects(0)
    resumed = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(),
        "alice",
        reviewers=["bob"],
        max_revisions=4,
        poll=_poll,
        resume_item_id=item.id,
    )
    assert resumed["item_id"] == item.id
    assert resumed["verdict"] == "pass"
    assert eng.store.get_work_item(item.id).decision_required is None


def test_machine_gate_budget_exhaustion_projects_blocked_decision():
    eng = _engine()
    MockStore.set_kind_delivery(
        "amendment", {"amendment": "schema: omac.dag-amendment/v1"})

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(),
            "alice",
            reviewers=["bob"],
            max_revisions=1,
            guard=lambda _item: ["proposal is not executable"],
            poll=_poll,
        )

    item = eng.store.get_work_item(exc.value.report["item_id"])
    assert item.status == WorkItemStatus.BLOCKED
    assert item.phase == TaskPhase.REVIEW
    assert item.decision_required == {
        "schema": "omac.decision-required/v1",
        "reason_code": "guard-budget-exhausted",
        "kind": "amendment",
        "phase": "review",
        "gate": "guard",
        "rounds": 1,
        "resume_issue_id": item.id,
        "machine_feedback_ref": item.machine_feedback_ref,
    }


def _exhausted_decompose_review(
        eng, *, verdict="reject", bounce=8, continuation=None):
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.DECOMPOSE,
        title="decompose review",
        dag_key="decompose-p-review",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="meta:\n  name: review\nnodes: []\n",
        phase=TaskPhase.REVIEW,
        review_verdict=verdict or "",
        review_comment="budget exhausted",
        review_report=_review_report(verdict) if verdict else None,
        review_bounce=bounce,
    )
    current = eng.store.get_work_item(item.id)
    if continuation is not None:
        current.review_continuation = continuation
    if verdict == "reject" and bounce > 0:
        current.review_subject_digest = tasks_module._review_subject_digest(
            TaskKind.DECOMPOSE, current, bounce)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    return current


def test_legacy_config_budget_increase_reworks_rejected_delivery_before_review():
    """旧 config 提高上限仍兼容，但 reject 必须先回 producer，不能直接重审旧交付。"""
    eng = _engine()
    _exhausted_decompose_review(eng)
    MockStore.set_kind_delivery(
        "decompose", {"manifest": "meta:\n  name: revised\nnodes: []\n"})

    result = run_task(
        eng,
        TaskKind.DECOMPOSE,
        {"title": "decompose review"},
        "alice",
        reviewers=["bob"],
        max_revisions=9,
        poll=_poll,
        resume_item_id=eng.store.list_work_items("ws")[0].id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 9
    assert [entry[2] for entry in eng.store.assign_log] == ["worker", "reviewer"]


def test_persisted_review_continuation_survives_process_resume_for_reject():
    first = _engine(MOCK_AUTO_COMPLETE="false")
    continuation = {
        "schema": "omac.review-continuation/v1",
        "stage": "decompose",
        "mode": "producer-rework",
        "authorized_through_round": 9,
        "decision_count": 1,
        "reason": "operator approved one more round",
    }
    item = _exhausted_decompose_review(first, continuation=continuation)
    first.store.reset_review(item.id)
    first.store.update_status(item.id, WorkItemStatus.TODO)
    MockStore.set_kind_delivery(
        "decompose", {"manifest": "meta:\n  name: revised\nnodes: []\n"})

    resumed = _engine()
    result = run_task(
        resumed,
        TaskKind.DECOMPOSE,
        {"title": "decompose review"},
        "alice",
        reviewers=["bob"],
        max_revisions=8,
        poll=_poll,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 9
    assert [entry[2] for entry in resumed.store.assign_log] == ["worker", "reviewer"]


def test_persisted_review_continuation_reviews_final_nits_delivery_once():
    first = _engine(MOCK_AUTO_COMPLETE="false")
    continuation = {
        "schema": "omac.review-continuation/v1",
        "stage": "decompose",
        "mode": "review-only",
        "authorized_through_round": 9,
        "decision_count": 1,
        "reason": "operator approved one more round",
    }
    item = _exhausted_decompose_review(
        first, verdict=None, continuation=continuation)

    resumed = _engine()
    result = run_task(
        resumed,
        TaskKind.DECOMPOSE,
        {"title": "decompose review"},
        "alice",
        reviewers=["bob"],
        max_revisions=8,
        poll=_poll,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 9
    assert [entry[2] for entry in resumed.store.assign_log] == ["reviewer"]


def test_final_pass_with_nits_exhaustion_can_continue_one_review_round():
    from omac.pipeline.plan import plan_continue_review

    eng = _engine()
    MockStore.set_kind_delivery_sequence("decompose", [
        {"manifest": "meta:\n  name: first\nnodes: []\n"},
        {"manifest": "meta:\n  name: revised\nnodes: []\n"},
    ])
    MockStore.set_review_verdict_sequence(["pass-with-nits"])

    with pytest.raises(NeedsDecision):
        run_task(
            eng,
            TaskKind.DECOMPOSE,
            {"title": "decompose review"},
            "alice",
            reviewers=["bob"],
            max_revisions=1,
            poll=_poll,
            dag_key="decompose-p-final-nits",
        )

    item = eng.store.list_work_items("ws")[0]
    assert "revised" in item.deliverable
    assert item.review_verdict is None
    assert item.bounces.review == 1

    decision = plan_continue_review(
        eng, 1, dag_key=item.dag_key,
        reason="operator approved final nits recheck")
    assert decision["mode"] == "review-only"
    authorized = eng.store.get_work_item(item.id)
    assert authorized.status == WorkItemStatus.IN_REVIEW
    assert authorized.decision_required == {}
    MockStore.set_review_verdict("pass")

    resumed = _engine()
    result = run_task(
        resumed,
        TaskKind.DECOMPOSE,
        {"title": "decompose review"},
        "alice",
        reviewers=["bob"],
        max_revisions=1,
        poll=_poll,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert result["rounds"] == 2
    assert [entry[2] for entry in resumed.store.assign_log] == [
        "worker", "reviewer", "worker", "reviewer",
    ]


def test_consumed_review_continuation_does_not_reset_budget_again():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    continuation = {
        "schema": "omac.review-continuation/v1",
        "stage": "decompose",
        "mode": "producer-rework",
        "authorized_through_round": 9,
        "decision_count": 1,
        "reason": "operator approved one more round",
    }
    item = _exhausted_decompose_review(
        eng, bounce=9, continuation=continuation)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.DECOMPOSE,
            {"title": "decompose review"},
            "alice",
            reviewers=["bob"],
            max_revisions=8,
            poll=lambda: pytest.fail("consumed authorization must not dispatch"),
            resume_item_id=item.id,
        )

    assert exc.value.report["rounds"] == 9
    assert [entry[2] for entry in eng.store.assign_log] == []
    assert "omac plan continue-review --dag-key decompose-p-review" in str(exc.value)


def test_final_reject_does_not_start_unreviewed_producer_revision():
    """最后一轮 reject 后直接交人决策，不再启动一份无人评审的新产物。"""
    eng = _engine()
    MockStore.set_kind_delivery_sequence(
        "plan", [{"plan": "计划正文-v1"}, {"plan": "计划正文-v2"}])
    MockStore.set_review_rejects(99)

    with pytest.raises(NeedsDecision):
        run_task(
            eng, TaskKind.PLAN, _payload(), "alice",
            reviewers=["bob"], max_revisions=1, poll=_poll)

    item = eng.store.list_work_items("ws")[0]
    assert item.deliverable == "计划正文-v1"
    assert item.review_verdict == "reject"
    assert item.bounces.review == 1


def test_resume_uses_persisted_review_bounce_limit():
    """平台状态滞后为 in_progress 时也必须收割 verdict 和 review_bounce。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="feature-x",
        dag_key="plan-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="计划正文-v3",
        phase=TaskPhase.REVIEW,
        review_verdict="reject",
        review_comment="第三轮仍有 blocker",
        review_report=_review_report("reject"),
        review_bounce=2,
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.PLAN, current, 3)
    # Multica 在 Reviewer run 完成后可能仍保留 in_progress；持久化 verdict、
    # phase 和 subject digest 才是可恢复的业务事实。
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.PLAN,
            _payload(),
            "alice",
            reviewers=["bob", "charlie"],
            max_revisions=3,
            poll=lambda: pytest.fail("final reject must not start a producer"),
            resume_item_id=item.id,
        )

    got = eng.store.get_work_item(item.id)
    assert exc.value.report["rounds"] == 3
    assert got.bounces.review == 3
    assert got.review_verdict == "reject"
    assert got.phase == TaskPhase.REVIEW


def test_resume_invalidates_verdict_from_an_unbound_review_subject():
    """旧 CLI 遗留的 verdict 未绑定当前交付时，不得被新评审轮次消费。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.ACCEPTANCE,
        title="acceptance document",
        dag_key="acceptance-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="当前验收正文",
        phase=TaskPhase.REVIEW,
        review_verdict="pass-with-nits",
        review_comment="旧交付的建议",
    )
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    def submit_fresh_review():
        current = eng.store.get_work_item(item.id)
        assert current.phase == TaskPhase.REVIEW
        assert current.review_verdict is None
        assert current.review_subject_digest
        eng.store.update_work_item_metadata(
            item.id, review_verdict="pass",
            review_report=_review_report(item=current))

    result = run_task(
        eng,
        TaskKind.ACCEPTANCE,
        _payload(title="acceptance document"),
        "alice",
        reviewers=["bob"],
        poll=submit_fresh_review,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert [entry[2] for entry in eng.store.assign_log] == ["reviewer"]


def test_resume_consumes_verdict_bound_to_current_review_subject():
    """进程重启后，已绑定当前交付的 verdict 应直接收割，不得重复派 Reviewer。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="feature-x",
        dag_key="plan-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="计划正文",
        phase=TaskPhase.REVIEW,
        review_verdict="pass",
        review_report=_review_report(),
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.PLAN, current, 1)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=lambda: pytest.fail("bound verdict must not be redispatched"),
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert eng.store.assign_log == []


def test_resume_rejects_invalid_verdict_bound_to_current_review_subject():
    """review 阶段收割旧 verdict 时也必须重新验证持久化 report。"""
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.ACCEPTANCE,
        title="acceptance document",
        dag_key="acceptance-p1",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="当前验收正文",
        phase=TaskPhase.REVIEW,
        review_verdict="pass",
        review_report={},
    )
    current = eng.store.get_work_item(item.id)
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.ACCEPTANCE, current, 1)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    def submit_valid_review():
        current = eng.store.get_work_item(item.id)
        if current.phase == TaskPhase.REVIEW and current.review_verdict is None:
            eng.store.update_work_item_metadata(
                item.id,
                review_verdict="pass",
                review_report=_review_report(item=current),
            )

    result = run_task(
        eng,
        TaskKind.ACCEPTANCE,
        _payload(title="acceptance document"),
        "alice",
        reviewers=["bob"],
        poll=submit_valid_review,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert [entry[2] for entry in eng.store.assign_log] == ["reviewer"]


def test_reviewer_rotation_avoids_producer():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["alice", "bob", "charlie"], poll=_poll)
    item = eng.store.get_work_item(res["item_id"])
    # reviewer ≠ producer (alice)
    assert item.reviewer in ("bob", "charlie")


def test_pick_reviewer_falls_back_to_self_when_only_producer():
    """池里仅产出者时回退自审(角色可自由指定),不再报错。"""
    from omac.pipeline.tasks import _pick_reviewer
    assert _pick_reviewer(["alice"], "alice", 0) == "alice"


def test_pick_reviewer_prefers_non_producer_when_available():
    """有非产出者时仍优先选非产出者(保留独立性)。"""
    from omac.pipeline.tasks import _pick_reviewer
    assert _pick_reviewer(["alice", "bob"], "alice", 0) == "bob"
    assert _pick_reviewer(["alice", "bob"], "alice", 1) == "bob"


def test_failure_in_production_short_circuits():
    eng = _engine()
    # 失败注入按 dag_key 命中;显式 key 覆盖自动生成路径。
    MockStore.set_fail_keys({"plan"})
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.PLAN, _payload(), "alice",
                 poll=_poll, dag_key="plan")
    assert exc.value.report["rounds"] == 0
    assert "producer failed" in exc.value.report["last_opinion"]


def test_historical_amendment_review_obligation_uses_reviewed_apply_evidence():
    eng = _engine()
    historical_item = eng.store.create_work_item(
        "ws", "historical", "completed delivery", "historical-node", "alice",
        reviewer="bob",
    )
    contract = Contract(
        objective="historical delivery",
        source_of_truth=["docs/design.md"],
        acceptance=["UJ-HISTORICAL-001"],
        non_goals=["do not replay the completed delivery"],
        verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "historical-gate",
            "layer": "L1",
            "delivery_goal": "preserve the completed delivery",
            "source_of_truth": ["docs/design.md"],
            "covers": ["historical-node"],
            "acceptance_refs": ["UJ-HISTORICAL-001"],
            "commands": ["pytest -q"],
        }],
        pr_base="main",
    )
    eng.store.set_node_contract(historical_item.id, contract)
    eng.store.update_work_item_metadata(
        historical_item.id,
        artifacts={"pr_url": "https://example.test/pr/1"},
        verification={"subject_digest": "verification-1"},
        review_verdict="pass",
        review_report={"blockers": []},
        review_subject_digest="review-subject-1",
        review_ledger={"schema": "omac.review-ledger/v1", "rounds": []},
    )
    eng.store.update_status(historical_item.id, WorkItemStatus.DONE)
    manifest = Manifest(meta={}, nodes={
        "historical-node": Node(
            id="historical-node",
            worker="alice",
            reviewer="bob",
            contract=contract,
            work_item_id=historical_item.id,
            status="done",
            merged=True,
        ),
    })
    proposal = {
        "schema": "omac.dag-amendment/v1",
        "reason": "correct historical acceptance responsibility",
        "operations": [{
            "op": "update-responsibility",
            "node": "historical-node",
            "acceptance_claims": ["UJ-HISTORICAL-001"],
            "acceptance_contributions": [{
                "flow_id": "UJ-HISTORICAL-001",
                "action_ids": ["ACT-HISTORICAL-001"],
            }],
            "acceptance_refs": ["UJ-HISTORICAL-001"],
            "clear_legacy_acceptance": True,
            "integration_gate_responsibility_patches": [{
                "name": "historical-gate",
                "acceptance_refs": ["UJ-HISTORICAL-001"],
            }],
            "historical_contract_correction": True,
            "reason": "correct metadata without replaying the completed delivery",
        }],
    }
    acceptance = load_acceptance_doc({
        "schema": "omac.acceptance/v2",
        "flows": [{
            "id": "UJ-HISTORICAL-001",
            "name": "historical flow",
            "actions": [{
                "id": "ACT-HISTORICAL-001",
                "kind": "business-action",
                "step": "complete the historical action",
                "how": "use the existing delivery",
                "expected": "the responsibility metadata is corrected",
            }],
        }],
    })
    MockStore.set_kind_delivery(
        "amendment", {"amendment": yaml.safe_dump(proposal, sort_keys=False)})

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="historical amendment"),
        "charlie",
        reviewers=["bob"],
        poll=_poll,
        review_acceptance_doc=acceptance,
        review_amendment_manifest=manifest,
    )

    amendment_item = eng.store.get_work_item(result["item_id"])
    obligation = next(
        entry for entry in amendment_item.review_obligations
        if entry["obligation_id"] == "acceptance-responsibility:amendment-matrix"
    )
    obligation_digest = obligation["historical_contract_corrections"][0][
        "evidence_sha256"
    ]
    expected_digest = historical_work_item_evidence_digest(
        eng.store.get_work_item(historical_item.id))
    reviewed = build_reviewed_amendment(
        manifest,
        proposal,
        eng.store,
        issue_id=amendment_item.id,
        reviewer_verdict="pass",
        acceptance=acceptance,
    )

    assert obligation_digest == expected_digest
    assert reviewed["base"]["evidence_sha256"]["historical-node"] == expected_digest
    assert reviewed["analysis"]["historical_contract_corrections"][0][
        "evidence_sha256"
    ] == expected_digest


def test_explicit_resume_failed_authoring_reruns_same_item_once(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-demo",
        assignee="alice",
        contract=_payload()["contract"],
    ))
    eng.store.update_work_item_metadata(item.id, review_bounce=2)
    eng.store.update_status(item.id, WorkItemStatus.FAILED)
    before = eng.store.get_work_item(item.id)
    contract_before = before.contract
    contract_ref_before = before.contract_ref
    wakes = []

    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: False)

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_work_item_metadata(
            item_id,
            deliverable="schema: omac.dag-amendment/v1\nreason: fixed\noperations: []\n",
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(item_id, WorkItemStatus.IN_REVIEW)

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        poll=_poll,
        resume_item_id=item.id,
    )

    assert result["item_id"] == item.id
    assert len(eng.store.list_work_items("ws")) == 1
    assert wakes == [(item.id, "alice", "worker")]
    resumed = eng.store.get_work_item(item.id)
    assert resumed.contract == contract_before
    assert resumed.contract_ref == contract_ref_before
    assert resumed.bounces.review == 2


def test_explicit_resume_completed_without_submit_reruns_once(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="plan",
        dag_key="plan-resume",
        assignee="alice",
    ))
    item.agent_run_finished_without_submit = True
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    wakes = []

    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: False)

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_work_item_metadata(
            item_id, deliverable="plan body", phase=TaskPhase.REVIEW)
        eng.store.update_status(item_id, WorkItemStatus.IN_REVIEW)

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng, TaskKind.PLAN, _payload(), "alice", poll=_poll,
        resume_item_id=item.id,
    )

    assert result["item_id"] == item.id
    assert wakes == [(item.id, "alice", "worker")]


def test_explicit_resume_does_not_duplicate_active_authoring_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-active",
        assignee="alice",
    ))
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: True)
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("active run must not be woken again"),
    )

    def finish_existing_run():
        eng.store.update_work_item_metadata(
            item.id,
            deliverable="schema: omac.dag-amendment/v1\nreason: fixed\noperations: []\n",
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        poll=finish_existing_run,
        resume_item_id=item.id,
    )

    assert result["item_id"] == item.id
    assert eng.store.assign_log == []


def test_explicit_resume_waits_for_active_failure_then_reruns_once(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-active-failure",
        assignee="alice",
    ))
    eng.store.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    wakes = []

    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: True)

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_work_item_metadata(
            item_id,
            deliverable="schema: omac.dag-amendment/v1\nreason: fixed\noperations: []\n",
            phase=TaskPhase.REVIEW,
        )
        eng.store.update_status(item_id, WorkItemStatus.IN_REVIEW)

    monkeypatch.setattr(eng.runtime, "wake", wake)

    def fail_existing_run():
        eng.store.update_status(item.id, WorkItemStatus.FAILED)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        poll=fail_existing_run,
        resume_item_id=item.id,
    )

    assert result["item_id"] == item.id
    assert wakes == [(item.id, "alice", "worker")]


def test_explicit_resume_failed_authoring_stops_after_one_real_retry(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-provider-failure",
        assignee="alice",
    ))
    eng.store.update_status(item.id, WorkItemStatus.FAILED)
    wakes = []
    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: False)

    def fail_again(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_status(item_id, WorkItemStatus.FAILED)

    monkeypatch.setattr(eng.runtime, "wake", fail_again)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            poll=_poll,
            resume_item_id=item.id,
        )

    assert exc.value.report["last_opinion"] == "producer failed"
    assert wakes == [(item.id, "alice", "worker")]


def test_explicit_resume_failed_review_stays_with_reviewer(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.PLAN,
        title="plan",
        dag_key="plan-review-resume",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id, deliverable="plan body", phase=TaskPhase.REVIEW)
    eng.store.update_status(item.id, WorkItemStatus.FAILED)
    item.agent_run_failed = True
    wakes = []

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        current = eng.store.get_work_item(item_id)
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict="pass",
            review_report=_review_report(item=current),
        )

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.PLAN,
        _payload(),
        "alice",
        reviewers=["bob"],
        poll=_poll,
        resume_item_id=item.id,
    )

    assert result["verdict"] == "pass"
    assert wakes == [(item.id, "bob", "reviewer")]


def test_explicit_resume_confirmation_failure_never_reruns_agent(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-confirmation",
        assignee="alice",
    ))
    eng.store.update_work_item_metadata(
        item.id, deliverable="amendment body", phase=TaskPhase.CONFIRMATION)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    item.agent_run_failed = True
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("confirmation must never rerun an agent"),
    )

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=_poll,
            resume_item_id=item.id,
        )

    assert exc.value.report["phase"] == "confirmation"


def _confirmed_amendment(eng):
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-restart",
        assignee="alice",
        contract=_payload()["contract"],
    ))
    eng.store.update_work_item_metadata(
        item.id,
        deliverable="old amendment",
        review_report=_review_report(),
        review_ledger={"schema": "omac.review-ledger/v1", "blockers": []},
        review_verdict="pass",
        phase=TaskPhase.CONFIRMATION,
    )
    current = eng.store.get_work_item(item.id)
    current.deliverable_ref = {"sha256": "old-delivery"}
    current.review_report_ref = {"sha256": "old-report"}
    current.review_ledger_ref = {"sha256": "old-ledger"}
    current.decision_required = {"reason_code": "old-decision"}
    current.machine_feedback = {"schema": "omac.machine-feedback/v1"}
    current.machine_feedback_ref = {"sha256": "old-feedback"}
    current.review_subject_digest = tasks_module._review_subject_digest(
        TaskKind.AMENDMENT, current, 1)
    eng.store.update_status(item.id, WorkItemStatus.IN_REVIEW)
    eng.store.add_comment(item.id, "historical reviewer evidence")
    return item


def test_restart_authoring_reuses_issue_and_runs_fresh_worker_review_cycle(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    wakes = []
    refreshes = []
    monkeypatch.setattr(eng.runtime, "is_active", lambda _item_id: False)
    original_refresh = tasks_module.refresh_authoring_task

    def count_refresh(*args, **kwargs):
        refreshes.append(args[1])
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(tasks_module, "refresh_authoring_task", count_refresh)

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        current = eng.store.get_work_item(item_id)
        if role == "worker":
            assert current.phase == TaskPhase.AUTHORING
            assert current.deliverable is None
            assert current.deliverable_ref is None
            assert current.review_verdict is None
            assert current.review_report_ref is None
            assert current.review_ledger_ref is None
            assert current.decision_required is None
            assert current.machine_feedback_ref is None
            eng.store.update_work_item_metadata(
                item_id,
                deliverable="fresh amendment",
                phase=TaskPhase.REVIEW,
            )
            fresh = eng.store.get_work_item(item_id)
            fresh.deliverable_ref = {"sha256": "fresh-delivery"}
            eng.store.update_status(item_id, WorkItemStatus.IN_REVIEW)
            return
        assert role == "reviewer"
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict="pass",
            review_report=_review_report(item=current),
        )

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(
            title="running DAG amendment",
            description="new report and docs inputs",
        ),
        "alice",
        reviewers=["bob"],
        confirm=True,
        pause_at_confirmation=True,
        poll=_poll,
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert result["delivery"]["amendment"] == "fresh amendment"
    assert wakes == [
        (item.id, "alice", "worker"),
        (item.id, "bob", "reviewer"),
    ]
    current = eng.store.get_work_item(item.id)
    assert current.phase == TaskPhase.CONFIRMATION
    assert current.deliverable_ref == {"sha256": "fresh-delivery"}
    assert "new report and docs inputs" in current.description
    assert eng.store.get_comments(item.id) == ["historical reviewer evidence"]
    assert len(eng.store.list_work_items("ws")) == 1
    assert refreshes == [item.id]


def test_restart_reviewer_reject_dispatches_fresh_worker_generation_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    wakes = []
    worker_round = {"value": 0}
    reviewer_round = {"value": 0}
    runs = []
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))

    def wake(item_id, agent, role):
        wakes.append((agent, role))
        current = eng.store.get_work_item(item_id)
        if role == "worker":
            worker_round["value"] += 1
            round_no = worker_round["value"]
            eng.store.update_work_item_metadata(
                item_id,
                deliverable=f"fresh amendment {round_no}",
                phase=TaskPhase.REVIEW,
            )
            current.deliverable_ref = {
                "attachment_id": f"worker-delivery-{round_no}",
                "sha256": f"worker-digest-{round_no}",
            }
            eng.store.mark_in_review(item_id)
            runs.append(AgentRunObservation(
                id=f"worker-run-{round_no}", kind="direct", status="completed"))
            return
        reviewer_round["value"] += 1
        verdict = "reject" if reviewer_round["value"] == 1 else "pass"
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict=verdict,
            review_report=_review_report(item=current, verdict=verdict),
        )
        runs.append(AgentRunObservation(
            id=f"reviewer-run-{reviewer_round['value']}",
            kind="direct",
            status="completed",
        ))

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        reviewers=["bob"],
        max_revisions=3,
        confirm=True,
        pause_at_confirmation=True,
        poll=_poll,
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert wakes == [
        ("alice", "worker"),
        ("bob", "reviewer"),
        ("alice", "worker"),
        ("bob", "reviewer"),
    ]
    assert eng.store.get_work_item(item.id).authoring_restart.state == "confirmation"


def test_restart_authoring_refuses_active_run_without_cancel_or_wake(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    monkeypatch.setattr(
        eng.runtime,
        "list_runs",
        lambda _item_id: [SimpleNamespace(
            id="active-run", kind="direct", status="running", active=True,
        )],
    )
    monkeypatch.setattr(
        eng.runtime, "cancel",
        lambda *_args: pytest.fail("restart must never cancel an active run"),
    )
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("restart must not wake while a run is active"),
    )

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=_poll,
            resume_item_id=item.id,
            restart_authoring=True,
        )

    assert exc.value.report["reason_code"] == "active-agent-run"
    assert eng.store.get_work_item(item.id).phase == TaskPhase.CONFIRMATION


@pytest.mark.parametrize("kind", ["comment", "indirect"])
def test_restart_authoring_refuses_active_non_direct_run(monkeypatch, kind):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    monkeypatch.setattr(
        eng.runtime,
        "list_runs",
        lambda _item_id: [AgentRunObservation(
            id=f"active-{kind}", kind=kind, status="running")],
    )
    monkeypatch.setattr(
        eng.runtime, "wake",
        lambda *_args: pytest.fail("active non-direct run must fail closed"),
    )

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=_poll,
            resume_item_id=item.id,
            restart_authoring=True,
        )

    assert exc.value.report["reason_code"] == "active-agent-run"


def test_restart_authoring_completed_without_submit_converges_to_decision(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    wakes = []
    completed = {"value": False}
    monkeypatch.setattr(
        eng.runtime,
        "list_runs",
        lambda _item_id: ([AgentRunObservation(
            id="generation-worker-run", kind="direct", status="completed",
        )] if completed["value"] else []),
    )

    def complete_without_submit(item_id, agent, role):
        wakes.append((item_id, agent, role))
        completed["value"] = True

    monkeypatch.setattr(eng.runtime, "wake", complete_without_submit)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=_poll,
            resume_item_id=item.id,
            restart_authoring=True,
        )

    assert exc.value.report["reason_code"] == "completed-without-submit"
    assert wakes == [(item.id, "alice", "worker")]
    current = eng.store.get_work_item(item.id)
    assert current.status == WorkItemStatus.BLOCKED
    assert current.decision_required["reason_code"] == "completed-without-submit"


def test_restart_authoring_store_transition_is_restart_safe_and_preserves_history():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    claim = eng.store.claim_authoring_restart(
        item.id,
        _restart_candidate(item, generation="generation-idempotent"),
        now=100.0,
    ).restart

    first = eng.store.restart_authoring(
        item.id, generation=claim.generation, owner_nonce=claim.owner_nonce)
    second = eng.store.restart_authoring(
        item.id, generation=claim.generation, owner_nonce=claim.owner_nonce)

    assert first.id == second.id == item.id
    assert second.phase == TaskPhase.AUTHORING
    assert second.status == WorkItemStatus.TODO
    assert second.deliverable is None
    assert eng.store.get_comments(item.id) == ["historical reviewer evidence"]


def test_restart_authoring_finishes_a_partially_cleared_confirmation():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    claim = eng.store.claim_authoring_restart(
        item.id,
        _restart_candidate(item, generation="generation-partial"),
        now=100.0,
    ).restart
    current = eng.store.get_work_item(item.id)
    current.deliverable = None
    current.deliverable_ref = None
    current.review_verdict = None
    current.review_subject_digest = None

    restarted = eng.store.restart_authoring(
        item.id, generation=claim.generation, owner_nonce=claim.owner_nonce)

    assert restarted.phase == TaskPhase.AUTHORING
    assert restarted.status == WorkItemStatus.TODO
    assert restarted.review_report_ref is None
    assert restarted.review_ledger_ref is None
    assert restarted.machine_feedback_ref is None


def test_restart_recovers_after_cleanup_before_journal_advance():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    claim = eng.store.claim_authoring_restart(
        item.id,
        _restart_candidate(item, generation="generation-cleanup-crash"),
        now=100.0,
    ).restart
    current = eng.store.get_work_item(item.id)
    current.deliverable = None
    current.deliverable_ref = None
    current.review_verdict = None
    current.review_report_ref = None
    current.review_ledger_ref = None
    current.review_subject_digest = None
    current.decision_required = None
    current.machine_feedback_ref = None
    current.phase = TaskPhase.AUTHORING
    current.status = WorkItemStatus.IN_PROGRESS

    recovered = eng.store.restart_authoring(
        item.id, generation=claim.generation, owner_nonce=claim.owner_nonce)

    assert recovered.status == WorkItemStatus.TODO
    assert recovered.authoring_restart.state == "cleaned"


def _restart_candidate(item, *, generation, owner="owner-1", now=100.0):
    return RestartState(
        generation=generation,
        owner_nonce=owner,
        request_digest="request-v2",
        state="claimed",
        base_kind="amendment",
        base_phase="confirmation",
        base_status="in_review",
        base_review_subject_digest=item.review_subject_digest or "subject",
        base_deliverable_identity=deliverable_identity(item),
        baseline_run_ids=("historical-run",),
        lease_expires_at=now + 90,
    )


def test_restart_claim_has_one_winner_under_two_concurrent_callers():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    barrier = threading.Barrier(2)
    results = []

    def compete(generation):
        barrier.wait()
        results.append(eng.store.claim_authoring_restart(
            item.id,
            _restart_candidate(item, generation=generation),
            now=100.0,
        ))

    threads = [
        threading.Thread(target=compete, args=("generation-a",)),
        threading.Thread(target=compete, args=("generation-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result.acquired for result in results) == 1
    assert len({result.restart.generation for result in results}) == 1


def test_restart_claim_rejects_done_confirmation():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    eng.store.mark_done(item.id)

    with pytest.raises(NeedsDecision) as exc:
        eng.store.claim_authoring_restart(
            item.id,
            _restart_candidate(item, generation="generation-done"),
            now=100.0,
        )

    assert exc.value.report["reason_code"] == "restart-base-mismatch"


def test_restart_takeover_resumes_same_generation_after_expired_owner():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    first = _restart_candidate(
        item, generation="generation-stable", owner="owner-old", now=0.0)
    claimed = eng.store.claim_authoring_restart(item.id, first, now=0.0)
    assert claimed.acquired is True

    takeover = _restart_candidate(
        item, generation="ignored-new-generation", owner="owner-new", now=200.0)
    resumed = eng.store.claim_authoring_restart(item.id, takeover, now=200.0)

    assert resumed.acquired is True
    assert resumed.resumed is True
    assert resumed.restart.generation == "generation-stable"
    assert resumed.restart.owner_nonce == "owner-new"


def test_restart_terminal_needs_decision_cannot_be_reclaimed_after_lease():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    first = _restart_candidate(
        item, generation="generation-terminal", owner="owner-old", now=0.0)
    claimed = eng.store.claim_authoring_restart(item.id, first, now=0.0).restart
    terminal = claimed.evolve(
        state="needs_decision", detail="completed-without-submit",
        lease_expires_at=0.0,
    )
    eng.store.update_authoring_restart(
        item.id,
        generation=claimed.generation,
        owner_nonce=claimed.owner_nonce,
        state=terminal,
    )

    retry = _restart_candidate(
        item, generation="generation-new", owner="owner-new", now=200.0)
    result = eng.store.claim_authoring_restart(item.id, retry, now=200.0)

    assert result.acquired is False
    assert result.restart == terminal


def test_restart_resets_review_bounce_with_continuation():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    current = eng.store.get_work_item(item.id)
    current.bounces.review = 3
    current.review_continuation = {"authorized_through_round": 4}
    claim = eng.store.claim_authoring_restart(
        item.id,
        _restart_candidate(item, generation="generation-budget"),
        now=100.0,
    )

    restarted = eng.store.restart_authoring(
        item.id,
        generation=claim.restart.generation,
        owner_nonce=claim.restart.owner_nonce,
    )

    assert restarted.bounces.review == 0
    assert restarted.review_continuation is None


def test_restart_ignores_historical_completed_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    historical = AgentRunObservation(
        id="historical-completed", kind="direct", status="completed")
    runs = [historical]
    monkeypatch.setattr(eng.runtime, "list_runs", lambda _item_id: list(runs))

    def wake(item_id, agent, role):
        current = eng.store.get_work_item(item_id)
        if role == "worker":
            eng.store.update_work_item_metadata(
                item_id, deliverable="fresh amendment", phase=TaskPhase.REVIEW)
            eng.store.get_work_item(item_id).deliverable_ref = {
                "attachment_id": "fresh-worker-delivery",
                "sha256": "fresh-amendment",
            }
            eng.store.mark_in_review(item_id)
            runs.append(AgentRunObservation(
                id="fresh-worker-run", kind="direct", status="completed"))
        else:
            eng.store.update_work_item_metadata(
                item_id,
                review_verdict="pass",
                review_report=_review_report(item=current),
            )
            runs.append(AgentRunObservation(
                id="fresh-reviewer-run", kind="direct", status="completed"))

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        reviewers=["bob"],
        confirm=True,
        pause_at_confirmation=True,
        poll=_poll,
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True


def test_restart_waits_for_generation_run_visibility(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    dispatched = {"value": False, "reads": 0, "reviewer": False}

    def list_runs(_item_id):
        if not dispatched["value"]:
            return []
        dispatched["reads"] += 1
        if dispatched["reads"] <= 2:
            return []
        runs = [AgentRunObservation(
            id="fresh-visible-run", kind="direct", status="running")]
        if dispatched["reviewer"]:
            runs.append(AgentRunObservation(
                id="fresh-reviewer-run", kind="direct", status="running"))
        return runs

    monkeypatch.setattr(eng.runtime, "list_runs", list_runs)

    def wake(item_id, agent, role):
        current = eng.store.get_work_item(item_id)
        if role == "worker":
            dispatched["value"] = True
            return
        eng.store.update_work_item_metadata(
            item_id,
            review_verdict="pass",
            review_report=_review_report(item=current),
        )
        dispatched["reviewer"] = True

    monkeypatch.setattr(eng.runtime, "wake", wake)

    def poll():
        if dispatched["reads"] >= 3:
            current = eng.store.get_work_item(item.id)
            if current.phase == TaskPhase.AUTHORING:
                eng.store.update_work_item_metadata(
                    item.id,
                    deliverable="fresh amendment",
                    phase=TaskPhase.REVIEW,
                )
                eng.store.get_work_item(item.id).deliverable_ref = {
                    "attachment_id": "fresh-visible-delivery",
                    "sha256": "fresh-amendment",
                }
                eng.store.mark_in_review(item.id)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        _payload(title="running DAG amendment"),
        "alice",
        reviewers=["bob"],
        confirm=True,
        pause_at_confirmation=True,
        poll=poll,
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert dispatched["reads"] >= 3


def test_restart_reviewer_completed_without_verdict_is_bounded(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    stage = {"value": "initial"}

    def list_runs(_item_id):
        if stage["value"] == "initial":
            return []
        worker = AgentRunObservation(
            id="worker-run", kind="direct", status="completed")
        if stage["value"] == "worker-submitted":
            return [worker]
        return [worker, AgentRunObservation(
            id="reviewer-run", kind="direct", status="completed")]

    monkeypatch.setattr(eng.runtime, "list_runs", list_runs)

    def wake(item_id, agent, role):
        if role == "worker":
            eng.store.update_work_item_metadata(
                item_id, deliverable="fresh amendment", phase=TaskPhase.REVIEW)
            eng.store.get_work_item(item_id).deliverable_ref = {
                "attachment_id": "fresh-review-delivery",
                "sha256": "fresh-amendment",
            }
            eng.store.mark_in_review(item_id)
            stage["value"] = "worker-submitted"
        else:
            stage["value"] = "reviewer-completed"

    monkeypatch.setattr(eng.runtime, "wake", wake)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.AMENDMENT,
            _payload(title="running DAG amendment"),
            "alice",
            reviewers=["bob"],
            confirm=True,
            pause_at_confirmation=True,
            poll=_poll,
            resume_item_id=item.id,
            restart_authoring=True,
        )

    assert exc.value.report["reason_code"] == "reviewer-completed-without-verdict"
    assert exc.value.report["phase"] == "review"
    current = eng.store.get_work_item(item.id)
    assert current.phase == TaskPhase.REVIEW
    assert current.authoring_restart.state == "needs_decision"


def _prepare_dispatching_restart(eng, item, *, assigned: bool):
    payload = _payload(title="running DAG amendment")
    spec = AuthoringTaskSpec(
        kind=TaskKind.AMENDMENT,
        title="running DAG amendment",
        dag_key="amend-recovery",
        assignee="alice",
        description="",
        contract=payload["contract"],
    )
    candidate = _restart_candidate(
        item, generation="generation-recovery", owner="owner-crashed", now=0.0)
    candidate = candidate.evolve(
        request_digest=tasks_module._restart_request_digest(spec),
        lease_expires_at=0.0,
    )
    claim = eng.store.claim_authoring_restart(item.id, candidate, now=0.0).restart
    eng.store.restart_authoring(
        item.id, generation=claim.generation, owner_nonce=claim.owner_nonce)
    cleaned = eng.store.get_work_item(item.id).authoring_restart
    tasks_module.refresh_authoring_task(
        eng, item.id, spec, item_snapshot=item, restart=cleaned)
    dispatching = cleaned.evolve(state="dispatching", lease_expires_at=0.0)
    eng.store.update_authoring_restart(
        item.id,
        generation=cleaned.generation,
        owner_nonce=cleaned.owner_nonce,
        state=dispatching,
    )
    if assigned:
        eng.store.mark_in_progress(item.id)
        eng.store.assign_work_item(item.id, "alice", "worker")
    return payload, dispatching


def test_restart_recovers_crash_after_assign_before_wake_without_parallel_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    payload, restart = _prepare_dispatching_restart(eng, item, assigned=True)
    existing_runs = eng.runtime.list_runs(item.id)
    wakes = []

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_work_item_metadata(
            item_id, deliverable="fresh amendment", phase=TaskPhase.REVIEW)
        eng.store.get_work_item(item_id).deliverable_ref = {
            "attachment_id": "fresh-recovered-delivery",
            "sha256": "fresh-amendment",
        }
        eng.store.mark_in_review(item_id)

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        payload,
        "alice",
        confirm=True,
        pause_at_confirmation=True,
        poll=lambda: (
            eng.store.update_work_item_metadata(
                item.id, deliverable="fresh amendment", phase=TaskPhase.REVIEW),
            setattr(eng.store.get_work_item(item.id), "deliverable_ref", {
                "attachment_id": "fresh-recovered-delivery",
                "sha256": "fresh-amendment",
            }),
            eng.store.mark_in_review(item.id),
        ),
        dag_key="amend-recovery",
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert wakes == []
    assert eng.store.get_work_item(item.id).authoring_restart.generation == restart.generation
    assert len(eng.runtime.list_runs(item.id)) == len(existing_runs)


def test_restart_recovers_crash_after_wake_on_same_visible_run(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    payload, restart = _prepare_dispatching_restart(eng, item, assigned=True)
    run_ids_before = [run.id for run in eng.runtime.list_runs(item.id)]
    assignments_before = len(eng.store.assign_log)

    def poll():
        current = eng.store.get_work_item(item.id)
        if current.phase != TaskPhase.AUTHORING:
            return
        eng.store.update_work_item_metadata(
            item.id, deliverable="fresh amendment", phase=TaskPhase.REVIEW)
        current.deliverable_ref = {
            "attachment_id": "fresh-after-wake-delivery",
            "sha256": "fresh-amendment",
        }
        eng.store.mark_in_review(item.id)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        payload,
        "alice",
        confirm=True,
        pause_at_confirmation=True,
        poll=poll,
        dag_key="amend-recovery",
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert eng.store.get_work_item(item.id).authoring_restart.generation == restart.generation
    assert [run.id for run in eng.runtime.list_runs(item.id)] == run_ids_before
    assert len(eng.store.assign_log) == assignments_before


def test_restart_recovers_crash_before_assign_on_same_generation(monkeypatch):
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = _confirmed_amendment(eng)
    payload, restart = _prepare_dispatching_restart(eng, item, assigned=False)
    wakes = []

    def wake(item_id, agent, role):
        wakes.append((item_id, agent, role))
        eng.store.update_work_item_metadata(
            item_id, deliverable="fresh amendment", phase=TaskPhase.REVIEW)
        eng.store.get_work_item(item_id).deliverable_ref = {
            "attachment_id": "fresh-before-assign-delivery",
            "sha256": "fresh-amendment",
        }
        eng.store.mark_in_review(item_id)

    monkeypatch.setattr(eng.runtime, "wake", wake)

    result = run_task(
        eng,
        TaskKind.AMENDMENT,
        payload,
        "alice",
        confirm=True,
        pause_at_confirmation=True,
        poll=_poll,
        dag_key="amend-recovery",
        resume_item_id=item.id,
        restart_authoring=True,
    )

    assert result["pending_confirmation"] is True
    assert wakes == [(item.id, "alice", "worker")]
    assert eng.store.get_work_item(item.id).authoring_restart.generation == restart.generation


def test_blocked_production_short_circuits_on_resume():
    eng = _engine(MOCK_AUTO_COMPLETE="false")
    item = create_authoring_task(eng, AuthoringTaskSpec(
        kind=TaskKind.ACCEPTANCE,
        title="acceptance document",
        dag_key="acceptance-p1",
        assignee="alice",
    ))
    eng.store.update_status(item.id, WorkItemStatus.BLOCKED)

    with pytest.raises(NeedsDecision) as exc:
        run_task(
            eng,
            TaskKind.ACCEPTANCE,
            _payload(title="acceptance document"),
            "alice",
            poll=lambda: pytest.fail("blocked item must not be polled"),
            resume_item_id=item.id,
        )

    assert exc.value.report["rounds"] == 0
    assert "producer blocked" in exc.value.report["last_opinion"]
