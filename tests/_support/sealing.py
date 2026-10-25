"""Controller-sealed delivery fixtures for store-level tests."""
import hashlib

import yaml


def seal_mock_delivery(store, item_id, pr_url, verification, *, phase=None):
    """Persist a complete controller-sealed delivery for MockStore tests."""
    from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity

    head_sha = hashlib.sha256(pr_url.encode("utf-8")).hexdigest()
    kwargs = {
        "artifacts": {"pr_url": pr_url, "head_sha": head_sha},
        "verification": verification,
        "verification_source": yaml.safe_dump(
            verification, allow_unicode=True, sort_keys=False),
    }
    if phase is not None:
        kwargs["phase"] = phase
    store.update_work_item_metadata(item_id, **kwargs)
    item = store.get_work_item(item_id)
    ref = item.verification_ref
    task_id = f"test-run-{ref['attachment_id']}"
    ref["task_id"] = task_id
    store.update_work_item_metadata(
        item_id,
        delivery_identity=DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation=f"test-handoff-{ref['attachment_id']}",
            worker=item.worker,
            agent_id=store.resolve_agent_id(item.worker),
            run_id=task_id,
            pr_url=pr_url,
            pr_head_sha=head_sha,
            verification_sha256=ref["sha256"],
            verification_attachment_id=ref["attachment_id"],
            verification_comment_id=ref["comment_id"],
            verification_uploader_id=ref.get("uploader_id"),
            verification_uploader_type=ref.get("uploader_type"),
            verification_task_id=task_id,
            verification_created_at=ref["created_at"],
        ),
    )
    return store.get_work_item(item_id)
