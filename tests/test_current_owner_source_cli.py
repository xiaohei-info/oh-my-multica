"""Public operator boundary and complete normal independent-review context."""
import json
from pathlib import Path

import pytest

from test_ui_preserved_owner_assessment import captured, ALLOWED
from test_current_owner_source import public_args, bind_cli
from omac.cli.main import main


pytestmark = pytest.mark.integration


def approve_public(c, monkeypatch, capsys):
    bind_cli(c, monkeypatch)
    args = public_args(c)
    source_file = str(Path(c.output).with_name("current-source.json"))
    args[2] = "prepare-owner-source"
    args[args.index("--output-file") + 1] = source_file
    before = Path(c.path).read_bytes()
    assert main(args) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["state"] == "pending_current_source_resolution"
    assert Path(c.path).read_bytes() == before
    assert main(["dag", "amend", "resolve-owner-source", c.path, source_file,
                 "--source-sha256", draft["source_sha256"],
                 "--authority", "Explicit offline current Root scope",
                 "--reason", "Assessment only, no history consumption or allocation"]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["state"] == "approved"
    # Same exact resolution observes the original ACK without rewriting history.
    resolved = Path(c.path).read_bytes()
    assert main(["dag", "amend", "resolve-owner-source", c.path, source_file,
                 "--source-sha256", draft["source_sha256"],
                 "--authority", "Explicit offline current Root scope",
                 "--reason", "Assessment only, no history consumption or allocation"]) == 0
    capsys.readouterr()
    assert Path(c.path).read_bytes() == resolved
    assert main(public_args(c) + ["--current-source-file", source_file]) == 0
    request = json.loads(capsys.readouterr().out)
    assert main(["dag", "amend", "resolve-owner", c.path, c.output,
                 "--request-sha256", request["request_sha256"],
                 "--authority", "Explicit independent assessment Root scope",
                 "--reason", "New normal independent proposal review"]) == 0
    capsys.readouterr()
    return source_file


def test_public_current_capture_resolution_and_owner_request(captured, monkeypatch, capsys):
    source_file = approve_public(captured, monkeypatch, capsys)
    request = json.loads(Path(captured.output).read_bytes())
    assert request["current_source_qualification"]["input"]["file"] == source_file
    assert "source" not in request["current_source_qualification"]


def test_current_complete_source_reaches_normal_independent_context(captured, monkeypatch, capsys):
    import omac.pipeline.amendment as pipeline
    c = captured
    source_file = approve_public(c, monkeypatch, capsys)
    c.engine.store.list_members = lambda workspace: ["offline-planner", "offline-reviewer"]
    c.engine.store.find_work_item_by_dag_key = lambda *args: None
    observed = {}

    class StopBeforeActor(Exception):
        pass

    def stop(engine, kind, payload, *args, **kwargs):
        observed.update(payload)
        raise StopBeforeActor

    monkeypatch.setattr(pipeline, "run_task", stop)
    with pytest.raises(StopBeforeActor):
        pipeline.propose_amendment(
            c.engine, c.path, report_file=c.report, docs=c.docs,
            blocked_nodes=["identity-local"], owner_request_file=c.output,
            orchestrator="offline-planner", reviewers=["offline-reviewer"],
            max_revisions=1, output_file=str(Path(c.output).with_name("reviewed.json")),
        )
    from omac.pipeline.portable_owner import assessment_files
    request = json.loads(Path(c.output).read_bytes())
    files = assessment_files(c.path, c.output, request, c.report, c.docs)
    assert source_file in {str(p) for p in files.values()}
    assert all("omac work read" in source for source in observed["contract"].source_of_truth)
    assert "every full historical entry" in observed["description"]
    assert "Historical authority is not current authority" in observed["description"]
    assert "candidate allocation/application/dispatch" in observed["description"]
    assert any("complete current Source" in text for text in observed["contract"].acceptance)
