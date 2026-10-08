"""Each present Source identity must be complete and exact; no fallback."""
from copy import deepcopy
import pytest

HEAD='e9b4440f9c119275ccbacf989087b8b067385ca7'
PR='https://github.com/xiaohei-info/open-agent-cluster/pull/96'


def nested():return {'review_protocol':'omac.review/v2','reviewed_source':{'head':HEAD,'pr_url':PR}}


@pytest.mark.parametrize('shape',['nested','legacy','both','legacy-v2'])
def test_exact_review_source_shapes(shape):
    from omac.core.evidence import review_source_matches
    report=nested() if shape in ('nested','both') else {}
    if shape!='nested':report.update(source_commit=HEAD,pr_url=PR)
    if shape=='legacy-v2':report['review_protocol']='omac.review/v2'
    assert review_source_matches(report,HEAD,PR)


@pytest.mark.parametrize('change',['missing','none','list','nested-none','nested-list','nested-empty','missing-head','missing-pr','head-none','head-int','head-foreign','pr-none','pr-int','pr-foreign','protocol-none','protocol-unknown','nested-no-protocol','top-partial-head','top-partial-pr','top-none','top-foreign','top-malformed'])
def test_every_present_identity_shape_fails_closed(change):
    from omac.core.evidence import review_source_matches
    report=nested()
    if change=='missing':report={}
    elif change=='none':report=None
    elif change=='list':report=[]
    elif change=='nested-none':report['reviewed_source']=None
    elif change=='nested-list':report['reviewed_source']=[]
    elif change=='nested-empty':report['reviewed_source']={}
    elif change=='missing-head':del report['reviewed_source']['head']
    elif change=='missing-pr':del report['reviewed_source']['pr_url']
    elif change.startswith('head-'):report['reviewed_source']['head']={'none':None,'int':1,'foreign':'a'*40}[change[5:]]
    elif change.startswith('pr-'):report['reviewed_source']['pr_url']={'none':None,'int':1,'foreign':PR+'0'}[change[3:]]
    elif change=='protocol-none':report['review_protocol']=None
    elif change=='protocol-unknown':report['review_protocol']='omac.review/v99'
    elif change=='nested-no-protocol':del report['review_protocol']
    elif change=='top-partial-head':report['source_commit']=HEAD
    elif change=='top-partial-pr':report['pr_url']=PR
    elif change=='top-none':report.update(source_commit=None,pr_url=PR)
    elif change=='top-foreign':report.update(source_commit='a'*40,pr_url=PR)
    else:report.update(source_commit=HEAD,pr_url=[])
    assert not review_source_matches(report,HEAD,PR)
