"""Current Root qualification of authentic historical assessment sources."""
import copy
import json
from pathlib import Path

import pytest

from test_ui_preserved_owner_assessment import captured, ALLOWED
from omac.core.manifest import load_manifest
from omac.core.owner_amendment import digest, manifest_source, JOURNAL
from omac.engines.models import WorkItem, WorkItemStatus
from omac.core.taskmeta import TaskKind, TaskPhase, Bounces, parse_worker_handoff
from omac.engines.multica import MulticaRuntime

FIXTURES = Path(__file__).parent / "fixtures/current_owner_source"


@pytest.fixture
def current(captured):
    c = captured
    Path(c.path).write_bytes((FIXTURES / "manifest.yaml").read_bytes())
    c.manifest = load_manifest(c.path)
    raw_runs = json.loads((FIXTURES / "native-runs.json").read_bytes())
    for key in ALLOWED:
        value = json.loads((FIXTURES / (key + ".json")).read_bytes())
        item = WorkItem(**{
            **value, "status": WorkItemStatus[value["status"].split(".")[-1]],
            "kind": TaskKind[value["kind"].split(".")[-1]],
            "phase": TaskPhase[value["phase"].split(".")[-1]],
            "bounces": Bounces(**value["bounces"]),
            "worker_handoff": parse_worker_handoff(value["worker_handoff"]),
        })
        c.items[item.id] = item
        # Reuse the actual adapter's parser over supplied captured bytes only.
        adapter = object.__new__(MulticaRuntime)
        adapter._issue_runs = lambda issue, data=raw_runs[key]: copy.deepcopy(data)
        c.runs[item.id] = adapter.list_runs(item.id)
    assert digest(manifest_source(c.manifest)) == "bc4c98f037ada6627b61513a5aeaf90f9ff2082e1466960370662acbbe4e51c3"
    assert len(c.manifest.meta[JOURNAL]) == 3
    return c


def public_args(c):
    args = ["dag", "amend", "prepare-owner", c.path,
            "--blocked-node", "identity-local", "--report-file", c.report,
            "--docs", c.docs[0], "--output-file", c.output,
            "--source-witness-file", c.witness]
    for node in ALLOWED:
        args += ["--allowed-node", node]
    return args


def bind_cli(c, monkeypatch):
    import omac.cli.commands.dag as dag
    monkeypatch.setattr(dag, "_assemble_engine", lambda args: (c.engine, None))
    monkeypatch.setattr(dag, "commit_manifest", lambda *args, **kwargs: None)


def test_authentic_current_public_legacy_prepare_is_still_red(current, monkeypatch, capsys):
    from omac.cli.main import main
    c = current
    bind_cli(c, monkeypatch)
    before = Path(c.path).read_bytes()
    assert main(public_args(c)) == 5
    assert "Complete qualified source/manifest/budget identity changed" in capsys.readouterr().err
    assert Path(c.path).read_bytes() == before
    assert not Path(c.output).exists()


def test_fresh_current_root_qualification_enables_public_prepare_resolve(current):
    from omac.pipeline.owner_amendment import (
        prepare_current_owner_source, resolve_current_owner_source,
        prepare_owner_amendment, resolve_owner_amendment,
    )
    from omac.core.owner_amendment import authorized_request
    c = current
    history = copy.deepcopy(c.manifest.meta[JOURNAL])
    source_file = str(Path(c.output).with_name("current-source.json"))
    draft = prepare_current_owner_source(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=source_file,
        source_witness_file=c.witness,
    )
    assert draft["state"] == "pending_current_source_resolution"
    resolve_current_owner_source(
        c.engine, c.path, source_file, source_sha256=draft["source_sha256"],
        authority="Explicit offline Root current-source fixture",
        reason="Assessment only; preserve every old entry and Product obligation",
    )
    result = prepare_owner_amendment(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=c.output,
        source_witness_file=c.witness, current_source_file=source_file,
    )
    resolve_owner_amendment(
        c.engine, c.path, c.output, request_sha256=result["request_sha256"],
        authority="Explicit offline Root assessment resolution",
        reason="New independent assessment only",
    )
    m = load_manifest(c.path)
    entry = authorized_request(m, result["request_sha256"], c.engine.store, c.engine.runtime)
    assert entry["state"] == "approved"
    assert {k: m.meta[JOURNAL][k] for k in history} == history
    from omac.core.owner_amendment import owner_request
    ref = owner_request(entry)["current_source_qualification"]["input"]
    assert json.loads(Path(ref["file"]).read_bytes())["owner_history"] == history
