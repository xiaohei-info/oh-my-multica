"""Use the installed YAML accelerator without changing retained state values."""
from datetime import datetime, timezone

import pytest
import yaml

from omac.core.manifest import Manifest, load_manifest, save_manifest


@pytest.mark.parametrize("native", [True, False])
def test_manifest_dumper_preserves_values_and_has_portable_fallback(tmp_path, monkeypatch, native):
    if native and not hasattr(yaml, "CDumper"):
        pytest.skip("Optional LibYAML accelerator unavailable")
    expected = yaml.CDumper if native else yaml.Dumper
    if not native:
        monkeypatch.delattr(yaml, "CDumper", raising=False)
    shared = {"unicode": "中文 😀", "multiline": "one\ntwo\n", "binary": b"\x00\xff"}
    meta = {"original": shared, "history": [shared], "timestamp": datetime(2026, 10, 10, tzinfo=timezone.utc),
            "quoted": "true", "empty": "", "nothing": None}
    original_dump = yaml.dump
    dumpers = []
    path = tmp_path / "state.yaml"

    def observe(*args, **kwargs):
        dumpers.append(kwargs.get("Dumper", yaml.Dumper))
        return original_dump(*args, **kwargs)

    monkeypatch.setattr(yaml, "dump", observe)
    save_manifest(Manifest(meta=meta, nodes={}), str(path))
    assert dumpers == [expected]
    loaded = load_manifest(str(path))
    assert loaded.meta == meta
    assert meta["original"] is meta["history"][0]
    assert not list(tmp_path.glob("*.tmp"))
