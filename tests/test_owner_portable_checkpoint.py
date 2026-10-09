"""Canonical dispatch binds the same bytes for Planner and independent Review."""
from copy import deepcopy

import pytest

from omac.core.manifest import Manifest, save_manifest, load_manifest
from omac.core.owner_amendment import JOURNAL, digest
from omac.core.taskmeta import TaskKind
from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.errors import ValidationError
from omac.pipeline.portable_owner import publish_sources
import omac.pipeline.owner_amendment as pipeline


def test_review_cannot_replace_both_portable_frame_and_locator(tmp_path, monkeypatch):
    engine = create_engine('mock', EngineConfig('mock', 'ws', extra={'MOCK_AUTO_COMPLETE': 'false'}))
    item = engine.store.create_work_item('ws', 'assessment', 'Source', 'amend-owner-fixture', 'planner', kind=TaskKind.AMENDMENT)
    file = tmp_path / 'request.json'
    file.write_text('{"full_source": true}\n')
    request = {'full_source': True}
    from omac.pipeline.portable_owner import _sha
    authority = {'owner_resolution': digest(request), 'approval_sha256': 'fixture', 'owner_history_sha256': digest({}), 'files': {
        'owner-request': {'provenance': str(file), 'bytes': len(file.read_bytes()), 'sha256': _sha(file.read_bytes())}}}
    ref = publish_sources(engine.store, item.id, {'owner-request': file}, owner_resolution=digest(request), approval='fixture', expected=authority)[0]
    manifest_path = tmp_path / 'dag.yaml'
    entry = {'dag_key': item.dag_key, 'assessment': {'orchestrator': 'planner'}, 'state': 'assessment_started', 'request': request, 'approval_sha256': 'fixture'}
    save_manifest(Manifest(meta={JOURNAL: {'fixture': entry}}, nodes={}), str(manifest_path))
    monkeypatch.setattr(pipeline, 'authorized_request', lambda manifest, *args: manifest.meta[JOURNAL]['fixture'])
    pipeline.checkpoint_authoring_dispatch(engine, str(manifest_path), 'fixture', item.id, 'before', portable_reference=ref, portable_authority=authority)
    saved = load_manifest(str(manifest_path)).meta[JOURNAL]['fixture']['portable_source']
    assert saved == ref
    raw_before = manifest_path.read_bytes()
    # A different Store context cannot inherit the canonical current Root pin.
    item.project_rules = '{"forged": "complete-looking frame"}'
    item.source_refs = [{**ref, 'content_sha256': '0' * 64}]
    with pytest.raises(ValidationError, match='exact canonical Planner context'):
        pipeline.checkpoint_review_dispatch(engine, str(manifest_path), 'fixture', deepcopy(item), 'reviewer')
    assert manifest_path.read_bytes() == raw_before
