"""Full authenticated GH001 state, never a small replacement fixture."""
import gzip
import hashlib
from pathlib import Path
import json
import base64
import pytest
import yaml

from omac.core.manifest import load_manifest
from omac.errors import ValidationError

FIXTURE = Path(__file__).parent / "fixtures/full_state_transport"


def captured_bytes():
    raw = gzip.decompress((FIXTURE / "cd1615c57abaa6f1980124a3e8b9e1d081581949.blob.raw.gz").read_bytes())
    assert len(raw) == 105650411
    assert hashlib.sha256(raw).hexdigest() == "07826bb3ccb9db13cce23cfdff452cefa98bcf4212fe0a036a577431505f0232"
    return raw


def test_real_capture_explicit_transport_roundtrip(tmp_path):
    from omac.core.state_transport import encode, decode

    raw = captured_bytes()
    physical = encode(raw)
    assert len(physical) <= 90 * 1024 * 1024
    assert decode(physical) == raw
    legacy = tmp_path / "manifest.yaml"
    legacy.write_bytes(raw)
    original = load_manifest(str(legacy))
    legacy.write_bytes(physical)
    materialized = load_manifest(str(legacy))
    assert materialized == original
    assert len(materialized.nodes) == 183
    assert sum(n.status == "done" for n in materialized.nodes.values()) == 80
    assert len(materialized.meta) == 23


@pytest.mark.parametrize("change", [
    {"schema": "omac.full-state-transport/v2"}, {"encoding": "plain"},
    {"decoded_bytes": True}, {"decoded_bytes": 0}, {"decoded_bytes": 268435457},
    {"decoded_sha256": "0" * 64}, {"payload": "not-base64!"}, {"payload": None},
    {"payload": base64.b64encode(b"not gzip").decode()}, {"extra": True},
])
def test_exact_transport_negative_fields_before_overwrite(tmp_path, change):
    from omac.core.state_transport import encode, decode
    from omac.core.manifest import save_manifest
    plain = b"meta: {name: original}\nnodes: [{id: n, worker: w}]\n"
    value = yaml.safe_load(encode(plain))
    value.update(change)
    raw = yaml.safe_dump(value, sort_keys=False).encode()
    with pytest.raises(ValidationError):
        decode(raw)
    path = tmp_path / "manifest.yaml"
    path.write_bytes(raw)
    original = tmp_path / "original.yaml"
    original.write_bytes(plain)
    with pytest.raises(ValidationError):
        save_manifest(load_manifest(str(original)), str(path))
    assert path.read_bytes() == raw


def test_selected_save_preserves_format_and_plain_save_never_migrates(tmp_path):
    from omac.core.state_transport import encode, decode, is_transport
    from omac.core.manifest import save_manifest
    plain = b"meta: {name: original, unknown: null}\nnodes: [{id: n, worker: w}]\n"
    a, b = tmp_path / "plain.yaml", tmp_path / "transport.yaml"
    a.write_bytes(plain)
    b.write_bytes(encode(plain))
    state = load_manifest(str(a))
    save_manifest(state, str(a))
    save_manifest(state, str(b))
    assert not is_transport(a.read_bytes())
    assert is_transport(b.read_bytes())
    assert load_manifest(str(a)) == load_manifest(str(b)) == state


def test_duplicate_missing_nested_truncated_and_caps_reject(tmp_path, monkeypatch):
    from omac.core import state_transport as t
    plain = b"meta: {name: original}\nnodes: [{id: n, worker: w}]\n"
    value = yaml.safe_load(t.encode(plain))
    for broken in [t.encode(plain) + b"schema: omac.full-state-transport/v1\n",
                   yaml.safe_dump({k:v for k,v in value.items() if k!='decoded_sha256'}, sort_keys=False).encode(),
                   t.encode(plain)[:-4]]:
        with pytest.raises(ValidationError):
            t.decode(broken)
    with pytest.raises(ValidationError):
        t.encode(t.encode(plain))
    monkeypatch.setattr(t, "ENCODED_MAX", 8)
    with pytest.raises(ValidationError):
        t.encode(plain)
    with pytest.raises(ValidationError):
        t.decode(yaml.safe_dump(value, sort_keys=False).encode())


@pytest.mark.parametrize("form", ["json", "flow", "indented", "missing-schema"])
def test_alternate_yaml_envelopes_cannot_fall_through_legacy_loader(form):
    from omac.core.state_transport import encode, decode
    plain = b"meta: {name: original}\nnodes: []\n"
    value = yaml.safe_load(encode(plain))
    value["decoded_sha256"] = "0" * 64
    if form == "json":
        raw = json.dumps(value).encode()
    elif form == "flow":
        raw = yaml.safe_dump(value, default_flow_style=True).encode()
    elif form == "indented":
        raw = b"\n".join(b"  " + line for line in yaml.safe_dump(value).encode().splitlines())
    else:
        del value["schema"]
        raw = yaml.safe_dump(value).encode()
    with pytest.raises(ValidationError):
        decode(raw)


def test_legacy_nested_schema_and_flow_manifest_remain_legacy():
    from omac.core.state_transport import decode
    for plain in [b"meta:\n  schema: historical\nnodes: []\n",
                  b'{"meta": {"schema": "historical"}, "nodes": []}']:
        assert decode(plain) == plain


@pytest.mark.parametrize("form", ["document-flow", "directive-indent", "bom", "tab"])
def test_document_forms_do_not_bypass_exact_transport_validation(form):
    from omac.core.state_transport import encode, decode
    value = yaml.safe_load(encode(b"meta: {name: original}\nnodes: []\n"))
    value["decoded_sha256"] = "0" * 64
    if form == "document-flow":
        raw = b"--- " + json.dumps(value).encode()
    elif form == "directive-indent":
        raw = b"%YAML 1.1\n---\n" + b"\n".join(b"  " + line for line in yaml.safe_dump(value).encode().splitlines())
    elif form == "bom":
        raw = b"\xef\xbb\xbf" + yaml.safe_dump(value).encode()
    else:
        raw = b"\n".join(b"\t" + line for line in yaml.safe_dump(value).encode().splitlines())
    with pytest.raises(ValidationError):
        decode(raw)
