"""P4.4 交付级 e2e(CLI 子进程级):plan create 产出(manifest + 验收文档)→
dag run(含假 CI/merge 脚本)→ 总控验收(一轮 fail→增量→pass)→ exit 0。

plan create 的 planner/orchestrator/验收/ reviewer 环节在真实世界跑 LLM;本测
试把它们的 *产出*(manifest + 验收文档)直接落盘到 .omac/,再对
dag run 这条真实主线做端到端验收——这是 P4 交付闭环的可测核心。

5 场景(pytest,标记 e2e):
  1. README quickstart 首批命令(init + --check)可执行。
  2. 收敛链路:happy path(dag run,无 CI/merge)→ exit 0,全节点 done。
  3. CI 绿链:dag run 含假 CI 脚本(pass)→ exit 0,断言 CI 门实际被调用。
  4. merge 链:dag run 含假 CI + merge 脚本(pass)→ exit 0 + merged。
  5. 总控验收外层循环:首轮 1 fail → 增量 1 fix 节点并入原 manifest →
     次轮全 pass → exit 0;退出码链全程正确。

验收标准:
  - 退出码链精确(0 / 20)
  - mock delay=0 + poll_interval=0,无 sleep 竞态
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omac.cli import exit_codes  # noqa: E402

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


# ── 共享 helpers ─────────────────────────────────────────────────────────

# 假 CI 脚本:rc 取环境 OMIC_CI_RC(缺省 0 = 绿),写日志 + 退出码 = rc。
_CI_LOG = (
    "#!/bin/sh\n"
    "rc=${OMIC_CI_RC:-0}\n"
    'echo "fake-ci: rc=$rc"\n'
    '[ -n "$OMIC_LOG" ] && printf "%s\\n" "$rc" >> "$OMIC_LOG"\n'
    'exit "$rc"\n'
)

# 假 merge 脚本:exit 0 即合入。
_MERGE_LOG = (
    "#!/bin/sh\n"
    'echo "fake-merge: ok"\n'
    '[ -n "$OMIC_LOG" ] && printf "merge\\n" >> "$OMIC_LOG"\n'
    "exit 0\n"
)


def _write_exec(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body)
    os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _env(tmp_path: Path, *, log_file: str | None = None,
         accepted: dict | None = None, increments: dict | None = None,
         fail_keys: str | None = None) -> dict:
    env = os.environ.copy()
    env.update({
        "OMAC_ENGINE": "mock",
        "OMAC_WORKSPACE_ID": "mock-workspace",
        "MOCK_AUTO_COMPLETE": "true",
        "MOCK_AUTO_COMPLETE_DELAY": "0",
        "PYTHONPATH": str(SRC_DIR),
    })
    if log_file is not None:
        env["OMIC_LOG"] = log_file
    if fail_keys:
        env["OMAC_MOCK_FAIL_KEYS"] = fail_keys
    if accepted is not None:
        env["OMAC_MOCK_ACCEPTED"] = json.dumps(accepted)
    if increments is not None:
        env["OMAC_MOCK_INCREMENTS"] = json.dumps(increments)
    return env


def _run(args: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["omac", *args], cwd=str(cwd), capture_output=True, text=True, env=env)


def _parse_json(stdout: str) -> dict:
    return json.loads(stdout.strip())


def _init(cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return _run([
        "init", "--engine", "mock", "--workspace", "mock-workspace",
        "--planner", "alice", "--orchestrator", "bob",
        "--workers", "alice,bob", "--reviewers", "charlie",
    ], cwd=cwd, env=env)


def _dag_run_json(cwd: Path, env: Path, manifest: Path):
    return _run(["dag", "run", str(manifest), "--output", "json"], cwd=cwd, env=env)


# plan 流水线产出(manifest + 验收文档),直接落盘到 .omac/ —— 等价于
# plan create(...)→ planner/orchestrator/reviewer 产出。
FULL_MANIFEST = """\
meta:
  name: smoke-full
  workspace_id: mock-workspace
nodes:
  - id: foundation
    worker: alice

    reviewer: charlie
    contract:
      objective: foundation
      acceptance: [flow-foundation]
      non_goals: []
      verification_commands: [pytest tests/test_a.py -q]
      integration_gates:
        - name: gate-foundation
          layer: L1
          delivery_goal: foundation delivered
          source_of_truth: [docs/a.md]
          covers: [a]
          acceptance_refs: [flow-foundation]
          commands: [pytest tests/int_a -q]
          required_metrics: {route_coverage: 100}
          artifacts: [coverage.xml]
      quality:
        required_outcomes:
          - id: foundation-outcome
            source_ref: acceptance#flow-foundation.run
        business_tests:
          - id: foundation-business
            outcome_refs: [foundation-outcome]
            command: pytest tests/int_a -q
            level: integration
            real_dependencies: [none]
            must_fail_on_base: true
        runtime_data_policy: real-or-error
      pr_base: feature/p4-smoke
      coverage_gate: 90
  - id: middle
    worker: bob

    reviewer: charlie
    blocked_by: [foundation]
    contract:
      objective: middle
      acceptance: [flow-middle]
      non_goals: []
      verification_commands: [pytest tests/test_b.py -q]
      integration_gates:
        - name: gate-middle
          layer: L1
          delivery_goal: middle delivered
          source_of_truth: [docs/b.md]
          covers: [b]
          acceptance_refs: [flow-middle]
          commands: [pytest tests/int_b -q]
          required_metrics: {route_coverage: 100}
          artifacts: [coverage.xml]
      quality:
        required_outcomes:
          - id: middle-outcome
            source_ref: acceptance#flow-middle.run
        business_tests:
          - id: middle-business
            outcome_refs: [middle-outcome]
            command: pytest tests/int_b -q
            level: integration
            real_dependencies: [none]
            must_fail_on_base: true
        runtime_data_policy: real-or-error
      pr_base: feature/p4-smoke
      coverage_gate: 90
  - id: final
    worker: alice

    reviewer: charlie
    blocked_by: [middle]
    contract:
      objective: final
      acceptance: [flow-final]
      non_goals: []
      verification_commands: [pytest tests/test_c.py -q]
      integration_gates:
        - name: gate-final
          layer: L1
          delivery_goal: final delivered
          source_of_truth: [docs/c.md]
          covers: [c]
          acceptance_refs: [flow-final]
          commands: [pytest tests/int_c -q]
          required_metrics: {route_coverage: 100}
          artifacts: [coverage.xml]
      quality:
        required_outcomes:
          - id: final-outcome
            source_ref: acceptance#flow-final.run
        business_tests:
          - id: final-business
            outcome_refs: [final-outcome]
            command: pytest tests/int_c -q
            level: integration
            real_dependencies: [none]
            must_fail_on_base: true
        runtime_data_policy: real-or-error
      pr_base: feature/p4-smoke
      coverage_gate: 90
"""

ACCEPTANCE_DOC = """\
flows:
  - id: flow-foundation
    name: foundation 流程
    actions:
      - id: run
        step: 走通 foundation
        how: GET /foundation
        expected: ok
  - id: flow-middle
    name: middle 流程
    actions:
      - id: run
        step: 走通 middle
        how: GET /middle
        expected: ok
  - id: flow-final
    name: final 流程
    actions:
      - id: run
        step: 走通 final
        how: GET /final
        expected: ok
"""


def _stage_plan_artifacts(cwd: Path, with_acceptance: bool = True) -> None:
    d = cwd / ".omac"
    d.mkdir(parents=True, exist_ok=True)
    (d / "smoke-full.yaml").write_text(FULL_MANIFEST)
    if with_acceptance:
        (d / "smoke-full.acceptance.yaml").write_text(ACCEPTANCE_DOC)


# ── 场景 1:README quickstart 首批命令(init)可执行 ─────────────────────────

@pytest.mark.e2e
class TestReadmeQuickstart:
    def test_init_then_check_works(self, tmp_path: Path):
        env = _env(tmp_path)
        r = _init(tmp_path, env)
        assert r.returncode == exit_codes.OK, r.stderr
        assert (tmp_path / ".omac" / "config.yaml").exists()
        r2 = _run(["init", "--check"], cwd=tmp_path, env=env)
        assert r2.returncode == exit_codes.OK, r2.stderr


# ── 场景 2:收敛链路 happy path(dag run,无 CI/merge)→ exit 0 ──────────────

@pytest.mark.e2e
class TestDagRunConverges:
    def _prep(self, tmp_path: Path, env: dict) -> Path:
        _init(tmp_path, env)
        # 不验收文档 → dag run 收敛即 exit 0(验收环节整体跳过)。
        _stage_plan_artifacts(tmp_path, with_acceptance=False)
        return tmp_path / ".omac" / "smoke-full.yaml"

    def test_dag_run_converges_exit_0(self, tmp_path: Path):
        env = _env(tmp_path)
        m = self._prep(tmp_path, env)
        r = _run(["dag", "run", str(m), "--output", "json"], cwd=tmp_path, env=env)
        assert r.returncode == exit_codes.OK, r.stderr
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        assert sorted(data["done"]) == ["final", "foundation", "middle"]
        assert data["running"] == []
        assert data["report"] is None

    def test_dag_status_table_lists_keys(self, tmp_path: Path):
        env = _env(tmp_path)
        m = self._prep(tmp_path, env)
        _run(["dag", "run", str(m)], cwd=tmp_path, env=env)
        r = _run(["dag", "status", str(m)], cwd=tmp_path, env=env)
        assert r.returncode == exit_codes.OK, r.stderr
        for k in ("foundation", "middle", "final"):
            assert k in r.stdout


# ── 场景 3:CI 绿链(dag run 含假 CI 脚本)→ exit 0 ─────────────────────────

@pytest.mark.e2e
class TestCiGreen:
    def test_ci_pass_greens_nodes(self, tmp_path: Path):
        log = str(tmp_path / "ci.log")
        env = _env(tmp_path, log_file=log)
        ci = _write_exec(tmp_path, "ci.sh", _CI_LOG)
        _init(tmp_path, env)
        _stage_plan_artifacts(tmp_path, with_acceptance=False)
        rc_set = _run(["config", "set", "defaults.poll_interval", "0"],
                      cwd=tmp_path, env=env)
        assert rc_set.returncode == 0, rc_set.stderr
        rc_set = _run(["config", "set", "ci.check_command",
                       f"sh {ci} {{pr_url}}"], cwd=tmp_path, env=env)
        assert rc_set.returncode == 0, rc_set.stderr
        rc_set = _run(["config", "set", "ci.timeout_minutes", "30"],
                      cwd=tmp_path, env=env)
        assert rc_set.returncode == 0
        m = tmp_path / ".omac" / "smoke-full.yaml"
        r = _run(["dag", "run", str(m), "--output", "json"], cwd=tmp_path, env=env)
        assert r.returncode == exit_codes.OK, r.stderr + "\n" + r.stdout
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        logged = Path(log).read_text().splitlines() if Path(log).exists() else []
        assert any(l == "0" for l in logged), f"假 CI 未实际调用: {logged}"


# ── 场景 4:merge 链(假 CI + merge 脚本)→ exit 0 + merged ─────────────────

@pytest.mark.e2e
class TestMergeChain:
    def test_ci_and_merge_pass_set_merged(self, tmp_path: Path):
        log = str(tmp_path / "merge.log")
        env = _env(tmp_path, log_file=log)
        ci = _write_exec(tmp_path, "ci.sh", _CI_LOG)
        merge = _write_exec(tmp_path, "merge.sh", _MERGE_LOG)
        _init(tmp_path, env)
        _stage_plan_artifacts(tmp_path, with_acceptance=False)
        assert _run(["config", "set", "defaults.poll_interval", "0"],
                    cwd=tmp_path, env=env).returncode == 0
        assert _run(["config", "set", "ci.check_command",
                     f"sh {ci} {{pr_url}}"], cwd=tmp_path, env=env).returncode == 0
        assert _run(["config", "set", "merge.command", f"sh {merge} {{pr_url}}"],
                    cwd=tmp_path, env=env).returncode == 0
        m = tmp_path / ".omac" / "smoke-full.yaml"
        r = _run(["dag", "run", str(m), "--output", "json"], cwd=tmp_path, env=env)
        assert r.returncode == exit_codes.OK, r.stderr + "\n" + r.stdout
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        logged = Path(log).read_text() if Path(log).exists() else ""
        assert "merge" in logged, f"假 merge 未实际调用: {logged!r}"


# ── 场景 5:总控验收外层循环 fail→增量→pass → exit 0 ──────────────────────

@pytest.mark.e2e
class TestAcceptanceOuterLoop:
    def _prep(self, tmp_path: Path) -> Path:
        _stage_plan_artifacts(tmp_path, with_acceptance=True)
        return tmp_path / ".omac" / "smoke-full.yaml"

    def test_fail_then_increment_then_pass_exit_0(self, tmp_path: Path):
        """首轮 flow-final fail → 增量 1 fix 节点 → 次轮全 pass → exit 0。"""
        env = _env(tmp_path)
        _init(tmp_path, env)
        m = self._prep(tmp_path)
        assert _run(["config", "set", "defaults.poll_interval", "0"],
                    cwd=tmp_path, env=env).returncode == 0

        increments = {
            "decompose-r1": {
                "meta": {},
                "nodes": [
                    {"id": "fix-final", "worker": "bob", "blocked_by": ["middle"]},
                ],
            }
        }
        accepted = {
            "final-acceptance-r1": [
                {"id": "flow-foundation", "status": "pass"},
                {"id": "flow-middle", "status": "pass"},
                {"id": "flow-final", "status": "fail", "notes": "regression"},
            ],
            "final-acceptance-r2": [
                {"id": "flow-foundation", "status": "pass"},
                {"id": "flow-middle", "status": "pass"},
                {"id": "flow-final", "status": "pass"},
            ],
        }
        env2 = _env(tmp_path, accepted=accepted, increments=increments)
        r = _run(["dag", "run", str(m), "--output", "json"], cwd=tmp_path, env=env2)
        assert r.returncode == exit_codes.OK, r.stderr + "\n" + r.stdout
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        reloaded = yaml.safe_load(m.read_text())
        ids = [n["id"] for n in reloaded["nodes"]]
        assert "fix-final" in ids, f"增量 fix 节点未并入: {ids}"
        assert sorted(data["done"]) == sorted(
            ["final", "foundation", "middle", "fix-final"])

    def test_acceptance_max_rounds_exhausted_exit_20(self, tmp_path: Path):
        """永远 fail + max_rounds=1 → 耗尽 → exit 20。"""
        env = _env(tmp_path)
        _init(tmp_path, env)
        m = self._prep(tmp_path)
        accepted = {
            "final-acceptance-r1": [
                {"id": "flow-foundation", "status": "pass"},
                {"id": "flow-middle", "status": "pass"},
                {"id": "flow-final", "status": "fail", "notes": "stale"},
            ],
        }
        env2 = _env(tmp_path, accepted=accepted)
        assert _run(["config", "set", "acceptance.max_rounds", "1"],
                    cwd=tmp_path, env=env2).returncode == 0
        r = _run(["dag", "run", str(m), "--output", "json"], cwd=tmp_path, env=env2)
        assert r.returncode == exit_codes.NEEDS_DECISION, r.stderr
