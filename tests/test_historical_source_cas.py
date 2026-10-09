"""Real captured historical source rejects drift before its journal effect."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_system_historical_source import system as system_fixture, FIXTURES
from test_current_owner_source import current as current_fixture, captured as captured_fixture
from omac.core.manifest import load_manifest, save_manifest
from omac.core.owner_amendment import JOURNAL
from omac.errors import ValidationError
from omac.pipeline.historical_review_source import prepare_review_source, resolve_review_source


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    c = captured_fixture.__wrapped__(tmp_path_factory.mktemp('historical-cas'), monkeypatch)
    c = current_fixture.__wrapped__(c)
    c = system_fixture.__wrapped__(c)
    file = str(Path(c.path).with_name('historical.json'))
    result = prepare_review_source(c.engine, c.path, 'system-upgrade', str(FIXTURES/'witness.json'), file)
    c.historical_file, c.historical_sha = file, result['source_sha256']
    c.canonical = Path(c.path).read_bytes()
    c.original_item = copy.deepcopy(c.system_item)
    c.original_runs = copy.deepcopy(c.runs[c.system_item.id])
    yield c
    monkeypatch.undo()


@pytest.mark.parametrize('change', ['uploader', 'native-task', 'native-time', 'active-run', 'extra-run', 'budget', 'opaque-type', 'foreign-node', 'DONE', 'owner-history', 'required-bytes'])
def test_historical_source_drift_never_approves(prepared, monkeypatch, change):
    c = prepared
    Path(c.path).write_bytes(c.canonical)
    c.items[c.system_item.id] = copy.deepcopy(c.original_item)
    item = c.items[c.system_item.id]
    c.runs[item.id] = copy.deepcopy(c.original_runs)
    original_attachment = c.engine.store.observe_verification_attachment
    if change in {'uploader', 'native-task', 'native-time'}:
        field, value = {'uploader': ('uploader_id', 'foreign'), 'native-task': ('task_id', 'foreign'),
                        'native-time': ('created_at', '2026-10-09T03:59:02Z')}[change]
        monkeypatch.setattr(c.engine.store, 'observe_verification_attachment',
                            lambda *args: replace(original_attachment(*args), **{field: value}))
    elif change == 'active-run':
        c.runs[item.id][0] = replace(c.runs[item.id][0], status='running')
    elif change == 'extra-run':
        c.runs[item.id].append(replace(c.runs[item.id][0], id='extra-terminal'))
    elif change == 'budget':
        item.bounces.worker += 1
    elif change == 'opaque-type':
        item.unknown_persisted_fields['metadata.opaque'] = False
    elif change in {'foreign-node', 'DONE', 'owner-history'}:
        manifest = load_manifest(c.path)
        if change == 'foreign-node':
            node = next(n for n in manifest.nodes.values() if n.id != 'system-upgrade' and n.status != 'done')
            node.worker += '-changed'
        elif change == 'DONE':
            next(n for n in manifest.nodes.values() if n.status == 'done').description = 'foreign DONE drift'
        else:
            manifest.meta[JOURNAL][next(iter(manifest.meta[JOURNAL]))]['foreign'] = True
        save_manifest(manifest, c.path)
    else:
        manifest = load_manifest(c.path)
        acceptance = Path(c.path).parent / manifest.meta['acceptance_file']
        raw = acceptance.read_bytes()
        acceptance.write_bytes(raw + b'\n')
    before = Path(c.path).read_bytes()
    try:
        with pytest.raises(ValidationError):
            resolve_review_source(c.engine, c.path, c.historical_file, source_sha256=c.historical_sha,
                                 authority='Exact offline Root test scope', reason='Source identity only')
        assert Path(c.path).read_bytes() == before
    finally:
        if change == 'required-bytes':
            acceptance.write_bytes(raw)
        Path(c.path).write_bytes(c.canonical)
        c.items[item.id] = copy.deepcopy(c.original_item)
        c.runs[item.id] = copy.deepcopy(c.original_runs)
