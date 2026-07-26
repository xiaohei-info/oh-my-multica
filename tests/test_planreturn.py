"""PlanReturn 人工计划门(切片 2 / Stage 3)的 RED 测试:严格解析 + 不可变快照 + exit 20。

批准的三种形式(plan 文档 §B「Human plan gate」):
    PlanReturn path=/absolute/path/to/plan.md
    PlanReturn artifact=https://artifactd.example/...
    PlanReturn host=artemis path=/absolute/path/to/plan.md sha256=<digest>

语义:
  - 解析错误(评论小说、相对路径、未支持 scheme、未知/重复键、歧义多返回、
    非法组合)→ ValidationError(exit 5),报错即教学 + 可复制的正确形式;
  - 无法安全解析的输入(缺失/不可读/读取期间变动的文件、hash 缺失或不匹配、
    未配置的安全 fetch)→ NeedsDecision(exit 20,结构化报告 + 可复制修复行);
  - 可读本地输入被快照进 plan store(按内容寻址 <sha256>.md,幂等),
    记录不可变 SHA-256;
  - artifact/host 形式只走注入的窄 fetch 接口,绝不内嵌任意 shell 执行。
"""
from __future__ import annotations

import hashlib
import os
import stat

import pytest

from omac.core.config import resolve_plan_gate
from omac.core.planreturn import (
    PlanReturn,
    parse_plan_return,
    resolve_plan_return,
)
from omac.errors import NeedsDecision, ValidationError

PLAN_BODY = b"# Plan\n\nDo the thing.\n"
PLAN_SHA = hashlib.sha256(PLAN_BODY).hexdigest()


# ── helpers ───────────────────────────────────────────────────────────────

def _plan_file(tmp_path, body=PLAN_BODY, name="plan.md"):
    p = tmp_path / name
    p.write_bytes(body)
    return p


def _store_dir(tmp_path):
    return str(tmp_path / "plan-store")


# ── parse: 合法形式 ──────────────────────────────────────────────────────

def test_parse_path_form():
    ret = parse_plan_return("PlanReturn path=/abs/plan.md")
    assert ret.kind == "path"
    assert ret.path == "/abs/plan.md"
    assert ret.sha256 is None


def test_parse_artifact_form():
    ret = parse_plan_return("PlanReturn artifact=https://artifactd.example/x/1")
    assert ret.kind == "artifact"
    assert ret.url == "https://artifactd.example/x/1"


def test_parse_host_form():
    ret = parse_plan_return(
        f"PlanReturn host=artemis path=/abs/plan.md sha256={PLAN_SHA}")
    assert ret.kind == "host"
    assert ret.host == "artemis"
    assert ret.path == "/abs/plan.md"
    assert ret.sha256 == PLAN_SHA


def test_parse_allows_surrounding_whitespace():
    ret = parse_plan_return("  PlanReturn path=/abs/plan.md  \n")
    assert ret.kind == "path"


# ── parse: 拒绝(ValidationError / exit 5)─────────────────────────────

@pytest.mark.parametrize("text", [
    "",                                   # 空
    "please see my plan, it is great",    # 评论小说:没有 PlanReturn 行
    "PlanReturn path=/a.md\n\nThanks!",   # 评论小说:附加散文
    "PlanReturn path=/a.md\nPlanReturn path=/b.md",  # 歧义多返回
    "PlanReturn",                         # 没有任何键
    "PlanReturn path=relative/plan.md",   # 相对路径
    "PlanReturn artifact=http://insecure.example/x",  # 未支持 scheme
    "PlanReturn artifact=ftp://x/y",      # 未支持 scheme
    "PlanReturn path=/a.md bogus=1",      # 未知键
    "PlanReturn path=/a.md path=/b.md",   # 重复键
    "PlanReturn path=",                   # 空值
    "PlanReturn /abs/plan.md",            # 裸 token(非 key=value)
    "PlanReturn path=/a.md artifact=https://x/y",     # 歧义组合
    "PlanReturn path=/a.md sha256=" + "a" * 64,       # 缺 host 的 sha256
    "PlanReturn host=artemis path=/a.md",             # host 缺 sha256
    "PlanReturn host=artemis sha256=" + "a" * 64,     # host 缺 path
    "PlanReturn host=artemis path=rel.md sha256=" + "a" * 64,  # host 相对路径
    "PlanReturn host=artemis path=/a.md sha256=xyz",  # sha256 非法
    "PlanReturn host=artemis path=/a.md sha256=" + "A" * 64,  # 必须小写
])
def test_parse_rejects_invalid_forms(text):
    with pytest.raises(ValidationError) as exc:
        parse_plan_return(text)
    assert exc.value.exit_code == 5
    # 报错即教学:给出可复制的正确形式
    assert "PlanReturn path=" in str(exc.value)


# ── resolve: 本地 path 快照 ──────────────────────────────────────────────

def test_resolve_path_snapshots_immutably(tmp_path):
    plan = _plan_file(tmp_path)
    snap = resolve_plan_return(
        PlanReturn(kind="path", path=str(plan)),
        plan_store_dir=_store_dir(tmp_path))
    assert snap.sha256 == PLAN_SHA
    assert snap.size == len(PLAN_BODY)
    assert os.path.basename(snap.snapshot_path) == f"{PLAN_SHA}.md"
    with open(snap.snapshot_path, "rb") as f:
        assert f.read() == PLAN_BODY
    assert snap.source == {"kind": "path", "path": str(plan)}


def test_resolve_path_is_idempotent(tmp_path):
    plan = _plan_file(tmp_path)
    first = resolve_plan_return(
        PlanReturn(kind="path", path=str(plan)),
        plan_store_dir=_store_dir(tmp_path))
    second = resolve_plan_return(
        PlanReturn(kind="path", path=str(plan)),
        plan_store_dir=_store_dir(tmp_path))
    assert first == second


# ── resolve: 无法安全解析 → NeedsDecision / exit 20 ─────────────────────

def test_resolve_missing_file_exit20(tmp_path):
    with pytest.raises(NeedsDecision) as exc:
        resolve_plan_return(
            PlanReturn(kind="path", path=str(tmp_path / "nope.md")),
            plan_store_dir=_store_dir(tmp_path))
    assert exc.value.exit_code == 20
    report = exc.value.report
    assert report["kind"] == "plan_return"
    assert "PlanReturn path=" in report["repair"]


def test_resolve_unreadable_file_exit20(tmp_path):
    plan = _plan_file(tmp_path)
    plan.chmod(stat.S_IWUSR)  # 去掉读权限
    try:
        with pytest.raises(NeedsDecision) as exc:
            resolve_plan_return(
                PlanReturn(kind="path", path=str(plan)),
                plan_store_dir=_store_dir(tmp_path))
        assert exc.value.exit_code == 20
    finally:
        plan.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_resolve_mutating_file_exit20(tmp_path, monkeypatch):
    plan = _plan_file(tmp_path)
    reads = {"n": 0}
    original = type(plan).read_bytes

    def flaky(self):
        reads["n"] += 1
        if self == plan and reads["n"] > 1:
            return PLAN_BODY + b"changed while reading"
        return original(self)

    monkeypatch.setattr(type(plan), "read_bytes", flaky)
    with pytest.raises(NeedsDecision) as exc:
        resolve_plan_return(
            PlanReturn(kind="path", path=str(plan)),
            plan_store_dir=_store_dir(tmp_path))
    assert exc.value.exit_code == 20
    assert "chang" in str(exc.value).lower()


# ── resolve: artifact 形式(注入 fetch,绝不 shell)──────────────────────

def test_resolve_artifact_requires_fetcher(tmp_path):
    ret = PlanReturn(kind="artifact", url="https://artifactd.example/x/1")
    with pytest.raises(NeedsDecision) as exc:
        resolve_plan_return(ret, plan_store_dir=_store_dir(tmp_path))
    assert exc.value.exit_code == 20


def test_resolve_artifact_via_injected_fetch(tmp_path):
    calls = []

    def fetch(source):
        calls.append(source)
        return PLAN_BODY

    ret = PlanReturn(kind="artifact", url="https://artifactd.example/x/1")
    snap = resolve_plan_return(
        ret, plan_store_dir=_store_dir(tmp_path), fetch=fetch)
    assert snap.sha256 == PLAN_SHA
    assert calls == [{"kind": "artifact", "url": "https://artifactd.example/x/1"}]


# ── resolve: host 形式(配置 allowlist + hash 绑定)──────────────────────

def test_resolve_host_not_allowed_exit20(tmp_path):
    ret = PlanReturn(kind="host", host="artemis",
                     path="/abs/plan.md", sha256=PLAN_SHA)
    with pytest.raises(NeedsDecision) as exc:
        resolve_plan_return(ret, plan_store_dir=_store_dir(tmp_path),
                            fetch=lambda s: PLAN_BODY, allowed_hosts=())
    assert exc.value.exit_code == 20


def test_resolve_host_hash_mismatch_exit20(tmp_path):
    ret = PlanReturn(kind="host", host="artemis",
                     path="/abs/plan.md", sha256="b" * 64)
    with pytest.raises(NeedsDecision) as exc:
        resolve_plan_return(ret, plan_store_dir=_store_dir(tmp_path),
                            fetch=lambda s: PLAN_BODY,
                            allowed_hosts=("artemis",))
    assert exc.value.exit_code == 20
    assert "mismatch" in str(exc.value).lower()


def test_resolve_host_via_injected_fetch(tmp_path):
    calls = []

    def fetch(source):
        calls.append(source)
        return PLAN_BODY

    ret = PlanReturn(kind="host", host="artemis",
                     path="/abs/plan.md", sha256=PLAN_SHA)
    snap = resolve_plan_return(ret, plan_store_dir=_store_dir(tmp_path),
                               fetch=fetch, allowed_hosts=("artemis",))
    assert snap.sha256 == PLAN_SHA
    assert calls == [{"kind": "host", "host": "artemis", "path": "/abs/plan.md"}]


# ── config: plan_gate 块 ────────────────────────────────────────────────

def test_resolve_plan_gate_defaults():
    gate = resolve_plan_gate({})
    assert gate["allowed_hosts"] == []
    assert gate["store_dir"]


def test_resolve_plan_gate_configured():
    gate = resolve_plan_gate({"plan_gate": {
        "store_dir": "/atlas/plans", "allowed_hosts": ["artemis"]}})
    assert gate == {"store_dir": "/atlas/plans", "allowed_hosts": ["artemis"]}


@pytest.mark.parametrize("raw", [
    {"plan_gate": "nope"},
    {"plan_gate": {"store_dir": 42}},
    {"plan_gate": {"allowed_hosts": "artemis"}},
    {"plan_gate": {"allowed_hosts": [42]}},
])
def test_resolve_plan_gate_rejects_malformed(raw):
    with pytest.raises(ValidationError) as exc:
        resolve_plan_gate(raw)
    assert exc.value.exit_code == 5
