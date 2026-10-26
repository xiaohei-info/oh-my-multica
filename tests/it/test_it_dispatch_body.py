"""The issue a tick creates for a node, as the Worker finds it on a real Multica: real id in the entry command, refreshed on retry."""
from _support.loopkit import contract
from omac.core.workitem import WorkItemStatus


def test_a_dispatched_issue_carries_its_real_id_in_the_agent_entry_command(kit):
    manifest = kit.manifest([kit.node("a", title="Add login", reviewer="bob", contract=contract())])

    kit.tick(manifest, kit.save(manifest), max_parallel=4)

    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    assert item.id in item.description and f"omac work show {item.id} --output json" in item.description
    assert "## Task summary" in item.description and "硬约束" not in item.description


def test_retrying_a_node_refreshes_the_body_of_its_old_issue_instead_of_creating_a_new_one(kit):
    scoped = contract(scope_paths=["src/auth/**"])
    manifest = kit.manifest([kit.node("a", title="Add login", reviewer="bob", contract=scoped)])
    path = kit.save(manifest)
    kit.tick(manifest, path, max_parallel=4)
    item_id = manifest.nodes["a"].work_item_id
    assert "package.json" not in kit.store.get_work_item(item_id).description
    kit.runtime.cancel(item_id)
    kit.store.update_work_item_metadata(item_id, verification={"schema": "legacy-manual-verification"}, delivery_identity={})
    creations = kit.record(kit.store, "create_work_item")

    manifest.nodes["a"].contract.scope_paths.append("package.json")
    manifest.nodes["a"].status = "todo"
    kit.store.update_status(item_id, WorkItemStatus.DONE)
    kit.store.clear_assignment(item_id)
    kit.store.update_work_item_metadata(item_id, worker_handoff={})
    kit.store.update_status(item_id, WorkItemStatus.BLOCKED)
    kit.tick(manifest, path, max_parallel=4)

    refreshed = kit.store.get_work_item(item_id)
    first_screen = refreshed.description.split("# Add login", 1)[0]
    assert manifest.nodes["a"].work_item_id == item_id and not creations
    assert "package.json" in refreshed.description and "Primary code ownership" in refreshed.description
    assert "first action" in first_screen and "multica issue metadata" in first_screen and "delivery_identity" in first_screen
    assert refreshed.delivery_identity is None and refreshed.status == WorkItemStatus.IN_PROGRESS


def test_a_delivery_goes_from_worker_to_reviewer_to_done_by_assignment_and_metadata_not_by_comments(kit):
    kit.stack.set_scenario("alice", [[kit.stack.tool("worker-submit")]])
    kit.stack.set_scenario("bob", [[kit.stack.review("pass")]])
    manifest = kit.manifest([kit.node("a", reviewer="bob", contract=contract())])
    path = kit.save(manifest)
    assigns = kit.record(kit.store, "assign_work_item")

    result = kit.settle(manifest, path)

    item = kit.store.get_work_item(manifest.nodes["a"].work_item_id)
    assert result.state == "converged" and manifest.nodes["a"].status == "done" and item.status == WorkItemStatus.DONE
    assert f"omac work show {item.id} --output json" in item.description and item.reviewer == "bob"
    assert [a[1][1:3] for a in assigns] == [("alice", "worker"), ("bob", "reviewer")]
    assert not any("阶段变更" in comment for comment in kit.comments(item.id))      # the handoff posts no comment that could wake a second Run
