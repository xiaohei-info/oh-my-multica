"""Gather the follow-ups (pass-with-nits of the merged nodes) from the platform; the one reader of ``omac dag followups``
and of the closeout review's ``followups:triage`` obligation."""
from __future__ import annotations

from ..core.followups import followups_of, triage_obligation
from ..core.manifest import Manifest
from ..engines.store import WorkItemStore
from ..errors import WorkItemNotFoundError


def collect_followups(store: WorkItemStore, manifest: Manifest, *, exclude: str | None = None) -> list[dict]:
    """Follow-ups of every done node except ``exclude`` (the closeout node reviews the others', not its own)."""
    entries = []
    for key, node in manifest.nodes.items():
        if key == exclude or node.status != "done" or not node.work_item_id:
            continue
        try:
            entries.append((key, store.get_work_item(node.work_item_id)))
        except WorkItemNotFoundError:
            continue
    return followups_of(entries)


def review_obligations_for(store: WorkItemStore, manifest: Manifest, key: str, obligations: list[dict]) -> list[dict]:
    """The node's review obligations; the closeout node's gain ``followups:triage`` when there is anything to triage."""
    if key != manifest.meta.get("closeout_node"):
        return obligations
    triage = triage_obligation(collect_followups(store, manifest, exclude=key))
    return obligations if triage is None else [*obligations, triage]
