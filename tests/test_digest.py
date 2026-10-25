import hashlib
from enum import Enum

import pytest

from omac.core.digest import digest, plain, read_attachment
from omac.engines.models import VerificationAttachmentObservation
from omac.errors import ValidationError


class Color(Enum):
    RED = "red"


def test_digest_ignores_key_order_and_whitespace():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_plain_converts_enums_and_keeps_unicode():
    assert plain({"c": Color.RED, "t": "审"}) == {"c": "red", "t": "审"}


class _Store:
    def __init__(self, observed):
        self.observed = observed

    def observe_verification_attachment(self, item_id, ref):
        return self.observed


def _ref_and_store(content=b"verdict: pass\n", **overrides):
    ref = {"attachment_id": "att", "comment_id": "com", "sha256": hashlib.sha256(content).hexdigest(),
           "bytes": len(content)}
    observed = VerificationAttachmentObservation(
        attachment_id="att", comment_id="com", sha256=ref["sha256"], content=content)
    return {**ref, **overrides}, _Store(observed)


def test_read_attachment_returns_decoded_body_and_native_identity():
    ref, store = _ref_and_store()
    got = read_attachment(store, "item", ref)
    assert got["body"] == {"verdict": "pass"}
    assert got["native"]["attachment_id"] == "att"


@pytest.mark.parametrize("override", [{"attachment_id": ""}, {"bytes": 0}, {"bytes": "3"}])
def test_read_attachment_rejects_incomplete_reference(override):
    ref, store = _ref_and_store(**override)
    with pytest.raises(ValidationError):
        read_attachment(store, "item", ref)


def test_read_attachment_rejects_changed_bytes_or_identity():
    ref, store = _ref_and_store()
    with pytest.raises(ValidationError):
        read_attachment(store, "item", {**ref, "sha256": "0" * 64})
    with pytest.raises(ValidationError):
        read_attachment(store, "item", {**ref, "comment_id": "other"})


def test_read_attachment_rejects_non_mapping_body():
    ref, store = _ref_and_store(b"- a\n- b\n")
    with pytest.raises(ValidationError):
        read_attachment(store, "item", ref)
