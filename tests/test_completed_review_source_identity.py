"""Complete actual Review3 native report/ledger/control; no product verdict."""
from pathlib import Path
from dataclasses import fields,replace
from types import SimpleNamespace
from copy import deepcopy
import ast,json,hashlib
import pytest
from omac.core.taskmeta import TaskKind,TaskPhase,Bounces,parse_reviewer_run_baseline,parse_delivery_identity,parse_worker_handoff
from omac.engines.models import WorkItem,WorkItemStatus,AgentRunObservation,VerificationAttachmentObservation
from omac.core.manifest import _load_contract
from omac.engines.multica import _run_trigger_kind
from omac.core.evidence import validate_review_evidence
from omac.pipeline.evidence_handoff import _completed_independent_review
from omac.errors import ValidationError


@pytest.fixture
def complete():
    data=json.loads((Path(__file__).parent/'fixtures/completed_review_source_identity/complete-capture.json').read_bytes());raw=data['complete_control51'];kw={f.name:deepcopy(raw[f.name])for f in fields(WorkItem)if f.name in raw}
    kw.update(kind=TaskKind[raw['kind'].split('.')[-1]] if raw['kind'].startswith('TaskKind.') else TaskKind(raw['kind']),phase=TaskPhase[raw['phase'].split('.')[-1]] if raw['phase'].startswith('TaskPhase.') else TaskPhase(raw['phase']),status=WorkItemStatus[raw['status'].split('.')[-1]] if raw['status'].startswith('WorkItemStatus.') else WorkItemStatus(raw['status']),bounces=Bounces(**raw['bounces']),reviewer_run_baseline=parse_reviewer_run_baseline(raw['reviewer_run_baseline']),delivery_identity=parse_delivery_identity(raw['delivery_identity']),worker_handoff=parse_worker_handoff(raw['worker_handoff']))
    item=WorkItem(**kw);attachments={}
    for row in data['full_native_capture']['rows']:
        value=dict(row['native_attachment_observation']);value['content']=ast.literal_eval(value['content']);assert hashlib.sha256(value['content']).hexdigest()==row['sha256'];attachments[value['attachment_id']]=VerificationAttachmentObservation(**value)
    runs=[AgentRunObservation(id=r['id'],kind=r['kind'],status=r['status'],agent_id=r['agent_id'],created_at=r['created_at'],updated_at=r.get('completed_at')or r.get('started_at')or r['created_at'],error=r.get('error'),trigger_kind=_run_trigger_kind(r),retry_of_run_id=r.get('retry_of_run_id'))for r in data['runs']];run=next(r for r in runs if r.id==data['formal_run_id'])
    def attachment(issue,ref):
        assert issue==item.id;return attachments[ref['attachment_id']]
    return SimpleNamespace(data=data,item=item,runs=runs,run=run,store=SimpleNamespace(observe_verification_attachment=attachment),node=SimpleNamespace(contract=_load_contract(data['contract'])),attachments=attachments)


def test_complete_authentic_public_validation_and_completed_reject_consumption(complete):
    c=complete;report=c.item.review_report;ledger=c.item.review_ledger;before=deepcopy(c.item)
    assert report['review_protocol']=='omac.review/v2' and 'source_commit'not in report and 'pr_url'not in report
    assert len(c.attachments[c.item.review_report_ref['attachment_id']].content)==55250
    assert len(c.attachments[c.item.review_ledger_ref['attachment_id']].content)==29842
    assert not validate_review_evidence(c.node,replace(c.item,review_ledger=c.data['request']['tuple']['full_control']['review_ledger']))
    control,native=_completed_independent_review(c.store,c.item,c.runs,c.run,c.data['request'],c.data['expected_control'],c.node)
    assert control['review_verdict']=='reject' and control['review_report']==report and control['review_ledger']==ledger
    assert report['blockers'] and c.item==before and c.item.bounce_baseline is None and c.item.reviewer_run_baseline is not None
    assert ledger['cycles'][:2]==c.data['request']['tuple']['full_control']['review_ledger']['cycles']
    assert native['report']['task_id']==native['ledger']['task_id']==c.run.id


@pytest.mark.parametrize('change',['report-native-time','uploader','task','active','nonformal','publication-index','payload','ledger','source-conflict'])
def test_complete_authentic_native_publication_ledger_guards_stay_closed(complete,change):
    c=complete;ref=c.item.review_report_ref['attachment_id'];obs=c.attachments[ref]
    if change=='report-native-time':c.attachments[ref]=replace(obs,created_at='2020-01-01T00:00:00Z')
    elif change=='uploader':c.attachments[ref]=replace(obs,uploader_id='foreign')
    elif change=='task':c.attachments[ref]=replace(obs,task_id='foreign')
    elif change=='active':c.run=replace(c.run,status='running')
    elif change=='nonformal':c.run=replace(c.run,kind='indirect');c.runs=[c.run if r.id==c.run.id else r for r in c.runs]
    elif change=='publication-index':c.data['request']['tuple']['current_publication']['sha256']='0'*64
    elif change=='payload':c.data['request']['tuple']['current_publication']['artifacts'][0]['publication']['commit']='0'*40
    elif change=='ledger':c.item.review_ledger['cycles'][0]['verdict']='pass'
    else:c.item.review_report['source_commit']='a'*40
    with pytest.raises(ValidationError):_completed_independent_review(c.store,c.item,c.runs,c.run,c.data['request'],c.data['expected_control'],c.node)


def test_complete_authentic_current_control_cannot_retire_unbound_review(complete):
    from omac.pipeline.evidence_handoff import _retired_review, _digest, RESOLUTIONS
    from omac.pipeline.operator_review_recovery import _plain, _control

    c = complete
    context = json.loads((Path(__file__).parent / 'fixtures/completed_review_source_identity/retirement-context.json').read_bytes())
    before = deepcopy(c.item)
    assert c.run.formal and c.item.platform_assignee_id is None
    assert c.item.reviewer_run_baseline.target_run_id is None
    _, native = _completed_independent_review(c.store, c.item, c.runs, c.run, c.data['request'], c.data['expected_control'], c.node)
    record = deepcopy(context['record'])
    dispatch = record['review_dispatch']
    dispatch.update(state='normal-review-handed-off', target_run_id=c.run.id, completed_review=native)
    dispatch['retirement'] = dict(request_sha256=context['token'], baseline=dispatch['baseline'], run=_plain(c.run), completed_review=native, completed_control=_control(c.item))
    dispatch['retirement_sha256'] = _digest(dispatch['retirement'])
    manifest = SimpleNamespace(meta={RESOLUTIONS: {context['token']: context['resolution']}})
    with pytest.raises(ValidationError, match='Original full retired control changed outside its one validated publication'):
        _retired_review(manifest, context['token'], record)
    assert c.item == before
    assert c.data['complete_control51']['platform_assignee_id'] is None
    assert c.data['complete_control51']['reviewer_run_baseline']['target_run_id'] is None
