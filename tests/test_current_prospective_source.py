"""Current qualification keeps prospective declarations unallocated."""
import copy
from pathlib import Path

import pytest

from test_prospective_owner_assessment import captured, FIXTURES
from omac.core.manifest import load_manifest, save_manifest
from omac.core.owner_amendment import authorized_request, JOURNAL
from omac.core.prospective_owner import reject_unallocated_dispatch, reject_unallocated_application
from omac.errors import ValidationError
from omac.pipeline.owner_amendment import (
    prepare_current_owner_source, resolve_current_owner_source,
    prepare_owner_amendment, resolve_owner_amendment,
)


@pytest.mark.parametrize("group", ["agent-api", "mcp"])
def test_current_prospective_approval_preserves_full_existing_sources_and_blocks_candidates(captured, group):
    c = captured
    node = c.manifest.nodes["system-upgrade"]
    node.status = "blocked" if node.status != "blocked" else "todo"
    save_manifest(c.manifest, c.path)
    historical = copy.deepcopy(c.manifest.meta[JOURNAL])
    witness = FIXTURES / "witness.json"
    if group == "mcp":
        import json
        value = json.loads(witness.read_bytes())
        value["group"] = "mcp"
        # Preserve exact same original referenced files with absolute paths.
        for ref in value["references"].values():
            ref["file"] = str((FIXTURES / ref["file"]).resolve())
        witness = Path(c.output).with_name("mcp-witness.json")
        witness.write_text(json.dumps(value))
    blocked = ["api-agent"] if group == "agent-api" else ["mcp-catalog-governed-hardening"]
    allowed = blocked if group == "agent-api" else ["api-mcp", *blocked]
    source_file = str(Path(c.output).with_name("current-prospective.json"))
    result = prepare_current_owner_source(
        c.engine, c.path, blocked_nodes=blocked, allowed_nodes=allowed,
        report_file=c.report, docs=c.docs, output_file=source_file,
        prospective_source_file=str(witness),
    )
    resolve_current_owner_source(
        c.engine, c.path, source_file, source_sha256=result["source_sha256"],
        authority="Exact offline Root current prospective assessment scope",
        reason="Current complete existing sources; candidates remain unallocated",
    )
    request = prepare_owner_amendment(
        c.engine, c.path, blocked_nodes=blocked, allowed_nodes=allowed,
        report_file=c.report, docs=c.docs, output_file=c.output,
        prospective_source_file=str(witness), current_source_file=source_file,
    )
    resolve_owner_amendment(
        c.engine, c.path, c.output, request_sha256=request["request_sha256"],
        authority="Separate offline Root normal assessment resolution", reason="Fresh independent Review only",
    )
    manifest = load_manifest(c.path)
    entry = authorized_request(manifest, request["request_sha256"], c.engine.store, c.engine.runtime)
    assert {key: manifest.meta[JOURNAL][key] for key in historical} == historical
    from omac.core.owner_amendment import owner_request
    declarations = owner_request(entry)["prospective_assessment"]["declarations"]
    for key in declarations:
        with pytest.raises(ValidationError):
            reject_unallocated_dispatch([key])
        with pytest.raises(ValidationError):
            reject_unallocated_application({"operations": [{"op": "add", "value": {"id": key}}]})
