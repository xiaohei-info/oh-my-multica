"""``run_task`` on a real Multica: a task is authored, machine-checked, reviewed and revised by scripted agents."""
from _support.loopkit import contract  # noqa: F401  (re-exported for sibling modules)
from _support.taskkit import decompose_manifest, run, submit_step
from omac.core.taskmeta import TaskKind


def test_a_machine_failure_reworks_the_worker_before_any_reviewer_is_dispatched(kit, tmp_path):
    bad, good = tmp_path / "bad.yaml", tmp_path / "good.yaml"
    bad.write_text(decompose_manifest("go test cmd/entrypoint/..."))
    good.write_text(decompose_manifest("go test ./cmd/entrypoint/..."))
    kit.stack.set_scenario("alice", [[submit_step(kit.stack, "--manifest-file", str(bad))],
                                     [submit_step(kit.stack, "--manifest-file", str(good))]])
    kit.stack.set_scenario("bob", [[kit.stack.review("pass")]])
    assigns = kit.record(kit.store, "assign_work_item")

    result = run(kit, TaskKind.DECOMPOSE, {"title": "decompose"}, "alice", reviewers=["bob"], max_revisions=3)

    assert result["verdict"] == "pass"
    assert [a[1][2] for a in assigns] == ["worker", "worker", "reviewer"]
