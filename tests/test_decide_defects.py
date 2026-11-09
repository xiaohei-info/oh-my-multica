"""Where a reported defect goes: every row of the routing table, the closed table, and the commands that release a stop."""
import itertools
import shlex
from dataclasses import replace

import pytest

from omac.cli.main import build_parser
from omac.pipeline.decide_defects import (
    DEFECT_ROUTES, STOPS, DefectFacts, Route, decide_defect, decide_report, render_release,
)

BASE = DefectFacts(
    auto_fix="design-gap", unroutable=None, defect_class="design-gap", reporter_generation=0, state=None,
    human_amendment_pending=False, inflight_other_fix=False, done_other_fix=False, other_fix_after_run=None,
    fixed_on_suspected=0, suspected_fix_cap=2, budget_left=True)

ROWS = [   # (facts that make exactly this row the first match, route)
    (dict(auto_fix="off"), Route.NEEDS_OWNER),
    (dict(unroutable="suspected node is todo"), Route.UNROUTABLE),
    (dict(defect_class="tradeoff"), Route.NEEDS_OWNER),
    (dict(defect_class="contract-gap"), Route.NEEDS_OWNER),
    (dict(reporter_generation=1), Route.GENERATION_CAP),
    (dict(state="fixed"), Route.RECURRED),
    (dict(state="recurred"), Route.RECURRED),
    (dict(state="fixing", human_amendment_pending=True), Route.QUEUED),       # S14: attaching also rewrites the definition
    (dict(state="fixing"), Route.ATTACH),
    (dict(state="applying"), Route.ATTACH),
    (dict(inflight_other_fix=True), Route.QUEUED),
    (dict(done_other_fix=True, other_fix_after_run=True), Route.RETRY_AFTER_FIX),     # the fix is newer than the failed run
    (dict(done_other_fix=True, other_fix_after_run=None), Route.RETRY_AFTER_FIX),     # times unknown: the old behaviour
    (dict(fixed_on_suspected=2), Route.NODE_FIX_CAP),
    (dict(budget_left=False), Route.BUDGET_EXHAUSTED),                          # S5
    (dict(human_amendment_pending=True), Route.QUEUED),
    (dict(), Route.AUTO_FIX),
]


@pytest.mark.parametrize("change,route", ROWS, ids=[f"{r[1].value}-{i}" for i, r in enumerate(ROWS)])
def test_each_row_of_the_routing_table_fires_on_its_facts(change, route):
    assert decide_defect(replace(BASE, **change)) is route


def test_every_row_of_the_table_is_reached_by_some_facts():
    reached = {route for _, route in ROWS}
    assert reached == {route for _, route in DEFECT_ROUTES}


def test_the_table_is_closed_every_combination_of_facts_has_a_route():
    for auto, unroutable, klass, generation, state, human, inflight, done, after, fixed, budget in itertools.product(
            ("off", "design-gap"), (None, "why"), ("design-gap", "tradeoff"), (0, 1),
            (None, "queued", "applying", "fixing", "fixed", "recurred", "needs-owner"),
            (False, True), (False, True), (False, True), (None, False, True), (0, 2), (False, True)):
        facts = DefectFacts(auto, unroutable, klass, generation, state, human, inflight, done, after, fixed, 2, budget)
        assert isinstance(decide_defect(facts), Route)


def test_a_merged_fix_older_than_the_failed_run_is_not_retried_and_a_new_fix_is_inserted():
    stale = replace(BASE, done_other_fix=True, other_fix_after_run=False, fixed_on_suspected=1)
    assert decide_defect(stale) is Route.AUTO_FIX
    assert decide_defect(replace(stale, budget_left=False)) is Route.BUDGET_EXHAUSTED


def test_a_suspected_node_at_its_fix_cap_stops_instead_of_getting_another_fix_node():
    capped = replace(BASE, done_other_fix=True, other_fix_after_run=False, fixed_on_suspected=2)
    assert decide_defect(capped) is Route.NODE_FIX_CAP and Route.NODE_FIX_CAP in STOPS
    assert decide_defect(replace(capped, other_fix_after_run=True)) is Route.RETRY_AFTER_FIX       # a retry adds no node


def test_an_unknown_ledger_state_is_refused_not_routed():
    with pytest.raises(ValueError):
        decide_defect(replace(BASE, state="mystery"))


def test_a_recurred_defect_stops_even_while_a_human_amendment_is_pending():
    assert decide_defect(replace(BASE, state="fixed", human_amendment_pending=True)) is Route.RECURRED


@pytest.mark.parametrize("routes,verdict", [
    ([Route.AUTO_FIX, Route.ATTACH], Route.AUTO_FIX),
    ([Route.AUTO_FIX, Route.QUEUED], Route.QUEUED),
    ([Route.QUEUED, Route.NEEDS_OWNER], Route.NEEDS_OWNER),                      # a stop beats a wait
    ([Route.AUTO_FIX, Route.BUDGET_EXHAUSTED, Route.RECURRED], Route.BUDGET_EXHAUSTED),
])
def test_one_stopping_defect_stops_the_whole_report(routes, verdict):
    assert decide_report(routes) is verdict


@pytest.mark.parametrize("route", sorted(STOPS, key=lambda r: r.value))
def test_every_release_command_parses_with_the_real_argparse_parser(route):
    commands = render_release(route, ".omac/my dag.yaml", "api-items")
    assert commands and len(set(commands)) == len(commands)
    for command in commands:
        words = shlex.split(command.replace("<report.md>", "r.md").replace("<design.md>", "d.md"))
        assert words[0] == "omac"
        build_parser().parse_args(words[1:])                                      # a next_action that cannot run is a bug


def test_a_spent_budget_names_grant_first_and_a_recurrence_does_not():
    assert render_release(Route.BUDGET_EXHAUSTED, "m.yaml", "n")[0].startswith("omac defect grant m.yaml --reporter n")
    assert not render_release(Route.RECURRED, "m.yaml", "n")[0].startswith("omac defect grant")
