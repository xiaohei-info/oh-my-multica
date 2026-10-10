import copy
import json
from pathlib import Path

import pytest

from test_current_owner_source import current, captured
from omac.core.owner_amendment import capture_source
from omac.core.taskmeta import TaskKind, TaskPhase, Bounces, parse_worker_handoff
from omac.engines.models import WorkItem, WorkItemStatus, VerificationAttachmentObservation
from omac.engines.multica import MulticaRuntime
from omac.errors import ValidationError

FIXTURES = Path(__file__).parent / "fixtures/system_historical_source"


pytestmark = pytest.mark.integration


@pytest.fixture
def system(current):
    c = current
    value = json.loads((FIXTURES / "current.json").read_bytes())
    item = WorkItem(**{**value, "status": WorkItemStatus[value["status"].split(".")[-1]],
                      "kind": TaskKind[value["kind"].split(".")[-1]],
                      "phase": TaskPhase[value["phase"].split(".")[-1]],
                      "bounces": Bounces(**value["bounces"]),
                      "worker_handoff": parse_worker_handoff(value["worker_handoff"])})
    c.items[item.id] = item
    raw = json.loads((FIXTURES.parent / "current_owner_source/native-runs.json").read_bytes())["system-upgrade"]
    adapter = object.__new__(MulticaRuntime)
    adapter._issue_runs = lambda _: copy.deepcopy(raw)
    c.runs[item.id] = adapter.list_runs(item.id)
    attribution = json.loads((FIXTURES / "native-attribution.json").read_bytes())["rows"]
    def attachment(issue, ref):
        row = next(r for r in attribution if r["ref"] == ref)
        body = (FIXTURES / ("report.yaml" if row["kind"] == "report_ref" else "ledger.yaml")).read_bytes()
        return VerificationAttachmentObservation(**row["native_primary_metadata"], content=body)
    c.engine.store.observe_verification_attachment = attachment
    c.engine.store.resolve_agent_id = lambda _: attribution[0]["native_primary_metadata"]["uploader_id"]
    c.system_item = item
    return c


def test_captured_system_mismatch_is_closed_without_source_resolution(system):
    with pytest.raises(ValidationError, match="no exact current Root qualification"):
        capture_source(system.manifest, "system-upgrade", system.engine.store, system.engine.runtime, held=True)



def test_public_retry_rejects_already_mismatched_historical_pair(system, monkeypatch, capsys):
    from omac.cli.main import main
    import omac.cli.commands.node as command
    monkeypatch.setenv("OMAC_WORKSPACE_ID", system.engine.store.config.workspace_id)
    monkeypatch.setenv("OMAC_ENGINE", "multica")
    monkeypatch.setattr(command, "_build_engine", lambda config: system.engine)
    item = copy.deepcopy(system.system_item)
    assert main(["node", "retry", system.path, "system-upgrade", "--stage", "authoring"]) == 5
    assert system.system_item == item
    capsys.readouterr()


def test_historical_source_prepare_resolve_preserves_current_record(system, monkeypatch, capsys):
    from omac.pipeline.historical_review_source import prepare_review_source, resolve_review_source, observe_review_source
    c = system
    before = Path(c.path).read_bytes()
    item_before = copy.deepcopy(c.system_item)
    output = str(Path(c.path).with_name("historical-source.json"))
    from omac.cli.main import main
    from test_current_owner_source import bind_cli
    bind_cli(c, monkeypatch)
    assert main(["dag", "amend", "prepare-review-source", c.path, "--node", "system-upgrade",
                 "--witness-file", str(FIXTURES / "witness.json"), "--output-file", output]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert Path(c.path).read_bytes() == before
    assert main(["dag", "amend", "resolve-review-source", c.path, output, "--source-sha256", prepared["source_sha256"],
                 "--authority", "Exact offline Root historical identity scope", "--reason", "Source qualification only"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["state"] == "approved_historical_source"
    assert observe_review_source(c.engine, c.path, output)["state"] == "approved_historical_source"
    assert c.system_item == item_before
    from omac.core.manifest import load_manifest
    source = capture_source(load_manifest(c.path), "system-upgrade", c.engine.store, c.engine.runtime, held=True)
    assert source["subject_digest"] == "fa83fd5f835570e7065d27900752952d79fc4ae335c99f0830303cf08b5ca5ed"
    assert source["item"]["worker_handoff"]["source_review_subject_digest"] == "4c20d79ca35e4fcfd4ef75803dc3514fb44ed9ca076cb83187965052d4891144"
