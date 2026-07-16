"""P3.1:run_task 确定性原语 —— 派任务→等终态→取交付→有界修订循环。

验收标准:
- mock:一次过 / reject 2 次后过 / 耗尽 NeedsDecision 三条路径单测
- 全程同一 issue id(不新建评审 issue)
- issue body 取自 dispatch.render_issue_body(Human-first + 单一 Agent 入口模板)
"""
from __future__ import annotations

import pytest

import omac.pipeline.tasks as tasks_module
from omac.core.manifest import Contract
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig, WorkItemStatus
from omac.errors import NeedsDecision
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


def test_run_task_handoff_to_reviewer_does_not_post_trigger_comment():
    """正常转派 reviewer 只靠 assign + metadata 交接,不发评论触发第二次 run。"""
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], poll=_poll)
    comments = eng.store.get_comments(res["item_id"])
    assert not any("阶段变更" in c and "omac work submit" in c for c in comments)


def test_run_task_pass_with_nits_accepts_worker_followup_without_second_review():
    """pass-with-nits 转回产出者修完即收口,不再浪费第二轮 reviewer。"""
    eng = _engine()
    MockStore.set_kind_delivery_sequence(
        "plan", [{"plan": "计划正文-v1"}, {"plan": "计划正文-v2"}])
    MockStore.set_review_verdict_sequence(["pass-with-nits", "reject"])

    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], poll=_poll)

    item = eng.store.get_work_item(res["item_id"])
    assert res["verdict"] == "pass-with-nits"
    assert res["rounds"] == 1
    assert res["delivery"]["plan"] == "计划正文-v2"
    assert item.status == WorkItemStatus.DONE
    assert item.bounces.review == 0
    assert item.decision_required is None
    assert eng.store.get_comments(item.id) == []


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
            eng.store.update_work_item_metadata(item.id, review_verdict="pass")
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
    """confirm=True 且无人工确认时,人机门不放行:产出停在 IN_REVIEW 等 DONE。"""
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


def test_human_gate_passes_when_confirmed_to_done():
    """confirm=True:人工把 issue 流转到 DONE(auto_confirm 模拟)→ 翻回评审 → 通过。"""
    eng = _engine()
    MockStore.set_auto_confirm(True)
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    res = run_task(eng, TaskKind.PLAN, _payload(), "alice",
                   reviewers=["bob"], confirm=True, poll=_poll)
    assert res["verdict"] == "pass"
    item = eng.store.get_work_item(res["item_id"])
    assert item.status == WorkItemStatus.DONE


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
    # 全程同一 issue id,未新建评审 issue
    assert len(eng.store.list_work_items("ws")) == 1
    assert item.id == res["item_id"]
    # reject 返工不再通过评论交接,避免评论本身再次触发 agent run。
    assert eng.store.get_comments(res["item_id"]) == []


def test_exhausted_needs_decision():
    eng = _engine()
    MockStore.set_kind_delivery("plan", {"plan": "计划正文"})
    MockStore.set_review_rejects(99)  # 永远 reject
    with pytest.raises(NeedsDecision) as exc:
        run_task(eng, TaskKind.PLAN, _payload(), "alice",
                 reviewers=["bob"], max_revisions=3, poll=_poll)
    report = exc.value.report
    assert report["rounds"] == 3
    assert report["last_opinion"]
    assert report["item_id"]
    assert report["kind"] == "plan"
    # 全程同一 issue id
    assert len(eng.store.list_work_items("ws")) == 1
    assert report["item_id"] == eng.store.list_work_items("ws")[0].id


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
