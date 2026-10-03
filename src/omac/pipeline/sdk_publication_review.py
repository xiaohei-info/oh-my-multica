"""One approved SDK case; native Git publication is evidence, not a verdict."""

import base64
from copy import deepcopy
import hashlib
import json
import re
import shlex

from ..core.taskmeta import TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import ValidationError
from . import publication_review as shared
from .evidence_review import _attachment, _digest, _mapping, _plain, _state, _time

SCHEMA = "omac.sdk-native-publication-review/v1"
JOURNAL = "sdk_native_publication_reviews"
KEY = "harness-sdk-production-build-repair"
ROOT = "harness-sdk-repair-published-evidence-not-pr-head"
ARTIFACT = "harness-sdk-build-repair-evidence"
ISSUE = "01a0fb38-0558-7ae0-a2b1-23b00e56190a"
LATEST = "01a100a6-13a4-7d17-97ef-cca4e913b8e0"
EXECUTION = "01a10062-994b-740f-9916-d7849dc301cc"
COMMIT = "8bbf89abc19d1e9d77aa6a4d97375a43ef9f1c96"
HEAD = "1ebf12d36d29c2a3726257bd03685139f5bc568a"
CONTRACT = "9af6bfb0d160406d11d53d7403bd45750e1c0b71f35523c35d1b8b774c08ee05"
GENERATION = "amendment-bc8e16de37e0f355b497022f"
WORKER = "8dc91607-6825-41c6-b0de-45c001afc58d"
PREFIX = "artifacts/" + ARTIFACT + "/"
FILES = {
    "bundle-digest.txt",
    "component-manifest.json",
    "conformance-tests.json",
    "coverage-attribution.json",
    "coverage-audit.md",
    "coverage-block-audit.json",
    "coverage-block-audit.md",
    "coverage.out",
    ARTIFACT + ".tar.gz",
    "owner-repair.md",
    "receipt-tree-design-evidence.md",
    "receipt-tree-tests.json",
    "semantic-evidence.json",
    "source-bundle.tar.zst",
    "source-snapshot.json",
    "source-snapshot.md",
    "source.patch.gz",
    "verification-results.json",
}
INDEX = (
    "https://github.com/xiaohei-info/open-agent-cluster/blob/"
    + COMMIT
    + "/"
    + PREFIX
    + "evidence-index.json"
)
HOLD_ACTION = "Await explicit SDK-specific native-publication recovery capability-work authorization; after tested independent capability review, fresh read-only request preparation and separate exact request approval. Do not retry unchanged authoring, forge a seal or transplant Preview/Executor approvals."


def _fail(message):
    raise ValidationError(
        message
        + "; preserve the hold/source and run omac node review-sdk-publication --help"
    )


def _urls(value):
    return {
        u.rstrip(".,")
        for u in re.findall(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[A-Za-z0-9._/-]+",
            json.dumps(value, ensure_ascii=False),
        )
    }


def _json_body(body):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("Duplicate publication JSON key")
        return value

    value = json.loads(body, object_pairs_hook=unique)
    if not isinstance(value, dict):
        _fail("SDK metadata must be one JSON object")
    return value


def _source_reference(store, runtime, item, reviewer_run):
    _, pairs = shared._native_pairs(runtime, item.id, reviewer_run)
    found = []
    # This native Reviewer queried verification then contract in one terminal call.
    # Only this pair of read-only query shapes is allowed; no shell program import.
    for call, result in pairs:
        raw = (call.get("input") or {}).get("text", "")
        lexer = shlex.shlex(raw.removeprefix("$ "), posix=True, punctuation_chars=";")
        lexer.whitespace_split = True
        try:
            commands = list(lexer)
        except ValueError:
            continue
        if commands.count(";") != 1:
            continue
        at = commands.index(";")
        first, second = commands[:at], commands[at + 1 :]
        # The exact query has 12 tokens, including the issue and comment IDs.
        if not all(
            len(a) == 12
            and a[:5] == ["multica", "issue", "comment", "list", item.id]
            and a[5] == "--thread"
            and re.fullmatch(r"[0-9a-f-]{36}", a[6])
            and a[7:] == ["--tail", "30", "--compact", "--output", "json"]
            for a in (first, second)
        ):
            continue
        output = result.get("output", "")
        if not re.search(r"- \*\*exit_code:\*\* 0\s*$", output):
            continue
        ref = store.read_verification_reference(item.id, first[6])
        if any(
            ref.get(k) != v
            for k, v in {
                "comment_id": "01a10029-913f-7ae5-88c2-428d0e21b04c",
                "attachment_id": "01a10029-90c6-72e8-9c68-d98953768adb",
                "sha256": "07dd43f64a7cc72dece91e41e25d7bdfb5716544a3889a228285990c300675b3",
            }.items()
        ):
            _fail("Original SDK verification reference is outside the supported reject")
        # A truncated body is not a historical seal. The native response prefix
        # must expose this exact comment and attachment; all bytes come afresh.
        if ('"comment_id": "' + ref["comment_id"] + '"') not in output or (
            "/api/attachments/" + ref["attachment_id"] + "/download"
        ) not in output:
            continue
        found.append((ref, {"call": call, "result": result}))
    if len(found) != 1:
        _fail("Original independent Reviewer verification association is ambiguous")
    return found[0][0], _digest(found[0][1])


def _publication(store, index_url, item, original, expected=None, *, download=True):
    if (
        index_url != INDEX
        or index_url not in _urls(item.verification)
        or index_url in _urls(original)
    ):
        _fail("Only the changed, exact immutable SDK publication is supported")
    body = (
        store.observe_git_artifacts([INDEX])[0].content
        if expected is None
        else base64.b64decode(expected["index_bytes"], validate=True)
    )
    try:
        index = _json_body(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("SDK publication index is not complete JSON") from exc
    if (
        any(
            index.get(k) != v
            for k, v in {
                "schema": "aiteam.evidence-index/v1",
                "artifact_id": ARTIFACT,
                "dag_key": KEY,
                "component_owner": "harness-sdk",
                "source_commit": HEAD,
                "final_pr_head": HEAD,
                "source_pr": item.artifacts["pr_url"],
            }.items()
        )
        or index.get("publication", {}).get("kind")
        != "separate-content-addressed-git-commit"
    ):
        _fail("Index does not bind exact SDK owner, PR and source HEAD")
    if set(index) != {
        "schema",
        "artifact_id",
        "component_owner",
        "dag_key",
        "source_commit",
        "final_pr_head",
        "base_commit",
        "source_pr",
        "publication_ref",
        "publication",
        "files",
    }:
        _fail("SDK index has unexpected authority or payload fields")
    rows = index.get("files")
    if not isinstance(rows, list) or len(rows) != len(FILES):
        _fail("All eighteen indexed SDK payloads are required")
    by_url = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "path",
            "git_blob_oid",
            "sha256",
            "size_bytes",
        }:
            _fail("SDK index row shape changed")
        path = row["path"]
        if (
            not isinstance(path, str)
            or path.removeprefix(PREFIX) not in FILES
            or path != PREFIX + path.removeprefix(PREFIX)
        ):
            _fail("SDK index path is outside the exact payload set")
        url = INDEX.rsplit("/", 1)[0] + "/" + path.removeprefix(PREFIX)
        if (
            url in by_url
            or not re.fullmatch(r"[0-9a-f]{40}", str(row["git_blob_oid"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"]))
            or type(row["size_bytes"]) is not int
            or not 0 < row["size_bytes"] <= 16 * 1024 * 1024
        ):
            _fail("SDK index identity, digest or size is invalid")
        by_url[url] = row
    urls = sorted([INDEX, *by_url])
    observed = store.observe_git_artifacts(urls, download=download)
    if len(observed) != len(urls):
        _fail("Native Git observations are incomplete")
    refs = []
    bindings = {}
    prior = {r["url"]: r for r in expected["assets"]} if expected else {}
    for url, obs in zip(urls, observed):
        ref = deepcopy(obs.reference)
        if (
            set(ref)
            != {
                "url",
                "repository",
                "commit_sha",
                "tree_sha",
                "parent_shas",
                "path",
                "blob_oid",
                "bytes",
            }
            or ref["url"] != url
            or ref["repository"] != "xiaohei-info/open-agent-cluster"
            or ref["commit_sha"] != COMMIT
            or ref["parent_shas"] != [HEAD]
            or url
            != "https://github.com/"
            + ref["repository"]
            + "/blob/"
            + COMMIT
            + "/"
            + ref["path"]
            or not re.fullmatch(r"[0-9a-f]{40}", str(ref["tree_sha"]))
            or not re.fullmatch(r"[0-9a-f]{40}", str(ref["blob_oid"]))
            or type(ref["bytes"]) is not int
            or not 0 < ref["bytes"] <= 16 * 1024 * 1024
        ):
            _fail("Exact native commit/tree/parent/blob identity is required")
        if download:
            if (
                len(obs.content) != ref["bytes"]
                or hashlib.sha1(
                    b"blob " + str(len(obs.content)).encode() + b"\0" + obs.content
                ).hexdigest()
                != ref["blob_oid"]
            ):
                _fail("Native Git blob bytes or size changed")
            ref["sha256"] = hashlib.sha256(obs.content).hexdigest()
        else:
            old = prior.get(url, {})
            if {k: v for k, v in old.items() if k != "sha256"} != ref:
                _fail("Native Git publication changed before recovery write")
            ref["sha256"] = old["sha256"]
        if url == INDEX:
            if (
                ref["bytes"] != len(body)
                or ref["sha256"] != hashlib.sha256(body).hexdigest()
                or (download and obs.content != body)
            ):
                _fail("SDK index bytes changed")
        else:
            row = by_url[url]
            if (
                ref["blob_oid"] != row["git_blob_oid"]
                or ref["sha256"] != row["sha256"]
                or ref["bytes"] != row["size_bytes"]
            ):
                _fail("SDK index and native payload disagree")
            name = ref["path"].removeprefix(PREFIX)
            if name in {
                "component-manifest.json",
                "source-snapshot.json",
                "semantic-evidence.json",
                "verification-results.json",
            }:
                value = (
                    _json_body(obs.content)
                    if download
                    else expected["source_bindings"][name]
                )
                if value.get("source_revision") != HEAD:
                    _fail("Published metadata is bound to the original rejected parent")
                bindings[name] = {"source_revision": value["source_revision"]}
        refs.append(ref)
    if (
        len({r["path"] for r in refs}) != 19
        or len({r["tree_sha"] for r in refs}) != 1
        or sum(r["bytes"] for r in refs) > 64 * 1024 * 1024
    ):
        _fail("SDK publication is ambiguous or oversized")
    result = {
        "index_url": INDEX,
        "index_bytes": base64.b64encode(body).decode(),
        "assets": refs,
        "source_bindings": bindings,
        "policy": "native content-addressed publication; independent SDK review still required",
    }
    if expected is not None and result != expected:
        _fail("SDK frozen publication changed")
    return result


def _hold(item, prepared):
    hold = prepared["operator_hold"] if prepared else item.decision_required
    expected = {
        "schema": "omac.decision-required/v1",
        "reason_code": "sdk-same-head-publication-authorization-required",
        "kind": "develop",
        "phase": "authoring",
        "gate": "operator-authorization",
        "resume_issue_id": ISSUE,
        "node_id": KEY,
        "run_id": LATEST,
        "source_issue_id": "01a100bc-8416-7e50-9af6-0a7395ecee5a",
        "source_run_id": "01a100bd-400d-7486-a0d9-fb1e078a9500",
        "source_report_sha256": "9262aa884f27cdcddddf158437ba38e8a8ff3835cf223fd62422c1f0f12f1ddc",
        "review_context_binding": {
            "generation": GENERATION,
            "contract_sha256": CONTRACT,
        },
        "verification_ref": item.verification_ref,
        "next_action": HOLD_ACTION,
    }
    if hold != expected or (item.decision_required and item.decision_required != hold):
        _fail("Exact supported operator authorization hold is required")
    return deepcopy(hold)


def _chain(
    store, runtime, manifest, key, item, handoff, target, candidate, runs, prepared
):
    if (
        item.id != ISSUE
        or item.worker != "codex-ubuntu-newapi"
        or target.id != LATEST
        or target.agent_id != WORKER
        or handoff.generation != "handoff-65ee94b1e5be4060"
        or item.review_generation != GENERATION
        or candidate.sha256
        != "a1714098052381671a4b23b68820e7995d2bf10d63ab865d5f941b56271d44f1"
        or candidate.attachment_id != "01a100b2-e634-75b3-b991-3267999f7240"
        or item.artifacts["pr_url"]
        != "https://github.com/xiaohei-info/open-agent-cluster/pull/90"
        or item.artifacts["head_sha"] != HEAD
    ):
        _fail("SDK case differs from explicitly supported native chain")
    if (
        shared.review_context_binding(item)
        != {"generation": GENERATION, "contract_sha256": CONTRACT}
        or handoff.source_review_feedback["report_ref"]["sha256"]
        != "c5fa31452edbf6fb47d68c60c316f99f9d4eccf82fc00915de4c122883d8c1c2"
        or handoff.source_review_feedback["ledger_ref"]["sha256"]
        != "dddd52526aac568e25f01e90a878363cd0839107fb430b8aeacda0f4952e6298"
        or (item.bounces.worker, item.bounces.review, item.bounces.merge) != (1, 1, 0)
        or item.bounce_baseline != {"worker": 0, "review": 0, "merge": 0}
    ):
        _fail("SDK original reject, contract or budget authority changed")
    known = {
        LATEST: WORKER,
        EXECUTION: WORKER,
        "01a10038-4447-719d-a676-746776d68d9b": "94f847b9-c10f-4d64-88b2-e90cb5c03ffc",
        "01a0ffef-2192-7627-95bb-03c1b1a64f49": WORKER,
        "01a0fb38-a98b-7418-a208-f7cfdac73b8c": "037ad7f0-98f9-489f-bf43-0d1148233356",
    }
    if {r.id for r in runs} != set(known) or any(
        r.agent_id != known[r.id]
        or not r.formal
        or r.kind != "direct"
        or r.status != "completed"
        for r in runs
    ):
        _fail("SDK native history contains an unknown or unbound Run")
    hold = _hold(item, prepared)
    execution = next((r for r in runs if r.id == EXECUTION), None)
    if (
        execution is None
        or execution.agent_id != WORKER
        or not execution.formal
        or execution.status != "completed"
        or _time(execution.updated_at) >= _time(target.created_at)
        or EXECUTION not in handoff.baseline_direct_run_ids
    ):
        _fail("The preceding execution Run is unavailable or misattributed")
    ref = store.read_verification_reference(
        item.id, attachment_id=handoff.baseline_verification_attachment_id
    )
    blob = _attachment(store, item.id, ref)
    previous = _mapping(blob.content, "SDK preceding verification")
    if (
        blob.task_id != EXECUTION
        or blob.uploader_id != WORKER
        or blob.uploader_type != "agent"
        or not shared._in_run(blob.created_at, execution)
    ):
        _fail("Preceding verification belongs to another actor or Run")

    def commands(v):
        return [
            {"cmd": c.get("cmd"), "exit_code": c.get("exit_code")}
            for c in v.get("commands", [])
        ]

    current, prior = commands(item.verification), commands(previous)
    if (
        len(current) != 23
        or current != prior
        or any(c["exit_code"] != 0 for c in current)
    ):
        _fail("All twenty-three inherited commands/results must remain unchanged")
    native, _ = shared._native_pairs(runtime, item.id, execution)
    receipt = shared._submit_receipt(runtime, item, execution, item.artifacts["pr_url"])
    if _time(blob.created_at) > _time(receipt["created_at"]):
        _fail("Execution attachment was not present at its accepted submit")
    return {
        "operator_hold": hold,
        "execution_provenance": {
            "run": _plain(execution),
            "verification_ref": ref,
            "native_messages_sha256": _digest(native),
            "terminal_submit": receipt,
            "commands": prior,
            "latest_run_role": "verification correction and prior-record inspection; not a new twenty-three-command execution",
        },
        "retained_manifest_sha256": _digest(
            {
                "meta": {k: v for k, v in manifest.meta.items() if k != JOURNAL},
                "other_nodes": {
                    k: {**_plain(n), "contract": shared._dump_contract(n.contract)}
                    for k, n in manifest.nodes.items()
                    if k != key
                },
            }
        ),
    }


def _initial(item, value):
    if (
        item.status != WorkItemStatus.BLOCKED
        or item.phase != TaskPhase.AUTHORING
        or item.delivery_identity
        or item.platform_assignee_id
        or item.reviewer
        or item.decision_required != value["operator_hold"]
        or not item.worker_handoff
        or item.worker_handoff.as_dict() != value["source_handoff"]
        or any(
            getattr(item, k)
            for k in (
                "review_verdict",
                "review_subject_digest",
                "review_report_ref",
                "reviewer_run_baseline",
                "machine_feedback_ref",
            )
        )
    ):
        _fail("Keep the unassigned, held SDK authoring submission intact")


def _obligations(obligations, value):
    obligations = shared._obligations(obligations, value)
    evidence = next(
        o for o in obligations if o["obligation_id"] == "dimension:evidence"
    )
    evidence["publication_recovery"]["execution_provenance"] = deepcopy(
        value["execution_provenance"]
    )
    evidence["publication_recovery"]["operator_hold"] = deepcopy(value["operator_hold"])
    evidence["requirement"] += (
        " Retain all SDK23/raw85/wholeSDK90/Host90/security/downstream requirements. Latest uploader did not rerun the twenty-three commands; inspect the preceding native execution Run and independently review the exact publication. No retained verdict is a new pass."
    )
    return obligations


def prepare_sdk_publication_review(store, runtime, manifest, key, index_url, reason):
    try:
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.encode()) > 2048
        ):
            _fail("Explicit bounded SDK operator reason is required")
        item, value, identity, runs = shared._verify(
            store, runtime, manifest, key, index_url, sdk=True
        )
        _initial(item, value)
        before = _state(item)
        if _state(store.get_work_item(item.id)) != before:
            _fail("SDK control changed during preparation")
        return {
            "schema": SCHEMA,
            "reason": reason,
            "tuple": value,
            "expected_control": before,
            "runs_sha256": _digest(
                sorted((_plain(r) for r in runs), key=lambda r: r["id"])
            ),
        }
    except ValidationError as exc:
        raise ValidationError(
            str(exc)
            .replace("review-evidence", "review-sdk-publication")
            .replace("review-publication", "review-sdk-publication")
        ) from exc
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise ValidationError(
            "Incomplete or ambiguous SDK native facts; run omac node review-sdk-publication --help"
        ) from exc


def apply_sdk_publication_review(
    store, runtime, manifest_path, key, request, *, approved_request_sha256
):
    try:
        return shared.apply_publication_review(
            store,
            runtime,
            manifest_path,
            key,
            request,
            approved_request_sha256=approved_request_sha256,
            _sdk=True,
        )
    except ValidationError as exc:
        raise ValidationError(
            str(exc)
            .replace("review-evidence", "review-sdk-publication")
            .replace("review-publication", "review-sdk-publication")
        ) from exc
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise ValidationError(
            "Incomplete or ambiguous SDK request; preserve its receipt and run omac node review-sdk-publication --help"
        ) from exc


def ensure_sdk_publication_review_complete(manifest, manifest_path):
    shared.ensure_publication_review_complete(
        manifest,
        manifest_path,
        journal=JOURNAL,
        schema=SCHEMA,
        node=KEY,
        command="review-sdk-publication",
    )
