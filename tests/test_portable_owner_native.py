import base64
import hashlib
from types import SimpleNamespace

import pytest

from omac.engines.models import EngineConfig
from omac.engines.multica import MulticaStore
from omac.errors import PlatformError


def test_native_owner_source_lost_upload_ack_is_observed_without_republication(monkeypatch):
    store = MulticaStore(EngineConfig("multica", "ws"))
    comments, bodies, uploads = [], {}, []
    monkeypatch.setattr(store, "_run_multica", lambda *a, **kw: comments)
    monkeypatch.setattr(store, "observe_work_item_control", lambda _: SimpleNamespace(work_item=SimpleNamespace(platform_assignee_id=None)))
    def publish(issue, key, text, suffix):
        uploads.append(text)
        raw = text.encode()
        sha = hashlib.sha256(raw).hexdigest()
        ref = {"comment_id": "comment", "attachment_id": "attachment", "filename": "omac-owner-source-" + sha[:12] + suffix,
               "sha256": sha, "bytes": len(raw)}
        comments.append({"id": "comment", "content": "- sha256: " + sha + "\n- bytes: " + str(len(raw)),
                         "attachments": [{"id": "attachment", "filename": ref["filename"], "sha256": sha, "bytes": len(raw)}]})
        bodies["attachment"] = raw
        raise PlatformError("native upload committed but response lost")
    monkeypatch.setattr(store, "_publish_payload_comment", publish)
    monkeypatch.setattr(store, "_download_attachment_bytes", lambda attachment, *a, **kw: bodies[attachment])
    raw = b"\x00exact full native source\xff"
    with pytest.raises(PlatformError, match="response lost"):
        store.publish_source_artifact("assessment", raw)
    ref = store.publish_source_artifact("assessment", raw)
    assert store.read_source_artifact(ref) == raw
    assert len(uploads) == 1
    with pytest.raises(PlatformError, match="identity changed"):
        store.read_source_artifact({**ref, "attachment_id": "foreign"})
    bodies["attachment"] = bodies["attachment"][:-1]
    with pytest.raises(PlatformError, match="digest"):
        store.read_source_artifact(ref)


def test_native_owner_source_bound_is_checked_before_any_remote_call(monkeypatch):
    store = MulticaStore(EngineConfig("multica", "ws"))
    monkeypatch.setattr(store, "_run_multica", lambda *a, **kw: pytest.fail("no remote effect"))
    with pytest.raises(PlatformError, match="1 MiB"):
        store.publish_source_artifact("assessment", b"x" * (1024 * 1024 + 1))
