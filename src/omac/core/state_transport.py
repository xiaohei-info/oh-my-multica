"""One explicitly selected, lossless manifest transport; no external truth store."""
import base64
import gzip
import hashlib
import io
import re

import yaml

from ..errors import ValidationError

SCHEMA = "omac.full-state-transport/v1"
ENCODING = "gzip+base64"
ENCODED_MAX = 90 * 1024 * 1024
DECODED_MAX = 256 * 1024 * 1024
FIELDS = {"schema", "encoding", "decoded_bytes", "decoded_sha256", "payload"}


def fail(detail):
    raise ValidationError(detail + "; preserve original state and inspect `omac dag recover-sync --help`")


def is_transport(raw):
    if len(raw) > DECODED_MAX:
        fail("Complete manifest/transport exceeds256MiB")
    # A plain block mapping with only literal meta/nodes root keys cannot declare
    # transport fields. Any other root line (including merge/explicit/escaped
    # keys after meta) takes the semantic path below. Dash entries belong to
    # the ordinary indentationless nodes list, not to the root mapping.
    if re.match(rb"(?:meta|nodes)\s*:", raw):
        root_lines = re.findall(rb"(?m)^[^\s#][^\r\n]*", raw)
        if all(re.match(rb"(?:meta|nodes)\s*:|-[ \t]", line) for line in root_lines):
            return False
    # Inspect semantic root keys without constructing legacy values. This keeps
    # legacy safe-constructor errors intact and follows merge aliases in any order.
    try:
        root = yaml.compose(raw, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError:
        fail("Malformed manifest/transport")
    pending, seen = [root], set()
    while pending:
        node = pending.pop()
        if id(node) in seen or not isinstance(node, yaml.MappingNode):
            continue
        seen.add(id(node))
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value in FIELDS:
                return True
            if key.tag == "tag:yaml.org,2002:merge":
                pending.extend(value.value if isinstance(value, yaml.SequenceNode) else [value])
    return False


class _EnvelopeLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
    pass


def _mapping(loader, node):
    value = {}
    for key, item in node.value:
        name = loader.construct_object(key)
        if not isinstance(name, str):
            fail("Full-state transport field names must be strings")
        if name in value:
            fail("Duplicate full-state transport field")
        value[name] = loader.construct_object(item)
    return value


_EnvelopeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def decode(raw):
    if not is_transport(raw):
        if len(raw) > DECODED_MAX:
            fail("Legacy complete manifest exceeds256MiB")
        return raw
    if len(raw) > ENCODED_MAX:
        fail("Physical full-state transport exceeds90MiB")
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_EnvelopeLoader)
        if not isinstance(value, dict) or set(value) != FIELDS:
            fail("Complete exact transport fields are required")
        if value["schema"] != SCHEMA or value["encoding"] != ENCODING:
            fail("Unknown full-state transport schema/encoding")
        length = value["decoded_bytes"]
        if type(length) is not int or not 0 < length <= DECODED_MAX:
            fail("Invalid decoded full-state size")
        sha = value["decoded_sha256"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            fail("Invalid decoded full-state SHA")
        if not isinstance(value["payload"], str):
            fail("Full-state payload must be a Base64 string")
        zipped = base64.b64decode(value["payload"], validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(zipped)) as stream:
            body = stream.read(length + 1)
            if len(body) != length or stream.read(1):
                fail("Decoded full-state size differs or exceeds bound")
        if hashlib.sha256(body).hexdigest() != sha:
            fail("Decoded full-state SHA differs")
        body.decode("utf-8")
        if is_transport(body):
            fail("Nested full-state transport is not allowed")
        return body
    except (ValueError, TypeError, UnicodeError, OSError, EOFError, yaml.YAMLError) as exc:
        raise ValidationError("Malformed full-state transport; preserve original and inspect `omac dag recover-sync --help`") from exc


def encode(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= DECODED_MAX:
        fail("Complete decoded state must be bytes within256MiB")
    body.decode("utf-8")
    if is_transport(body):
        fail("Cannot encode a transport as another transport")
    value = {"schema": SCHEMA, "encoding": ENCODING, "decoded_bytes": len(body),
             "decoded_sha256": hashlib.sha256(body).hexdigest(),
             "payload": base64.b64encode(gzip.compress(body, mtime=0)).decode("ascii")}
    physical = yaml.dump(value, Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
                         allow_unicode=True, sort_keys=False).encode("utf-8")
    if len(physical) > ENCODED_MAX:
        fail("Encoded complete state exceeds90MiB; no chunking or truncation permitted")
    return physical


def identity(raw):
    body = decode(raw)
    return {"physical_bytes": len(raw), "physical_sha256": hashlib.sha256(raw).hexdigest(),
            "decoded_bytes": len(body), "decoded_sha256": hashlib.sha256(body).hexdigest(),
            "format": SCHEMA if is_transport(raw) else "legacy-yaml"}
