"""Exact owner context carried by the existing managed project-rules payload."""
import hashlib
import json
from pathlib import Path

from ..core.state_transport import DECODED_MAX
from ..errors import ValidationError

SCHEMA = "omac.portable-owner-source/v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_frame(store, item_id, files, *, owner_resolution, approval, expected=None, contents=None):
    entries = {}
    # Validate the entire local corpus before the first publication effect.
    for label, file in sorted(files.items()):
        path = Path(file)
        if not path.is_file() or path.stat().st_size > DECODED_MAX:
            raise ValidationError("Required portable source is missing or exceeds the existing source bound")
        raw = contents[label] if contents is not None else path.read_bytes()
        entries[label] = {"provenance": str(file), "bytes": len(raw), "sha256": _sha(raw)}
    header = {"owner_resolution": owner_resolution, "approval_sha256": approval}
    if expected is not None and "owner_history_sha256" in expected:
        header["owner_history_sha256"] = expected["owner_history_sha256"]
    if expected is not None and expected != {**header, "files": entries}:
        raise ValidationError("Source differs from authenticated approved corpus; do not publish")
    for label, file in sorted(files.items()):
        raw = contents[label] if contents is not None else Path(file).read_bytes()
        if len(raw) != entries[label]["bytes"] or _sha(raw) != entries[label]["sha256"]:
            raise ValidationError("Owner source changed before publication; do not dispatch")
        chunks = [store.publish_source_artifact(item_id, raw[i:i + 1024 * 1024])
                  for i in range(0, len(raw), 1024 * 1024)]
        if b"".join(store.read_source_artifact(ref) for ref in chunks) != raw:
            raise ValidationError("Published owner source cannot be independently retrieved exactly")
        entries[label]["chunks"] = chunks
    frame = json.dumps({"schema": SCHEMA, **header, "files": entries},
                       ensure_ascii=False, sort_keys=True)
    if len(frame.encode()) > DECODED_MAX:
        raise ValidationError("Portable owner index exceeds the existing source bound; do not dispatch")
    return frame


def publish_sources(store, item_id, files, *, owner_resolution="", approval="", expected=None, contents=None):
    frame = source_frame(store, item_id, files, owner_resolution=owner_resolution, approval=approval, expected=expected, contents=contents)
    raw = frame.encode()
    ref = {"issue_id": item_id, "label": "owner-source", "kind": "amendment-source",
           "delivery_key": "project-rules", "content_sha256": _sha(raw),
           "content_bytes": len(raw), "content_externalized": True}
    item = store.get_work_item(item_id)
    if item.project_rules not in (None, "", frame):
        raise ValidationError("Existing portable owner context differs; inspect work show before dispatch")
    refs = [r for r in item.source_refs if r.get("label") != "owner-source"] + [ref]
    if item.project_rules != frame or item.source_refs != refs:
        store.update_work_item_metadata(item_id, project_rules=frame, source_refs=refs)
    observed = store.get_work_item(item_id)
    if observed.project_rules != frame or observed.source_refs != refs:
        raise ValidationError("Portable owner source publication was not confirmed; inspect work show without dispatch")
    read_source(store, ref)
    return [ref]


def read_source(store, ref, *, entry=None):
    item = store.get_work_item(ref["issue_id"])
    raw = (item.project_rules or "").encode()
    if (len(raw) > DECODED_MAX or len(raw) != ref.get("content_bytes")
            or _sha(raw) != ref.get("content_sha256")):
        raise ValidationError("Portable owner source index differs from the attached exact reference")
    try:
        frame = json.loads(raw)
        if frame["schema"] != SCHEMA or not isinstance(frame["files"], dict):
            raise ValueError("unsupported source frame")
        labels = [entry] if entry is not None else list(frame["files"])
        selected = None
        for label in labels:
            value = frame["files"][label]
            if type(value["bytes"]) is not int or not 0 <= value["bytes"] <= DECODED_MAX:
                raise ValueError("source entry exceeds its bound")
            chunks = value["chunks"]
            if not isinstance(chunks, list) or len(chunks) != (value["bytes"] + 1024 * 1024 - 1) // (1024 * 1024):
                raise ValueError("source chunk count differs from its exact bounded frame")
            body = bytearray()
            for index, chunk in enumerate(chunks):
                if chunk.get("issue_id") != item.id:
                    raise ValueError("source locator belongs to another issue")
                part = store.read_source_artifact(chunk)
                expected_size = min(1024 * 1024, value["bytes"] - index * 1024 * 1024)
                if not isinstance(part, bytes) or len(part) != expected_size:
                    raise ValueError("invalid source chunk")
                body.extend(part)
                if len(body) > value["bytes"]:
                    raise ValueError("source entry overflows its declared bound")
            if len(body) != value["bytes"] or _sha(body) != value["sha256"]:
                raise ValueError("source entry bytes differ")
            if entry is not None:
                selected = bytes(body)
        return selected if entry is not None else raw
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Invalid complete portable owner source; inspect work show and exact source label") from exc


def assessment_files(manifest_path, request_file, request, report_file, docs):
    from .amendment import _manifest_project_root, _resolve_docs_input, _docs_snapshot
    from .owner_amendment import request_inputs, required_inputs
    from ..core.manifest import load_manifest
    if (request_inputs(manifest_path, report_file, docs, request["blocked_nodes"]) != request["inputs"]
            or required_inputs(load_manifest(manifest_path), manifest_path) != request["required_inputs"]):
        raise ValidationError("Complete authoritative report/docs/required inputs changed before portable publication")
    root = _manifest_project_root(manifest_path)
    files = {"owner-request": Path(request_file), "current-runtime-manifest": Path(manifest_path),
             "assessment-report": Path(report_file)}
    def references(value):
        if isinstance(value, dict):
            if {"file", "bytes", "sha256"} <= set(value):
                path = Path(value["file"])
                raw = path.read_bytes()
                if len(raw) != value["bytes"] or _sha(raw) != value["sha256"]:
                    raise ValidationError("Required owner Source reference changed before portable publication")
                files.setdefault("source-" + value["sha256"] + "-" + _sha(str(path).encode()), path)
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)
    for field in ("source_qualification", "prospective_assessment", "current_source_qualification"):
        references(request.get(field))
    current_source = request.get("current_source_qualification")
    if current_source is not None:
        context = json.loads(Path(current_source["input"]["file"]).read_bytes())
        for entry in context["owner_history"].values():
            if "request_ref" in entry:
                references(entry["request_ref"])
    snapshots = [request["required_inputs"]["files"],
                 _docs_snapshot([str(_resolve_docs_input(p, root)) for p in docs], project_root=root)]
    for snapshot in snapshots:
        for logical in snapshot["docs_files"]:
            path = _resolve_docs_input(logical, root)
            files["document:" + logical] = path
    return files

def authenticate_sources(engine, manifest_path, request_file, request_digest, request, report_file, docs):
    """Pin the approved complete corpus before the first native publication."""
    from ..core.manifest import load_manifest
    from ..core.owner_amendment import digest, owner_request, owner_history_sha
    from .owner_amendment import authorized_request
    manifest_raw = Path(manifest_path).read_bytes()
    manifest = load_manifest(manifest_path)
    entry = authorized_request(manifest, request_digest, engine.store, engine.runtime)
    canonical = owner_request(entry)
    if not Path(request_file).is_file() or Path(request_file).stat().st_size > DECODED_MAX:
        raise ValidationError("Owner request exceeds the existing source bound; do not publish")
    request_raw = Path(request_file).read_bytes()
    try:
        actual = json.loads(request_raw)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("Owner request bytes are not the approved complete request") from exc
    if digest(actual) != request_digest or digest(request) != digest(canonical) or digest(actual) != digest(canonical):
        raise ValidationError("Owner request bytes differ from canonical approved request; do not publish")
    files = assessment_files(manifest_path, request_file, canonical, report_file, docs)
    contents = {}
    for label, file in files.items():
        if not Path(file).is_file() or Path(file).stat().st_size > DECODED_MAX:
            raise ValidationError("Approved Source is missing or exceeds the existing source bound")
        contents[label] = Path(file).read_bytes()
    if contents["owner-request"] != request_raw or contents["current-runtime-manifest"] != manifest_raw:
        raise ValidationError("Approved request/manifest changed during Source authentication")
    # Recompute approved document digests from the very bytes to be published.
    for snapshot in (canonical["inputs"]["docs"], canonical["required_inputs"]["files"]):
        document_hash = hashlib.sha256()
        for logical in snapshot["docs_files"]:
            encoded = logical.encode("utf-8")
            raw = contents["document:" + logical]
            document_hash.update(len(encoded).to_bytes(8, "big"))
            document_hash.update(encoded)
            document_hash.update(len(raw).to_bytes(8, "big"))
            document_hash.update(raw)
        if document_hash.hexdigest() != snapshot["docs_sha256"]:
            raise ValidationError("Approved document bytes changed before publication")
    report = contents["assessment-report"].decode("utf-8").replace("\\r\\n", "\\n").replace("\\r", "\\n")
    if report != canonical["inputs"]["report"]:
        raise ValidationError("Approved report bytes changed before publication")
    # All referenced qualification/history bodies retain their approved raw pins.
    by_path = {str(Path(files[label])): raw for label, raw in contents.items()}
    def verify_refs(value):
        if isinstance(value, dict):
            if {"file", "bytes", "sha256"} <= set(value):
                raw = by_path.get(str(Path(value["file"])))
                if raw is not None and (len(raw) != value["bytes"] or _sha(raw) != value["sha256"]):
                    raise ValidationError("Approved referenced Source bytes changed before publication")
            for child in value.values():
                verify_refs(child)
        elif isinstance(value, list):
            for child in value:
                verify_refs(child)
    verify_refs(canonical)
    qualification = canonical.get("current_source_qualification")
    if qualification is not None:
        context = json.loads(by_path[str(Path(qualification["input"]["file"]))])
        verify_refs(context)
        report_pin = context["input_bytes"]["report"]
        if len(contents["assessment-report"]) != report_pin["bytes"] or _sha(contents["assessment-report"]) != report_pin["sha256"]:
            raise ValidationError("Approved current report raw bytes changed before publication")
    # A complete fresh authorization and raw sandwich includes the whole history.
    fresh = load_manifest(manifest_path)
    final_entry = authorized_request(fresh, request_digest, engine.store, engine.runtime)
    if (digest(final_entry) != digest(entry) or Path(manifest_path).read_bytes() != manifest_raw
            or any(Path(files[label]).read_bytes() != raw for label, raw in contents.items())):
        raise ValidationError("Approved complete Source changed during publication authentication")
    authority = {"owner_resolution": request_digest, "approval_sha256": entry["approval_sha256"],
                 "owner_history_sha256": owner_history_sha(manifest, request_digest),
                 "files": {label: {"provenance": str(files[label]), "bytes": len(raw), "sha256": _sha(raw)}
                           for label, raw in contents.items()}}
    return files, contents, authority


def verify_approved_source(store, reference, entry, *, authority=None):
    from ..core.owner_amendment import digest, owner_request, owner_history_sha
    expected = authority if authority is not None else entry.get("portable_authority")
    if not isinstance(expected, dict):
        raise ValidationError("Portable Source lacks authenticated canonical approval; do not dispatch")
    frame = json.loads(read_source(store, reference))
    actual = {key: frame.get(key) for key in ("owner_resolution", "approval_sha256")}
    if "owner_history_sha256" in expected:
        actual["owner_history_sha256"] = frame.get("owner_history_sha256")
    actual["files"] = {label: {k: v for k, v in value.items() if k != "chunks"}
                       for label, value in frame["files"].items()}
    request = owner_request(entry)
    if (digest(actual) != digest(expected) or actual["owner_resolution"] != digest(request)
            or actual["approval_sha256"] != entry["approval_sha256"]
            or digest(json.loads(read_source(store, reference, entry="owner-request"))) != digest(request)):
        raise ValidationError("Portable Source differs from authenticated complete approved corpus")
    return frame
