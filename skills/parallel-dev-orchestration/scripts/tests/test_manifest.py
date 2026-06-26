# tests/test_manifest.py
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import load_manifest, Manifest
from core.manifest import Node

def test_load_minimal_manifest(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "meta:\n  squad: dev team\n  integration_branch: feature/v1.0.0\n"
        "nodes:\n"
        "  - id: M0\n    worker: agent-be\n    blocked_by: []\n"
        "  - id: M1\n    worker: agent-fe\n    blocked_by: [M0]\n    reviewer: agent-rev\n    risk: high\n"
    )
    m = load_manifest(str(p))
    assert isinstance(m, Manifest)
    assert m.meta["integration_branch"] == "feature/v1.0.0"
    assert m.nodes["M1"].blocked_by == ["M0"]
    assert m.nodes["M1"].reviewer == "agent-rev"
    assert m.nodes["M0"].reviewer is None   # 可选旋钮缺省 None
    assert m.nodes["M1"].risk == "high"

def test_missing_required_worker_raises(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("meta: {squad: x}\nnodes:\n  - id: M0\n    blocked_by: []\n")
    with pytest.raises(ValueError, match="worker"):
        load_manifest(str(p))
