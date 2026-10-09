import json
from pathlib import Path

import pytest

from omac.core.prospective_owner import prospective_input, reject_unallocated_application, reject_unallocated_dispatch
from omac.errors import ValidationError


def test_credential_draft_never_allocates_or_dispatches():
    candidate = "credential-rotation-wire-publication"
    with pytest.raises(ValidationError, match="separate typed"):
        reject_unallocated_dispatch([candidate])
    with pytest.raises(ValidationError, match="separate typed"):
        reject_unallocated_application({"operations": [{"op": "add", "value": {"id": candidate}}]})


def test_credential_requires_complete_original_planner_messages(tmp_path):
    witness = tmp_path / "credential.json"
    witness.write_text(json.dumps({"schema": "omac.prospective-owner-assessment/v1", "group": "credential", "references": {}}))
    with pytest.raises(ValidationError, match="complete Credential.*147"):
        prospective_input(witness)
