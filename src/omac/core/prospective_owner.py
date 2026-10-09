"""Exact qualified prospective declarations, without native allocation authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path

import yaml

from ..engines.models import WorkItem
from ..errors import ValidationError


GROUPS = {
    "credential": ("credential-store", {"credential-store"}, {"credential-rotation-wire-publication"}),
    "agent-api": ("api-agent", {"api-agent"}, {"agent-catalog-production-commands"}),
    "mcp": ("mcp-catalog-governed-hardening", {"mcp-catalog-governed-hardening", "api-mcp"}, {"mcp-platform-transaction-composition", "agentrun-mcp-consumption-evidence"}),
}
CANDIDATES = {node for _, _, ids in GROUPS.values() for node in ids}
_DOCUMENTS = {
    "authority.json": (2202, "720d9e4076456157d6011a33620009ecf7a95f2651d4ae4ceba37d91b245a293"),
    "scope.yaml": (11969, "ff34789f03bef8b04200e4f812670db0c6f3aac19596217e666b122c9e7a9154"),
    "scope-native.json": (282090, "03145440d22267727099873d6ae613f41250a42275fa07518a8a7c5af243b71a"),
    "scope-issue.json": (216331, "00391c26bdd1e533117b4a024e2224b79c71a36b63098087941fb51bb4267059"),
    "scope-runs.json": (1589, "e081a73424034bd5c90b3520fac60ac449500bd26b7bd832972fbc127a1ff9ad"),
    "manifest.yaml": (13226304, "b024b36cc3c651902f8847289432e0edfa4eef9095f36e81ef4f0056e29f2c2b"),
    "original-request.json": (2812299, "0889de59d82e0245bbc1da5a8af4875e8949ecfad738d1a86fd1860a19f01792"),
    "api-mcp.json": (88184, "8f2815a4302304ae544af4736ec7101333a8970635027c32e847f11bc6f4695b"),
    "api-mcp-contract.yaml": (8367, "7bc97dbf863ed234411d57f3193ee67f39466417369ab96c764822aac9c6ba8b"),
    "api-mcp-ledger.yaml": (51178, "fef9fdfb12d70cd2db2f3acfebf0e7c232ad0defcd439e39041034d539a1f333"),
    "api-mcp-verification.yaml": (7844, "74950f0650d211e75ed565ea4dcba9d865a4d12d840347011630f1e7ad1d941f"),
    "recovery.yaml": (118932, "f1c8c4ac80c79170666b576f89a955f268825eb36d38605e15cb77c21e730f5f"),
    "history.json": (378953, "1b65a852d2c4138f82b8a214bc3a766db331f689e237b955cd54753c78ddbc69"),
}
_MANIFEST = "d8e360cb9cfca4a764989de19a7319e9ebb3057b5a73f751287f79dcf228ede1"
_HISTORY = {
    "6a871cb224504c60ebcbf69f2efed045de378a57a88b8af56c10c4f4a6dd9bf2": "17bf1e9592aa10341eb9d7083741b5c51ea1a13ecf5f15d3a2b1b1d008b3cc9f",
    "76af977110f0e80c0ba0578ebc8ecb81dee3cb75aacba1008c5a153d2bad56fb": "80e8de4db258a4acd6250e1d602c92c8827f526ea99dcf518c8440ddfe48c003",
    "d43b1570760168e5769890f5ac57821ebb220801a44714c3706bae333cfe94d1": "cd55c1845a953885ea2addde4e1d8770689cb1689f092c5a1bfa0d2f9db31963",
}


def _invalid(message):
    raise ValidationError(message + "; inspect the exact qualified source and run `omac dag amend prepare-owner --help`.")


def prospective_input(file):
    """Reread the whole qualified corpus; historical scope is not a proposal."""
    from .owner_amendment import digest

    try:
        path = Path(file).resolve()
        if not path.is_file() or path.stat().st_size > 65536:
            _invalid("Prospective source witness must be a bounded regular file")
        raw = path.read_bytes()
        if len(raw) > 65536:
            _invalid("Prospective source witness exceeds its byte bound")
        witness = json.loads(raw)
        if (isinstance(witness, dict) and set(witness) == {"schema", "group", "references"}
                and witness["schema"] == "omac.prospective-owner-assessment/v1" and witness["group"] == "credential"):
            from .credential_prospective import credential_input
            return credential_input(path, witness, raw)
        if (set(witness) != {"schema", "group", "references"}
                or witness["schema"] != "omac.prospective-owner-assessment/v1"
                or witness["group"] not in GROUPS
                or set(witness["references"]) != set(_DOCUMENTS)):
            _invalid("Prospective source witness or Root-decided group is unsupported")
        references, bodies = {}, {}
        for key, (size, sha) in _DOCUMENTS.items():
            ref = witness["references"][key]
            if (set(ref) != {"file", "bytes", "sha256"}
                    or type(ref["bytes"]) is not int
                    or ref["bytes"] != size or ref["sha256"] != sha):
                _invalid("Full prospective source references differ from qualification")
            source = (path.parent / ref["file"]).resolve()
            if not source.is_file() or source.stat().st_size != size:
                _invalid("Full prospective source bytes are missing")
            data = source.read_bytes()
            if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                _invalid("Full prospective source bytes changed")
            references[key] = {**ref, "file": str(source)}
            bodies[key] = data
        group = witness["group"]
        scope = yaml.safe_load(bodies["scope.yaml"])
        publication = next(s for s in scope["requests"] if s["group"] == group)
        publication_sha = digest(publication)
        declarations = {
            key: {"state": "assessment-only", "native_workitem": "not-created",
                  "allocation": "not-authorized", "history": "not-observed",
                  "publication_scope_sha256": publication_sha}
            for key in sorted(GROUPS[group][2])
        }
        return {
            "schema": witness["schema"], "group": group,
            "references": references, "declarations": declarations,
            "publication_scope_sha256": publication_sha,
            "input": {"file": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        }, bodies
    except (OSError, TypeError, ValueError, KeyError, StopIteration) as exc:
        _invalid("Complete prospective assessment qualification is malformed: " + str(exc))


def qualify_prospective(manifest, qualification, store, runtime, *, request_sha=None, current_source=None):
    """Keep existing-node history/budgets and both original held sources strict."""
    from .owner_amendment import JOURNAL, capture_source, digest, manifest_source

    if qualification.get("group") == "credential":
        from .credential_prospective import qualify_credential
        return qualify_credential(manifest, qualification, store, runtime, current_source=current_source, request_sha=request_sha)
    observed, bodies = prospective_input(qualification.get("input", {}).get("file"))
    if observed != {k: v for k, v in qualification.items() if k != "global_held_sources"}:
        _invalid("Prospective declarations or exact publication boundaries changed")
    if current_source is None and digest(manifest_source(manifest)) != _MANIFEST:
        _invalid("Full manifest/DONE/history/recovery source changed")
    history = {k: digest(v) for k, v in manifest.meta.get(JOURNAL, {}).items() if k != request_sha}
    if current_source is not None:
        from .current_owner_source import historical_current_binding
        historical_current_binding(manifest, current_source, _HISTORY, request_sha=request_sha)
    if (current_source is None and history != _HISTORY) or CANDIDATES & set(manifest.nodes):
        _invalid("Original owner history changed or a prospective candidate already exists")
    for candidate in CANDIDATES:
        if store.find_work_item_by_dag_key(store.config.workspace_id, candidate) is not None:
            _invalid("Prospective candidate already has a native WorkItem; fresh source qualification is required")
    old = json.loads(bodies["original-request.json"])
    held = {k: capture_source(manifest, k, store, runtime, held=True) for k in old["blocked_nodes"]}
    if digest(held) != digest(old["sources"]):
        _invalid("Original held sources/failed bytes/generations/budgets changed")
    if "global_held_sources" in qualification and held != qualification["global_held_sources"]:
        _invalid("Global held source CAS changed")
    if qualification["group"] == "mcp":
        derived = json.loads(bodies["api-mcp.json"])
        actual = capture_source(manifest, "api-mcp", store, runtime)
        expected = {f.name: derived["control"][f.name] for f in fields(WorkItem) if f.name in derived["control"]}
        if (digest(actual["item"]) != digest(expected)
                or digest(actual["runs"]) != digest(sorted(derived["runs"], key=lambda run: run["id"]))
                or actual["budget"] != derived["effective_absolute_relative_budget"]):
            _invalid("Actual derived api-mcp full source/budget authority changed")
    return {**observed, "global_held_sources": held}


def reject_unallocated_dispatch(node_ids):
    if CANDIDATES.intersection(node_ids):
        _invalid("Prospective candidates are not allocated; Worker dispatch requires separate typed allocation/application authority")


def reject_unallocated_application(proposal):
    """No allocation/application permission exists for these exact declarations."""
    operations = proposal.get("operations")
    if not isinstance(operations, list):
        return  # Existing structural validation owns malformed operations.
    if any(isinstance(op, dict) and (
           (op.get("op") == "add" and isinstance(op.get("value"), dict)
            and op["value"].get("id") in CANDIDATES)
           or op.get("node") in CANDIDATES)
           for op in operations):
        _invalid("Prospective node application requires separate typed allocation/application authority; assessment or Reviewer PASS is insufficient")
