from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock

import pytest

from omac.core.manifest import Manifest, save_manifest
from omac.core.owner_amendment import JOURNAL, digest
from omac.engines.models import AgentRunObservation, WorkItem, WorkItemStatus
from omac.core.taskmeta import TaskKind, TaskPhase
from omac.errors import PlatformError, ValidationError


@pytest.mark.parametrize('drift', ['contract', 'generation', 'assignee', 'source', 'run', 'extra-run'])
@pytest.mark.parametrize('lost', [None, 'metadata', 'status'])
def test_gap_revalidates_complete_binding_after_each_effect(drift, lost):
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
    item = WorkItem('assessment', 'ws', 'assessment', '', WorkItemStatus.IN_PROGRESS,
                    'amend-owner-' + 'a' * 64, worker='planner', kind=TaskKind.AMENDMENT,
                    contract={'objective': 'approved'})
    runs = [AgentRunObservation('run', 'direct', 'running', agent_id='planner-id', trigger_kind='issue_assignment')]
    store, runtime = Mock(), Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    store.resolve_agent_id.return_value = 'planner-id'
    runtime.list_runs.side_effect = lambda _: deepcopy(runs)
    def mutate():
        if drift == 'contract':
            item.contract = {'objective': 'foreign'}
        elif drift == 'generation':
            item.review_generation = 'foreign'
        elif drift == 'assignee':
            item.platform_assignee_id = 'foreign'
        elif drift == 'source':
            item.project_rules = 'foreign'
        elif drift == 'run':
            runs[0] = AgentRunObservation('run', 'direct', 'running', agent_id='foreign', trigger_kind='issue_assignment')
        else:
            runs.append(AgentRunObservation('foreign', 'direct', 'running', agent_id='planner-id', trigger_kind='rerun'))
    def metadata(_, **values):
        for k, v in values.items():
            setattr(item, k, v)
        if lost != 'status':
            mutate()
        if lost == 'metadata':
            raise PlatformError('metadata committed, ACK lost')
    def status(_, value):
        item.status = value
        mutate()
        raise PlatformError('status committed, ACK lost')
    store.update_work_item_metadata.side_effect = metadata
    store.update_status.side_effect = status
    report = scope_gap_template(item)
    report.update(run_id='run', summary='missing complete Source', decision_needed='publish exact Source')
    with pytest.raises(ValidationError, match='binding|control|Run'):
        report_scope_gap(store, runtime, item.id, report)
    if lost != 'status':
        store.update_status.assert_not_called()


def test_initial_publication_authenticates_local_request_before_any_effect(tmp_path, monkeypatch):
    from omac.pipeline import portable_owner, owner_amendment
    request = {'schema': 'approved-request', 'full_body': {'authority': 'Root'}}
    request_digest = digest(request)
    approval = {'request_sha256': request_digest, 'authority': 'Root'}
    entry = {'request': request, 'approval': approval, 'approval_sha256': digest(approval)}
    path = tmp_path / 'dag.yaml'
    save_manifest(Manifest(meta={JOURNAL: {request_digest: entry}}, nodes={}), str(path))
    file = tmp_path / 'request.json'
    file.write_text('{"schema":"replaced-unapproved-request"}')
    engine = Mock()
    monkeypatch.setattr(owner_amendment, 'authorized_request', lambda manifest, *args: manifest.meta[JOURNAL][request_digest])
    with pytest.raises(ValidationError, match='request'):
        portable_owner.authenticate_sources(engine, str(path), str(file), request_digest, request, '', [])
    engine.store.publish_source_artifact.assert_not_called()


def test_authenticated_corpus_cannot_adopt_new_hash_baseline(tmp_path):
    import hashlib
    from omac.pipeline.portable_owner import source_frame
    path = tmp_path / 'request.json'
    raw = b'{"approved": true}'
    path.write_bytes(b'{"unapproved": true}')
    expected = {'owner_resolution': 'root', 'approval_sha256': 'approval', 'files': {
        'owner-request': {'provenance': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}
    store = Mock()
    with pytest.raises(ValidationError, match='approved|authenticated'):
        source_frame(store, 'assessment', {'owner-request': path}, owner_resolution='root', approval='approval', expected=expected)
    store.publish_source_artifact.assert_not_called()

@pytest.mark.parametrize("value", [1, 0.0])
def test_portable_authority_retains_complete_prior_history(tmp_path, monkeypatch, value):
    import omac.core.owner_amendment as core
    request = {"schema": "offline-complete-request"}
    key = digest(request)
    approval = {"request_sha256": key, "authority": "Root", "reason": "offline assessment"}
    history = {"prior": {"opaque_complete_history": {"counter": 0}}}
    entry = {"request": request, "approval": approval, "approval_sha256": digest(approval),
             "state": "approved", "portable_authority": {"owner_history_sha256": digest(history)}}
    manifest = Manifest(meta={JOURNAL: {**history, key: entry}}, nodes={})
    manifest.meta[JOURNAL]["prior"]["opaque_complete_history"]["counter"] = value
    monkeypatch.setattr(core, "verify_request", lambda *args: None)
    with pytest.raises(ValidationError, match="history"):
        core.authorized_request(manifest, key, Mock(), Mock())

@pytest.mark.parametrize('late_call', [2, 4])
def test_gap_native_observation_cannot_cross_control_effect(late_call):
    from omac.pipeline.amendment_scope_gap import report_scope_gap, scope_gap_template
    item = WorkItem('assessment', 'ws', 'assessment', '', WorkItemStatus.IN_PROGRESS,
                    'amend-owner-' + 'a' * 64, worker='planner', kind=TaskKind.AMENDMENT,
                    contract={'objective': 'approved'})
    store, runtime = Mock(), Mock()
    store.worker_control_lock.side_effect = lambda _: nullcontext()
    store.get_work_item.side_effect = lambda _: deepcopy(item)
    store.resolve_agent_id.return_value = 'planner-id'
    calls = 0
    def runs(_):
        nonlocal calls
        calls += 1
        if calls == late_call:
            item.contract = {'objective': 'foreign during native observation'}
        return [AgentRunObservation('run', 'direct', 'running', agent_id='planner-id', trigger_kind='issue_assignment')]
    runtime.list_runs.side_effect = runs
    def metadata(_, **values):
        for k, v in values.items():
            setattr(item, k, v)
    store.update_work_item_metadata.side_effect = metadata
    store.update_status.side_effect = lambda _, value: setattr(item, 'status', value)
    report = scope_gap_template(item)
    report.update(run_id='run', summary='missing Source', decision_needed='publish Source')
    with pytest.raises(ValidationError, match='control|binding'):
        report_scope_gap(store, runtime, item.id, report)
    if late_call == 2:
        store.update_work_item_metadata.assert_not_called()
    store.update_status.assert_not_called()


def test_last_native_read_cannot_overwrite_foreign_canonical_history(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from omac.core.manifest import load_manifest
    from omac.pipeline import owner_amendment as pipeline, portable_owner
    item = WorkItem('assessment', 'ws', 'assessment', '', WorkItemStatus.IN_PROGRESS,
                    'amend-owner-fixture', worker='planner', kind=TaskKind.AMENDMENT)
    entry = {'dag_key': item.dag_key, 'assessment': {'orchestrator': 'planner'}, 'state': 'assessment_started'}
    path = tmp_path / 'dag.yaml'
    save_manifest(Manifest(meta={JOURNAL: {'fixture': entry}}, nodes={}), str(path))
    ref = {'label': 'owner-source'}
    item.source_refs = [ref]
    engine = Mock()
    engine.store.config.workspace_id = 'ws'
    engine.store.observe_work_item_control.return_value = SimpleNamespace(work_item=item)
    engine.store.get_work_item.return_value = item
    monkeypatch.setattr(pipeline, 'authorized_request', lambda manifest, *args: manifest.meta[JOURNAL]['fixture'])
    monkeypatch.setattr(pipeline, 'terminal_runs', lambda *args: [])
    changed = False
    def last_native_read(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            foreign = load_manifest(str(path))
            foreign.meta[JOURNAL]['foreign'] = {'complete_history': 'must survive'}
            save_manifest(foreign, str(path))
    monkeypatch.setattr(portable_owner, 'verify_approved_source', last_native_read)
    authority = {'owner_history_sha256': digest({}), 'files': {}}
    with pytest.raises(ValidationError, match='manifest|Source|canonical'):
        pipeline.checkpoint_authoring_dispatch(engine, str(path), 'fixture', item.id, 'before',
                                              portable_reference=ref, portable_authority=authority)
    assert load_manifest(str(path)).meta[JOURNAL]['foreign'] == {'complete_history': 'must survive'}
