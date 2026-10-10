"""Authentic complete Credential Source through public assessment commands."""
import copy
import json
from pathlib import Path

import pytest

from test_current_owner_source import captured, bind_cli
from omac.cli.main import main
from omac.core.manifest import load_manifest
from omac.core.owner_amendment import JOURNAL, authorized_request
from omac.core.taskmeta import TaskKind, TaskPhase, Bounces, parse_worker_handoff
from omac.engines.models import WorkItem, WorkItemStatus, AgentRunObservation, VerificationAttachmentObservation
from omac.engines.multica import MulticaRuntime

FIXTURES = Path(__file__).parent / 'fixtures/credential_prospective_source'


pytestmark = pytest.mark.integration


@pytest.fixture
def credential(captured):
    c = captured
    Path(c.path).write_bytes((FIXTURES / 'manifest.yaml').read_bytes())
    c.manifest = load_manifest(c.path)
    value = json.loads((FIXTURES / 'current.json').read_bytes())
    item = WorkItem(**{**value, 'status': WorkItemStatus[value['status'].split('.')[-1]],
                      'kind': TaskKind[value['kind'].split('.')[-1]],
                      'phase': TaskPhase[value['phase'].split('.')[-1]],
                      'bounces': Bounces(**value['bounces']),
                      'worker_handoff': parse_worker_handoff(value['worker_handoff'])})
    c.items[item.id] = item
    original = json.loads((FIXTURES / 'original-request.json').read_bytes())
    source = original['sources']['credential-store']
    c.runs[item.id] = [AgentRunObservation(**r) for r in source['runs']]
    native = {v['ref']['attachment_id']: v['native'] for v in source['failed'].values()}
    c.engine.store.observe_verification_attachment = lambda issue, ref: VerificationAttachmentObservation(
        **{**native[ref['attachment_id']], 'content': native[ref['attachment_id']]['content'].encode()})
    c.engine.store.find_work_item_by_dag_key = lambda workspace, key: None
    prior_issue = '01a11f7b-d5ac-73a8-972c-7939d1458ccf'
    adapter = object.__new__(MulticaRuntime)
    adapter._issue_runs = lambda _: copy.deepcopy(json.loads((FIXTURES / 'planner-runs.json').read_bytes()))
    c.runs[prior_issue] = adapter.list_runs(prior_issue)
    messages = json.loads((FIXTURES / 'planner-messages.json').read_bytes())
    def read_messages(issue, run):
        assert issue == prior_issue and run == '01a11f7d-a4b4-77b1-99ae-4212cf3c1e8a'
        return copy.deepcopy(messages)
    c.engine.runtime.read_run_messages = read_messages
    c.credential_item = item
    assert len(c.manifest.meta[JOURNAL]) == 4
    return c


def test_complete_credential_current_root_source_and_normal_resolution(credential, monkeypatch, capsys):
    c = credential
    bind_cli(c, monkeypatch)
    source_file = str(Path(c.output).with_name('credential-current-source.json'))
    selectors = ['--blocked-node', 'credential-store', '--allowed-node', 'credential-store',
                 '--report-file', c.report, '--docs', c.docs[0], '--prospective-source-file', str(FIXTURES / 'witness.json')]
    history = copy.deepcopy(c.manifest.meta[JOURNAL])
    item = copy.deepcopy(c.credential_item)
    canonical = Path(c.path).read_bytes()
    assert main(['dag', 'amend', 'prepare-owner-source', c.path, *selectors, '--output-file', source_file]) == 0
    source = json.loads(capsys.readouterr().out)
    assert Path(c.path).read_bytes() == canonical
    assert main(['dag', 'amend', 'resolve-owner-source', c.path, source_file, '--source-sha256', source['source_sha256'],
                 '--authority', 'Offline exact current Root Credential Source', '--reason', 'Assessment only; no allocation']) == 0
    capsys.readouterr()
    assert main(['dag', 'amend', 'prepare-owner', c.path, *selectors, '--current-source-file', source_file, '--output-file', c.output]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert main(['dag', 'amend', 'resolve-owner', c.path, c.output, '--request-sha256', prepared['request_sha256'],
                 '--authority', 'Separate offline current Root assessment resolution', '--reason', 'Draft and fresh independent Review only']) == 0
    capsys.readouterr()
    current = load_manifest(c.path)
    entry = authorized_request(current, prepared['request_sha256'], c.engine.store, c.engine.runtime)
    from omac.core.owner_amendment import owner_request
    declaration = owner_request(entry)['prospective_assessment']['declarations']['credential-rotation-wire-publication']
    assert 'request_ref' in entry and 'request' not in entry
    assert declaration['allocation'] == 'not-authorized'
    assert declaration['native_workitem'] == 'not-created'
    assert c.credential_item == item
    assert {k: v for k, v in current.meta[JOURNAL].items() if k != prepared['request_sha256']} == history
    assert current.nodes['contracts-credential'].status == 'done'
    assert current.nodes['contracts-credential'].merged
    assert 'credential-rotation-wire-publication' not in current.nodes
    # Complete actual captured context crosses the Store boundary without a
    # filesystem path dependency; this does not execute Planner or Reviewer.
    from types import SimpleNamespace
    import hashlib
    from omac.pipeline.portable_owner import authenticate_sources, publish_sources, read_source, verify_approved_source
    files, contents, authority = authenticate_sources(c.engine, c.path, c.output, prepared['request_sha256'], owner_request(entry), c.report, c.docs)
    remote_item = SimpleNamespace(id="offline-source-assessment", project_rules=None, source_refs=[])
    chunks = {}
    def publish(issue, raw):
        sha = hashlib.sha256(raw).hexdigest()
        chunks[sha] = raw
        return {"issue_id": issue, "sha256": sha, "bytes": len(raw)}
    def update(issue, **values):
        for name, value in values.items():
            setattr(remote_item, name, value)
    remote_store = SimpleNamespace(get_work_item=lambda _: remote_item,
                                   publish_source_artifact=publish,
                                   read_source_artifact=lambda ref: chunks[ref["sha256"]],
                                   update_work_item_metadata=update)
    portable = publish_sources(remote_store, remote_item.id, files,
                               owner_resolution=prepared["request_sha256"], approval=entry["approval_sha256"], expected=authority, contents=contents)[0]
    verify_approved_source(remote_store, portable, entry, authority=authority)
    expected = Path(c.output).read_bytes()
    with monkeypatch.context() as remote_filesystem:
        remote_filesystem.setattr(Path, "read_bytes", lambda *a: pytest.fail("remote Reader must not use an operator file"))
        assert read_source(remote_store, portable, entry="owner-request") == expected
        frame = json.loads(read_source(remote_store, portable))
    assert frame["owner_resolution"] == prepared["request_sha256"]
    assert frame["approval_sha256"] == entry["approval_sha256"]
    assert len(frame["files"]) == len(files)

