from types import SimpleNamespace

from omac.core.review_preflight import run_plan_preflight, run_review_preflight
from omac.core.taskmeta import TaskKind
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig
from omac.pipeline.tasks import run_task


def _item(command_a, command_b=None, *, blocked_by=None, scope_paths=None):
    commands = [command_a] + ([command_b] if command_b else [])
    manifest = {
        "meta": {"name": "demo"},
        "nodes": [{
            "id": "node-a",
            "worker": "alice",
            "blocked_by": blocked_by or [],
            "contract": {
                "objective": "demo",
                "source_of_truth": ["docs/design.md"],
                "acceptance": ["UJ-1"],
                "non_goals": ["none"],
                "verification_commands": commands,
                "integration_gates": [{
                    "name": "gate-a",
                    "layer": "L0",
                    "delivery_goal": "demo",
                    "source_of_truth": ["docs/design.md"],
                    "covers": ["node-a"],
                    "acceptance_refs": ["UJ-1"],
                    "commands": commands,
                }],
                "pr_base": "main",
                "scope_paths": scope_paths or [],
            },
        }],
    }
    import yaml
    return SimpleNamespace(
        kind=TaskKind.DECOMPOSE,
        deliverable=yaml.safe_dump(manifest, sort_keys=False),
    )


def test_plan_preflight_requires_explicit_owner_scope_and_typed_io():
    import yaml

    text = yaml.safe_dump({
        "meta": {"name": "demo"},
        "nodes": [{
            "id": "node-a",
            "worker": "alice",
            "contract": {
                "acceptance": ["UJ-1"],
            },
        }],
    }, sort_keys=False)

    errors = run_plan_preflight(text)

    assert any("scope_paths" in error for error in errors)
    assert any("evidence_mode" in error for error in errors)
    assert any("consumes" in error for error in errors)


def test_plan_preflight_accepts_contribution_only_typed_node():
    import yaml

    text = yaml.safe_dump({
        "meta": {"name": "demo"},
        "nodes": [{
            "id": "node-a",
            "worker": "alice",
            "description": "Implement the concrete login behavior.",
            "contract": {
                "objective": "implement login",
                "acceptance_contributions": [{
                    "flow_id": "UJ-1", "action_ids": ["ACT-1"],
                }],
                "source_of_truth": ["docs/login.md"],
                "non_goals": ["no dashboard changes"],
                "verification_commands": ["pytest -q"],
                "integration_gates": [{
                    "name": "login",
                    "layer": "L1",
                    "delivery_goal": "login works",
                    "source_of_truth": ["docs/login.md"],
                    "covers": ["login"],
                    "acceptance_refs": ["UJ-1"],
                    "commands": ["pytest -q"],
                }],
                "pr_base": "main",
                "scope_paths": ["src/login/**"],
                "evidence_mode": "fixture",
                "produces": [],
                "consumes": [],
            },
        }],
    }, sort_keys=False)

    assert run_plan_preflight(text) == []


def test_plan_preflight_rejects_generic_description_and_duplicate_scope_owner():
    import yaml

    contract = {
        "objective": "implement login",
        "acceptance": ["UJ-1"],
        "source_of_truth": ["docs/login.md"],
        "non_goals": ["no dashboard changes"],
        "verification_commands": ["pytest -q"],
        "integration_gates": [{
            "name": "login",
            "layer": "L1",
            "delivery_goal": "login works",
            "source_of_truth": ["docs/login.md"],
            "covers": ["login"],
            "acceptance_refs": ["UJ-1"],
            "commands": ["pytest -q"],
        }],
        "pr_base": "main",
        "scope_paths": ["src/shared/**"],
        "evidence_mode": "fixture",
        "produces": [],
        "consumes": [],
    }
    text = yaml.safe_dump({
        "meta": {"name": "demo"},
        "nodes": [
            {
                "id": "node-a", "worker": "alice",
                "description": "Smallest independently PR-able unit.",
                "contract": contract,
            },
            {
                "id": "node-b", "worker": "bob",
                "description": "Another concrete behavior.",
                "contract": contract,
            },
        ],
    }, sort_keys=False)

    errors = run_plan_preflight(text)

    assert any("generic smallest independently PR-able" in error for error in errors)
    assert any("scope ownership conflict" in error for error in errors)


def test_plan_preflight_reports_malformed_manifest_as_machine_error():
    errors = run_plan_preflight("nodes: [")

    assert len(errors) == 1
    assert errors[0].startswith("plan preflight could not parse manifest:")


def test_plan_compose_guard_reports_malformed_manifest_as_machine_error():
    from omac.pipeline.plan import _compose_guard

    guard = _compose_guard({"alice"}, strict_plan=True)

    errors = guard(SimpleNamespace(deliverable="nodes: ["))

    assert len(errors) == 1
    assert errors[0].startswith("manifest preflight could not parse manifest:")


def test_preflight_rejects_bare_go_local_package_target():
    errors = run_review_preflight(_item("go test cmd/..."))

    assert errors == [
        "node node-a: Go local package target must start with ./ or ../: "
        "cmd/..."
    ]


def test_preflight_skips_ambiguous_component_manifest_flag_semantics():
    errors = run_review_preflight(_item(
        "python aggregate.py --component-manifest future/components.yaml --kind first",
        "python aggregate.py --component-manifest future/components.yaml --kind second",
    ))

    assert errors == []


def test_preflight_skips_future_input_and_manifest_materialization_without_typed_io():
    errors = run_review_preflight(_item(
        "python aggregate.py --input future/a.json "
        "--manifest future/review-manifest.yaml"))

    assert errors == []


def test_preflight_accepts_command_input_owned_by_node_scope():
    errors = run_review_preflight(_item(
        "python verify.py --manifest generated/review-manifest.yaml",
        scope_paths=["generated/**"],
    ))

    assert errors == []


def test_preflight_accepts_input_from_reachable_predecessor():
    import yaml
    manifest = {
        "meta": {"name": "demo"},
        "nodes": [
            {
                "id": "producer",
                "worker": "alice",
                "blocked_by": [],
                "contract": {
                    "verification_commands": [
                        "python build.py --output generated/review-manifest.yaml"],
                },
            },
            {
                "id": "consumer",
                "worker": "bob",
                "blocked_by": ["producer"],
                "contract": {
                    "verification_commands": [
                        "python verify.py --manifest generated/review-manifest.yaml"],
                },
            },
        ],
    }
    item = SimpleNamespace(
        kind=TaskKind.DECOMPOSE,
        deliverable=yaml.safe_dump(manifest, sort_keys=False),
    )

    assert run_review_preflight(item) == []


def test_preflight_rejects_typed_consume_from_non_upstream_producer():
    import yaml
    manifest = {
        "meta": {"name": "demo"},
        "nodes": [
            {
                "id": "future",
                "worker": "alice",
                "blocked_by": [],
                "contract": {
                    "evidence_mode": "artifact",
                    "produces": [{"artifact_id": "future-output"}],
                    "verification_commands": ["pytest -q"],
                },
            },
            {
                "id": "current",
                "worker": "bob",
                "blocked_by": [],
                "contract": {
                    "evidence_mode": "fixture",
                    "consumes": [{
                        "artifact_id": "future-output",
                        "producer": "future",
                        "evidence_mode": "artifact",
                    }],
                    "verification_commands": ["pytest -q"],
                },
            },
        ],
    }
    item = SimpleNamespace(
        kind=TaskKind.DECOMPOSE,
        deliverable=yaml.safe_dump(manifest, sort_keys=False),
    )

    errors = run_review_preflight(item)

    assert any(
        "producer 'future' is not a transitive upstream dependency" in error
        for error in errors
    )


def test_preflight_accepts_explicit_relative_go_local_package_target():
    assert run_review_preflight(_item("go test ./cmd/...")) == []


def test_preflight_rejects_invalid_shell_syntax_once_per_command():
    errors = run_review_preflight(_item("if true; then echo missing-fi"))

    assert len(errors) == 1
    assert errors[0].startswith("node node-a: shell syntax is invalid:")


def test_non_manifest_review_has_no_manifest_specific_preflight():
    item = SimpleNamespace(kind=TaskKind.PLAN, deliverable="# plan")

    assert run_review_preflight(item) == []


def test_run_task_reworks_machine_failure_before_dispatching_reviewer():
    import yaml
    MockStore.reset()
    engine = create_engine("mock", EngineConfig(
        engine_type="mock",
        workspace_id="ws",
        extra={"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"},
    ))

    def manifest(command):
        return yaml.safe_dump({
            "meta": {"name": "demo"},
            "nodes": [{
                "id": "node-a",
                "worker": "alice",
                "blocked_by": [],
                "contract": {"verification_commands": [command]},
            }],
        }, sort_keys=False)

    MockStore.set_kind_delivery_sequence("decompose", [
        {"manifest": manifest("go test cmd/oactl/entrypoint/...")},
        {"manifest": manifest("go test ./cmd/oactl/entrypoint/...")},
    ])

    result = run_task(
        engine,
        TaskKind.DECOMPOSE,
        {"title": "decompose"},
        "alice",
        reviewers=["bob"],
        max_revisions=3,
        poll=lambda: None,
    )

    roles = [entry[2] for entry in engine.store.assign_log]
    assert result["verdict"] == "pass"
    assert roles == ["worker", "worker", "reviewer"]
