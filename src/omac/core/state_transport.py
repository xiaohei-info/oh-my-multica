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
    # Legacy manifests contain meta/nodes. Only a top-level transport declaration
    # is reserved; quoted historical declarations inside meta remain ordinary data.
    content = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    opening = re.search(rb'(?m)^(?![ \t]*(?:#|%|---[ \t]*(?:#[^\n]*)?$|$))([ \t]*)(\S[^\r\n]*)', content)
    first = opening.group(2) if opening else b""
    if first.startswith(b"--- "):
        first = first[4:].lstrip()
    if first.startswith((b"{", b"!", b"&", b"*", b"?", b'"')):
        try:
            # Unusual YAML root syntax must retain semantic field identity.
            value = yaml.load(raw, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        except yaml.YAMLError:
            fail("Malformed flow-style manifest/transport")
        return isinstance(value, dict) and bool(FIELDS.intersection(value))
    fields = b"|".join(name.encode() for name in sorted(FIELDS))
    key = rb'(?:' + fields + rb'|["\'](?:' + fields + rb')["\'])\s*:'
    indent = re.escape(opening.group(1)) if opening else b""
    return bool(re.match(key, first) or re.search(rb'(?m)^' + indent + key, content))


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
    physical = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")
    if len(physical) > ENCODED_MAX:
        fail("Encoded complete state exceeds90MiB; no chunking or truncation permitted")
    return physical


def identity(raw):
    body = decode(raw)
    return {"physical_bytes": len(raw), "physical_sha256": hashlib.sha256(raw).hexdigest(),
            "decoded_bytes": len(body), "decoded_sha256": hashlib.sha256(body).hexdigest(),
            "format": SCHEMA if is_transport(raw) else "legacy-yaml"}
