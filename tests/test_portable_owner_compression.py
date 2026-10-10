import hashlib
import json
import zlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omac.errors import ValidationError
from omac.pipeline import portable_owner as portable


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def remote():
    item = SimpleNamespace(id='assessment', project_rules=None, source_refs=[])
    bodies = {}
    store = Mock()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    def update(_, **values):
        item.__dict__.update(values)
    store.update_work_item_metadata.side_effect = update
    def publish(issue, body):
        assert 0 < len(body) <= 1024 * 1024
        bodies[sha(body)] = body
        return {'issue_id': issue, 'sha256': sha(body), 'bytes': len(body)}
    store.publish_source_artifact.side_effect = publish
    store.read_source_artifact.side_effect = lambda ref: bodies[ref['sha256']]
    return store, item, bodies


def repin(item, frame):
    item.project_rules = json.dumps(frame, sort_keys=True)
    raw = item.project_rules.encode()
    ref = {'issue_id': item.id, 'content_bytes': len(raw), 'content_sha256': sha(raw)}
    return ref


def test_compress_complete_binary_and_preserve_raw_authority(tmp_path, remote):
    store, item, bodies = remote
    raw = bytes(range(256)) * 10000
    path = tmp_path / 'source'
    path.write_bytes(raw)
    authority = {'owner_resolution': 'root', 'approval_sha256': 'approval', 'files': {
        'source': {'provenance': str(path), 'bytes': len(raw), 'sha256': sha(raw)}}}
    ref = portable.publish_sources(store, item.id, {'source': path}, owner_resolution='root', approval='approval', expected=authority)[0]
    frame = json.loads(item.project_rules)
    value = frame['files']['source']
    assert frame['schema'] == 'omac.portable-owner-source/v2'
    assert value['transport']['encoding'] == 'zlib'
    assert len(value['chunks']) == 1
    assert {k: value[k] for k in ('provenance', 'bytes', 'sha256')} == authority['files']['source']
    path.unlink()
    assert portable.read_source(store, ref, entry='source') == raw
    store.read_source_artifact.assert_called()


@pytest.mark.parametrize('raw', [b'', b'\x00', bytes(range(256))])
def test_identity_fallback_and_empty_file(tmp_path, remote, raw):
    store, item, _ = remote
    path = tmp_path / 'source'
    path.write_bytes(raw)
    ref = portable.publish_sources(store, item.id, {'source': path})[0]
    assert json.loads(item.project_rules)['files']['source']['transport']['encoding'] == 'identity'
    assert portable.read_source(store, ref, entry='source') == raw


def test_same_request_resume_keeps_existing_v1_frame(tmp_path, remote):
    store, item, bodies = remote
    path = tmp_path / 'source'
    raw = b'old complete history' * 100000
    path.write_bytes(raw)
    chunks = [store.publish_source_artifact(item.id, raw[i:i+1048576]) for i in range(0, len(raw), 1048576)]
    frame = {'schema': 'omac.portable-owner-source/v1', 'owner_resolution': 'root', 'approval_sha256': 'approval',
             'files': {'source': {'provenance': str(path), 'bytes': len(raw), 'sha256': sha(raw), 'chunks': chunks}}}
    item.project_rules = json.dumps(frame, ensure_ascii=False, sort_keys=True)
    original = item.project_rules
    ref = portable.publish_sources(store, item.id, {'source': path}, owner_resolution='root', approval='approval')[0]
    assert item.project_rules == original
    assert portable.read_source(store, ref, entry='source') == raw
    before = deepcopy(item.source_refs)
    assert portable.publish_sources(store, item.id, {'source': path}, owner_resolution='root', approval='approval') == before
    assert item.project_rules == original


@pytest.mark.parametrize('damage', ['codec', 'wire-sha', 'wire-size', 'raw-sha', 'raw-size', 'bool-size', 'cap', 'truncated', 'trailing', 'multistream', 'expansion', 'foreign', 'chunk-size', 'extra-transport'])
def test_reject_malformed_compressed_source(tmp_path, remote, damage):
    store, item, bodies = remote
    path = tmp_path / 'source'
    raw = b'complete history\x00\xff' * 10000
    path.write_bytes(raw)
    portable.publish_sources(store, item.id, {'source': path})
    frame = json.loads(item.project_rules)
    value = frame['files']['source']
    transport = value['transport']
    chunk = value['chunks'][0]
    if damage == 'codec': transport['encoding'] = 'unknown'
    elif damage == 'wire-sha': transport['sha256'] = '0' * 64
    elif damage == 'wire-size': transport['bytes'] += 1
    elif damage == 'raw-sha': value['sha256'] = '0' * 64
    elif damage == 'raw-size': value['bytes'] += 1
    elif damage == 'bool-size': transport['bytes'] = True
    elif damage == 'cap': value['bytes'] = 256 * 1024 * 1024 + 1
    elif damage == 'expansion': value['bytes'] = 1
    elif damage == 'foreign': chunk['issue_id'] = 'foreign'
    elif damage == 'extra-transport': transport['unexpected'] = True
    else:
        wire = bodies[chunk['sha256']]
        wire = wire[:-1] if damage == 'truncated' else wire + (zlib.compress(b'extra') if damage == 'multistream' else b'extra')
        if damage == 'chunk-size':
            bodies[chunk['sha256']] = wire
        else:
            chunk = store.publish_source_artifact(item.id, wire)
            value['chunks'] = [chunk]
            transport.update(bytes=len(wire), sha256=sha(wire))
    ref = repin(item, frame)
    with pytest.raises(ValidationError):
        portable.read_source(store, ref, entry='source')


def test_all_files_preflight_before_effect(tmp_path, remote, monkeypatch):
    store, item, _ = remote
    path = tmp_path / 'source'
    path.write_bytes(b'ok')
    monkeypatch.setattr(portable, 'DECODED_MAX', 2)
    with pytest.raises(ValidationError):
        portable.publish_sources(store, item.id, {'source': path}, contents={'source': b'too large'})
    store.publish_source_artifact.assert_not_called()


def test_v2_duplicate_resume_and_mixed_history(tmp_path, remote):
    store, item, bodies = remote
    path = tmp_path / 'source'
    path.write_bytes(b'complete history' * 100000)
    ref = portable.publish_sources(store, item.id, {'source': path})[0]
    original = item.project_rules
    assert portable.publish_sources(store, item.id, {'source': path}) == [ref]
    assert item.project_rules == original
    # Another immutable historical v1 frame remains independently readable.
    frame = json.loads(original)
    frame['schema'] = 'omac.portable-owner-source/v1'
    value = frame['files']['source']
    value.pop('transport')
    raw = path.read_bytes()
    value['chunks'] = [store.publish_source_artifact(item.id, raw[i:i+1048576]) for i in range(0, len(raw), 1048576)]
    old_ref = repin(item, frame)
    assert portable.read_source(store, old_ref, entry='source') == raw
    item.project_rules = original
    assert portable.read_source(store, ref, entry='source') == raw


def test_compressed_frame_preserves_complete_approval_and_history(tmp_path, remote):
    from omac.core.owner_amendment import digest
    store, item, bodies = remote
    request = {"history": ["retained failure", "old DONE"] * 10000}
    raw = json.dumps(request).encode()
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    authority = {"owner_resolution": digest(request), "approval_sha256": "approval",
                 "owner_history_sha256": "complete-history",
                 "files": {"owner-request": {"provenance": str(path), "bytes": len(raw), "sha256": sha(raw)}}}
    entry = {"request": request, "approval_sha256": "approval", "portable_authority": authority}
    ref = portable.publish_sources(store, item.id, {"owner-request": path}, owner_resolution=digest(request),
                                   approval="approval", expected=authority)[0]
    assert portable.verify_approved_source(store, ref, entry)["owner_history_sha256"] == "complete-history"
    original = deepcopy(entry)
    for field in ("owner_history_sha256", "approval_sha256"):
        bad = deepcopy(entry)
        bad["portable_authority"][field] = "changed"
        with pytest.raises(ValidationError):
            portable.verify_approved_source(store, ref, bad)
    assert entry == original


def test_unknown_publication_does_not_publish_index(tmp_path, remote):
    from omac.errors import PlatformError
    store, item, _ = remote
    path = tmp_path / "source"
    path.write_bytes(b"full history" * 1000)
    store.publish_source_artifact.side_effect = PlatformError("offline unknown outcome")
    with pytest.raises(PlatformError, match="unknown"):
        portable.publish_sources(store, item.id, {"source": path})
    assert store.publish_source_artifact.call_count == 1
    store.update_work_item_metadata.assert_not_called()
    assert item.project_rules is None


def test_old_partial_chunks_remain_immutable_on_v2_resume(tmp_path, remote):
    store, item, bodies = remote
    path = tmp_path / "source"
    raw = b"complete raw Source" * 100000
    path.write_bytes(raw)
    # A former v1 publication stopped before index adoption.
    old = store.publish_source_artifact(item.id, raw[:1048576])
    old_body = bodies[old["sha256"]]
    ref = portable.publish_sources(store, item.id, {"source": path})[0]
    assert bodies[old["sha256"]] == old_body
    assert portable.read_source(store, ref, entry="source") == raw
    assert portable.publish_sources(store, item.id, {"source": path}) == [ref]


def test_v2_resume_preserves_wire_across_compressor_versions(tmp_path, remote, monkeypatch):
    store, item, _ = remote
    path = tmp_path / "source"
    raw = b"immutable full history" * 100000
    path.write_bytes(raw)
    compressor = zlib.compress
    monkeypatch.setattr(portable.zlib, "compress", lambda data: compressor(data, 1))
    ref = portable.publish_sources(store, item.id, {"source": path})[0]
    original = item.project_rules
    publications = store.publish_source_artifact.call_count
    reads = store.read_source_artifact.call_count
    monkeypatch.setattr(portable.zlib, "compress", lambda data: pytest.fail("existing immutable frame must not be recompressed"))
    assert portable.publish_sources(store, item.id, {"source": path}) == [ref]
    assert item.project_rules == original
    assert store.publish_source_artifact.call_count == publications
    assert store.read_source_artifact.call_count > reads
    path.write_bytes(raw + b"changed")
    with pytest.raises(ValidationError):
        portable.publish_sources(store, item.id, {"source": path})
    assert item.project_rules == original
