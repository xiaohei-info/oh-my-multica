from types import SimpleNamespace

from omac.core.review_preflight import run_review_preflight
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


def test_preflight_rejects_bare_go_local_package_target():
    errors = run_review_preflight(_item("go test cmd/oactl/entrypoint/..."))

    assert errors == [
        "node node-a: Go local package target must start with ./ or ../: "
        "cmd/oactl/entrypoint/..."
    ]


def test_preflight_rejects_duplicate_explicit_output_path():
    errors = run_review_preflight(_item(
        "python package.py --bundle artifacts/source.tar.zst",
        "python package.py --bundle artifacts/source.tar.zst --kind second",
    ))

    assert errors == [
        "artifact output path has multiple producers: artifacts/source.tar.zst "
        "(node-a command 1, node-a command 2)"
    ]


def test_preflight_rejects_unmaterialized_command_input():
    errors = run_review_preflight(_item(
        "python verify.py --manifest generated/review-manifest.yaml"))

    assert errors == [
        "node node-a: command input has no reachable producer or owned scope: "
        "generated/review-manifest.yaml"
    ]


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
