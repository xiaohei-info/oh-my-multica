"""Evidence a Worker submits for a contract: built from the contract alone, like an agent would from `omac work show`."""
from __future__ import annotations

from omac.core.acceptance_responsibility import evidence_targets
from omac.core.manifest import Contract


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
