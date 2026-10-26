"""Evidence a Worker submits for a contract: built from the contract alone, like an agent would from `omac work show`."""
from __future__ import annotations

from omac.core.acceptance_responsibility import evidence_targets
from omac.core.manifest import Contract, _load_contract


def verification_for(contract: Contract, dag_key: str) -> dict:
    """A verification document that passes the worker evidence gate for ``contract``."""
    commands = [{"cmd": cmd, "exit_code": 0, "summary": "passed"} for cmd in contract.verification_commands]
    if commands:
        commands[0]["business_tests"] = [
            {"acceptance": acceptance, "test": f"tests://{dag_key}/acceptance/{acceptance}"}
            for acceptance in evidence_targets(contract)
        ]
    return {
        "commands": commands,
        "integration_gates": [
            {
                "name": gate.get("name"),
                "commands": [{"cmd": cmd, "exit_code": 0, "summary": "integration passed"} for cmd in gate.get("commands", [])],
                "metrics": dict(gate.get("required_metrics", {})),
                "artifacts": list(gate.get("artifacts", [])),
                "covers": list(gate.get("covers", [])),
                "source_of_truth": list(gate.get("source_of_truth", [])),
                "delivery_goal": gate.get("delivery_goal"),
            }
            for gate in contract.integration_gates
        ],
        "pr_base": contract.pr_base,
        "ci_status": "passed",
        "coverage": contract.coverage_gate,
        "env_setup": [f"env: {gate.get('name')}" for gate in contract.integration_gates] if contract.integration_gates else [],
    }


def review_report_for(context: dict, verdict: str, round_index: int = 1, *, repeat: bool = False) -> dict:
    """A review report for the obligations listed by `omac work show` (context), with the given verdict.
    ``repeat``: the blockers of earlier reviews are still open and reported again unchanged."""
    contract = _load_contract(context["contract"]) if context.get("contract") else None
    failed = "dimension:structure" if verdict == "reject" else None
    return {
        "review_protocol": context["review_protocol"],
        "obligation_results": [
            {"obligation_id": o["obligation_id"], "status": "fail" if o["obligation_id"] == failed else "pass",
             "evidence": "covered"}
            for o in context.get("review_obligations") or []
        ],
        "prior_blocker_results": [
            {"blocker_id": b["blocker_id"], "status": "unchanged" if repeat else "fixed",
             "evidence": "the same invariant still fails" if repeat else "regression check passed"}
            for b in context.get("prior_open_blockers") or []
        ],
        "diff_reviewed": True, "tests_rerun": True, "coverage_checked": True, "full_review_completed": True,
        **({} if contract is None else {"review_goals": ["review goal"], "integration_tests_rerun": True}),
        **({} if contract is None else {"integration_gate_mapping": [
            {"gate": gate.get("name"), "source_of_truth": list(gate.get("source_of_truth", [])),
             "delivery_goal": gate.get("delivery_goal"), "evidence": f"integration gate {gate.get('name')}",
             "commands": [{"cmd": cmd, "exit_code": 0, "summary": "rerun passed"} for cmd in gate.get("commands", [])],
             "metrics": dict(gate.get("required_metrics", {})), "artifacts": list(gate.get("artifacts", [])),
             "status": "pass"}
            for gate in contract.integration_gates
        ], "acceptance_mapping": [
            {"acceptance": a, "evidence": f"reviewed {a}", "status": "fail" if failed else "pass"}
            for a in evidence_targets(contract)
        ]}),
        "blockers": ([{
            "root_cause_key": (context["prior_open_blockers"][0]["root_cause_key"] if repeat else f"review-{round_index}"),
            "obligation_id": failed, "classification": "unchanged" if repeat else "new",
            "summary": "needs revision", "evidence": "the reviewer found a blocker",
            "required_fix": "revise the deliverable",
        }] if failed else []),
        "nits": ["apply the non-blocking follow-up"] if verdict == "pass-with-nits" else [],
    }
