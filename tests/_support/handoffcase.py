"""Helpers shared by several test modules (moved out of the test file that happened to define them)."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import pytest
from _support import samples
from omac.engines.multica import MulticaStore
from omac.engines.models import EngineConfig
from omac.errors import PlatformError


@pytest.fixture
def case(monkeypatch):
    metadata = samples.control_metadata_before_handoff()
    intent = samples.failed_handoff_intent()
    store = MulticaStore(EngineConfig("multica", "ws"))
    bodies = {}
    writes = []
    publications = []

    def issue():
        return {
            "id": "issue-1",
            "title": "node",
            "status": "in_review",
            "metadata": deepcopy(metadata),
        }

    def run(args, **kwargs):
        if args[:2] == ["issue", "get"]:
            return issue()
        if args[:3] == ["issue", "comment", "list"]:
            return [
                {
                    "id": ref["comment_id"],
                    "content": "- sha256: "
                    + ref["sha256"]
                    + "\n- bytes: "
                    + str(ref["bytes"]),
                    "attachments": [
                        {"id": ref["attachment_id"], "filename": ref["filename"]}
                    ],
                }
                for ref in publications
            ]
        if args[:3] == ["issue", "metadata", "set"]:
            key = args[args.index("--key") + 1]
            value = args[args.index("--value") + 1]
            candidate = {**metadata, key: value}
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > 8192:
                raise PlatformError("metadata exceeds the 8KB size limit")
            writes.append((key, value))
            metadata[key] = value
            return {}
        pytest.fail(str(args))

    def publish(item_id, key, content, suffix):
        digest = hashlib.sha256(content.encode()).hexdigest()
        ref = {
            "comment_id": "comment-" + digest[:8],
            "attachment_id": "attachment-" + digest[:8],
            "sha256": digest,
            "bytes": len(content.encode()),
            "filename": "omac-" + key + "-" + digest[:12] + suffix,
        }
        bodies[ref["attachment_id"]] = content
        publications.append(ref)
        return ref

    monkeypatch.setattr(store, "_run_multica", run)
    monkeypatch.setattr(store, "_publish_payload_comment", publish)
    monkeypatch.setattr(
        store,
        "_load_payload_comment",
        lambda _id, _key, ref: bodies[ref["attachment_id"]],
    )
    return store, metadata, intent, bodies, writes, publications, issue

