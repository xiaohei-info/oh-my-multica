"""引擎层桥接（方案 a / 单一事实源）。

executor 与 orchestrator 共用同一套协作引擎（engines/）与证据校验（core.evidence）。
为遵守「单一事实源：每条口径只有一个权威出处，禁止平行拷贝」，本 skill 不拷贝引擎层，
而是把 orchestration skill 的 scripts 目录加入 sys.path、直接引用其权威实现。

代价：executor 与 orchestration 必须作为同仓同级 skill 一起安装（本来就是一个
parallel-dev-skills bundle）。import 本模块即完成引擎层接入：

    import _engine_path  # noqa: F401
    from engines import create_engine_from_env
    from core.evidence import validate_worker_evidence
"""
import sys
from pathlib import Path

# .../skills/parallel-dev-executor/scripts/_engine_path.py
#   parents[2] == .../skills
ENGINE_HOME = Path(__file__).resolve().parents[2] / "parallel-dev-orchestration" / "scripts"

if not ENGINE_HOME.is_dir():
    raise RuntimeError(
        f"找不到引擎层目录: {ENGINE_HOME}\n"
        "  → parallel-dev-executor 依赖 parallel-dev-orchestration 同仓同级安装"
        "（共用引擎层，单一事实源，不平行拷贝）。"
    )

_engine_home_str = str(ENGINE_HOME)
if _engine_home_str not in sys.path:
    sys.path.insert(0, _engine_home_str)
