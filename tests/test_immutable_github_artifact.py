"""Frozen GitHub responses: no CLI/network or binary execution."""
import base64
import hashlib
import json
import subprocess

import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from omac.engines.models import EngineConfig
from omac.engines.multica import MulticaStore
import omac.engines.multica as multica
from omac.errors import PlatformError


FIXTURE = Path(__file__).parent / "fixtures/immutable_github_binary"
URL = (
    "https://github.com/xiaohei-info/open-agent-cluster/blob/"
    "7c47fd53bcad5e3551ff2eccfe99b56f9a9d4bc4/"
    "artifacts/agentrun-malformed-marker-fixture/release/agentrun-job"
)


def test_frozen_large_file_contents_none_reads_exact_git_blob(monkeypatch):
    contents = (FIXTURE / "contents.json").read_bytes()
    blob = (FIXTURE / "git-blob.json").read_bytes()
    expected = (FIXTURE / "binary").read_bytes()
    assert len(expected) == 6357118
    assert hashlib.sha256(expected).hexdigest() == (
        "e6fa73538b354ab51832de3b04d8da84ff6d5934a5d1adefe3c2c56a31295a12"
    )
    call = Mock(side_effect=[
        SimpleNamespace(returncode=0, stdout=contents, stderr=b""),
        SimpleNamespace(returncode=0, stdout=blob, stderr=b""),
    ])
    monkeypatch.setattr(multica.subprocess, "run", call)
    store = MulticaStore(EngineConfig("multica", "ws"), sleeper=lambda _: None)
    assert store.read_immutable_artifact(URL) == expected
    assert [c.args[0] for c in call.call_args_list] == [
        ["gh", "api", "repos/xiaohei-info/open-agent-cluster/contents/"
         "artifacts/agentrun-malformed-marker-fixture/release/agentrun-job"
         "?ref=7c47fd53bcad5e3551ff2eccfe99b56f9a9d4bc4"],
        ["gh", "api", "repos/xiaohei-info/open-agent-cluster/git/blobs/"
         "b871d9d327c809c0569f9c6748a2d3c97570921e"],
    ]

def _payloads(body=b"binary\x00data"):
    oid = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
    return (
        {"type": "file", "path": "file", "encoding": "none", "content": "",
         "size": len(body), "sha": oid},
        {"encoding": "base64", "content": base64.b64encode(body).decode(),
         "size": len(body), "sha": oid},
    )


def _response(payload):
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode(), stderr=b"")


def _read(monkeypatch, contents, blob):
    call = Mock(side_effect=[_response(contents), _response(blob)])
    monkeypatch.setattr(multica.subprocess, "run", call)
    store = MulticaStore(EngineConfig("multica", "ws"), sleeper=lambda _: None)
    return store, call


def test_response_urls_are_not_followed(monkeypatch):
    contents, blob = _payloads()
    contents.update(git_url="https://evil.test/blob", download_url="https://evil.test/raw")
    contents["_links"] = {"git": "https://evil.test/redirect"}
    store, call = _read(monkeypatch, contents, blob)
    assert store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file") == b"binary\x00data"
    assert call.call_args_list[1].args[0] == ["gh", "api", "repos/acme/repo/git/blobs/" + contents["sha"]]


@pytest.mark.parametrize("changes", [
    {"type": "dir"}, {"type": "symlink"}, {"encoding": "utf-8"},
    {"path": "other"}, {"content": "unexpected"}, {"sha": None},
    {"sha": "b" * 39}, {"sha": 1}, {"size": -1}, {"size": True}, {"size": 1.0},
])
def test_invalid_contents_does_not_request_blob(monkeypatch, changes):
    contents, blob = _payloads()
    contents.update(changes)
    store, call = _read(monkeypatch, contents, blob)
    with pytest.raises(PlatformError):
        store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file")
    assert call.call_count == 1


@pytest.mark.parametrize("changes", [
    {"encoding": "none"}, {"content": "%%%"}, {"content": None},
    {"content": base64.b64encode(b"other bytes").decode()},
    {"sha": "0" * 40}, {"size": 999}, {"size": True}, {"size": 1.0},
])
def test_invalid_blob_never_returns_bytes(monkeypatch, changes):
    contents, blob = _payloads()
    blob.update(changes)
    store, call = _read(monkeypatch, contents, blob)
    with pytest.raises(PlatformError):
        store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file")
    assert call.call_count == 2


@pytest.mark.parametrize("bad", [[], None, "not an object"])
def test_nonobject_blob_fails_closed(monkeypatch, bad):
    contents, _ = _payloads()
    store, call = _read(monkeypatch, contents, bad)
    with pytest.raises(PlatformError):
        store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file")
    assert call.call_count == 2


def test_original_size_cap_rejects_before_blob_request(monkeypatch):
    contents, blob = _payloads()
    contents["size"] = 16 * 1024 * 1024 + 1
    store, call = _read(monkeypatch, contents, blob)
    with pytest.raises(PlatformError, match="16 MiB"):
        store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file")
    assert call.call_count == 1


def test_exact_existing_size_cap_is_supported(monkeypatch):
    body = b"\x00" * (16 * 1024 * 1024)
    contents, blob = _payloads(body)
    store, call = _read(monkeypatch, contents, blob)
    assert store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file") == body
    assert call.call_count == 2


def test_blob_transient_transport_retry_stays_on_pinned_read(monkeypatch):
    contents, blob = _payloads()
    call = Mock(side_effect=[
        _response(contents),
        SimpleNamespace(returncode=1, stdout=b"", stderr=b"net/http: TLS handshake timeout"),
        _response(contents), _response(blob),
    ])
    monkeypatch.setattr(multica.subprocess, "run", call)
    store = MulticaStore(EngineConfig("multica", "ws"), sleeper=lambda _: None)
    assert store.read_immutable_artifact("https://github.com/acme/repo/blob/" + "a" * 40 + "/file") == b"binary\x00data"
    assert call.call_count == 4
    assert call.call_args_list[0].args == call.call_args_list[2].args
    assert call.call_args_list[1].args == call.call_args_list[3].args


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "hard-error", "malformed-json"])
def test_blob_transport_unknown_is_bounded_and_never_uses_other_transport(monkeypatch, failure):
    contents, _ = _payloads()
    url = "https://github.com/acme/repo/blob/" + "a" * 40 + "/file"
    def api(args, **kwargs):
        if "/contents/" in args[2]:
            return _response(contents)
        assert args == ["gh", "api", "repos/acme/repo/git/blobs/" + contents["sha"]]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        if failure == "unavailable":
            raise OSError("CLI unavailable")
        if failure == "malformed-json":
            return SimpleNamespace(returncode=0, stdout=b"{", stderr=b"")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"HTTP 404")
    call = Mock(side_effect=api)
    monkeypatch.setattr(multica.subprocess, "run", call)
    store = MulticaStore(EngineConfig("multica", "ws"), sleeper=lambda _: None)
    with pytest.raises(PlatformError):
        store.read_immutable_artifact(url)
    assert 2 <= call.call_count <= 2 * multica._MULTICA_READ_MAX_ATTEMPTS
