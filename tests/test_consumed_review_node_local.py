from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from test_completed_review_source_identity import complete
from omac.core.manifest import load_manifest
from omac.errors import ValidationError

CAPTURE = Path(__file__).parent / "fixtures/consumed_review_node_local"


@pytest.fixture
def local_complete(complete,monkeypatch):
    from omac.pipeline.evidence_handoff import completed_review_recovery_decision
    c=complete
    manifest=load_manifest(CAPTURE/'current-manifest.yaml')
    before=deepcopy(c.item)
    import yaml,hashlib
    from omac.engines.models import VerificationAttachmentObservation
    ref=c.item.review_obligations_ref
    body=yaml.safe_dump(c.item.review_obligations,allow_unicode=True,sort_keys=False).encode()
    assert len(body)==ref['bytes'] and hashlib.sha256(body).hexdigest()==ref['sha256']
    c.attachments[ref['attachment_id']]=VerificationAttachmentObservation(ref['attachment_id'],ref['comment_id'],ref['sha256'],body,None,None,None,None)
    c.store.get_work_item=lambda _:c.item
    context=json.loads((Path(__file__).parent/'fixtures/completed_review_source_identity/retirement-context.json').read_bytes())
    request=context['record']['request']
    monkeypatch.setattr('omac.pipeline.evidence_handoff._verify_source',lambda *a,**kw:(c.item,deepcopy(request['tuple']),c.item.delivery_identity,c.runs))
    # The full owning publication/old-history verifier is a transport seam here;
    # full public DAG coverage supplies every actual capture below.
    return SimpleNamespace(c=c,manifest=manifest,runtime=SimpleNamespace(capabilities=SimpleNamespace(stable_direct_run_identity=True),list_runs=lambda _:c.runs))

def test_authentic_consumed_completed_review_becomes_typed_block_not_adoption(local_complete):
    from omac.pipeline.evidence_handoff import completed_review_recovery_decision
    x=local_complete;c=x.c;before=deepcopy(c.item)
    decision=completed_review_recovery_decision(c.store,x.runtime,x.manifest,str(CAPTURE/'current-manifest.yaml'),'agentrun-malformed-marker-fixture',{})
    assert decision['reason_code']=='consumed-completed-review-recovery-required'
    assert decision['source_reviewer_run_id']==c.run.id
    assert decision['mismatch_fields']==['platform_assignee_id']
    assert c.item==before and c.item.platform_assignee_id is None
    assert c.item.reviewer_run_baseline.target_run_id is None
    assert c.item.review_verdict=='reject'

from test_evidence_handoff import captured


def _item(raw):
    from dataclasses import fields
    from omac.engines.models import WorkItem,WorkItemStatus
    from omac.core.taskmeta import TaskKind,TaskPhase,Bounces,parse_worker_handoff,parse_delivery_identity,parse_reviewer_run_baseline
    value={f.name:deepcopy(raw[f.name])for f in fields(WorkItem)if f.name in raw}
    for key,kind in [('kind',TaskKind),('phase',TaskPhase),('status',WorkItemStatus)]:
        text=raw[key];value[key]=kind[text.split('.')[-1]]if '.'in text else kind(text)
    value.update(bounces=Bounces(**raw['bounces']),worker_handoff=parse_worker_handoff(raw.get('worker_handoff')),delivery_identity=parse_delivery_identity(raw.get('delivery_identity')),reviewer_run_baseline=parse_reviewer_run_baseline(raw.get('reviewer_run_baseline')))
    return WorkItem(**value)


@pytest.fixture
def actual_dag(captured,complete,tmp_path,monkeypatch):
    from omac.engines import mock
    from omac.engines.multica import _run_trigger_kind
    from omac.engines.models import AgentRunObservation,VerificationAttachmentObservation
    from omac.core.manifest import save_manifest
    import hashlib,yaml
    c=captured
    c.config=json.loads((CAPTURE/'config.json').read_bytes())
    c.manifest=load_manifest(CAPTURE/'current-manifest.yaml')
    c.path=str(tmp_path/'actual-current-dag.yaml');save_manifest(c.manifest,c.path)
    c.item=_item(json.loads((CAPTURE/'current-control.json').read_bytes()))
    system=_item(json.loads((CAPTURE/'system-control.json').read_bytes()))
    for item in (c.item,system):
        mock._shared_work_items[item.id]=item
        mock._shared_contracts_by_item_id[item.id]=c.manifest.nodes[item.dag_key].contract
    c.system=system
    c.observations.update(complete.attachments)
    ref=c.item.review_obligations_ref;body=yaml.safe_dump(c.item.review_obligations,allow_unicode=True,sort_keys=False).encode()
    assert hashlib.sha256(body).hexdigest()==ref['sha256']and len(body)==ref['bytes']
    c.observations[ref['attachment_id']]=VerificationAttachmentObservation(ref['attachment_id'],ref['comment_id'],ref['sha256'],body,None,None,None,None)
    rawruns=json.loads((CAPTURE/'current-runs.json').read_bytes())
    c.runs=[AgentRunObservation(id=r['id'],kind=r['kind'],status=r['status'],agent_id=r['agent_id'],created_at=r['created_at'],updated_at=r.get('completed_at')or r.get('started_at')or r['created_at'],error=r.get('error'),trigger_kind=_run_trigger_kind(r),retry_of_run_id=r.get('retry_of_run_id'))for r in rawruns]
    monkeypatch.setattr(c.engine.runtime,'list_runs',lambda issue:deepcopy(c.runs)if issue==c.item.id else list(mock._shared_runs.get(issue,[])))
    from omac.pipeline.evidence_handoff import JOURNAL
    record=next(r for r in c.manifest.meta[JOURNAL].values()if r['state']=='consumed')
    request=record['request']
    for field in ('original_native','current_native','report_native','ledger_native'):
        raw=deepcopy(request['tuple'][field]);raw['content']=raw['content'].encode()
        assert hashlib.sha256(raw['content']).hexdigest()==raw['sha256']
        c.observations[raw['attachment_id']]=VerificationAttachmentObservation(**raw)
    files={f['path']:deepcopy(f)for f in [request['tuple']['source_file'],*request['tuple']['history_files']]}
    monkeypatch.setattr('omac.pipeline.evidence_handoff._file',lambda p:deepcopy(files[p]))
    monkeypatch.setattr('omac.pipeline.owner_amendment.required_inputs',lambda *a:deepcopy(request['tuple']['required_inputs']))
    return c


def test_actual_completed_review_hold_all_native_history_and_publication_guards(actual_dag):
    from omac.pipeline.evidence_handoff import completed_review_recovery_decision
    c=actual_dag
    decision=completed_review_recovery_decision(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    assert decision['reason_code']=='consumed-completed-review-recovery-required'
    assert decision['mismatch_fields']==['platform_assignee_id']
    assert c.item.platform_assignee_id is None and c.item.review_verdict=='reject'


@pytest.mark.parametrize('change',['missing-approval','receipt-step','receipt-id','missing-run','duplicate-run','foreign-agent','nonformal','missing-trigger','unknown-state','stale-time','foreign-baseline','foreign-assignee','control-bounces','control-unknown','report-native','ledger-native','publication','budget','DONE','contract','dependency','meta-history','source-unknown','caps-unknown'])
def test_authentic_local_hold_guard_failures_never_become_decisions(local_complete,monkeypatch,change):
    from omac.pipeline import evidence_handoff as eh
    from omac.pipeline.operator_review_recovery import _plain
    x=local_complete;c=x.c;m=x.manifest;token,record=eh.active_evidence_handoffs(m,'agentrun-malformed-marker-fixture')[0]
    if change=='missing-approval':m.meta[eh.RESOLUTIONS].pop(token)
    elif change=='receipt-step':record['step']=7
    elif change=='receipt-id':record['request_sha256']='0'*64
    elif change=='missing-run':c.runs=[r for r in c.runs if r.id!=c.run.id]
    elif change=='duplicate-run':c.runs.append(replace(c.run,id='foreign'))
    elif change in ('foreign-agent','nonformal','missing-trigger','unknown-state','stale-time'):
        kw={'foreign-agent':{'agent_id':'foreign'},'nonformal':{'kind':'indirect'},'missing-trigger':{'trigger_kind':None},'unknown-state':{'status':'unknown'},'stale-time':{'created_at':'2020-01-01T00:00:00Z'}}[change]
        c.runs=[replace(r,**kw)if r.id==c.run.id else r for r in c.runs]
    elif change=='foreign-baseline':c.item.reviewer_run_baseline=replace(c.item.reviewer_run_baseline,target_agent_id='foreign')
    elif change=='foreign-assignee':c.item.platform_assignee_id='foreign'
    elif change=='control-bounces':c.item.bounces.review+=1
    elif change=='control-unknown':c.item.unknown_persisted_fields={'foreign':'value'}
    elif change in ('report-native','ledger-native'):
        field='review_report_ref'if change=='report-native'else'review_ledger_ref';ref=getattr(c.item,field);c.attachments[ref['attachment_id']]=replace(c.attachments[ref['attachment_id']],content=b'foreign')
    elif change=='publication':c.item.review_report['evidence_publication']['index_sha256']='0'*64
    elif change=='budget':
        original=eh._verify_source
        def wrong(*a,**kw):
            out=list(original(*a,**kw));out[1]['budget']['remaining']['review']=0;return tuple(out)
        monkeypatch.setattr(eh,'_verify_source',wrong)
    elif change=='DONE':next(n for n in m.nodes.values()if n.status=='done').status='todo'
    elif change=='contract':m.nodes['system-upgrade'].description='foreign'
    elif change=='dependency':m.nodes['system-upgrade'].blocked_by=[]
    elif change=='meta-history':m.meta['repository_revision']='foreign'
    elif change=='source-unknown':m.meta['reconcile_audit']['last_full_scan_at']='unknown'
    else:x.runtime.capabilities.stable_direct_run_identity=False
    with pytest.raises(ValidationError):eh.completed_review_recovery_decision(c.store,x.runtime,m,str(CAPTURE/'current-manifest.yaml'),'agentrun-malformed-marker-fixture',{})


def test_active_run_still_uses_original_observer_not_completed_hold(local_complete):
    from omac.pipeline.evidence_handoff import completed_review_recovery_decision
    x=local_complete;c=x.c;c.runs=[replace(r,status='running')if r.id==c.run.id else r for r in c.runs]
    assert completed_review_recovery_decision(c.store,x.runtime,x.manifest,str(CAPTURE/'current-manifest.yaml'),'agentrun-malformed-marker-fixture',{})is None


def test_current_normal_tick_isolates_fixture_and_dispatches_independent_system(actual_dag,monkeypatch):
    from omac.pipeline import loop
    from omac.engines.models import WorkItemControlProjection,WorkItemStatus
    from omac.engines import mock
    from omac.core.manifest import load_manifest
    c=actual_dag;before=deepcopy(c.item);receipt=deepcopy(c.manifest.meta['rejected_evidence_handoffs']);done={k:deepcopy(n)for k,n in c.manifest.nodes.items()if n.status=='done'}
    observations={}
    for k,n in c.manifest.nodes.items():
        observations[k]=WorkItemControlProjection(c.engine.store.get_work_item(n.work_item_id))if n.work_item_id in mock._shared_work_items else None
    monkeypatch.setattr(loop,'reconcile_with_observations',lambda *a,**kw:loop.ReconcileResult(False,observations))
    result=loop.tick(c.engine.store,c.engine.runtime,c.manifest,c.path,max_parallel=4,config=c.config)
    assert result.dispatched==['system-upgrade']
    assert c.item.status==WorkItemStatus.BLOCKED
    assert c.item.decision_required['reason_code']=='consumed-completed-review-recovery-required'
    for field in ('platform_assignee_id','reviewer_run_baseline','review_report','review_ledger','review_verdict','bounces','bounce_baseline','delivery_identity'):
        assert getattr(c.item,field)==getattr(before,field)
    assert c.manifest.meta['rejected_evidence_handoffs']==receipt
    assert all(c.manifest.nodes[k]==n for k,n in done.items())
    assert c.manifest.nodes[c.key].status=='blocked'
    assert c.manifest.nodes['system-upgrade'].status=='in_progress'
    assert len(mock._shared_runs.get(c.system.id,[]))==1
    assert not mock._shared_runs.get(c.item.id)
    persisted=load_manifest(c.path)
    assert persisted.nodes[c.key].status=='blocked'
    # Complete original real dependency graph is retained; Fixture descendants
    # cannot become ready merely because System is independent.
    for k,n in c.manifest.nodes.items():
        if c.key in n.blocked_by:assert n.status=='blocked'


def test_current_completed_hold_restart_has_no_duplicate_writes_or_runs(actual_dag,monkeypatch):
    from omac.pipeline.evidence_handoff import block_completed_review_recovery
    from omac.core.manifest import load_manifest
    c=actual_dag;calls=[];metadata=c.engine.store.update_work_item_metadata;status=c.engine.store.update_status
    def update(item,**changes):calls.append(('metadata',item));return metadata(item,**changes)
    def state(item,value):calls.append(('status',item));return status(item,value)
    monkeypatch.setattr(c.engine.store,'update_work_item_metadata',update);monkeypatch.setattr(c.engine.store,'update_status',state)
    assert block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    restarted=load_manifest(c.path)
    assert block_completed_review_recovery(c.engine.store,c.engine.runtime,restarted,c.path,c.key,c.config)
    assert calls==[('metadata',c.item.id),('status',c.item.id)]
    assert c.item.platform_assignee_id is None and c.item.reviewer_run_baseline.target_run_id is None


def test_current_completed_hold_lost_status_ack_observes_without_replay(actual_dag,monkeypatch):
    from omac.pipeline.evidence_handoff import block_completed_review_recovery
    from omac.errors import PlatformError
    c=actual_dag;calls=[];status=c.engine.store.update_status
    def lost(item,value):
        calls.append(item);status(item,value);raise PlatformError('lost status ACK')
    monkeypatch.setattr(c.engine.store,'update_status',lost)
    with pytest.raises(PlatformError,match='lost status ACK'):block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    monkeypatch.setattr(c.engine.store,'update_status',lambda *a:pytest.fail('acknowledged status replay'))
    assert block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    assert calls==[c.item.id]
    assert c.manifest.nodes[c.key].status=='blocked'


def test_current_completed_hold_actual_control_CAS_drift_prevents_all_writes(actual_dag,monkeypatch):
    from omac.pipeline import evidence_handoff as eh
    c=actual_dag;confirm=eh._confirm_disk
    def drift(*args):confirm(*args);c.item.bounces.review+=1
    monkeypatch.setattr(eh,'_confirm_disk',drift)
    monkeypatch.setattr(c.engine.store,'update_work_item_metadata',lambda *a,**kw:pytest.fail('unexpected write'))
    with pytest.raises(ValidationError,match='Actual control or Runs changed'):eh.block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)


def test_current_completed_hold_does_not_relax_original_observer(actual_dag):
    from omac.pipeline.evidence_handoff import observe_evidence_review
    c=actual_dag
    with pytest.raises(ValidationError,match='Full control changed before original Reviewer Run observation'):observe_evidence_review(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)


@pytest.mark.parametrize('change',['exhausted-budget','foreign-publication','old-native-missing','old-run-changed','current-PR-head','full-Source-budget-history'])
def test_full_authentication_failures_stop_without_new_decision(actual_dag,monkeypatch,change):
    from omac.pipeline.evidence_handoff import block_completed_review_recovery
    from omac.errors import OmacError
    c=actual_dag
    if change=='exhausted-budget':c.config['retry']['review']=2
    elif change=='foreign-publication':monkeypatch.setattr(c.engine.store,'read_immutable_artifact',lambda _:b'{"foreign":true}')
    elif change=='old-native-missing':
        from omac.pipeline.evidence_handoff import JOURNAL
        record=next(iter(c.manifest.meta[JOURNAL].values()));ref=record['request']['tuple']['original_native']['attachment_id'];c.observations.pop(ref)
    elif change=='old-run-changed':c.runs[0]=replace(c.runs[0],agent_id='foreign')
    elif change=='current-PR-head':
        from omac.engines.models import PullRequestReadiness
        monkeypatch.setattr(c.engine.store,'read_pull_request_readiness',lambda _:PullRequestReadiness(False,'OPEN','0'*40))
    else:c.manifest.meta['amendment_apply']['foreign']='unapproved'
    monkeypatch.setattr(c.engine.store,'update_work_item_metadata',lambda *a,**kw:pytest.fail('unqualified decision write'))
    with pytest.raises(OmacError):block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)


def test_current_completed_hold_lost_metadata_ack_does_not_repeat_owned_effect(actual_dag,monkeypatch):
    from omac.pipeline.evidence_handoff import block_completed_review_recovery
    from omac.errors import PlatformError
    c=actual_dag;calls=[];metadata=c.engine.store.update_work_item_metadata
    def lost(item,**changes):
        calls.append(item);metadata(item,**changes);raise PlatformError('lost metadata ACK')
    monkeypatch.setattr(c.engine.store,'update_work_item_metadata',lost)
    with pytest.raises(PlatformError,match='lost metadata ACK'):block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    monkeypatch.setattr(c.engine.store,'update_work_item_metadata',lambda *a,**kw:pytest.fail('acknowledged metadata replay'))
    assert block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    assert calls==[c.item.id]


def test_completed_hold_Run_list_order_is_not_a_false_CAS_failure(actual_dag,monkeypatch):
    from omac.pipeline import evidence_handoff as eh
    c=actual_dag;confirm=eh._confirm_disk
    def reorder(*args):confirm(*args);c.runs.reverse()
    monkeypatch.setattr(eh,'_confirm_disk',reorder)
    assert eh.block_completed_review_recovery(c.engine.store,c.engine.runtime,c.manifest,c.path,c.key,c.config)
    assert c.item.platform_assignee_id is None
@pytest.mark.parametrize('boundary', ['authenticated', 'metadata-ack', 'status-ack'])
@pytest.mark.parametrize('drift', ['control', 'Run', 'Source'])
def test_authenticated_hold_drift_stops_before_next_effect(actual_dag, monkeypatch, boundary, drift):
    from omac.pipeline import evidence_handoff as eh
    from omac.core.manifest import save_manifest, load_manifest
    from omac.errors import OmacError
    c = actual_dag
    effects = []
    mutated = False

    def mutate():
        nonlocal mutated
        if mutated:
            return
        mutated = True
        if drift == 'control':
            c.item.bounces.review += 1
        elif drift == 'Run':
            c.runs.append(replace(c.runs[-1], id='foreign-terminal-after-authentication'))
        else:
            c.manifest.nodes['system-upgrade'].description = 'foreign contract after authentication'
            save_manifest(c.manifest, c.path)

    authenticate = eh.completed_review_recovery_decision
    metadata = c.engine.store.update_work_item_metadata
    status = c.engine.store.update_status

    def observe(*args, **kwargs):
        decision = authenticate(*args, **kwargs)
        if boundary == 'authenticated':
            mutate()
        return decision

    def write_metadata(*args, **kwargs):
        effects.append('metadata')
        result = metadata(*args, **kwargs)
        if boundary == 'metadata-ack':
            mutate()
        return result

    def write_status(*args, **kwargs):
        effects.append('status')
        result = status(*args, **kwargs)
        if boundary == 'status-ack':
            mutate()
        return result

    monkeypatch.setattr(eh, 'completed_review_recovery_decision', observe)
    monkeypatch.setattr(c.engine.store, 'update_work_item_metadata', write_metadata)
    monkeypatch.setattr(c.engine.store, 'update_status', write_status)
    with pytest.raises(OmacError):
        eh.block_completed_review_recovery(c.engine.store, c.engine.runtime, c.manifest, c.path, c.key, c.config)
    assert effects == {'authenticated': [], 'metadata-ack': ['metadata'], 'status-ack': ['metadata', 'status']}[boundary]
    assert load_manifest(c.path).nodes[c.key].status == 'in_review'
