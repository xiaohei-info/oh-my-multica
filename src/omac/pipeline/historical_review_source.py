"""Public source-only preparation, observation and exact operator resolution."""
import copy
import hashlib
import json
from pathlib import Path

from ..core.manifest import load_manifest, save_manifest
from ..core.owner_amendment import JOURNAL, capture_source, digest, manifest_source, retry_policy, owner_request
from ..core.historical_review_source import SCHEMA, JOURNAL_KEY, _HistoricalCapture, read_witness, qualify_subject
from ..errors import ValidationError
from .owner_amendment import required_inputs
from .amendment import _write_yaml_atomic


def _capture(engine, manifest_path, node_id, witness_file, manifest=None):
    manifest = manifest or load_manifest(manifest_path)
    witness = read_witness(witness_file)[0]
    source = capture_source(manifest, node_id, engine.store, engine.runtime,
                            held=True, historical_identity=_HistoricalCapture(witness_file))
    return {"schema": SCHEMA, "scope": "historical-source-qualification-only",
            "manifest_path": str(Path(manifest_path).resolve()), "node_id": node_id,
            "manifest": manifest_source(manifest), "owner_history": copy.deepcopy(manifest.meta.get(JOURNAL, {})),
            "owner_history_inputs": {key: owner_request(entry) for key, entry in manifest.meta.get(JOURNAL, {}).items()
                                     if "request_ref" in entry},
            "source": source, "witness": witness,
            "required_inputs": required_inputs(manifest, manifest_path), "retry_policy": retry_policy(manifest_path)}



def _read_source(file):
    from ..core.state_transport import DECODED_MAX
    try:
        path = Path(file)
        if not path.is_file() or path.stat().st_size > DECODED_MAX:
            raise ValueError("source file is missing or exceeds the existing source bound")
        raw = path.read_bytes()
        source = json.loads(raw)
        if (not isinstance(source, dict) or set(source) != {"schema", "scope", "manifest_path", "node_id", "manifest", "owner_history", "source", "witness", "required_inputs", "retry_policy", "owner_history_inputs"}
                or source["schema"] != SCHEMA or source["scope"] != "historical-source-qualification-only"
                or not isinstance(source["manifest_path"], str) or not isinstance(source["node_id"], str)):
            raise ValueError("unsupported complete historical source schema")
        return source, raw
    except (OSError, ValueError, TypeError) as exc:
        raise ValidationError("Historical Source is malformed or unavailable; run omac dag amend prepare-review-source --help") from exc


def _observe(engine, manifest_path, source):
    manifest = load_manifest(manifest_path)
    node = source["node_id"]
    record = manifest.meta.get(JOURNAL_KEY, {}).get(node)
    normalized = copy.deepcopy(manifest)
    if record is not None:
        if record.get("approval", {}).get("source_sha256") != digest(source):
            raise ValidationError("A different historical source resolution already exists")
        normalized.meta[JOURNAL_KEY].pop(node)
        if not normalized.meta[JOURNAL_KEY] and JOURNAL_KEY not in source["manifest"]["meta"]:
            normalized.meta.pop(JOURNAL_KEY)
    actual = _capture(engine, manifest_path, node, source["witness"]["input"]["file"], normalized)
    if digest(actual) != digest(source):
        raise ValidationError("Whole historical qualification Source changed; prepare fresh current evidence")
    if digest(manifest_source(load_manifest(manifest_path))) != digest(manifest_source(manifest)):
        raise ValidationError("Manifest changed during historical source observation")
    if digest(load_manifest(manifest_path).meta.get(JOURNAL, {})) != digest(manifest.meta.get(JOURNAL, {})):
        raise ValidationError("Full owner history changed during historical source observation")
    return manifest, record


def prepare_review_source(engine, manifest_path, node_id, witness_file, output_file):
    source = _capture(engine, manifest_path, node_id, witness_file)
    _observe(engine, manifest_path, source)
    if Path(output_file).exists():
        if json.loads(Path(output_file).read_bytes()) != source:
            raise ValidationError("Immutable historical source output already differs; choose a new path")
    else:
        _write_yaml_atomic(output_file, source, as_json=True)
    return {"state": "pending_historical_source_resolution", "source_file": output_file,
            "source_sha256": digest(source),
            "next_action": f"omac dag amend resolve-review-source {manifest_path} {output_file} --source-sha256 {digest(source)} --authority <Root-scope> --reason <source-only>"}


def observe_review_source(engine, manifest_path, source_file):
    source, _ = _read_source(source_file)
    if source.get("schema") != SCHEMA or source.get("manifest_path") != str(Path(manifest_path).resolve()):
        raise ValidationError("Historical source schema or canonical manifest path differs")
    manifest, record = _observe(engine, manifest_path, source)
    if record:
        capture_source(manifest, source["node_id"], engine.store, engine.runtime, held=True)
    return {"state": "approved_historical_source" if record else "pending_historical_source_resolution",
            "source_sha256": digest(source), "approval_sha256": record["approval_sha256"] if record else None}


def resolve_review_source(engine, manifest_path, source_file, *, source_sha256, authority, reason):
    if any(not isinstance(v, str) or not v.strip() for v in (authority, reason)):
        raise ValidationError("Explicit exact Root authority and source-only reason are required")
    source, raw = _read_source(source_file)
    if (source.get("schema") != SCHEMA or source.get("manifest_path") != str(Path(manifest_path).resolve())
            or digest(source) != source_sha256):
        raise ValidationError("Explicit historical source SHA/schema/path differs")
    manifest, previous = _observe(engine, manifest_path, source)
    approval = {"source_sha256": source_sha256, "source_input": {"file": str(Path(source_file).resolve()),
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                "scope": "historical-source-qualification-only", "authority": authority, "reason": reason}
    record = {"state": "approved", "approval": approval, "approval_sha256": digest(approval)}
    if previous is not None and previous != record:
        raise ValidationError("Existing historical identity resolution cannot be replaced")
    if previous is None:
        _observe(engine, manifest_path, source)
        if Path(source_file).read_bytes() != raw:
            raise ValidationError("Historical source file changed before resolution effect")
        manifest.meta.setdefault(JOURNAL_KEY, {})[source["node_id"]] = record
        save_manifest(manifest, manifest_path)
    return observe_review_source(engine, manifest_path, source_file)
