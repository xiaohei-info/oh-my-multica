"""A metadata write over the platform budget blocks one node; it never ends the whole `dag run`."""
import json
from types import SimpleNamespace

from omac.core.manifest import Manifest, Node
from omac.errors import MetadataBudgetError
from omac.pipeline import loop


class _Store:
    def __init__(self):
        self.config = SimpleNamespace(engine_type="multica", workspace_id="ws", project_id=None, extra={})
        self.calls = []

    def observe_work_item_control(self, item_id):
        return SimpleNamespace(work_item=SimpleNamespace(
            id=item_id, identifier="P-1", bounces=SimpleNamespace(review=0)))

    def get_work_item(self, item_id):
        return self.observe_work_item_control(item_id).work_item

    def update_status(self, item_id, status):
        self.calls.append(("status", item_id, status.value))

    def add_comment(self, item_id, text):
        self.calls.append(("comment", item_id, text))


def _manifest(tmp_path, upstream: int) -> Manifest:
    nodes = {f"up-{n}": Node(id=f"up-{n}", title=f"Up {n}", worker="w", status="done", work_item_id=f"00000000-0000-4000-8000-{n:012d}")
             for n in range(upstream)}
    nodes["gate"] = Node(id="gate", title="Gate", worker="w", blocked_by=list(nodes), work_item_id="wi-gate")
    nodes["free"] = Node(id="free", title="Free", worker="w", work_item_id="wi-free")
    return Manifest(meta={}, nodes=nodes)


def test_hundred_upstream_nodes_make_a_body_whose_source_refs_exceed_the_metadata_cap(tmp_path):
    manifest = _manifest(tmp_path, 100)
    _, body = loop._develop_issue_body_metadata(_Store(), manifest, "gate", phase=loop.TaskPhase.AUTHORING)
    assert len(body["source_refs"]) == 100
    assert len(json.dumps(body["source_refs"]).encode()) > 8192  # the store must bound it, not the Loop


def test_budget_error_blocks_that_node_and_dispatch_continues(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, 100)
    path = str(tmp_path / "m.yaml")
    store = _Store()

    def handoff(_s, _r, _c, _m, key, **_kw):
        if key == "gate":
            raise MetadataBudgetError("source_refs metadata is 12763 bytes; Multica allows at most 8192 bytes.")
        return SimpleNamespace(state="dispatched")

    monkeypatch.setattr(loop, "_dispatch_worker_handoff", handoff)
    monkeypatch.setattr(loop, "save_manifest", lambda *_a, **_k: None)

    dispatched = loop._dispatch(store, None, None, manifest, path, ["gate", "free"], 5)

    assert dispatched == ["free"]
    assert manifest.nodes["gate"].status == "blocked"
    assert manifest.nodes["gate"].work_item_id == "wi-gate"  # the issue is kept: a retry reuses it
    comment = next(c for c in store.calls if c[0] == "comment")[2]
    assert "omac node retry gate" in comment
