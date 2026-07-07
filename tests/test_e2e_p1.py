"""P1 e2e:CLI 子进程级端到端验收(mock 引擎,无 sleep 竞态)。

4 场景(pytest,标记 e2e):
  1. 空目录 → init 非交互(mock)→ 写示例 manifest(含 contract,复用 smoke_p1.yaml)
     → dag run → exit 0,全节点 done
  2. 失败注入(mock set_fail_keys)→ dag run exit 20 → 断言 json 报告四段
     → node show → node retry(清除注入)→ dag run → exit 0
  3. abandon 路径:失败节点 abandon → 下游继续 → exit 0(带注记)
  4. 中断续跑:--max-rounds 1 多次分段直至收敛,断言 issue 不重复创建

验收标准:
  - 每步断言退出码 + stdout/stderr 关键内容
  - mock delay=0,无 sleep 竞态
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omac.cli import exit_codes  # noqa: E402


# ==================== constants ====================

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOKE_MANIFEST = FIXTURES / "smoke_p1.yaml"


# ==================== env helpers ====================

def _env(**extra) -> dict:
    """子进程环境变量:强制 mock 引擎 + delay=0。"""
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parent.parent / "src")
    old_pythonpath = env.get("PYTHONPATH")
    env.update({
        "OMAC_ENGINE": "mock",
        "OMAC_WORKSPACE_ID": "mock-workspace",
        "MOCK_AUTO_COMPLETE": "true",
        "MOCK_AUTO_COMPLETE_DELAY": "0",
        "PYTHONPATH": src if not old_pythonpath else src + os.pathsep + old_pythonpath,
    })
    env.update(extra)
    return env


def _run(args: list[str], cwd: Path, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """运行 omac 子进程,返回 CompletedProcess。"""
    return subprocess.run(
        ["omac", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env if env is not None else _env(),
    )


def _init(cwd: Path, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """非交互 init(mock 引擎)。"""
    return _run([
        "init",
        "--engine", "mock",
        "--workspace", "mock-workspace",
        "--planner", "alice",
        "--orchestrator", "bob",
        "--workers", "charlie",
        "--reviewers", "alice,bob",
    ], cwd=cwd, env=env)


def _write_manifest(cwd: Path, name: str = "smoke.yaml") -> Path:
    """把 fixtures 示例 manifest 拷到 cwd。"""
    dst = cwd / name
    shutil.copy(SMOKE_MANIFEST, dst)
    return dst


def _parse_json(stdout: str) -> dict:
    """解析 omac --output json 的 stdout,允许前后空白/hint 文本。"""
    s = stdout.strip()
    return json.loads(s)


# ==================== 1. 空目录 → init → 写 manifest → dag run → exit 0 ====================

@pytest.mark.e2e
class TestSingleNodeHappyPath:
    def test_init_non_interactive_writes_config(self, tmp_path: Path):
        r = _init(tmp_path)
        assert r.returncode == exit_codes.OK, r.stderr
        assert (tmp_path / ".omac" / "config.yaml").exists()
        combined = r.stdout + r.stderr
        assert "config.yaml" in combined

    def test_dag_run_converges_exit_0(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        r = _run(["dag", "run", str(manifest), "--output", "json"], cwd=tmp_path)
        assert r.returncode == exit_codes.OK, r.stderr
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        assert sorted(data["done"]) == ["smoke-A", "smoke-B", "smoke-C"]
        assert isinstance(data["running"], list) and len(data["running"]) == 0
        assert data.get("report") is None

    def test_dag_run_table_output_lists_keys(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        r = _run(["dag", "run", str(manifest)], cwd=tmp_path)
        assert r.returncode == exit_codes.OK, r.stderr
        for k in ("smoke-A", "smoke-B", "smoke-C"):
            assert k in r.stdout


# ==================== 2. 失败注入 → exit 20 → 四段报告 → node show → node retry → exit 0 ====================

@pytest.mark.e2e
class TestFailureInjection:
    def test_dag_run_failure_exit_20_with_report(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        r = _run(
            ["dag", "run", str(manifest), "--output", "json"],
            cwd=tmp_path,
            env=_env(OMAC_MOCK_FAIL_KEYS="smoke-A"),
        )
        assert r.returncode == exit_codes.NEEDS_DECISION, r.stderr
        data = _parse_json(r.stdout)
        assert data["state"] == "needs_decision"
        report = data["report"]
        # 四段 schema
        assert set(report.keys()) == {"failed_nodes", "blocked_downstream", "next_actions"}
        failed_keys = [n["key"] for n in report["failed_nodes"]]
        assert "smoke-A" in failed_keys
        # 下游被隔离
        assert "smoke-B" in report["blocked_downstream"]
        assert "smoke-C" in report["blocked_downstream"]
        # next_actions 含可执行命令
        assert any("node retry" in a and "smoke-A" in a for a in report["next_actions"])

    def test_node_show_blocked_node(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        # 触发失败
        _run(
            ["dag", "run", str(manifest), "--output", "json"],
            cwd=tmp_path,
            env=_env(OMAC_MOCK_FAIL_KEYS="smoke-A"),
        )
        # node show
        r = _run(
            ["node", "show", str(manifest), "smoke-A", "--output", "json"],
            cwd=tmp_path,
        )
        assert r.returncode == exit_codes.OK, r.stderr
        data = _parse_json(r.stdout)
        assert data["node_key"] == "smoke-A"
        assert data["status"] == "blocked"
        assert data["work_item_id"] is not None

    def test_node_retry_then_dag_run_converges(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        # 失败注入 → exit 20
        r_fail = _run(
            ["dag", "run", str(manifest), "--output", "json"],
            cwd=tmp_path,
            env=_env(OMAC_MOCK_FAIL_KEYS="smoke-A"),
        )
        assert r_fail.returncode == exit_codes.NEEDS_DECISION, r_fail.stderr

        # node retry(清除注入)
        r_retry = _run(
            ["node", "retry", str(manifest), "smoke-A"],
            cwd=tmp_path,
        )
        assert r_retry.returncode == exit_codes.OK, r_retry.stderr
        retry_data = _parse_json(r_retry.stdout)
        assert retry_data["status"] == "todo"
        assert retry_data["node_key"] == "smoke-A"
        assert retry_data["work_item_id"] is not None  # work_item_id 保留(同一 issue 续用)

        # 续跑收敛
        r = _run(["dag", "run", str(manifest), "--output", "json"], cwd=tmp_path)
        assert r.returncode == exit_codes.OK, r.stderr
        data = _parse_json(r.stdout)
        assert data["state"] == "converged"
        assert sorted(data["done"]) == ["smoke-A", "smoke-B", "smoke-C"]


# ==================== 3. abandon 路径 ====================

@pytest.mark.e2e
class TestAbandon:
    def test_abandon_unlocks_downstream(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        # 失败注入 → exit 20
        r_fail = _run(
            ["dag", "run", str(manifest), "--output", "json"],
            cwd=tmp_path,
            env=_env(OMAC_MOCK_FAIL_KEYS="smoke-A"),
        )
        assert r_fail.returncode == exit_codes.NEEDS_DECISION, r_fail.stderr

        # abandon smoke-A
        r_abandon = _run(
            ["node", "abandon", str(manifest), "smoke-A"],
            cwd=tmp_path,
        )
        assert r_abandon.returncode == exit_codes.OK, r_abandon.stderr
        abandon_data = _parse_json(r_abandon.stdout)
        assert abandon_data["status"] == "abandoned"
        assert "smoke-B" in abandon_data["affected_downstream"]
        assert "smoke-C" in abandon_data["affected_downstream"]

        # 续跑收敛(smoke-A 保持 abandoned,下游 done)
        r = _run(["dag", "run", str(manifest), "--output", "json"], cwd=tmp_path)
        assert r.returncode == exit_codes.OK, r.stderr
        data = _parse_json(r.stdout)
        assert data["state"] == "converged", data
        # 用 dag status 拿到完整节点表,断言 abandon 状态保留
        r_status = _run(
            ["dag", "status", str(manifest), "--output", "json"],
            cwd=tmp_path,
        )
        status = _parse_json(r_status.stdout)
        by_key = {n["key"]: n for n in status["nodes"]}
        assert by_key["smoke-A"]["status"] == "abandoned"
        assert by_key["smoke-B"]["status"] == "done"
        assert by_key["smoke-C"]["status"] == "done"


# ==================== 4. 中断续跑:--max-rounds 1 多次分段直至收敛 ====================

# 场景 4 的关键约束:mock 引擎的状态在同一进程内跨 main() 调用保留,但跨
# subprocess.run 会重置。用一个内部 Python 子进程脚本,在同一进程内多次调用
# main(["dag","run","--max-rounds","1","--output","json"]),并通过把工作项 id 写
# 回 manifest 文件这一事实校验「issue 不重复创建」:每个节点的 work_item_id
# 只允许写入一次,后续分段不得变更。

_BOUNDED_RESUME_SCRIPT = r"""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml
from omac.cli.main import main
from omac.cli import exit_codes

manifest = Path(sys.argv[1])


def load_manifest(path):
    with open(path) as f:
        raw = yaml.safe_load(f)
    nodes = raw.get("nodes", [])
    out = {}
    for n in nodes:
        out[n["id"]] = n.get("work_item_id")
    return out


def save_output(step, rc, out, err):
    sys.stderr.write(f"[step {step}] rc={rc} stdout={out!r} stderr={err!r}\n")


seen_ids: dict = {}
rounds = 0
while rounds < 50:
    rounds += 1
    # 记录本轮开始前的 work_item_id
    before = load_manifest(manifest)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    try:
        rc = main(["dag", "run", str(manifest), "--max-rounds", "1", "--output", "json"])
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    stdout, stderr = out_buf.getvalue(), err_buf.getvalue()
    save_output(rounds, rc, stdout, stderr)

    if rc not in (exit_codes.OK, exit_codes.IN_PROGRESS):
        print(json.dumps({"ok": False, "error": f"unexpected rc={rc}", "rounds": rounds, "stdout": stdout, "stderr": stderr}))
        sys.exit(2)

    # 校验 issue 不重复:每个节点的 work_item_id 一旦写入就固定
    after = load_manifest(manifest)
    for key, wid in after.items():
        if wid is None:
            continue
        if key in seen_ids:
            if seen_ids[key] != wid:
                print(json.dumps({"ok": False, "error": f"duplicate-issue {key}: {seen_ids[key]} -> {wid}", "rounds": rounds}))
                sys.exit(3)
        else:
            seen_ids[key] = wid

    # 判断是否收敛
    try:
        data = json.loads(stdout.strip())
    except Exception:
        data = {}
    if data.get("state") == "converged":
        print(json.dumps({"ok": True, "state": "converged", "done": data.get("done", []), "rounds": rounds, "seen_ids": seen_ids}))
        sys.exit(0)

print(json.dumps({"ok": False, "error": "no convergence", "rounds": rounds, "seen_ids": seen_ids}))
sys.exit(1)
"""


@pytest.mark.e2e
class TestBoundedResume:
    def test_max_rounds_resume_no_duplicate_issues(self, tmp_path: Path):
        _init(tmp_path)
        manifest = _write_manifest(tmp_path)

        # 写入子进程脚本
        script = tmp_path / "_bounded_resume.py"
        script.write_text(_BOUNDED_RESUME_SCRIPT)

        r = subprocess.run(
            [sys.executable, str(script), str(manifest)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=_env(),
        )
        # 脚本以最后一行 json 汇报结果
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        try:
            result = json.loads(line)
        except Exception as e:
            pytest.fail(f"script stdout parse failed: {e!r}\nstdout={r.stdout!r}\nstderr={r.stderr!r}")

        assert result.get("ok") is True, {"result": result, "stderr": r.stderr[-2000:]}
        assert result["state"] == "converged"
        assert sorted(result["done"]) == ["smoke-A", "smoke-B", "smoke-C"]
        # 每个节点只创建了 1 个 issue
        assert len(result["seen_ids"]) == 3
        assert result["rounds"] <= 50
