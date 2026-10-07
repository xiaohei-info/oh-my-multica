"""Explicit managed-manifest-only recovery. Never invoked by ordinary tick."""
import datetime
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from ..errors import NeedsDecision, ValidationError
from .manifest import loads_manifest, _ENV_PAT
from .state_transport import decode, encode, identity, ENCODED_MAX

SCHEMA = "omac.full-state-sync-recovery/v1"
ARCHIVE = "omac.unpublished-managed-state-archive/v1"


def fail(detail):
    raise ValidationError(detail + "; preserve original objects and inspect `omac dag recover-sync --help`")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _oid(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def _git(repo, *args, data=None, env=None):
    settings = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", **(env or {})}
    result = subprocess.run(["git", "-C", str(repo), *args], input=data,
                            capture_output=True, env=settings)
    if result.returncode:
        fail("Git observation/effect failed: " + result.stderr.decode(errors="replace"))
    return result.stdout


def _durable(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def _owned_lock(path, expected=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail("An existing regular writer lock is required; never create a replacement")
    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError("Existing canonical writer is active; preserve state and inspect `omac dag recover-sync --help`") from exc
        observed = {"path": str(path.resolve()), "device": os.fstat(stream.fileno()).st_dev,
                    "inode": os.fstat(stream.fileno()).st_ino}
        if expected is not None and observed != expected:
            fail("Existing writer lock identity changed")
        if not os.path.samestat(path.stat(), os.fstat(stream.fileno())):
            fail("Owned FD and canonical writer path differ")
        try:
            yield observed
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail("Regular complete source file required")
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(raw), "sha256": _sha(raw)}


def _logical(raw):
    manifest = loads_manifest(decode(raw).decode("utf-8"))
    value = json.loads(json.dumps(asdict(manifest), default=lambda v: v.value if isinstance(v, Enum) else v))
    return {"sha256": digest(value), "nodes": len(manifest.nodes),
            "DONE": sum(n.status == "done" for n in manifest.nodes.values()),
            "meta_keys": list(manifest.meta)}


def _environment(body):
    # Existing manifest expansion is part of logical identity; changing a
    # referenced variable cannot silently retain a previously approved Source.
    names = {match.group(1) for match in _ENV_PAT.finditer(body.decode("utf-8"))}
    return {name: (_sha(os.environ[name].encode()) if name in os.environ else None)
            for name in sorted(names)}


def _untracked(repo):
    rows = []
    for rel in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if rel:
            path = Path(repo) / rel.decode()
            rows.append({"file": rel.decode(), **_file(path)})
    return rows


def _remote(repo, branch):
    rows = _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/" + branch).splitlines()
    if len(rows) != 1:
        fail("Remote exact branch is unknown")
    return rows[0].split()[0].decode()


def _source(path, repo):
    path, repo = Path(path).absolute(), Path(repo).resolve()
    if path.is_symlink() or path.resolve() != path:
        fail("Recovery requires the actual canonical manifest path, not a symlink")
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        fail("Manifest is outside this repository")
    if not rel.startswith(".omac/") or not rel.endswith((".yaml", ".yml")):
        fail("Only managed .omac YAML state is eligible")
    if _git(repo, "rev-parse", "--show-toplevel").decode().strip() != str(repo):
        fail("Exact repository root is required")
    if _git(repo, "rev-parse", "--show-object-format").decode().strip() != "sha1":
        fail("Recovery v1 requires the exact SHA1 object format")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    if branch != "main":
        fail("Foreign branches or detached/user histories are not eligible")
    upstream = _git(repo, "rev-parse", "--symbolic-full-name", "@{upstream}").decode().strip()
    if upstream != "refs/remotes/origin/main":
        fail("Exact main/origin main upstream required")
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    base = _git(repo, "rev-parse", upstream).decode().strip()
    if _remote(repo, branch) != base:
        fail("Remote publication/upstream changed or is unknown")
    lines = _git(repo, "rev-list", "--parents", base + "..HEAD").decode().splitlines()
    if lines != [head + " " + base]:
        fail("Only one exact unpublished nonmerge manifest-only commit is eligible")
    if _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", head).decode().splitlines() != [rel]:
        fail("Unpublished history includes user business or mixed files")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        fail("Tracked/index/worktree state is dirty or unknown")
    untracked = _untracked(repo)
    if any(r["file"].startswith(".omac/") for r in untracked):
        fail("Managed untracked state interferes with exact recovery")
    original = path.read_bytes()
    blob = _git(repo, "rev-parse", head + ":" + rel).decode().strip()
    if _oid("blob", original) != blob:
        fail("Physical complete manifest differs from original Git blob")
    if identity(original)["format"] != "legacy-yaml" or len(original) <= ENCODED_MAX:
        fail("Only explicit oversized legacy-state migration is eligible")
    index = Path(_git(repo, "rev-parse", "--git-path", "index").decode().strip())
    if not index.is_absolute():
        index = repo / index
    config = repo / ".omac/config.yaml"
    return {"repo": str(repo), "path": str(path), "relative_path": rel,
            "branch": branch, "head": head, "base": base, "upstream": upstream,
            "remote": base, "origin_url": _git(repo, "remote", "get-url", "origin").decode().strip(),
            "index": _file(index), "untracked": untracked,
            "config": _file(config) if config.exists() else None,
            "state": identity(original), "logical": _logical(original),
            "environment": _environment(original), "original_blob": blob}


def _objects(repo, base, head):
    rows = []
    for line in _git(repo, "rev-list", "--objects", base + ".." + head).splitlines():
        oid = line.split()[0].decode()
        kind = _git(repo, "cat-file", "-t", oid).decode().strip()
        body = _git(repo, "cat-file", kind, oid)
        if _oid(kind, body) != oid:
            fail("Original immutable object identity differs")
        rows.append({"oid": oid, "kind": kind, "bytes": len(body), "sha256": _sha(body)})
    return rows


def _target_objects(repo, source, physical):
    """Build deterministic complete replacement tree; no original ancestry loss."""
    objects = [("blob", physical)]
    leaf = _oid("blob", physical)
    parts = source["relative_path"].split("/")

    def replace(tree, depth):
        raw = _git(repo, "cat-file", "tree", tree)
        result = bytearray()
        offset, found = 0, False
        while offset < len(raw):
            end = raw.index(b"\0", offset)
            entry = raw[offset:end]
            mode, name = entry.split(b" ", 1)
            oid = raw[end + 1:end + 21].hex()
            if name.decode() == parts[depth]:
                found = True
                if depth == len(parts) - 1:
                    if mode != b"100644":
                        fail("Managed manifest Git mode is not a regular file")
                    oid = leaf
                else:
                    if mode != b"40000":
                        fail("Managed state ancestor is not a Git directory")
                    oid = replace(oid, depth + 1)
            result.extend(entry + b"\0" + bytes.fromhex(oid))
            offset = end + 21
        if not found:
            fail("Original tree omits managed manifest")
        body = bytes(result)
        objects.append(("tree", body))
        return _oid("tree", body)

    tree = replace(_git(repo, "rev-parse", source["head"] + "^{tree}").decode().strip(), 0)
    original_commit = _git(repo, "cat-file", "commit", source["head"])
    headers = original_commit.split(b"\n\n", 1)[0].splitlines()
    authors = [h for h in headers if h.startswith((b"author ", b"committer "))]
    if len(authors) != 2:
        fail("Original commit identity headers unavailable")
    commit = (b"tree " + tree.encode() + b"\nparent " + source["base"].encode() + b"\n"
              + b"\n".join(authors) + b"\n\nchore(omac): preserve complete state transport\n"
              + b"Original unpublished commit: " + source["head"].encode() + b"\n")
    objects.append(("commit", commit))
    return objects


def _verify_archive(repo, archive, source, objects):
    """Recover into an independent object database with only the published base."""
    _git(repo, "bundle", "verify", str(archive))
    with tempfile.TemporaryDirectory(prefix="omac-archive-verify-") as temporary:
        restored = Path(temporary) / "restored.git"
        subprocess.run(["git", "init", "--bare", str(restored)], check=True, capture_output=True)
        _git(restored, "fetch", "--no-tags", str(repo), source["base"])
        _git(restored, "bundle", "unbundle", str(archive))
        if _objects(restored, source["base"], source["head"]) != objects:
            fail("Original archive cannot independently recover the exact unpublished closure")


def prepare(path, repo, archive_path, writer_lock_path):
    """Archive and pin exact Source; no state encoding or managed ref change."""
    archive = Path(archive_path).absolute()
    repo = Path(repo).resolve()
    if archive.is_relative_to(repo) or archive.is_symlink() or not archive.parent.is_dir() or archive.parent.resolve() != archive.parent:
        fail("A canonical durable archive outside the business worktree is required")
    receipt = archive.with_suffix(archive.suffix + ".json")
    if archive.exists() and not receipt.exists():
        fail("Existing archive has no original intention; never overwrite it")
    with _owned_lock(writer_lock_path) as lock:
        source = _source(path, repo)
        original = Path(path).read_bytes()
        physical = encode(original)
        objects = _objects(repo, source["base"], source["head"])
        if receipt.exists():
            _file(receipt)
            try:
                intention = json.loads(receipt.read_bytes())
            except (ValueError, UnicodeError) as exc:
                raise ValidationError("Original archive intention is malformed; inspect `omac dag recover-sync --help`") from exc
            if not isinstance(intention, dict) or intention.get("schema") != ARCHIVE or intention.get("source") != source or intention.get("objects") != objects or intention.get("writer_lock") != lock or intention.get("archive_path") != str(archive) or intention.get("state") not in ("archive-intended", "archive-verified"):
                fail("Original archive intention/Source/writer changed")
            if not archive.is_file():
                raise NeedsDecision("Original archive outcome unknown; observe its retained intention without recreating it")
            if intention["state"] == "archive-verified" and intention.get("archive") != _file(archive):
                fail("Previously verified original archive changed")
        else:
            intention = {"schema": ARCHIVE, "source": source, "objects": objects, "writer_lock": lock,
                         "state": "archive-intended", "archive_path": str(archive)}
            _durable(receipt, intention)
            _git(repo, "bundle", "create", str(archive), "HEAD", "^" + source["base"])
        if archive.stat().st_size > ENCODED_MAX:
            fail("Opaque original Git archive exceeds90MiB; preserve it and stop")
        with archive.open("rb") as stream:
            os.fsync(stream.fileno())
        _verify_archive(repo, archive, source, objects)
        heads = _git(repo, "bundle", "list-heads", str(archive)).decode().splitlines()
        if len(heads) != 1 or heads[0].split()[0] != source["head"]:
            fail("Archive exact original head is unavailable")
        if _source(path, repo) != source:
            fail("Full Source changed during original archive qualification")
        if intention["state"] != "archive-verified":
            intention.update(state="archive-verified", archive=_file(archive))
            _durable(receipt, intention)
        target_objects = _target_objects(repo, source, physical)
        target = _oid("commit", target_objects[-1][1])
        request = {"schema": SCHEMA, "source": source, "writer_lock": lock,
                   "archive": _file(archive), "archive_receipt": _file(receipt),
                   "original_objects": objects, "target_identity": identity(physical),
                   "target_commit": target, "target_objects": [
                       {"kind": kind, "oid": _oid(kind, body), "bytes": len(body), "sha256": _sha(body)}
                       for kind, body in target_objects]}
        return request


def _validate(request, request_sha256):
    fields = {"schema", "source", "writer_lock", "archive", "archive_receipt",
              "original_objects", "target_identity", "target_commit", "target_objects"}
    if not isinstance(request, dict) or set(request) != fields or request["schema"] != SCHEMA:
        fail("Exact complete prepared recovery request required")
    if digest(request) != request_sha256:
        fail("Exact Root request SHA differs")
    source_fields = {"repo", "path", "relative_path", "branch", "head", "base", "upstream", "remote",
                     "origin_url", "index", "untracked", "config", "state", "logical", "environment", "original_blob"}
    if not isinstance(request["source"], dict) or set(request["source"]) != source_fields:
        fail("Complete prepared Source fields required")
    if not isinstance(request["writer_lock"], dict) or set(request["writer_lock"]) != {"path", "device", "inode"}:
        fail("Exact existing writer identity required")
    for name in ("archive", "archive_receipt"):
        row = request[name]
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"} or not isinstance(row["path"], str):
            fail("Complete archive file identity required")
        if _file(row["path"]) != row:
            fail("Complete immutable original archive/receipt changed")
    try:
        receipt = json.loads(Path(request["archive_receipt"]["path"]).read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("Malformed archive receipt; inspect `omac dag recover-sync --help`") from exc
    if not isinstance(receipt, dict) or receipt.get("schema") != ARCHIVE or receipt.get("state") != "archive-verified" or receipt.get("source") != request["source"] or receipt.get("objects") != request["original_objects"] or receipt.get("writer_lock") != request["writer_lock"] or receipt.get("archive") != request["archive"] or receipt.get("archive_path") != request["archive"]["path"]:
        fail("Archive Source/object closure association changed")
    return receipt


def resolve(request, *, request_sha256, authority, reason):
    if not isinstance(authority, str) or not authority.strip() or not isinstance(reason, str) or not reason.strip():
        fail("New explicit Root authority and reason required")
    _validate(request, request_sha256)
    source = request["source"]
    repo, path = Path(source["repo"]), Path(source["path"])
    approval = {"request_sha256": request_sha256, "authority": authority, "reason": reason}
    journal = Path(request["archive"]["path"]).with_suffix(".recovery.json")
    with _owned_lock(request["writer_lock"]["path"], request["writer_lock"]):
        if journal.exists():
            _file(journal)
            record = json.loads(journal.read_bytes())
            if not isinstance(record, dict) or set(record) != {"schema", "request", "approval", "effects", "state"} or record.get("schema") != SCHEMA or record.get("request") != request or record.get("approval") != approval:
                fail("Existing exact recovery intention cannot be replaced")
            allowed_effects = {"preserve-original-ref", "state-encoding", "index", "branch-ref", "push"} | {"object-" + o["oid"] for o in request["target_objects"]}
            if not isinstance(record["effects"], dict) or not set(record["effects"]).issubset(allowed_effects) or any(v not in ("intended", "observed") for v in record["effects"].values()) or record["state"] not in ("prepared", "complete-remote-observed"):
                fail("Unknown recovery journal effect/state")
        else:
            if _source(path, repo) != source:
                fail("Full original Source/CAS changed before recovery")
            record = {"schema": SCHEMA, "request": request, "approval": approval, "effects": {}, "state": "prepared"}
            _durable(journal, record)

        original = _git(repo, "cat-file", "blob", source["original_blob"])
        if identity(original) != source["state"] or _logical(original) != source["logical"]:
            fail("Original complete state/source/budget/history changed")
        physical = encode(original)
        objects = _target_objects(repo, source, physical)
        if [{"kind": k, "oid": _oid(k, b), "bytes": len(b), "sha256": _sha(b)} for k, b in objects] != request["target_objects"] or identity(physical) != request["target_identity"]:
            fail("Exact deterministic target Source differs")
        _verify_archive(repo, request["archive"]["path"], source, request["original_objects"])

        def guard():
            lock = Path(request["writer_lock"]["path"])
            if lock.is_symlink() or not lock.is_file() or (lock.stat().st_dev, lock.stat().st_ino) != (request["writer_lock"]["device"], request["writer_lock"]["inode"]):
                fail("Canonical writer path changed while the original FD was owned")
            if _environment(original) != source["environment"]:
                fail("Referenced manifest environment/logical Source changed")
            if _untracked(repo) != source["untracked"]:
                fail("Unrelated/managed untracked bytes changed")
            config = repo / ".omac/config.yaml"
            if (_file(config) if config.exists() else None) != source["config"]:
                fail("Original configuration source changed")
            if _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip() != source["branch"] or _git(repo, "remote", "get-url", "origin").decode().strip() != source["origin_url"]:
                fail("Original branch/remote identity changed")
            current_raw = path.read_bytes()
            current_identity = identity(current_raw)
            # Complete original decoded-byte identity is stronger than a lossy
            # node projection and pins every Source/budget/history field. The
            # original full materialized digest was authenticated at prepare.
            if any(current_identity[k] != source["state"][k] for k in ("decoded_bytes", "decoded_sha256")):
                fail("Full current Source/DONE/budget/history differs")
            actual_head = _git(repo, "rev-parse", "HEAD").decode().strip()
            allowed_heads = {source["head"]}
            if "branch-ref" in record["effects"]:
                allowed_heads.add(request["target_commit"])
            if actual_head not in allowed_heads:
                fail("HEAD changed outside exact owned intention")
            current_upstream = _git(repo, "rev-parse", source["upstream"]).decode().strip()
            allowed_upstreams = {source["base"]}
            if "push" in record["effects"]:
                allowed_upstreams.add(request["target_commit"])
            if current_upstream not in allowed_upstreams:
                fail("Upstream ref changed outside owned publication intention")
            excluded = ["--", ".", ":(exclude)" + source["relative_path"]]
            if _git(repo, "diff", "--raw", *excluded) or _git(repo, "diff", "--cached", "--raw", *excluded):
                fail("Unrelated tracked/index/worktree source changed")
            if "index" not in record["effects"] and _file(source["index"]["path"]) != source["index"]:
                fail("Original index CAS changed")
            if "index" in record["effects"]:
                allowed_trees = {_git(repo, "rev-parse", source["head"] + "^{tree}").decode().strip(),
                                 _git(repo, "rev-parse", request["target_commit"] + "^{tree}").decode().strip()}
                if _git(repo, "write-tree").decode().strip() not in allowed_trees:
                    fail("Index tree changed outside exact owned intention")
            actual_state = current_identity
            allowed_states = [source["state"]]
            if "state-encoding" in record["effects"]:
                allowed_states.append(request["target_identity"])
            if actual_state not in allowed_states:
                fail("Physical state changed outside exact owned intention")
            allowed_remotes = {source["base"]}
            if "push" in record["effects"]:
                allowed_remotes.add(request["target_commit"])
            if _remote(repo, source["branch"]) not in allowed_remotes:
                fail("Remote publication changed outside exact owned effect")
            _validate(request, request_sha256)

        def effect(name, observed, call):
            guard()
            if name in record["effects"]:
                if observed():
                    record["effects"][name] = "observed"
                    _durable(journal, record)
                    return
                raise NeedsDecision("Recovery outcome unknown; observe existing intention without repeating " + name)
            if observed():
                fail("Unrecorded recovery effect was already present: " + name)
            record["effects"][name] = "intended"
            _durable(journal, record)
            call()
            if not observed():
                raise NeedsDecision("Recovery write is unconfirmed; preserve exact intention " + name)
            record["effects"][name] = "observed"
            _durable(journal, record)

        backup = "refs/omac/full-state-recovery/" + request_sha256 + "/original"
        def ref_is(ref, wanted):
            result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", ref], capture_output=True)
            return result.returncode == 0 and result.stdout.decode().strip() == wanted
        effect("preserve-original-ref", lambda: ref_is(backup, source["head"]),
               lambda: _git(repo, "update-ref", backup, source["head"], "0" * 40))
        for kind, body in objects:
            oid = _oid(kind, body)
            # Content objects may already exist; authenticated same OID is not a
            # branch/index/worktree effect and never substitutes different bytes.
            exists = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", oid], capture_output=True).returncode == 0
            if exists:
                if _git(repo, "cat-file", kind, oid) != body:
                    fail("Existing target object differs")
                if "object-" + oid in record["effects"]:
                    effect("object-" + oid, lambda: True, lambda: None)
                continue
            effect("object-" + oid,
                   lambda oid=oid, kind=kind, body=body: subprocess.run(["git", "-C", str(repo), "cat-file", "-e", oid], capture_output=True).returncode == 0 and _git(repo, "cat-file", kind, oid) == body,
                   lambda kind=kind, body=body: _git(repo, "hash-object", "-t", kind, "-w", "--stdin", data=body))
        target = request["target_commit"]
        for line in _git(repo, "rev-list", "--objects", source["base"] + ".." + target).splitlines():
            oid = line.split()[0].decode()
            if oid == source["original_blob"] or int(_git(repo, "cat-file", "-s", oid)) > ENCODED_MAX:
                fail("Published target closure contains original oversized blob/object")

        def migrate():
            if _sha(path.read_bytes()) != source["state"]["physical_sha256"]:
                fail("Exact physical Source changed before encoding")
            fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
            try:
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                with os.fdopen(fd, "wb") as stream:
                    stream.write(physical)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        effect("state-encoding", lambda: identity(path.read_bytes()) == request["target_identity"], migrate)
        target_tree = _git(repo, "rev-parse", target + "^{tree}").decode().strip()
        effect("index", lambda: _git(repo, "write-tree").decode().strip() == target_tree,
               lambda: _git(repo, "read-tree", target))
        effect("branch-ref", lambda: ref_is("HEAD", target),
               lambda: _git(repo, "update-ref", "refs/heads/" + source["branch"], target, source["head"]))
        if _git(repo, "status", "--porcelain", "--untracked-files=no"):
            fail("Target complete worktree/index does not match expected tree")
        effect("push", lambda: _remote(repo, source["branch"]) == target,
               lambda: _git(repo, "push", "origin", target + ":refs/heads/" + source["branch"]))
        record["state"] = "complete-remote-observed"
        _durable(journal, record)
        return record
