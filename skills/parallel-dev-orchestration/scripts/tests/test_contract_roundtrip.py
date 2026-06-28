"""contract 下发 → 读回 round-trip：解锁 worker 端完整 gate 自检。

编排器 dispatch 时把 manifest 节点的 contract 写到 work item（set_node_contract），
worker 读回（_issue_to_work_item → WorkItem.contract）后才能用同一套 validator 自校验。
守护两条契约：
1. multica/github 都能写入并读回 contract（单一事实源，引擎间口径一致）。
2. github 后续 metadata 更新不得冲掉已写入的 contract（frontmatter 全量重建的坑）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.manifest import Contract
from engines.multica import MulticaEngine
from engines.github import GithubEngine
from engines.models import EngineConfig


# ==================== Multica ====================

def _multica_engine():
    return MulticaEngine(EngineConfig(engine_type="multica", workspace_id="ws"))


def test_multica_issue_to_work_item_parses_contract():
    eng = _multica_engine()
    issue = {
        "id": "x", "title": "t", "status": "todo",
        "metadata": {
            "contract": json.dumps({
                "objective": "do A",
                "verification_commands": ["pytest tests/a"],
                "coverage_gate": 95,
            })
        },
    }
    item = eng._issue_to_work_item(issue, "ws")
    assert item.contract["objective"] == "do A"
    assert item.contract["verification_commands"] == ["pytest tests/a"]
    assert item.contract["coverage_gate"] == 95


def test_multica_issue_to_work_item_contract_absent_is_none():
    eng = _multica_engine()
    issue = {"id": "x", "title": "t", "status": "todo", "metadata": {}}
    item = eng._issue_to_work_item(issue, "ws")
    assert item.contract is None


def test_multica_set_node_contract_writes_json_metadata():
    eng = _multica_engine()
    calls = []
    eng._run_multica = lambda args, capture=True: calls.append(args)
    eng.set_node_contract("id1", Contract(objective="do A", verification_commands=["pytest tests/a"]))
    call = next(c for c in calls if "--key" in c and c[c.index("--key") + 1] == "contract")
    assert call[:4] == ["issue", "metadata", "set", "id1"]
    payload = json.loads(call[call.index("--value") + 1])
    assert payload["objective"] == "do A"
    assert payload["verification_commands"] == ["pytest tests/a"]


def test_multica_set_node_contract_accepts_plain_dict():
    eng = _multica_engine()
    calls = []
    eng._run_multica = lambda args, capture=True: calls.append(args)
    eng.set_node_contract("id1", {"objective": "do A"})
    call = next(c for c in calls if "--key" in c and c[c.index("--key") + 1] == "contract")
    payload = json.loads(call[call.index("--value") + 1])
    assert payload["objective"] == "do A"


# ==================== GitHub ====================

def _github_engine():
    return GithubEngine(EngineConfig(engine_type="github", workspace_id="o/r"))


def test_github_contract_roundtrips_through_body():
    eng = _github_engine()
    contract = {"objective": "do A", "verification_commands": ["pytest tests/a"], "coverage_gate": 95}
    body = eng._build_issue_body("desc", "K", "alice", contract=contract)
    issue = {"number": 5, "title": "t", "body": body, "labels": []}
    item = eng._issue_to_work_item(issue, "o/r")
    assert item.contract["objective"] == "do A"
    assert item.contract["coverage_gate"] == 95


def test_github_update_metadata_preserves_contract():
    """contract 写入后，后续 update_work_item_metadata 不得把它从 frontmatter 冲掉。"""
    eng = _github_engine()
    contract = {"objective": "do A"}
    body = eng._build_issue_body("desc", "K", "alice", contract=contract)
    issue = {"number": 5, "title": "t", "body": body, "labels": []}
    current = eng._issue_to_work_item(issue, "o/r")

    captured = {}

    def fake_gh(args, capture=True):
        if "--body" in args:
            captured["body"] = args[args.index("--body") + 1]
        return None

    eng.get_work_item = lambda i: current
    eng._run_gh = fake_gh
    eng.update_work_item_metadata("5", artifacts={"pr_url": "https://x/pr/1"})

    _, md = eng._parse_issue_body(captured["body"])
    assert md.get("contract", {}).get("objective") == "do A"
    assert md.get("artifacts", {}).get("pr_url") == "https://x/pr/1"


def test_github_set_node_contract_writes_contract_into_body():
    eng = _github_engine()
    base_body = eng._build_issue_body("desc", "K", "alice")
    issue = {"number": 5, "title": "t", "body": base_body, "labels": []}
    current = eng._issue_to_work_item(issue, "o/r")

    captured = {}

    def fake_gh(args, capture=True):
        if "--body" in args:
            captured["body"] = args[args.index("--body") + 1]
        return None

    eng.get_work_item = lambda i: current
    eng._run_gh = fake_gh
    eng.set_node_contract("5", Contract(objective="do A", coverage_gate=95))

    _, md = eng._parse_issue_body(captured["body"])
    assert md.get("contract", {}).get("objective") == "do A"
    assert md.get("contract", {}).get("coverage_gate") == 95
