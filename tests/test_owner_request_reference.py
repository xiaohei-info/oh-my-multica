import hashlib
import json

import pytest

from omac.core.owner_amendment import digest, JOURNAL
from omac.errors import ValidationError


def test_current_owner_request_reference_preserves_full_raw_bytes(tmp_path):
    from omac.core.owner_amendment import owner_request
    body = {'schema': 'omac.owner-amendment-request/v1', 'transport_fixture_only': 'full immutable body, no authority inferred'}
    path = tmp_path / 'request.json'
    raw = (json.dumps(body, indent=2) + '\n').encode()
    path.write_bytes(raw)
    ref = {'file': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    approval = {'request_sha256': digest(body), 'request_input': ref, 'authority': 'offline fixture', 'reason': 'reference representation only'}
    entry = {'request_ref': ref, 'approval': approval, 'approval_sha256': digest(approval), 'state': 'approved'}
    assert owner_request(entry) == body
    path.write_bytes(raw + b' ')
    with pytest.raises(ValidationError):
        owner_request(entry)


def test_legacy_owner_request_body_is_not_rewritten():
    from omac.core.owner_amendment import owner_request
    old = {'request': {'schema': 'omac.owner-amendment-request/v1', 'opaque': 0}, 'state': 'assessment_started'}
    before = json.dumps(old)
    assert owner_request(old) == old['request']
    assert json.dumps(old) == before


def test_fourth_current_history_numeric_type_drift_is_rejected():
    from omac.core.current_owner_source import _Capture, historical_current_binding
    from omac.core.manifest import Manifest
    manifest = Manifest(meta={JOURNAL: {str(i): {'opaque': 0} for i in range(4)}}, nodes={})
    context = _Capture(manifest)
    manifest.meta[JOURNAL]['3']['opaque'] = 0.0
    with pytest.raises(ValidationError):
        historical_current_binding(manifest, context, {})
