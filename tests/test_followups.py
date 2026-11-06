"""Follow-ups (S16): the nits of merged nodes are listed, and the closeout review cannot pass without disposing of each."""
from types import SimpleNamespace

from _support.ledgers import review_report
from omac.core.followups import TRIAGE_OBLIGATION_ID, followups_of, triage_gaps
from omac.core.manifest import Manifest, Node
from omac.core.review_convergence import validate_convergence_review
from omac.core.taskmeta import TaskKind
from omac.pipeline.followups import collect_followups, review_obligations_for


def merged(item_id, nits, verdict="pass-with-nits"):
    return SimpleNamespace(id=item_id, review_verdict=verdict, review_report={"nits": nits})


class Store:
    def __init__(self, items):
        self.items = items

    def get_work_item(self, item_id):
        return self.items[item_id]


def dag():
    """Three nodes that passed with nits, one that passed clean, one still running, and the closeout node."""
    nodes = {key: Node(id=key, worker="alice", status="done", work_item_id=f"wi-{key}") for key in "abcd"}
    nodes["e"] = Node(id="e", worker="alice", status="in_progress", work_item_id="wi-e")
    nodes["close"] = Node(id="close", worker="alice", status="todo", blocked_by=list("abcde"))
    items = {
        "wi-a": merged("wi-a", ["rename the helper", "  ", "add a docstring"]),
        "wi-b": merged("wi-b", ["trim the log line"]),
        "wi-c": merged("wi-c", ["drop the dead flag"]),
        "wi-d": merged("wi-d", [], verdict="pass"),
        "wi-e": merged("wi-e", ["not merged yet"]),
    }
    return Manifest(meta={"closeout_node": "close"}, nodes=nodes), Store(items)


def closeout_item(manifest, store):
    obligations = review_obligations_for(store, manifest, "close", [])
    return SimpleNamespace(kind=TaskKind.DEVELOP, deliverable=None, review_ledger=None, contract=None,
                           review_obligations=obligations), obligations


def test_followups_lists_every_nit_of_the_merged_nodes_that_passed_with_nits():
    manifest, store = dag()

    rows = collect_followups(store, manifest, exclude="close")

    assert [(r["followup_id"], r["nit"]) for r in rows] == [
        ("a#1", "rename the helper"), ("a#2", "add a docstring"), ("b#1", "trim the log line"), ("c#1", "drop the dead flag")]
    assert {r["work_item_id"] for r in rows} == {"wi-a", "wi-b", "wi-c"}


def test_s16_a_closeout_review_missing_the_disposition_of_any_followup_is_refused():
    manifest, store = dag()
    item, obligations = closeout_item(manifest, store)
    report = review_report(obligations)
    results = {r["obligation_id"]: r for r in report["obligation_results"]}

    results[TRIAGE_OBLIGATION_ID]["evidence"] = (
        "a#1: fix - src/util.py\na#2: defer - docs/followups.md\nb#1: drop - not worth it\n"   # c#1 is not mentioned
        "c#1 was looked at")                                                                    # named, but no disposition
    errors = validate_convergence_review(item, "pass", report)
    assert errors == [
        f"review_report obligation {TRIAGE_OBLIGATION_ID} does not dispose of: c#1; "
        "name each follow-up id with fix, defer or drop on its own line"]

    results[TRIAGE_OBLIGATION_ID]["evidence"] += "\nc#1: defer - docs/followups.md"
    assert validate_convergence_review(item, "pass", report) == []


def test_only_the_closeout_node_gets_the_triage_obligation_and_only_when_there_is_something_to_triage():
    manifest, store = dag()

    assert [o["obligation_id"] for o in review_obligations_for(store, manifest, "close", [])] == [TRIAGE_OBLIGATION_ID]
    assert review_obligations_for(store, manifest, "a", []) == []
    clean = Manifest(meta={"closeout_node": "close"}, nodes={"close": manifest.nodes["close"]})
    assert review_obligations_for(store, clean, "close", []) == []


def test_a_followup_id_is_not_matched_by_a_longer_id_with_the_same_prefix():
    assert triage_gaps(["a#1", "a#10"], "a#10: fix - x.py") == ["a#1"]
    assert triage_gaps(["api-core#1"], "see api-core#1: drop") == []
    assert triage_gaps(["x#1"], "x#1 and nothing else") == ["x#1"]


def test_followups_of_ignores_blank_nits_and_clean_passes():
    assert followups_of([("n", merged("i", ["  ", ""])), ("m", merged("j", ["x"], verdict="pass"))]) == []
