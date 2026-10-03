"""Exact native command18 checkpoint successor; no product verdict or dispatch.

The operator-retry subject and retained review subject have different meanings.
Only the captured, SHA-bound pair below is admitted; global delivery guards stay
unchanged. Truncated native output is association evidence, never a full receipt.
"""

from copy import deepcopy
import hashlib
import io
import re
import tarfile

from ..core.taskmeta import TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import ValidationError
from . import publication_review as shared
from . import sdk_publication_review as legacy
from .evidence_review import _attachment, _digest, _mapping, _plain, _state

KEY = legacy.KEY
ARTIFACT = legacy.ARTIFACT
ISSUE = legacy.ISSUE
HEAD = legacy.HEAD
WORKER = legacy.WORKER
CONTRACT = legacy.CONTRACT
ROOT = "harness-sdk-repair-published-semantic-ledger-overwritten-by-full-suite"
SCHEMA = "omac.sdk-command18-checkpoint-review/v1"
JOURNAL = "sdk_command18_checkpoint_reviews"
LATEST = "01a101bf-006b-7412-af52-258d6979a7cd"
REVIEW = "01a1019d-907b-7c70-872a-26fd10521987"
REVIEWER = "94f847b9-c10f-4d64-88b2-e90cb5c03ffc"
ORIGINAL = "01a100a6-13a4-7d17-97ef-cca4e913b8e0"
LEDGER_SUBJECT = "f36f91c4c423847008f98e2b8e6a609f023be5e7f59765f1c8bccab60c7baea3"
SOURCE_SUBJECT = "10a71305d06fe72fc0ad562422a4392279762b3e0fd5b8e12d879225f5bc4765"
COMMIT = "6aebcc6a53992e7e9bc77b249596655fb7bb4b7a"
INDEX = (
    "https://github.com/xiaohei-info/open-agent-cluster/blob/"
    + COMMIT
    + "/"
    + legacy.PREFIX
    + "evidence-index.json"
)
CHECKPOINT = "5e709ad69d68c6971a655a70f75a3cb754930b9f0f7c864a6f8d29304ee27670"
POST_SUITE = "2dcaeae5cd056b2e0c06152fed7b155c73fd4e36a12b609fee14fa04ab90e307"
OLD_TOKEN = "abcd70949aa5ae5048e4223528089cd019fece24171e8b4c875ae4d26433671e"
ORIGINAL_REF = {
    "comment_id": "01a100b2-e683-7b6d-8987-66778cf73fa1",
    "attachment_id": "01a100b2-e634-75b3-b991-3267999f7240",
    "filename": "omac-verification-a17140980523.yaml",
    "sha256": "a1714098052381671a4b23b68820e7995d2bf10d63ab865d5f941b56271d44f1",
    "bytes": 76149,
}


def _fail(message):
    raise ValidationError(
        message + "; preserve evidence and run omac node review-sdk-checkpoint --help"
    )


def _source_reference(store, runtime, item, reviewer_run):
    if reviewer_run.id != REVIEW:
        _fail("Original Reviewer Run differs")
    _, pairs = shared._native_pairs(runtime, item.id, reviewer_run)
    shows = [
        (c, r)
        for c, r in pairs
        if shared._command(c) == ["omac", "work", "show", ISSUE, "--output", "json"]
        and c["seq"] == 3
        and r["seq"] == 4
    ]
    associations = [
        (c, r)
        for c, r in pairs
        if c["seq"] == 46
        and r["seq"] == 47
        and r.get("tool") == "execute_code"
        and r.get("output", "").startswith("Exit code: 0\n")
        and ORIGINAL_REF["filename"] + " keys " in r.get("output", "")
    ]
    if (
        len(shows) != 1
        or len(associations) != 1
        or not re.search(
            r"(?m)^- \*\*exit_code:\*\* 0\s*$", shows[0][1].get("output", "")
        )
    ):
        _fail("Native original verification association is missing")
    # Native records expose only the filename prefix, not a historical seal.
    # Fetch complete bytes afresh by the exact attachment; shared verification
    # binds the independent full report/ledger, uploader, issue and Run window.
    if (
        hashlib.sha256(shows[0][0]["input"]["text"].encode()).hexdigest()
        != "0e3a7ec237471915f6f96e395f8d644a59341793e873e03aeab3a3cca0e1750e"
        or hashlib.sha256(shows[0][1]["output"].encode()).hexdigest()
        != "07fff6638c2e33fd6449e44a88b4f1a5a4628a45f5c46d65d53c4661e7fa3458"
        or hashlib.sha256(associations[0][1]["output"].encode()).hexdigest()
        != "389883eb5c811221aa7b403333f4a233576b2dd2937047e8d3d21b3607522d0c"
    ):
        _fail("Original native source association differs from the supported reject")
    ref = store.read_verification_reference(
        item.id, attachment_id=ORIGINAL_REF["attachment_id"]
    )
    if ref != ORIGINAL_REF:
        _fail("Original verification attachment changed")
    return ref, _digest(
        {"show": shows[0], "association": associations[0], "reference": ref}
    )


def _publication(store, index_url, item, original, expected=None, *, download=True):
    return legacy._publication(
        store,
        index_url,
        item,
        original,
        expected,
        download=download,
        publication_commit=COMMIT,
        publication_index_url=INDEX,
    )


def _checkpoint_bytes(store, item):
    urls = [INDEX.rsplit("/", 1)[0] + "/" + name for name in sorted(legacy.FILES)]
    payloads = {}
    for url, observation in zip(urls, store.observe_git_artifacts(urls)):
        name = url.rsplit("/", 1)[1]
        body = observation.content
        ref = observation.reference
        if (
            ref.get("url") != url
            or ref.get("commit_sha") != COMMIT
            or ref.get("repository") != "xiaohei-info/open-agent-cluster"
            or ref.get("parent_shas") != [HEAD]
            or ref.get("path") != legacy.PREFIX + name
            or ref.get("bytes") != len(body)
            or hashlib.sha1(
                b"blob " + str(len(body)).encode() + b"\0" + body
            ).hexdigest()
            != ref.get("blob_oid")
        ):
            _fail("Checkpoint bytes differ from their native Git blob identity")
        if not body or len(body) > 16 * 1024 * 1024:
            _fail("Complete bounded checkpoint payloads are required")
        payloads[name] = body
    if len(payloads) != len(legacy.FILES):
        _fail("Checkpoint payloads are incomplete")
    semantic = payloads["semantic-evidence.json"]
    if len(semantic) != 169561 or hashlib.sha256(semantic).hexdigest() != CHECKPOINT:
        _fail("Published semantic payload is not the actual command18 checkpoint")
    results = legacy._json_body(payloads["verification-results.json"])
    rows = results.get("verification_commands", [])
    commands = item.verification.get("commands", [])
    if len(rows) != 23 or len(commands) != 23:
        _fail("All twenty-three original commands are required")
    for i, (row, command) in enumerate(zip(rows, commands), 1):
        output = row.get("output")
        if (
            row.get("index") != i
            or row.get("cmd") != command.get("cmd")
            or row.get("exit_code") != 0
            or command.get("exit_code") != 0
            or not isinstance(output, str)
            or len(output.encode()) != row.get("output_bytes")
            or hashlib.sha256(output.encode()).hexdigest() != row.get("output_sha256")
        ):
            _fail("Published command sequence or complete output changed")
    env = results.get("environment", {})
    if (
        env.get("uname_sm") != "Linux aarch64"
        or env.get("go_version") != "go1.26.0 linux/arm64"
        or env.get("proc_self_fd_available") is not True
        or env.get("go_env")
        != {
            "GOOS": "linux",
            "GOARCH": "arm64",
            "GOVERSION": "go1.26.0",
            "CGO_ENABLED": "0",
            "GOFLAGS": "",
            "GOTOOLCHAIN": "auto",
        }
    ):
        _fail("Native Linux arm64 environment changed")
    checkpoint = results.get("semantic_checkpoint", {})
    if (
        checkpoint.get("captured_after_command") != 18
        or checkpoint.get("validated_command_exit_code") != 0
        or checkpoint.get("path") != legacy.PREFIX + "semantic-evidence.json"
        or checkpoint.get("sha256") != CHECKPOINT
        or checkpoint.get("bytes") != len(semantic)
    ):
        _fail("Checkpoint capture metadata changed")
    metrics = {
        "required_harness_conformance_test_count": 50,
        "required_measured_semantic_row_count": 13,
        "required_current_amendment_semantic_fixture_count": 7,
        "required_service_mode_fixture_count": 15,
    }
    if checkpoint.get("metrics") != metrics or any(
        legacy._json_body(semantic).get("metrics", {}).get(k) != v
        for k, v in metrics.items()
    ):
        _fail("Published checkpoint metric association changed")
    contrast = results.get("post_full_suite_semantic_observation", {})
    if (
        contrast.get("command_index") != 23
        or contrast.get("sha256") != POST_SUITE
        or contrast.get("bytes") != 169510
        or contrast.get("retained_in_publication") is not False
        or contrast.get("required_harness_conformance_test_count") is not None
    ):
        _fail("Post-suite contrast was substituted for the checkpoint")
    rerun = results.get("command18_checkpoint_revalidation", {})
    if (
        rerun.get("cmd") != commands[17]["cmd"]
        or rerun.get("exit_code") != 0
        or rerun.get("against_published_payload_sha256") != CHECKPOINT
        or rerun.get("output") != ""
        or rerun.get("output_sha256") != hashlib.sha256(b"").hexdigest()
    ):
        _fail("Published checkpoint revalidation record changed")
    names = set(legacy.FILES) - {"harness-sdk-build-repair-evidence.tar.gz"}
    try:
        with tarfile.open(
            fileobj=io.BytesIO(payloads["harness-sdk-build-repair-evidence.tar.gz"]),
            mode="r:gz",
        ) as archive:
            seen = set()
            for member in archive:
                if (
                    member.name not in names
                    or member.name in seen
                    or not member.isfile()
                    or member.size != len(payloads[member.name])
                ):
                    _fail("Archive member identity/type/size changed")
                seen.add(member.name)
                if (
                    archive.extractfile(member).read(member.size + 1)
                    != payloads[member.name]
                ):
                    _fail("Archive differs from the native published payload")
            if seen != names:
                _fail("Archive omits published checkpoint payloads")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValidationError(
            "Invalid checkpoint archive; run omac node review-sdk-checkpoint --help"
        ) from exc
    return {
        "sha256": CHECKPOINT,
        "bytes": len(semantic),
        "publication_commit": COMMIT,
        "captured_after_command": 18,
        "post_suite_sha256": POST_SUITE,
        "archive_payloads": len(names),
    }


def _chain(
    store, runtime, manifest, key, item, handoff, target, candidate, runs, prepared
):
    if (
        item.id != ISSUE
        or item.worker != "codex-ubuntu-newapi"
        or target.id != LATEST
        or target.agent_id != WORKER
        or handoff.generation != "handoff-abb73d1268d72c96"
        or handoff.source_review_subject_digest != SOURCE_SUBJECT
        or handoff.source_review_round != 2
        or handoff.baseline_verification_attachment_id != ORIGINAL_REF["attachment_id"]
        or shared.review_context_binding(item)
        != {
            "generation": "authoring-8d73ba953c9200ca5e1c7a5f",
            "contract_sha256": CONTRACT,
        }
        or item.review_generation != "authoring-8d73ba953c9200ca5e1c7a5f"
        or candidate.sha256
        != "7d9d1ee92e9d0971e8efea060b3d475f4d4756d0a8e63044dba1e32529d192d2"
        or candidate.attachment_id != "01a101dc-030d-73e4-ac67-d02441ffc6ba"
        or item.artifacts["head_sha"] != HEAD
        or item.artifacts["pr_url"]
        != "https://github.com/xiaohei-info/open-agent-cluster/pull/90"
        or handoff.source_review_feedback["report_ref"]["sha256"]
        != "0ba346be2c046ef8ce339263bb77f0ec9d8516c06044b815561ddec27c269f16"
        or handoff.source_review_feedback["ledger_ref"]["sha256"]
        != "89ff9fb7ccc6573648a9afb5189d26af573abec1ecf0e2191778ee1f7b53d7bb"
        or (
            item.bounces.worker,
            item.bounces.review,
            item.bounces.merge,
            item.bounces.ci,
        )
        != (1, 2, 0, 0)
        or item.bounce_baseline is not None
    ):
        _fail("Exact SDK checkpoint successor, feedback or budget differs")
    known = {
        LATEST: WORKER,
        ORIGINAL: WORKER,
        legacy.EXECUTION: WORKER,
        REVIEW: REVIEWER,
        "01a101b0-6066-788e-92f1-2932a56a8d18": WORKER,
        "01a10038-4447-719d-a676-746776d68d9b": REVIEWER,
        "01a0ffef-2192-7627-95bb-03c1b1a64f49": WORKER,
        "01a0fb38-a98b-7418-a208-f7cfdac73b8c": "037ad7f0-98f9-489f-bf43-0d1148233356",
    }
    if (
        set(r.id for r in runs) != set(known)
        or len(runs) != len(known)
        or set(handoff.baseline_direct_run_ids) != set(known) - {LATEST}
        or any(
            r.agent_id != known[r.id]
            or not r.formal
            or r.kind != "direct"
            or r.status != "completed"
            for r in runs
        )
    ):
        _fail("Native SDK history contains unknown, active or misattributed Runs")
    journal = manifest.meta.get(legacy.JOURNAL, {})
    old = journal.get(OLD_TOKEN, {})
    if (
        set(journal) != {OLD_TOKEN}
        or old.get("state") != "consumed"
        or old.get("step") != 8
        or old.get("request_sha256") != OLD_TOKEN
        or _digest(old.get("request")) != OLD_TOKEN
    ):
        _fail("Original consumed SDK receipt must remain intact")
    original = _mapping(
        _attachment(store, item.id, ORIGINAL_REF).content, "Original verification"
    )
    if [
        (c.get("cmd"), c.get("exit_code"))
        for c in item.verification.get("commands", [])
    ] != [(c.get("cmd"), c.get("exit_code")) for c in original.get("commands", [])]:
        _fail("Inherited twenty-three commands changed")
    _, pairs = shared._native_pairs(runtime, item.id, target)
    proof = []
    for seq, result_seq, command_hash, output_hash in [
        (
            43,
            48,
            "056446df7e713012259ce1166d3d9041f58761fff51bc755be5ea51ee60a2bff",
            "cc4e02a79f6b739c3c76f03176c1c3c0b455597374025e0ea3f7e38c7f6569c7",
        ),
        (
            63,
            64,
            "c1be6da5c603033ff2167e3cfa934a38340e01c22692bdc773d85a0fa43d5a39",
            "f1754790a6b793a389585b6e739d4a70718ee03c7ac7ecf99779392430bd1538",
        ),
    ]:
        matched = [
            (c, r) for c, r in pairs if c["seq"] == seq and r["seq"] == result_seq
        ]
        if len(matched) != 1:
            _fail("Complete native checkpoint execution receipt is missing")
        call, result = matched[0]
        if (
            call.get("tool") != "exec_command"
            or result.get("output_truncated") is not False
            or hashlib.sha256(
                (call.get("input", {}).get("command", "")).encode()
            ).hexdigest()
            != command_hash
            or hashlib.sha256(result.get("output", "").encode()).hexdigest()
            != output_hash
        ):
            _fail("Native checkpoint program or complete causal result changed")
        proof.append(_digest({"call": call, "result": result}))
    checkpoint = _checkpoint_bytes(store, item)
    checkpoint["native_receipts_sha256"] = proof
    checkpoint["source_subject_pair"] = {
        "operator_retry": SOURCE_SUBJECT,
        "retained_review": LEDGER_SUBJECT,
    }
    return {
        "checkpoint_provenance": checkpoint,
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
        _fail("Preserve the exact unassigned authoring submission")


def _obligations(obligations, value):
    obligations = shared._obligations(obligations, value)
    evidence = next(
        o for o in obligations if o["obligation_id"] == "dimension:evidence"
    )
    evidence["publication_recovery"]["checkpoint_provenance"] = deepcopy(
        value["checkpoint_provenance"]
    )
    evidence["requirement"] += (
        " Independently rerun unchanged SDK23 at the exact PR HEAD and validate the published command18 checkpoint and archive. "
        "Preserve raw SDK85/wholeSDK90/Host90/security/downstream requirements and both original reject cycles. "
        "Native execution receipts and Controller authorization are not a product pass; truncated remote revalidation output is not a complete receipt."
    )
    return obligations


def prepare_sdk_checkpoint_review(store, runtime, manifest, key, index_url, reason):
    try:
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.encode()) > 2048
        ):
            _fail("An explicit bounded operator reason is required")
        item, value, _, runs = shared._verify(
            store, runtime, manifest, key, index_url, sdk="checkpoint"
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
            .replace("review-sdk-publication", "review-sdk-checkpoint")
            .replace("review-evidence", "review-sdk-checkpoint")
            .replace("review-publication", "review-sdk-checkpoint")
        ) from exc
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise ValidationError(
            "Incomplete SDK checkpoint facts; run omac node review-sdk-checkpoint --help"
        ) from exc


def apply_sdk_checkpoint_review(
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
            _sdk="checkpoint",
        )
    except ValidationError as exc:
        raise ValidationError(
            str(exc)
            .replace("review-sdk-publication", "review-sdk-checkpoint")
            .replace("review-evidence", "review-sdk-checkpoint")
            .replace("review-publication", "review-sdk-checkpoint")
        ) from exc
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise ValidationError(
            "Incomplete SDK checkpoint request; run omac node review-sdk-checkpoint --help"
        ) from exc


def ensure_sdk_checkpoint_review_complete(manifest, manifest_path):
    shared.ensure_publication_review_complete(
        manifest,
        manifest_path,
        journal=JOURNAL,
        schema=SCHEMA,
        node=KEY,
        command="review-sdk-checkpoint",
    )
