"""Managed owner context survives a different filesystem and exact-byte checks."""
import hashlib
from copy import deepcopy
from unittest.mock import Mock

import pytest

from omac.errors import ValidationError


def test_portable_source_is_read_without_operator_files(tmp_path, monkeypatch):
    from omac.pipeline.portable_owner import publish_sources, read_source
    raw = b'{"history": [1, 2, 3], "opaque": null}\n'
    file = tmp_path / "operator-only.json"
    file.write_bytes(raw)
    from types import SimpleNamespace
    item = SimpleNamespace(id="assessment", project_rules=None, source_refs=[])
    store = Mock()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    def update(_, **values):
        for key, value in values.items():
            setattr(item, key, value)
    store.update_work_item_metadata.side_effect = update
    bodies = {}
    def publish(item_id, content):
        sha = hashlib.sha256(content).hexdigest()
        bodies[sha] = content
        return {"issue_id": item_id, "sha256": sha, "bytes": len(content)}
    store.publish_source_artifact.side_effect = publish
    store.read_source_artifact.side_effect = lambda ref: bodies[ref["sha256"]]
    refs = publish_sources(store, "assessment", {"owner-request": file})
    file.unlink()
    assert read_source(store, refs[0], entry="owner-request") == raw
    bad = deepcopy(refs[0])
    bad["content_bytes"] += 1
    with pytest.raises(ValidationError):
        read_source(store, bad)
    item.project_rules = item.project_rules[:-1]
    with pytest.raises(ValidationError):
        read_source(store, refs[0])


def test_public_work_read_materializes_same_source_for_both_roles(tmp_path, monkeypatch, capsys):
    from omac.engines import create_engine
    from omac.engines.models import EngineConfig
    from omac.core.taskmeta import TaskKind, TaskPhase
    from omac.cli.main import main
    import omac.cli.commands.work as command
    from omac.pipeline.portable_owner import publish_sources
    engine = create_engine("mock", EngineConfig("mock", "ws", extra={"MOCK_AUTO_COMPLETE": "false"}))
    item = engine.store.create_work_item("ws", "assessment", "exact Source", "amend-owner-example", "planner", kind=TaskKind.AMENDMENT)
    file = tmp_path / "operator-only.json"
    raw = b'{"full": "current", "opaque": null}\\n'
    file.write_bytes(raw)
    publish_sources(engine.store, item.id, {"owner-request": file}, owner_resolution="root", approval="exact")
    file.unlink()
    monkeypatch.setattr(command, "_resolve_store", lambda: engine.store)
    for phase in (TaskPhase.AUTHORING, TaskPhase.REVIEW):
        item.phase = phase
        target = tmp_path / (phase.value + ".json")
        assert main(["work", "read", item.id, "--source", "owner-source", "--entry", "owner-request", "--output-file", str(target)]) == 0
        assert target.read_bytes() == raw
        capsys.readouterr()
