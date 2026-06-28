"""agent_cli：executor 端统一入口（read/submit/block）的行为契约。

核心保证（对应 issue 12 与确定性写入目标）：
1. 写证据走唯一口径：artifacts/verification 以结构化对象交给引擎 JSON 写入，
   不产 dotted key、不产 prose。
2. 提交前自校验：复用 runner 同一套 validator；证据不全则**拒绝写入**并报缺项，
   不把缺口甩给 runner harvest。
3. 写证据即转状态：worker 成功 → in_review；reviewer pass → done；
   reviewer blocked / worker block → blocked。状态与证据原子绑定，消除「写了证据忘转态」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_cli
from engines.models import WorkItem, WorkItemStatus


# ==================== fixtures ====================

CONTRACT = {
    "objective": "Implement A",
    "acceptance": ["A returns success"],
    "verification_commands": ["pytest tests/a"],
    "integration_gates": [
        {
            "name": "a-contract",
            "source_of_truth": ["docs/req.md#a"],
            "delivery_goal": "A returns documented success",
            "commands": ["pytest tests/integration/a"],
            "required_metrics": {"route_contract_coverage": 100},
            "artifacts": ["coverage.xml"],
        }
    ],
    "pr_base": "feature/v1",
    "coverage_gate": 90,
}

MATCHING_GATES = [
    {
        "name": "a-contract",
        "commands": [{"cmd": "pytest tests/integration/a", "exit_code": 0}],
        "metrics": {"route_contract_coverage": 100},
        "artifacts": ["coverage.xml"],
        "source_of_truth": ["docs/req.md#a"],
        "delivery_goal": "A returns documented success",
    }
]


def _valid_report():
    return {
        "diff_reviewed": True,
        "tests_rerun": True,
        "integration_tests_rerun": True,
        "coverage_checked": True,
        "acceptance_mapping": [
            {"acceptance": "A returns success", "evidence": "tests/test_a.py::t", "status": "pass"}
        ],
        "integration_gate_mapping": [
            {
                "gate": "a-contract",
                "source_of_truth": ["docs/req.md#a"],
                "delivery_goal": "A returns documented success",
                "evidence": "tests/integration/test_a.py::t",
                "commands": [{"cmd": "pytest tests/integration/a", "exit_code": 0}],
                "metrics": {"route_contract_coverage": 100},
                "artifacts": ["coverage.xml"],
                "status": "pass",
            }
        ],
        "blockers": [],
        "nits": [],
    }


def _item(**kw):
    base = dict(
        id="1", workspace_id="ws", title="Task A", description="d",
        status=WorkItemStatus.IN_PROGRESS, dag_key="A", worker="alice", reviewer="bob",
        contract=CONTRACT,
    )
    base.update(kw)
    return WorkItem(**base)


class FakeEngine:
    def __init__(self, item):
        self._item = item
        self.metadata_calls = []
        self.status_calls = []
        self.comments = []

    def get_work_item(self, item_id):
        return self._item

    def update_work_item_metadata(self, item_id, **kw):
        self.metadata_calls.append(kw)
        return self._item

    def update_status(self, item_id, status):
        self.status_calls.append(status)

    def add_comment(self, item_id, comment):
        self.comments.append(comment)


# ==================== 纯函数 ====================

def test_parse_command_spec_full():
    assert agent_cli.parse_command_spec("pytest tests/a::0::3 passed") == {
        "cmd": "pytest tests/a", "exit_code": 0, "summary": "3 passed"
    }


def test_parse_command_spec_exit_only():
    assert agent_cli.parse_command_spec("pytest::1") == {"cmd": "pytest", "exit_code": 1}


def test_parse_command_spec_cmd_only_defaults_exit_zero():
    assert agent_cli.parse_command_spec("pytest") == {"cmd": "pytest", "exit_code": 0}


def test_build_verification_shape():
    v = agent_cli.build_verification(
        ["pytest tests/a::0::3 passed"], coverage=92, pr_base="feature/v1",
        integration_gates=MATCHING_GATES,
    )
    assert v["commands"] == [{"cmd": "pytest tests/a", "exit_code": 0, "summary": "3 passed"}]
    assert v["coverage"] == 92
    assert v["pr_base"] == "feature/v1"
    assert v["integration_gates"] == MATCHING_GATES


def test_worker_gate_errors_clean_when_evidence_matches_contract():
    item = _item(
        artifacts={"pr_url": "https://x/pr/1"},
        verification=agent_cli.build_verification(
            ["pytest tests/a::0"], coverage=92, pr_base="feature/v1",
            integration_gates=MATCHING_GATES,
        ),
    )
    assert agent_cli.worker_gate_errors(item) == []


def test_worker_gate_errors_flags_missing_pr_url():
    item = _item(artifacts={}, verification=agent_cli.build_verification(
        ["pytest tests/a::0"], coverage=92, pr_base="feature/v1", integration_gates=MATCHING_GATES))
    errs = agent_cli.worker_gate_errors(item)
    assert any("pr_url" in e for e in errs)


def test_worker_gate_errors_flags_low_coverage():
    item = _item(
        artifacts={"pr_url": "https://x/pr/1"},
        verification=agent_cli.build_verification(
            ["pytest tests/a::0"], coverage=80, pr_base="feature/v1",
            integration_gates=MATCHING_GATES),
    )
    errs = agent_cli.worker_gate_errors(item)
    assert any("coverage" in e for e in errs)


# ==================== submit-worker ====================

def test_submit_worker_success_writes_json_and_marks_done():
    item = _item()
    eng = FakeEngine(item)
    errors = agent_cli.submit_worker(
        eng, "1", pr_url="https://x/pr/1", branch="agent/a", commit="abc",
        commands=["pytest tests/a::0::3 passed"], coverage=92, pr_base="feature/v1",
        integration_gates=MATCHING_GATES,
    )
    assert errors == []
    assert len(eng.metadata_calls) == 1
    written = eng.metadata_calls[0]
    # 唯一口径：artifacts/verification 作为结构化对象交引擎（引擎负责 JSON 写入）
    assert written["artifacts"] == {"pr_url": "https://x/pr/1", "branch": "agent/a", "commit": "abc"}
    assert written["verification"]["commands"][0]["cmd"] == "pytest tests/a"
    # 标 done 交 runner harvest 收割（指派 reviewer + 转 in_review 是 runner 的职责）
    assert eng.status_calls == [WorkItemStatus.DONE]


def test_submit_worker_refuses_on_gate_failure():
    item = _item()
    eng = FakeEngine(item)
    # 缺 pr_url
    errors = agent_cli.submit_worker(
        eng, "1", pr_url="", commands=["pytest tests/a::0"], coverage=92,
        pr_base="feature/v1", integration_gates=MATCHING_GATES,
    )
    assert errors  # 报缺项
    assert eng.metadata_calls == []   # 拒绝写入
    assert eng.status_calls == []     # 不转状态


# ==================== submit-review ====================

def test_submit_review_pass_writes_verdict_and_done():
    item = _item(status=WorkItemStatus.IN_REVIEW)
    eng = FakeEngine(item)
    errors = agent_cli.submit_review(eng, "1", verdict="pass", report=_valid_report())
    assert errors == []
    written = eng.metadata_calls[0]
    assert written["review_verdict"] == "pass"
    assert written["review_report"]["diff_reviewed"] is True
    assert eng.status_calls == [WorkItemStatus.DONE]


def test_submit_review_pass_refuses_on_incomplete_report():
    item = _item(status=WorkItemStatus.IN_REVIEW)
    eng = FakeEngine(item)
    bad = _valid_report()
    bad["integration_tests_rerun"] = False
    errors = agent_cli.submit_review(eng, "1", verdict="pass", report=bad)
    assert errors
    assert eng.metadata_calls == []
    assert eng.status_calls == []


def test_submit_review_blocked_records_verdict_and_isolates():
    item = _item(status=WorkItemStatus.IN_REVIEW)
    eng = FakeEngine(item)
    errors = agent_cli.submit_review(eng, "1", verdict="blocked", report=None)
    assert errors == []  # blocked 无需证据门
    assert eng.metadata_calls[0]["review_verdict"] == "blocked"
    assert eng.status_calls == [WorkItemStatus.BLOCKED]


# ==================== block / reads ====================

def test_block_item_comments_and_blocks():
    item = _item()
    eng = FakeEngine(item)
    agent_cli.block_item(eng, "1", "依赖契约未冻结")
    assert eng.comments and "依赖契约未冻结" in eng.comments[0]
    assert eng.status_calls == [WorkItemStatus.BLOCKED]


def test_read_task_normalizes_config():
    item = _item()
    eng = FakeEngine(item)
    out = agent_cli.read_task(eng, "1")
    assert out["worker"] == "alice"
    assert out["reviewer"] == "bob"
    assert out["status"] == "in_progress"
    assert out["has_contract"] is True


def test_read_evidence_returns_upstream():
    item = _item(
        status=WorkItemStatus.IN_REVIEW,
        artifacts={"pr_url": "https://x/pr/1"},
        verification={"coverage": 92},
    )
    eng = FakeEngine(item)
    out = agent_cli.read_evidence(eng, "1")
    assert out["artifacts"]["pr_url"] == "https://x/pr/1"
    assert out["verification"]["coverage"] == 92


# ==================== CLI 接线（main）====================

def test_main_submit_worker_gate_failure_exits_1(monkeypatch):
    item = _item()
    eng = FakeEngine(item)
    monkeypatch.setattr(agent_cli, "create_engine_from_env", lambda **kw: eng)
    # 缺 --command/--coverage 等 → 证据门拦截 → 退出码 1，且不写入
    with __import__("pytest").raises(SystemExit) as exc:
        agent_cli.main(["--engine", "mock", "submit-worker", "1", "--pr-url", "https://x/pr/1"])
    assert exc.value.code == 1
    assert eng.metadata_calls == []
    assert eng.status_calls == []


def test_main_submit_worker_success_exits_0(monkeypatch, tmp_path):
    item = _item()
    eng = FakeEngine(item)
    monkeypatch.setattr(agent_cli, "create_engine_from_env", lambda **kw: eng)
    gates_file = tmp_path / "gates.json"
    gates_file.write_text(__import__("json").dumps(MATCHING_GATES))
    agent_cli.main([
        "--engine", "mock", "submit-worker", "1",
        "--pr-url", "https://x/pr/1",
        "--command", "pytest tests/a::0::3 passed",
        "--coverage", "92", "--pr-base", "feature/v1",
        "--integration-gates-file", str(gates_file),
    ])
    assert len(eng.metadata_calls) == 1
    assert eng.status_calls == [WorkItemStatus.DONE]
