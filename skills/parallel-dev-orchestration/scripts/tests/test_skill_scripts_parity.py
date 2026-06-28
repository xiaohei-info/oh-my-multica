"""两个 skill 的 scripts/ 必须逐字一致。

executor 自带完整引擎层（以 orchestration 为权威源、整体复制），这样 Multica 按 agent 隔离
物化 skill 时 executor 也能自给自足。开发只改 orchestration，改完跑 sync_to_executor.sh。
本测试拦截"改了没同步"导致的两边漂移（executor 的 agent_cli 会与 runner 行为不一致）。
"""
import hashlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "skills/parallel-dev-orchestration/scripts"
DST = REPO / "skills/parallel-dev-executor/scripts"

_IGNORE_PARTS = {"__pycache__", ".pytest_cache"}


def _index(root: Path) -> dict:
    files = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in _IGNORE_PARTS for part in rel.parts):
            continue
        if p.is_file() and p.suffix != ".pyc":
            files[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


@pytest.mark.skipif(
    not (SRC.is_dir() and DST.is_dir()),
    reason="两个 skill 目录需同仓共存（Multica 单 skill 物化时此测试无意义，跳过）",
)
def test_executor_scripts_mirror_orchestration():
    src, dst = _index(SRC), _index(DST)
    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    differ = sorted(k for k in src.keys() & dst.keys() if src[k] != dst[k])

    problems = []
    if missing:
        problems.append(f"executor 缺失: {missing}")
    if extra:
        problems.append(f"executor 多余: {extra}")
    if differ:
        problems.append(f"内容不一致: {differ}")

    assert not problems, (
        "两个 skill 的 scripts/ 未同步——在 orchestration 改完后跑 sync_to_executor.sh：\n"
        + "\n".join(problems)
    )
