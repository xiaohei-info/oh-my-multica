"""Source and canonical observation races fail before operator effects."""
import json
from pathlib import Path

import pytest

from test_ui_preserved_owner_assessment import captured, ALLOWED
from omac.core.manifest import load_manifest, save_manifest
from omac.core.owner_amendment import JOURNAL
from omac.errors import ValidationError
from omac.pipeline.owner_amendment import prepare_current_owner_source, resolve_current_owner_source


@pytest.mark.parametrize("drift", ["source-file", "canonical-history"])
def test_late_capture_drift_stops_before_current_source_approval(captured, monkeypatch, drift):
    import omac.core.current_owner_source as current
    c = captured
    source_file = str(Path(c.output).with_name("pending-current.json"))
    draft = prepare_current_owner_source(
        c.engine, c.path, blocked_nodes=["identity-local"], allowed_nodes=ALLOWED,
        report_file=c.report, docs=c.docs, output_file=source_file,
        source_witness_file=c.witness,
    )
    original = current.capture_current_source
    expected_manifest = Path(c.path).read_bytes()

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if drift == "source-file":
            Path(source_file).write_bytes(Path(source_file).read_bytes() + b" ")
        else:
            manifest = load_manifest(c.path)
            manifest.meta[JOURNAL]["foreign-after-observation"] = {"state": "approved"}
            save_manifest(manifest, c.path)
        return result

    monkeypatch.setattr(current, "capture_current_source", changed)
    with pytest.raises(ValidationError):
        resolve_current_owner_source(
            c.engine, c.path, source_file, source_sha256=draft["source_sha256"],
            authority="Explicit offline Root current source scope", reason="Assessment only",
        )
    manifest = load_manifest(c.path)
    assert current.CURRENT_JOURNAL not in manifest.meta
    if drift == "source-file":
        assert Path(c.path).read_bytes() == expected_manifest
    else:
        assert manifest.meta[JOURNAL]["foreign-after-observation"] == {"state": "approved"}
