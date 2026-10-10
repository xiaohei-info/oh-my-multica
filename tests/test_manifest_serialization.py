"""Use the installed YAML accelerator without changing retained state values."""
from datetime import datetime, timezone

import pytest
import yaml

from omac.core.manifest import Manifest, load_manifest, save_manifest
from omac.core.state_transport import decode, encode


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("selected_transport", [False, True])
def test_manifest_dumper_preserves_values_and_has_portable_fallback(tmp_path, monkeypatch, native, selected_transport):
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
    if selected_transport:
        path.write_bytes(encode(b"meta: {}\nnodes: []\n"))

    def observe(*args, **kwargs):
        dumpers.append(kwargs.get("Dumper", yaml.Dumper))
        return original_dump(*args, **kwargs)

    monkeypatch.setattr(yaml, "dump", observe)
    save_manifest(Manifest(meta=meta, nodes={}), str(path))
    assert dumpers == ([expected, getattr(yaml, "CSafeDumper", yaml.SafeDumper)]
                       if selected_transport else [expected])
    loaded = load_manifest(str(path))
    assert loaded.meta == meta
    assert meta["original"] is meta["history"][0]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("native", [True, False])
def test_transport_safe_dumper_keeps_exact_decoded_bytes(monkeypatch, native):
    if native and not hasattr(yaml, "CSafeDumper"):
        pytest.skip("Optional LibYAML accelerator unavailable")
    expected = yaml.CSafeDumper if native else yaml.SafeDumper
    if not native:
        monkeypatch.delattr(yaml, "CSafeDumper", raising=False)
    original_dump = yaml.dump
    dumpers = []

    def observe(*args, **kwargs):
        dumpers.append(kwargs.get("Dumper", yaml.Dumper))
        return original_dump(*args, **kwargs)

    monkeypatch.setattr(yaml, "dump", observe)
    raw = 'meta:\n  retained: "中文 😀"\nnodes: []\n'.encode()
    physical = encode(raw)
    assert dumpers == [expected]
    assert decode(physical) == raw
    # Native and Python emitters retain the same ordinary envelope fields.
    assert yaml.safe_load(physical)["decoded_bytes"] == len(raw)
