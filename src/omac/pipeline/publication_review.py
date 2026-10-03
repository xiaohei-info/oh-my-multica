"""Narrow Preview publication re-review: current native facts only, no session inputs."""

from copy import deepcopy
import base64
import hashlib
import json
import re
import shlex
from .evidence_review import (
    _digest,
    _state,
    _runs,
    _plain,
    _time,
    _attachment,
    _mapping,
    _apply_review_request,
)
from .loop import _seal_worker_delivery, _review_subject_for_current_delivery
from ..core.manifest import _dump_contract
from ..core.review_convergence import validate_review_ledger, _review_report_digest
from ..core.retry_budget import consumed_bounces
from ..core.taskmeta import (
    TaskKind,
    TaskPhase,
    parse_worker_handoff,
    review_context_binding,
)
from ..engines.models import WorkItemStatus
from ..errors import ValidationError, NeedsDecision

SCHEMA = "omac.preview-publication-review/v1"
JOURNAL = "preview_publication_reviews"
KEY = "release-preview-audit-vocabulary-repair"
ROOT = "preview-repair-immutable-evidence-not-published"
FILES = {
    "coverage.out",
    "preview-recovery-tests.json",
    "canonical-audit-tests.json",
    "source-bundle.tar.zst",
    "component-manifest.json",
    "bundle-digest.txt",
    "owner-repair.md",
    "verification.yaml",
}
URL = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
)


def _fail(message):
    raise ValidationError(
        message
        + "; inspect facts and run omac node review-publication --help; do not retry or clear feedback/budgets"
    )


def _in_run(timestamp, run):
    value, start, end = (_time(v) for v in (timestamp, run.created_at, run.updated_at))
    return (
        all(t is not None and t.tzinfo is not None for t in (value, start, end))
        and start <= value <= end
    )


def _native_pairs(runtime, issue_id, run):
    rows = runtime.read_run_messages(issue_id, run.id)
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        _fail("Complete bounded native Run tool records are required")
    if not all(isinstance(row, dict) for row in rows):
        _fail("Native record row is malformed")
    call_rows = [r for r in rows if r.get("type") == "tool_use"]
    if any(not r.get("call_id") for r in call_rows) or len(
        {r["call_id"] for r in call_rows}
    ) != len(call_rows):
        _fail("Native tool call identities are ambiguous")
    calls = {r["call_id"]: r for r in call_rows}
    result = []
    for row in rows:
        call = calls.get(row.get("call_id"))
        if row.get("type") != "tool_result" or not call:
            continue
        if (
            any(
                r.get("issue_id") != issue_id
                or r.get("task_id") != run.id
                or type(r.get("seq")) is not int
                or not _in_run(r.get("created_at"), run)
                for r in (call, row)
            )
            or call["seq"] >= row["seq"]
        ):
            _fail("Native record scope/time/sequence is invalid")
        result.append((call, row))
    return rows, result


def _command(call):
    data = call.get("input") or {}
    value = data.get("text", data.get("command"))
    if not isinstance(value, str) or "truncated" in value:
        return []
    try:
        words = shlex.split(value.removeprefix("$ "))
        if (
            len(words) == 3
            and words[0] in {"/bin/zsh", "/bin/bash", "/usr/bin/zsh", "/usr/bin/bash"}
            and words[1] == "-lc"
        ):
            words = shlex.split(words[2])
        while words and re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\s]*", words[0]):
            if words[0].split("=", 1)[0] not in {
                "OMAC_ENGINE",
                "OMAC_WORKSPACE_ID",
                "OMAC_PROJECT_ID",
                "GODEBUG",
                "MULTICA_HTTP_TIMEOUT",
            }:
                return []
            if (
                words[0].startswith("OMAC_ENGINE=")
                and words[0] != "OMAC_ENGINE=multica"
            ):
                return []
            words.pop(0)
        if any(
            any(char in word for char in (";", "|", "&", "$", "`", "\n", "<", ">"))
            for word in words
        ):
            return []
        return words
    except ValueError:
        return []


def _source_reference(store, runtime, item, reviewer_run):
    _, pairs = _native_pairs(runtime, item.id, reviewer_run)
    selected = []
    for call, row in pairs:
        args = _command(call)
        if (
            args[:5] != ["multica", "issue", "comment", "list", item.id]
            or "--thread" not in args
        ):
            continue
        flags = args[5:]
        if any(x in flags for x in (";", "&&", "|", "||")):
            continue
        try:
            cid = flags[flags.index("--thread") + 1]
            if flags[flags.index("--output") + 1] != "json":
                continue
        except (ValueError, IndexError):
            continue
        if not re.fullmatch(r"[0-9a-f-]{36}", cid) or not re.search(
            r"- \*\*exit_code:\*\* 0\s*$", row.get("output", "")
        ):
            continue
        selected.append((cid, {"call": call, "result": row}))
    unique = {cid for cid, _ in selected}
    if len(unique) != 1:
        _fail(
            "Original Reviewer needs one unambiguous native verification-comment read"
        )
    cid = next(iter(unique))
    record = next(record for found, record in selected if found == cid)
    return store.read_verification_reference(item.id, cid), _digest(record)


def _submit_receipt(runtime, item, run, pr_url):
    rows, pairs = _native_pairs(runtime, item.id, run)
    accepted = []
    for call, row in pairs:
        args = _command(call)
        if args[:4] != ["omac", "work", "submit", item.id]:
            continue
        try:
            if (
                args[args.index("--pr-url") + 1] != pr_url
                or "--verification-file" not in args
            ):
                continue
        except (ValueError, IndexError):
            continue
        if row.get("output_truncated") or not isinstance(row.get("output"), str):
            continue
        output = row["output"]
        for match in re.finditer(r"(?m)^\{", output):
            try:
                payload, end = json.JSONDecoder().raw_decode(output[match.start() :])
            except ValueError:
                continue
            if output[match.start() + end :].strip():
                continue
            if (
                payload.get("ok") is True
                and payload.get("terminal") is True
                and payload.get("next_action") == "stop"
                and payload.get("issue_id") == item.id
                and payload.get("submitted_phase") == "authoring"
                and payload.get("advanced_to") == "done"
            ):
                accepted.append({"call": call, "result": row})
    if len(accepted) != 1 or any(
        r.get("type") == "tool_use" and r.get("seq", 0) > accepted[0]["result"]["seq"]
        for r in rows
    ):
        _fail(
            "Latest Worker needs one complete terminal submit and no tools after stop"
        )
    return {
        "sha256": _digest(accepted[0]),
        "created_at": accepted[0]["result"]["created_at"],
        "call_id": accepted[0]["call"]["call_id"],
        "seq": accepted[0]["result"]["seq"],
    }


def _urls(verification):
    return set(URL.findall(json.dumps(verification, ensure_ascii=False)))


def _publication(store, index_url, item, original, expected=None, *, download=True):
    if (
        not isinstance(index_url, str)
        or URL.fullmatch(index_url) is None
        or not index_url.endswith("/evidence-index.json")
    ):
        _fail("An exact evidence-index release download URL is required")
    if index_url not in _urls(item.verification) or index_url in _urls(original):
        _fail("Publication must change relative to the real original reject")
    if expected is None:
        body = store.observe_release_assets([index_url])[0].content
    else:
        try:
            body = base64.b64decode(expected["index_bytes"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValidationError("Invalid prepared index bytes") from exc
    try:
        index = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("Published index is not complete JSON") from exc
    pr = (item.artifacts or {}).get("pr_url")
    head = (item.artifacts or {}).get("head_sha")
    if (
        not isinstance(index, dict)
        or index.get("schema") != "aiteam.evidence-index/v1"
        or index.get("artifact_id") != KEY + "-evidence"
        or index.get("dag_key") != KEY
        or index.get("component_owner") != "release-preview"
        or index.get("source_revision") != head
        or index.get("source_pr") != pr
    ):
        _fail("Index must bind the exact Preview owner, PR and HEAD")
    files = index.get("files")
    if not isinstance(files, list) or len(files) != len(FILES):
        _fail("The complete eight-file publication set is required")
    by_url, names = {}, set()
    for row in files:
        if not isinstance(row, dict):
            _fail("Index file row is not structured")
        path, url = row.get("path"), row.get("url")
        name = path.removeprefix(KEY + "/") if isinstance(path, str) else ""
        if (
            name not in FILES
            or name in names
            or path != KEY + "/" + name
            or not isinstance(url, str)
            or url != index_url.rsplit("/", 1)[0] + "/" + name
            or type(row.get("size_bytes")) is not int
            or row["size_bytes"] <= 0
            or not isinstance(row.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            _fail("Index path, URL, SHA256 or size is invalid")
        names.add(name)
        by_url[url] = row
    urls = sorted([index_url, *by_url])
    observations = store.observe_release_assets(urls, download=download)
    if len(observations) != len(urls):
        _fail("Asset observation is incomplete")
    refs, total = [], 0
    for url, observation in zip(urls, observations):
        ref = observation.reference
        if (
            not isinstance(ref, dict)
            or ref.get("url") != url
            or ref.get("repository") != pr.split("/")[3] + "/" + pr.split("/")[4]
            or ref.get("tag_commit_sha") != head
            or ref.get("target_commitish") != head
            or type(ref.get("release_immutable")) is not bool
            or type(ref.get("asset_id")) is not int
            or ref["asset_id"] <= 0
            or type(ref.get("release_id")) is not int
            or ref["release_id"] <= 0
            or not ref.get("asset_node_id")
            or not ref.get("release_node_id")
            or not ref.get("updated_at")
            or type(ref.get("bytes")) is not int
            or not 0 < ref["bytes"] <= 16 * 1024 * 1024
            or not isinstance(ref.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
        ):
            _fail("Stable release/asset/commit identity and size/digest are required")
        total += ref["bytes"]
        if download and (
            len(observation.content) != ref["bytes"]
            or hashlib.sha256(observation.content).hexdigest() != ref["sha256"]
        ):
            _fail("Asset bytes do not match frozen digest/size")
        if url == index_url:
            if (
                ref["bytes"] != len(body)
                or ref["sha256"] != hashlib.sha256(body).hexdigest()
                or (download and observation.content != body)
            ):
                _fail("Index identity or bytes changed")
        else:
            row = by_url[url]
            if row["sha256"] != ref["sha256"] or row["size_bytes"] != ref["bytes"]:
                _fail("Index and actual digests/sizes disagree")
            if (
                url.endswith("/verification.yaml")
                and ref["sha256"] != item.verification_ref["sha256"]
            ):
                _fail("Published verification is not exact latest submission")
        refs.append(deepcopy(ref))
    if (
        total > 64 * 1024 * 1024
        or len({r["asset_id"] for r in refs}) != len(refs)
        or len({r["release_id"] for r in refs}) != 1
    ):
        _fail("Asset set is oversized, ambiguous or crosses releases")
    result = {
        "index_url": index_url,
        "index_bytes": base64.b64encode(body).decode(),
        "assets": refs,
        "policy": "pinned-byte-review-only; mutable URLs are not immutable approval",
    }
    if expected is not None and result != expected:
        _fail("Frozen publication identities changed")
    return result


def _verify(
    store, runtime, manifest, key, index_url, prepared=None, *, download=True, sdk=False
):
    bound_key, bound_root, artifact = KEY, ROOT, KEY + "-evidence"
    source_reader, publication_reader = _source_reference, _publication
    if sdk:
        from . import sdk_publication_review as profile

        bound_key, bound_root, artifact = profile.KEY, profile.ROOT, profile.ARTIFACT
        source_reader, publication_reader = (
            profile._source_reference,
            profile._publication,
        )
    node = manifest.nodes.get(key)
    if (
        key != bound_key
        or node is None
        or not node.work_item_id
        or not node.reviewer
        or node.merged
        or node.status == "done"
        or node.worker == node.reviewer
    ):
        _fail(
            "Only existing unmerged Preview repair with independent Reviewer is supported"
        )
    item = store.get_work_item(node.work_item_id)
    handoff = item.worker_handoff
    if prepared is not None:
        expected = parse_worker_handoff(prepared["source_handoff"])
        if handoff is not None and handoff != expected:
            _fail("Intact handoff changed")
        handoff = expected
    if not isinstance((item.artifacts or {}).get("pr_url"), str) or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+",
        item.artifacts["pr_url"],
    ):
        _fail("An exact GitHub PR URL is required")
    if (
        item.kind != TaskKind.DEVELOP
        or handoff is None
        or not handoff.is_causally_bound()
        or handoff.authoring_recovery is not None
        or handoff.source_review_verdict != "reject"
        or handoff.gate != "review"
        or handoff.target_worker != node.worker
        or item.worker != node.worker
        or handoff.target_worker_bounce != item.bounces.worker
        or handoff.target_review_bounce != item.bounces.review
        or handoff.target_agent_id != store.resolve_agent_id(node.worker)
        or handoff.review_context_binding != review_context_binding(item)
        or review_context_binding(item)["contract_sha256"]
        != _digest(_dump_contract(node.contract))
        or item.contract.get("produces") != [{"artifact_id": artifact}]
    ):
        _fail("Issue, contract, generation and intact reject handoff must match")
    contract = _attachment(store, item.id, item.contract_ref)
    if _mapping(contract.content, "Contract") != item.contract:
        _fail("Contract differs from exact source bytes")
    runs = _runs(runtime, item.id)
    workers = [
        r
        for r in runs
        if r.kind == "direct" and r.formal and r.agent_id == handoff.target_agent_id
    ]
    target = next((r for r in workers if r.id == handoff.target_run_id), None)
    if (
        target is None
        or target.status != "completed"
        or target.id in handoff.baseline_direct_run_ids
        or any(_time(r.updated_at) is None for r in workers)
        or _time(target.updated_at) != max(_time(r.updated_at) for r in workers)
    ):
        _fail("Exact latest completed formal Worker Run is required")
    candidate = _attachment(store, item.id, item.verification_ref)
    if (
        candidate.task_id != target.id
        or candidate.uploader_type != "agent"
        or candidate.uploader_id != target.agent_id
        or not _in_run(candidate.created_at, target)
        or _mapping(candidate.content, "Verification") != item.verification
    ):
        _fail("Current bytes/uploader are not bound to latest formal Worker")
    feedback = handoff.source_review_feedback or {}
    report_ref, ledger_ref = feedback.get("report_ref"), feedback.get("ledger_ref")
    report_blob = _attachment(store, item.id, report_ref)
    ledger_blob = _attachment(store, item.id, ledger_ref)
    report = _mapping(report_blob.content, "Reject report")
    ledger = _mapping(ledger_blob.content, "Reject ledger")
    try:
        validate_review_ledger(ledger)
    except ValueError as exc:
        raise ValidationError("Preserve invalid original ledger and stop") from exc
    blockers = report.get("blockers", []) if isinstance(report, dict) else []
    latest = ledger["cycles"][-1] if ledger["cycles"] else {}
    if (
        len(blockers) != 1
        or blockers[0].get("root_cause_key") != bound_root
        or latest.get("subject_digest") != handoff.source_review_subject_digest
        or latest.get("verdict") != "reject"
        or latest.get("report_digest") != _review_report_digest(report)
        or report.get("reviewed_pr") != (item.artifacts or {}).get("pr_url")
        or report.get("reviewed_head_sha") != handoff.baseline_pr_head_sha
        or report.get("reviewed_head_sha") != (item.artifacts or {}).get("head_sha")
        or report.get("review_protocol") != "omac.review/v2"
        or report.get("full_review_completed") is not True
        or any(
            status != "pass"
            for name, status in latest.get("obligation_results", {}).items()
            if name != "dimension:evidence"
        )
        or latest.get("obligation_results", {}).get("dimension:evidence") != "fail"
        or item.review_ledger_ref != ledger_ref
        or item.review_ledger != ledger
        or any(
            b.get("root_cause_key") != bound_root
            for b in ledger["blockers"]
            if b.get("status") == "open"
        )
    ):
        _fail("Only full original publication-only reject is applicable")
    reviewer_id = store.resolve_agent_id(node.reviewer)
    review = next((r for r in runs if r.id == report_blob.task_id), None)
    if (
        review is None
        or review.agent_id != reviewer_id
        or reviewer_id == target.agent_id
        or review.kind != "direct"
        or not review.formal
        or review.status != "completed"
        or ledger_blob.task_id != review.id
        or any(
            b.uploader_id != reviewer_id
            or b.uploader_type != "agent"
            or not _in_run(b.created_at, review)
            for b in (report_blob, ledger_blob)
        )
    ):
        _fail("Original report/ledger must belong to independent formal Reviewer")
    original_ref, source_read = source_reader(store, runtime, item, review)
    original_blob = _attachment(store, item.id, original_ref)
    original_run = next((r for r in workers if r.id == original_blob.task_id), None)
    if (
        original_run is None
        or original_blob.uploader_id != target.agent_id
        or original_blob.uploader_type != "agent"
        or not _in_run(original_blob.created_at, original_run)
        or _time(original_run.updated_at) > _time(review.created_at)
        or original_blob.sha256 == candidate.sha256
    ):
        _fail(
            "Publication must change against real original reject, not a later retry baseline"
        )
    original = _mapping(original_blob.content, "Original verification")
    receipt = _submit_receipt(
        runtime, item, target, (item.artifacts or {}).get("pr_url")
    )
    if _time(candidate.created_at) > _time(receipt["created_at"]):
        _fail(
            "Candidate attachment was not present at the accepted terminal submission"
        )
    from ..core.evidence import validate_worker_evidence

    errors = validate_worker_evidence(node, item)
    if errors:
        _fail("Normal Worker evidence validation failed: " + "; ".join(errors))
    identity = _seal_worker_delivery(
        store, manifest, key, item, handoff, target, attachment=candidate
    )
    if identity.pr_head_sha != handoff.baseline_pr_head_sha:
        _fail("This profile requires exact original rejected HEAD")
    publication = publication_reader(
        store,
        index_url,
        item,
        original,
        prepared.get("publication") if prepared else None,
        download=download,
    )
    value = {
        "issue_id": item.id,
        "node_id": key,
        "binding": review_context_binding(item),
        "contract_ref": item.contract_ref,
        "pr_url": identity.pr_url,
        "head_sha": identity.pr_head_sha,
        "run": _plain(target),
        "source_handoff": handoff.as_dict(),
        "verification_ref": item.verification_ref,
        "candidate_provenance": {
            k: getattr(candidate, k)
            for k in ("uploader_id", "uploader_type", "task_id", "created_at")
        },
        "original_report_ref": report_ref,
        "original_ledger_ref": ledger_ref,
        "original_blockers": blockers,
        "original_verification_ref": original_ref,
        "original_run": _plain(original_run),
        "reviewer_run": _plain(review),
        "native_source_read_sha256": source_read,
        "terminal_submit": receipt,
        "publication": publication,
        "budget": {
            "absolute": {
                s: getattr(item.bounces, s) for s in ("worker", "review", "merge")
            },
            "consumed": {
                s: consumed_bounces(manifest, key, item, s)
                for s in ("worker", "review", "merge")
            },
            "baseline": item.bounce_baseline,
        },
        "manifest_authority_sha256": _digest(
            {k: manifest.meta.get(k) for k in ("last_amendment_id", "amendment_apply")}
        ),
        "node_definition_sha256": _digest(
            {
                "worker": node.worker,
                "reviewer": node.reviewer,
                "contract": _dump_contract(node.contract),
                "blocked_by": node.blocked_by,
            }
        ),
    }
    if sdk:
        value.update(
            profile._chain(
                store,
                runtime,
                manifest,
                key,
                item,
                handoff,
                target,
                candidate,
                runs,
                prepared,
            )
        )
    return item, value, identity, runs


def _initial(item, value):
    if (
        item.status != WorkItemStatus.DONE
        or item.phase != TaskPhase.AUTHORING
        or item.delivery_identity
        or item.platform_assignee_id
        or item.reviewer
        or item.decision_required
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
        _fail("Initial control must remain intact unassigned DONE authoring submission")


def prepare_publication_review(store, runtime, manifest, key, index_url, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 2048:
        _fail("Explicit bounded operator reason is required")
    item, value, identity, runs = _verify(store, runtime, manifest, key, index_url)
    _initial(item, value)
    before = _state(item)
    if _state(store.get_work_item(item.id)) != before:
        _fail("Control changed during read-only preparation")
    return {
        "schema": SCHEMA,
        "reason": reason,
        "tuple": value,
        "expected_control": before,
        "runs_sha256": _digest(
            sorted((_plain(r) for r in runs), key=lambda r: r["id"])
        ),
    }


def _obligations(obligations, value):
    evidence = next(
        o for o in obligations if o["obligation_id"] == "dimension:evidence"
    )
    evidence["requirement"] += (
        " Independently verify original reject and pinned bytes; mutable URLs and this authorization are not immutability or implementation approval."
    )
    evidence["publication_recovery"] = {
        k: deepcopy(value[k])
        for k in (
            "original_report_ref",
            "original_ledger_ref",
            "original_blockers",
            "original_verification_ref",
            "reviewer_run",
            "run",
            "candidate_provenance",
            "verification_ref",
            "publication",
        )
    }
    return obligations


def apply_publication_review(
    store, runtime, manifest_path, key, request, *, approved_request_sha256, _sdk=False
):
    schema, journal, initial, obligations = SCHEMA, JOURNAL, _initial, _obligations
    if _sdk:
        from . import sdk_publication_review as profile

        schema, journal, initial, obligations = (
            profile.SCHEMA,
            profile.JOURNAL,
            profile._initial,
            profile._obligations,
        )
    if (
        not isinstance(request, dict)
        or set(request)
        != {"schema", "reason", "tuple", "expected_control", "runs_sha256"}
        or request.get("schema") != schema
        or not isinstance(request.get("tuple"), dict)
        or not isinstance(request.get("expected_control"), dict)
        or not isinstance(request.get("reason"), str)
        or not request["reason"].strip()
        or len(request["reason"].encode()) > 2048
        or _digest(request) != approved_request_sha256
    ):
        _fail(
            "Use exact prepared request and explicitly approved SHA256; supplied identities are forbidden"
        )
    value = request["tuple"]
    try:
        index_url = value["publication"]["index_url"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("Incomplete prepared publication tuple") from exc
    token = _digest(request)

    def verify(manifest):
        return _verify(
            store, runtime, manifest, key, index_url, value, download=True, sdk=_sdk
        )

    def pre_write(manifest, expected):
        actual = _verify(
            store, runtime, manifest, key, index_url, value, download=False, sdk=_sdk
        )[1]
        if actual != expected:
            _fail("Approval source tuple changed before Controller recovery write")

    return _apply_review_request(
        store,
        runtime,
        manifest_path,
        key,
        request,
        token,
        schema=schema,
        journal=journal,
        verifier=verify,
        initial_control=initial,
        obligation_transform=obligations,
        subject_builder=_review_subject_for_current_delivery,
        pre_write_verify=pre_write,
    )


def ensure_publication_review_complete(
    manifest,
    manifest_path,
    *,
    journal=JOURNAL,
    schema=SCHEMA,
    node=KEY,
    command="review-publication",
):
    entries = manifest.meta.get(journal, {})
    if not isinstance(entries, dict):
        _fail("Publication review journal is malformed")
    for token, entry in entries.items():
        request = entry.get("request") if isinstance(entry, dict) else None
        if (
            not isinstance(request, dict)
            or set(entry) != {"request", "request_sha256", "state", "step"}
            or set(request)
            != {"schema", "reason", "tuple", "expected_control", "runs_sha256"}
            or request.get("schema") != schema
            or token != _digest(request)
            or entry["request_sha256"] != token
            or type(entry["step"]) is not int
            or not 0 <= entry["step"] <= 8
            or entry["state"] not in {"pending", "consumed"}
            or (entry["state"] == "consumed" and entry["step"] != 8)
        ):
            _fail(
                "Publication review journal receipt is invalid; do not start a Runner"
            )
        if entry["state"] == "pending":
            raise NeedsDecision(
                "Publication review preparation is incomplete; resume the identical approved request using omac node "
                + command
                + " "
                + str(manifest_path)
                + " "
                + node
                + " --help before starting a Runner"
            )
