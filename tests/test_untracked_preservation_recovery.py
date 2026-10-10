"""Opt-in Source preservation; private full captured manifest, no cleanup."""
import json
from pathlib import Path
import pytest
from omac.errors import ValidationError
from test_full_state_git_recovery import private_case,git


pytestmark = pytest.mark.integration


def test_full_captured_preservation_public_prepare_resolve_keeps_unknown_files(tmp_path,capsys):
    from omac.core.state_sync_recovery import prepare_preservation,prepare,resolve,digest
    from omac.core.state_transport import decode
    repo,manifest,remote,lock=private_case(tmp_path)
    protected=repo/'.omac/unknown.json';protected.write_bytes(b'{"activity":"UNKNOWN","must":"preserve-unmodified"}\n');protected.chmod(0o640)
    original=manifest.read_bytes();before=protected.stat()
    with pytest.raises(ValidationError):prepare(str(manifest),str(repo),str(tmp_path/'legacy.bundle'),str(lock))
    assert not(tmp_path/'legacy.bundle').exists()
    witness=prepare_preservation(str(manifest),str(repo),str(lock),authority='offline exact Root',reason='Keep every original path and byte; no new rights')
    path=tmp_path/'preservation.json';path.write_text(json.dumps(witness))
    request=prepare(str(manifest),str(repo),str(tmp_path/'preserved.bundle'),str(lock),preservation_file=str(path),preservation_sha256=digest(witness))
    result=resolve(request,request_sha256=digest(request),authority='offline exact recovery',reason='Original Source semantics unchanged')
    assert result['state']=='complete-remote-observed'
    assert decode(manifest.read_bytes())==original
    assert protected.read_bytes()==b'{"activity":"UNKNOWN","must":"preserve-unmodified"}\n'
    assert (protected.stat().st_ino,protected.stat().st_mode)==(before.st_ino,before.st_mode)
    assert git(repo,'rev-parse','HEAD').decode().strip()==request['target_commit']


@pytest.mark.parametrize('change',['digest','missing','bytes','mode','added','alias','held','hardlink','deleted','witness-alias','witness-hardlink','active-writer','index','config','source','logical','authority','canonical-inode'])
def test_exact_preservation_is_required_before_original_archive(tmp_path,change):
    from omac.core.state_sync_recovery import prepare_preservation,prepare,digest
    repo,manifest,remote,lock=private_case(tmp_path)
    protected=repo/'.omac/unknown.json';protected.write_text('{"activity":"UNKNOWN"}')
    witness=prepare_preservation(str(manifest),str(repo),str(lock),authority='offline exact Root',reason='Only original immutable Source')
    path=tmp_path/'preservation.json';path.write_text(json.dumps(witness));sha=digest(witness)
    held=None
    if change=='digest':sha='0'*64
    elif change=='missing':path.unlink()
    elif change=='bytes':protected.write_text('{"changed":true}')
    elif change=='mode':protected.chmod(0o640)
    elif change=='added':(repo/'.omac/new.json').write_text('{}')
    elif change=='alias':(repo/'.omac/alias.json').symlink_to(protected)
    elif change=='held':held=protected.open('rb')
    elif change=='hardlink':
        import os
        os.link(protected,repo/'.omac/hardlink.json')
    elif change=='deleted':protected.unlink()
    elif change=='witness-alias':
        actual=tmp_path/'actual-witness.json';path.rename(actual);path.symlink_to(actual)
    elif change=='witness-hardlink':
        import os
        os.link(path,tmp_path/'alias-witness.json')
    elif change=='active-writer':
        import fcntl
        held=lock.open('rb');fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
    elif change=='index':
        with (repo/'.git/index').open('ab') as stream:stream.write(b'changed source')
    elif change=='config':(repo/'.omac/config.yaml').write_text('changed: true\n')
    elif change in ('source','logical','authority'):
        if change=='source':witness['source']['head']='0'*40
        elif change=='logical':witness['source']['logical']['DONE']+=1
        else:witness['authority']=''
        path.write_text(json.dumps(witness));sha=digest(witness)
    elif change=='canonical-inode':
        replacement=tmp_path/'replacement.yaml';replacement.write_bytes(manifest.read_bytes());replacement.replace(manifest)

    original=manifest.read_bytes();head=git(repo,'rev-parse','HEAD');archive=tmp_path/'archive.bundle'
    try:
        with pytest.raises(ValidationError):prepare(str(manifest),str(repo),str(archive),str(lock),preservation_file=str(path),preservation_sha256=sha)
    finally:
        if held:held.close()
    assert not archive.exists()
    assert manifest.read_bytes()==original and git(repo,'rev-parse','HEAD')==head


def test_native_index_observation_never_refreshes_original_cas(tmp_path):
    """Fresh/racy native index must remain byte-exact during recovery guards."""
    from omac.core.state_sync_recovery import _git, _index_matches, _worktree_diff
    repo=tmp_path/'repo';repo.mkdir()
    git(repo,'init','-b','main');git(repo,'config','user.name','offline');git(repo,'config','user.email','offline@example.invalid')
    (repo/'tracked').write_text('same original body\n');git(repo,'add','tracked');git(repo,'commit','-m','original')
    index=repo/'.git/index';before=index.read_bytes()
    assert not _worktree_diff(repo,index,['--','.'])
    assert _index_matches(repo,'HEAD')
    assert index.read_bytes()==before
    (repo/'tracked').write_text('changed body\n')
    assert _worktree_diff(repo,index,['--','.'])
    assert _index_matches(repo,'HEAD')
    assert index.read_bytes()==before
    git(repo,'add','tracked');before=index.read_bytes()
    assert not _index_matches(repo,'HEAD')
    assert index.read_bytes()==before


@pytest.fixture
def actual212_inventory():
    import copy
    from omac.core.state_sync_recovery import validate_preserved_inventory
    captured=json.loads((Path(__file__).parent/'fixtures/untracked_preservation/actual212-hash-type-inventory.json').read_text())
    keys={'file','path','resolved_path','type','mode','device','inode','nlink','uid','gid','bytes','sha256'}
    rows=[{**{k:r[k] for k in keys},'disposition':'preserve-unmodified'} for r in captured['files']]
    source={'repo':'/Volumes/SSD1T/code/ai/open-agent-cluster','path':'/Volumes/SSD1T/code/ai/open-agent-cluster/.omac/open-agent-cluster.yaml','untracked':[{k:r[k] for k in ('file','path','bytes','sha256')} for r in rows]}
    assert len(validate_preserved_inventory(rows,source))==212
    return copy.deepcopy(rows),source


@pytest.mark.parametrize('change',['missing','sampled','duplicate','wildcard','absolute','parent','path','resolved','canonical','type','mode','inode','nlink','uid','size','sha','disposition','hardlink','extra','not-list'])
def test_actual212_hash_type_context_rejects_incomplete_or_forged_inventory(actual212_inventory,change):
    from omac.core.state_sync_recovery import validate_preserved_inventory
    rows,source=actual212_inventory
    if change=='missing':rows.pop()
    elif change=='sampled':rows=rows[:116]
    elif change=='duplicate':rows.append(rows[0])
    elif change=='wildcard':rows[0]['file']='.omac/*'
    elif change=='absolute':rows[0]['file']='/tmp/foreign'
    elif change=='parent':rows[0]['file']='../foreign'
    elif change=='path':rows[0]['path']+='.foreign'
    elif change=='resolved':rows[0]['resolved_path']+='.foreign'
    elif change=='canonical':rows[0]['path']=rows[0]['resolved_path']=source['path'];rows[0]['file']='.omac/open-agent-cluster.yaml'
    elif change=='type':rows[0]['type']='symlink'
    elif change=='mode':rows[0]['mode']=True
    elif change=='inode':rows[0]['inode']=0
    elif change=='nlink':rows[0]['nlink']=2
    elif change=='uid':rows[0]['uid']=-1
    elif change=='size':rows[0]['bytes']+=1
    elif change=='sha':rows[0]['sha256']='0'*64
    elif change=='disposition':rows[0]['disposition']='inactive'
    elif change=='hardlink':rows[1]['device']=rows[0]['device'];rows[1]['inode']=rows[0]['inode']
    elif change=='extra':rows[0]['ignored']=True
    else:rows={}
    with pytest.raises(ValidationError):validate_preserved_inventory(rows,source)


@pytest.mark.parametrize('file_value',[None,[],{}, {'path':None}, {'path':'/tmp/foreign','bytes':0,'sha256':'0'*64,'extra':True}])
def test_malformed_preservation_file_identity_fails_validation(file_value):
    from omac.core.state_sync_recovery import _validate,digest,PRESERVED_RECOVERY
    fields={'repo','path','relative_path','branch','head','base','upstream','remote','origin_url','index','untracked','config','state','logical','environment','original_blob'}
    source={k:None for k in fields};source['preservation']={'file':file_value,'sha256':'0'*64,'request':{}}
    request={'schema':PRESERVED_RECOVERY,'source':source,'writer_lock':None,'archive':None,'archive_receipt':None,'original_objects':None,'target_identity':None,'target_commit':None,'target_objects':None}
    with pytest.raises(ValidationError):_validate(request,digest(request))


def test_all116_original_files_survive_lost_ack_restarts(tmp_path,monkeypatch):
    import tarfile,hashlib
    import omac.core.state_sync_recovery as r
    from omac.core.state_transport import decode
    repo,manifest,remote,lock=private_case(tmp_path)
    fixture=Path(__file__).parent/'fixtures/untracked_preservation'
    captured=json.loads((fixture/'capture.json').read_text())
    with tarfile.open(fixture/'actual-managed-files.tar.gz') as archive:
        for member in archive.getmembers():
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            target=repo/member.name;assert not target.exists();target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read());target.chmod(member.mode)
    rows=captured['files'];assert len(rows)==116
    before=[]
    for row in rows:
        path=repo/row['file'];body=path.read_bytes()
        assert len(body)==row['bytes'] and hashlib.sha256(body).hexdigest()==row['sha256']
        before.append((path,body,path.stat().st_mode,path.stat().st_ino))
    original=manifest.read_bytes()
    value=r.prepare_preservation(manifest,repo,lock,authority='offline exact preservation',reason='UNKNOWN files stay outside effects')
    witness=tmp_path/'witness.json';witness.write_text(json.dumps(value))
    request=r.prepare(manifest,repo,tmp_path/'original.bundle',lock,preservation_file=witness,preservation_sha256=r.digest(value))
    durable=r._durable;pending=['object-'+request['target_objects'][0]['oid'],'state-encoding','index','branch-ref','push'];missed=[]
    def lost_ack(path,value):
        if pending and value.get('effects',{}).get(pending[0])=='observed':
            missed.append(pending.pop(0));raise TimeoutError('Actual private effect complete; durable ACK lost')
        return durable(path,value)
    monkeypatch.setattr(r,'_durable',lost_ack)
    for _ in range(5):
        with pytest.raises(TimeoutError):r.resolve(request,request_sha256=r.digest(request),authority='offline recovery',reason='observe original intent')
    result=r.resolve(request,request_sha256=r.digest(request),authority='offline recovery',reason='observe original intent')
    assert len(missed)==5 and not pending and result['state']=='complete-remote-observed'
    assert decode(manifest.read_bytes())==original
    for path,body,mode,inode in before:
        assert path.read_bytes()==body and path.stat().st_mode==mode and path.stat().st_ino==inode


def test_preservation_unknown_intended_object_is_not_replayed(tmp_path,monkeypatch):
    import omac.core.state_sync_recovery as r
    from omac.errors import NeedsDecision
    repo,manifest,remote,lock=private_case(tmp_path)
    protected=repo/'.omac/unknown.json';protected.write_bytes(b'unknown activity\n')
    witness=r.prepare_preservation(manifest,repo,lock,authority='offline preservation',reason='No new rights')
    file=tmp_path/'witness.json';file.write_text(json.dumps(witness))
    request=r.prepare(manifest,repo,tmp_path/'unknown.bundle',lock,preservation_file=file,preservation_sha256=r.digest(witness))
    actual=r._git;attempts=[]
    def unknown(repo,*args,**kwargs):
        if args[:1]==('hash-object',) and '-w' in args:
            attempts.append(args);raise TimeoutError('Native write outcome unknown after durable intention')
        return actual(repo,*args,**kwargs)
    monkeypatch.setattr(r,'_git',unknown)
    with pytest.raises(TimeoutError):r.resolve(request,request_sha256=r.digest(request),authority='offline recovery',reason='Same exact original intent')
    with pytest.raises(NeedsDecision):r.resolve(request,request_sha256=r.digest(request),authority='offline recovery',reason='Same exact original intent')
    assert len(attempts)==1
    assert protected.read_bytes()==b'unknown activity\n'
    assert git(repo,'rev-parse','HEAD').decode().strip()==request['source']['head']
    assert manifest.read_bytes()==__import__('test_full_state_transport').captured_bytes()


@pytest.mark.parametrize('change',['authority','archive','sha','inside','existing'])
def test_public_preservation_capture_requires_new_external_exact_request(tmp_path,change):
    from omac.cli.main import main
    repo=tmp_path/'repo';repo.mkdir();target=tmp_path/'witness.json'
    extra=['--authority','offline preservation','--reason','Every path unchanged']
    if change=='authority':extra=[]
    elif change=='archive':extra+=['--archive',str(tmp_path/'not-created.bundle')]
    elif change=='sha':extra+=['--preservation-sha256','0'*64]
    elif change=='inside':target=repo/'witness.json'
    elif change=='existing':target.write_bytes(b'original immutable witness')
    assert main(['dag','recover-sync',str(repo/'.omac/open-agent-cluster.yaml'),'--repo',str(repo),'--writer-lock',str(tmp_path/'unopened.lock'),'--prepare-preservation',str(target),*extra])==5
    assert not(tmp_path/'not-created.bundle').exists()
    if change=='existing':assert target.read_bytes()==b'original immutable witness'
    else:assert not target.exists()
