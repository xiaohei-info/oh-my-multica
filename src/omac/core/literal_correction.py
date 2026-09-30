"""Explicitly reviewed prose-token corrections; ordinary contract policy is unchanged."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import re
from urllib.parse import urlparse

import yaml

from .manifest import _dump_contract, _load_contract
from .taskmeta import TaskKind, TaskPhase
from ..engines.models import WorkItemStatus
from ..errors import ValidationError

OP = "correct-contract-literal"


def _json(value):
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )


def _contract(value):
    return value if isinstance(value, dict) else _dump_contract(value)


def literal_operation(proposal):
    operations = proposal.get("operations") or []
    matches = [op for op in operations if isinstance(op, dict) and op.get("op") == OP]
    if not matches:
        return None
    if len(operations) != 1:
        raise ValidationError("Literal correction must be the only amendment operation")
    return matches[0]


def validate_literal_operation(manifest, operation):
    """Only one token in one non_goals string; no scope/quality/topology changes."""
    try:
        if set(operation) != {"op", "node", "set", "correction"}:
            raise ValueError("unsupported operation fields")
        node = manifest.nodes[operation["node"]]
        c = operation["correction"]
        if set(c) != {
            "old_contract",
            "index",
            "old_token",
            "new_token",
            "authority",
            "source_reject",
            "snapshots",
        }:
            raise ValueError("incomplete correction witness")
        old = c["old_contract"]
        new = operation["set"]["contract"]
        if set(operation["set"]) != {"contract"} or old != _dump_contract(
            node.contract
        ):
            raise ValueError("old full contract does not match the target")
        if new != _dump_contract(_load_contract(new)):
            raise ValueError("new contract must be a complete canonical contract")
        index = c["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("non_goals index must be a non-negative integer")
        old_token, new_token = c["old_token"], c["new_token"]
        for token in (old_token, new_token):
            if not isinstance(token, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", token
            ):
                raise ValueError("literal tokens must be single identifiers")
        text = old["non_goals"][index]
        if old_token == new_token or text.count(old_token) != 1:
            raise ValueError("exactly one original token occurrence is required")
        expected = deepcopy(old)
        expected["non_goals"][index] = text.replace(old_token, new_token, 1)
        if expected != new:
            raise ValueError("only the declared non_goals token may change")
        authority = c["authority"]
        if set(authority) != {"url", "sha256", "quote"}:
            raise ValueError("authority needs immutable URL, SHA256 and exact quote")
        parsed = urlparse(authority["url"])
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or not re.fullmatch(r"/[^/]+/[^/]+/blob/[0-9a-f]{40}/docs/.+", parsed.path)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("authority must be a commit-pinned GitHub docs blob")
        if not re.fullmatch(r"[0-9a-f]{64}", authority["sha256"]):
            raise ValueError("authority SHA256 is required")
        quote = authority["quote"]
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or len(quote.encode()) > 2048
            or new_token not in quote
            or old_token in quote
        ):
            raise ValueError("authority quote must identify the canonical replacement")
        if node.status != "todo" or node.merged or not node.work_item_id:
            raise ValueError(
                "target must be an existing untouched TODO authoring shell"
            )
        if (
            not isinstance(c["snapshots"], dict)
            or not c["snapshots"]
            or not isinstance(c["source_reject"], dict)
        ):
            raise ValueError("frozen runtime and real reject witnesses are required")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise ValidationError(f"Invalid literal correction: {error}") from error


def _scope(manifest, target):
    from .amendment import _downstream_union

    selected = _downstream_union(manifest, manifest, {target}) | {target}
    return [key for key in manifest.nodes if key in selected]


def _snapshots(manifest, target, store, runtime):
    if runtime is None:
        raise ValidationError("Literal correction requires an AgentRuntime snapshot")
    result = {}
    for key in _scope(manifest, target):
        node = manifest.nodes[key]
        item = store.get_work_item(node.work_item_id) if node.work_item_id else None
        runs = runtime.list_runs(node.work_item_id) if node.work_item_id else []
        if any(not run.terminal for run in runs):
            raise ValidationError(
                f"Literal correction requires quiescent target/downstream Runs: {key}"
            )
        if key == target and (
            item is None
            or item.status != WorkItemStatus.TODO
            or item.phase != TaskPhase.AUTHORING
            or item.platform_assignee_id
            or item.worker_handoff
            or item.delivery_identity
            or item.review_verdict
            or item.review_report_ref
            or item.reviewer_run_baseline
            or item.requires_decision
            or item.unknown_persisted_fields
            or item.bounces.total()
            or runs
            or _contract(item.contract) != _dump_contract(node.contract)
        ):
            raise ValidationError(
                "Literal correction requires an unassigned, Run-free authoring target with the exact old contract"
            )
        result[key] = _json(
            {
                "node": asdict(node),
                "store": asdict(item) if item else None,
                "runs": [asdict(run) for run in runs],
            }
        )
    return result


def _authority(store, authority):
    body = store.read_immutable_artifact(authority["url"])
    if (
        not isinstance(body, bytes)
        or len(body) > 16 * 1024 * 1024
        or hashlib.sha256(body).hexdigest() != authority["sha256"]
    ):
        raise ValidationError(
            "Literal correction authority bytes changed or could not be verified"
        )
    try:
        if authority["quote"] not in body.decode("utf-8"):
            raise ValidationError(
                "Literal correction canonical quote is absent from authority"
            )
    except UnicodeDecodeError as error:
        raise ValidationError(
            "Literal correction authority must be UTF-8 text"
        ) from error


def _source_reject(store, runtime, issue_id):
    item = store.get_work_item(issue_id)
    context = store.recover_review_rework_context(issue_id)
    if (
        item.kind != TaskKind.AMENDMENT
        or context.get("verdict") != "reject"
        or not context.get("subject_digest")
    ):
        raise ValidationError(
            "Literal correction requires the real preserved rejected amendment"
        )
    runs = runtime.list_runs(issue_id)
    for name in ("report_ref", "ledger_ref"):
        ref = context.get(name)
        if (
            not isinstance(ref, dict)
            or not ref.get("attachment_id")
            or not ref.get("comment_id")
            or not ref.get("sha256")
        ):
            raise ValidationError(
                "Literal correction reject attachment identity is missing"
            )
        observed = store.observe_verification_attachment(issue_id, ref)
        if (
            hashlib.sha256(observed.content).hexdigest() != ref["sha256"]
            or observed.sha256 != ref["sha256"]
            or observed.attachment_id != ref["attachment_id"]
            or observed.comment_id != ref["comment_id"]
        ):
            raise ValidationError(
                "Literal correction reject attachment bytes or identity changed"
            )
        if name == "report_ref":
            reviewer_id = store.resolve_agent_id(item.reviewer)
            worker_id = store.resolve_agent_id(item.worker)
            matching = [
                run
                for run in runs
                if run.id == observed.task_id
                and run.formal
                and run.status == "completed"
                and run.agent_id == reviewer_id
            ]
            if (
                observed.uploader_type != "agent"
                or observed.uploader_id != reviewer_id
                or reviewer_id == worker_id
                or len(matching) != 1
            ):
                raise ValidationError(
                    "Original reject needs its exact completed independent Reviewer Run"
                )
    if any(not run.terminal for run in runs):
        raise ValidationError(
            "Original rejected amendment still has a non-terminal Run"
        )
    return _json(
        {
            "issue_id": issue_id,
            "context": context,
            "store": asdict(item),
            "runs": [asdict(run) for run in runs],
        }
    )


def prepare_literal_correction(
    manifest,
    store,
    runtime,
    *,
    node_id,
    index,
    old_token,
    new_token,
    authority_url,
    authority_sha256,
    authority_quote,
    source_issue_id,
    reason,
):
    """Read-only preparation; no review verdict or application authority is granted."""
    if node_id not in manifest.nodes or manifest.nodes[node_id].contract is None:
        raise ValidationError(
            "Literal correction requires a known node with a contract"
        )
    old = _dump_contract(manifest.nodes[node_id].contract)
    new = deepcopy(old)
    try:
        new["non_goals"][index] = old["non_goals"][index].replace(
            old_token, new_token, 1
        )
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValidationError(
            "Invalid literal correction non_goals index/token"
        ) from error
    authority = {
        "url": authority_url,
        "sha256": authority_sha256,
        "quote": authority_quote,
    }
    operation = {
        "op": OP,
        "node": node_id,
        "set": {"contract": new},
        "correction": {
            "old_contract": old,
            "index": index,
            "old_token": old_token,
            "new_token": new_token,
            "authority": authority,
            "source_reject": {"issue_id": source_issue_id},
            "snapshots": {node_id: {}},
        },
    }
    validate_literal_operation(manifest, operation)
    _authority(store, authority)
    operation["correction"]["source_reject"] = _source_reject(
        store, runtime, source_issue_id
    )
    operation["correction"]["snapshots"] = _snapshots(manifest, node_id, store, runtime)
    # A second read prevents a preparation assembled from changing snapshots.
    proposal = {
        "schema": "omac.dag-amendment/v1",
        "reason": reason,
        "operations": [operation],
    }
    verify_literal_correction(manifest, proposal, store, runtime)
    return proposal


def verify_literal_correction(manifest, proposal, store, runtime):
    operation = literal_operation(proposal)
    if operation is None:
        return
    validate_literal_operation(manifest, operation)
    c = operation["correction"]
    try:
        original = yaml.safe_load(c["source_reject"]["store"]["deliverable"])
        source_operations = original["operations"]
        if (
            len(source_operations) != 1
            or source_operations[0].get("op") != "update"
            or source_operations[0].get("node") != operation["node"]
            or source_operations[0].get("set") != operation["set"]
        ):
            raise ValidationError(
                "Original rejected proposal must be the same exact target/full replacement contract"
            )
    except (KeyError, TypeError, yaml.YAMLError) as error:
        raise ValidationError(
            "Original rejected proposal cannot be verified"
        ) from error
    _authority(store, c["authority"])
    if c["source_reject"] != _source_reject(
        store, runtime, c["source_reject"].get("issue_id")
    ):
        raise ValidationError(
            "Original rejected amendment changed after literal correction preparation"
        )
    if c["snapshots"] != _snapshots(manifest, operation["node"], store, runtime):
        raise ValidationError(
            "Literal correction target/downstream runtime snapshot changed; prepare and independently review again"
        )


def literal_review_obligation(proposal):
    operation = literal_operation(proposal)
    if operation is None:
        return None
    c = operation["correction"]
    return {
        "obligation_id": "amendment-literal-correction:" + operation["node"],
        "category": "acceptance-responsibility",
        "requirement": (
            "Independently verify the canonical token against the exact immutable authority quote; "
            "the single prose-token correction has no semantic scope, quality, ownership, output or "
            "downstream implementation effect. Verify both full contracts, preserved real reject, "
            "and every frozen target/downstream Store and Run snapshot. Do not approve generic "
            "non_goals relaxation or clear the original reject/budgets. Reject if this cannot be proved."
        ),
        "authority": c["authority"],
        "before": c["old_contract"]["non_goals"][c["index"]],
        "after": operation["set"]["contract"]["non_goals"][c["index"]],
        "snapshot_sha256": hashlib.sha256(
            json.dumps(c["snapshots"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "original_reject_subject": c["source_reject"]["context"]["subject_digest"],
    }


def literal_review_binding(proposal, store, runtime, issue_id):
    """Bind a new actual independent review, never the rejected amendment's verdict."""
    from .amendment import amendment_review_binding, _proposal_core

    operation = literal_operation(proposal)
    if operation is None:
        return None
    if (
        runtime is None
        or issue_id == operation["correction"]["source_reject"]["issue_id"]
    ):
        raise ValidationError(
            "Literal correction needs a new independent amendment review"
        )
    item = store.get_work_item(issue_id)
    if (
        item.kind != TaskKind.AMENDMENT
        or item.phase != TaskPhase.CONFIRMATION
        or item.review_verdict != "pass"
        or not item.review_report_ref
    ):
        raise ValidationError(
            "Literal correction requires a real independent Reviewer pass in confirmation"
        )
    from .review_convergence import REVIEW_PROTOCOL_VERSION

    obligation = literal_review_obligation(proposal)
    report = item.review_report
    if (
        obligation not in item.review_obligations
        or not isinstance(report, dict)
        or report.get("review_protocol") != REVIEW_PROTOCOL_VERSION
        or report.get("full_review_completed") is not True
    ):
        raise ValidationError(
            "Literal correction needs explicit independent non-propagation review"
        )
    matching = [
        result
        for result in report.get("obligation_results", [])
        if isinstance(result, dict)
        and result.get("obligation_id") == obligation["obligation_id"]
    ]
    if (
        len(matching) != 1
        or matching[0].get("status") != "pass"
        or not matching[0].get("evidence")
    ):
        raise ValidationError(
            "Literal correction non-propagation obligation was not passed with evidence"
        )
    try:
        submitted = yaml.safe_load(item.deliverable)
        if _proposal_core(submitted) != _proposal_core(proposal):
            raise ValidationError("Literal correction reviewed deliverable changed")
        ref = item.review_report_ref
        observed = store.observe_verification_attachment(issue_id, ref)
        reviewer_id = store.resolve_agent_id(item.reviewer)
        worker_id = store.resolve_agent_id(item.worker)
        if (
            observed.attachment_id != ref["attachment_id"]
            or observed.comment_id != ref["comment_id"]
            or observed.sha256 != ref["sha256"]
            or reviewer_id == worker_id
            or observed.uploader_type != "agent"
            or observed.uploader_id != reviewer_id
            or hashlib.sha256(observed.content).hexdigest() != ref["sha256"]
            or yaml.safe_load(observed.content) != item.review_report
        ):
            raise ValidationError(
                "Literal correction report is not the exact independent Reviewer evidence"
            )
        runs = runtime.list_runs(issue_id)
        matched = [
            run
            for run in runs
            if run.id == observed.task_id
            and run.formal
            and run.status == "completed"
            and run.agent_id == reviewer_id
        ]
        if len(matched) != 1 or any(not run.terminal for run in runs):
            raise ValidationError(
                "Literal correction independent Reviewer Run is not uniquely completed"
            )
    except (TypeError, KeyError, yaml.YAMLError) as error:
        raise ValidationError(
            "Literal correction review evidence is incomplete"
        ) from error
    return amendment_review_binding(item)
