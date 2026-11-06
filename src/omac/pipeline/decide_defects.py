"""Where one reported defect goes: the table that decides it, and the commands that release a stop (设计 §7.5).

Pure: facts in, a ``Route`` out. ``pipeline/defects.py`` observes the facts and carries the route out; nothing here reads a
platform. The table is the only place the routing order is written (``tests/test_decide_defects.py`` walks every row).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..i18n import ui


class Route(str, Enum):
    NEEDS_OWNER = "defect-needs-owner"                  # a person decides: auto-fix is off, or the report is a trade-off
    UNROUTABLE = "defect-unroutable"                    # the report cannot be tied to finished work
    GENERATION_CAP = "defect-fix-generation-cap"        # a fix node itself reported a defect: never fix a fix automatically
    RECURRED = "defect-recurred"                        # the same defect again after its fix was merged
    BUDGET_EXHAUSTED = "defect-budget-exhausted"
    QUEUED = "queued"                                   # wait: a person's amendment is pending or a fix is in flight
    ATTACH = "attach"                                   # the reporter waits for the fix that already exists
    RETRY_AFTER_FIX = "retry-after-fix"                 # a fix on the same node was merged meanwhile: rerun the reporter once
    AUTO_FIX = "auto-fix"                               # insert a fix node


STOPS = (Route.NEEDS_OWNER, Route.UNROUTABLE, Route.GENERATION_CAP, Route.RECURRED, Route.BUDGET_EXHAUSTED)
FIX_CLASSES = ("design-gap",)  # the only class `defects.auto_fix: design-gap` repairs by itself


@dataclass(frozen=True)
class DefectFacts:
    """Everything the table reads about one defect of one report."""
    auto_fix: str                         # "off" | "design-gap"
    unroutable: str | None                # why routing is impossible, None when it is possible
    defect_class: str
    reporter_generation: int              # 1 when the reporter is itself a fix node
    state: str | None                     # the ledger state of this fingerprint
    human_amendment_pending: bool
    inflight_other_fix: bool              # another fingerprint is being fixed on the same suspected node
    done_other_fix: bool                  # ... and that fix was merged, while this fingerprint was never retried
    budget_left: bool


def _in(*states: str) -> Callable[[DefectFacts], bool]:
    return lambda facts: facts.state in states


# First matching row wins; the last row matches everything, so the table is closed. Row order is a contract.
DEFECT_ROUTES: tuple[tuple[Callable[[DefectFacts], bool], Route], ...] = (
    (lambda f: f.auto_fix == "off", Route.NEEDS_OWNER),
    (lambda f: f.unroutable is not None, Route.UNROUTABLE),
    (lambda f: f.defect_class not in FIX_CLASSES, Route.NEEDS_OWNER),
    (lambda f: f.reporter_generation >= 1, Route.GENERATION_CAP),
    (_in("fixed", "recurred"), Route.RECURRED),
    # Attaching and retrying also rewrite the definition, so they wait for a person's amendment like a new fix does.
    (lambda f: f.human_amendment_pending, Route.QUEUED),
    (_in("fixing", "applying"), Route.ATTACH),
    (lambda f: f.inflight_other_fix, Route.QUEUED),
    (lambda f: f.done_other_fix, Route.RETRY_AFTER_FIX),
    (lambda f: not f.budget_left, Route.BUDGET_EXHAUSTED),
    (lambda f: True, Route.AUTO_FIX),
)
KNOWN_STATES = frozenset({None, "queued", "applying", "fixing", "fixed", "recurred", "needs-owner"})


def decide_defect(facts: DefectFacts) -> Route:
    if facts.state not in KNOWN_STATES:
        raise ValueError(f"unknown defect state {facts.state!r}")
    return next(route for matches, route in DEFECT_ROUTES if matches(facts))


def decide_report(routes: list[Route]) -> Route:
    """One verdict for a whole report: any stop stops it (the first stop wins), else a wait, else it is carried out."""
    for route in routes:
        if route in STOPS:
            return route
    return Route.QUEUED if Route.QUEUED in routes else Route.AUTO_FIX


def render_release(route: Route, manifest_path: str, reporter: str) -> list[str]:
    """The copyable commands that release a stop (the one place they are spelled). The first one is the main path."""
    manifest, node = shlex.quote(manifest_path), shlex.quote(reporter)
    proposal = f"omac dag amend propose {manifest} --report-file <report.md> --docs <design.md> --blocked-node {node}"
    decide = [f"omac node abandon {manifest} {node}", f"omac node accept {manifest} {node}"]
    if route is Route.BUDGET_EXHAUSTED:
        return [f"omac defect grant {manifest} --reporter {node} --count 1", proposal, *decide]
    if route is Route.RECURRED:
        return [proposal, f"omac node retry {manifest} {node}", *decide]
    return [proposal, *decide, f"omac node retry {manifest} {node}"]


def stop_summary(route: Route, detail: str | None = None) -> str:
    text = {
        Route.NEEDS_OWNER: ui("The reported defect needs an owner decision (a trade-off, a contract gap, or defects.auto_fix is off).",
                              "上报的缺陷需要 owner 决策(取舍、合同缺口,或 defects.auto_fix 已关闭)。"),
        Route.UNROUTABLE: ui("The reported defect cannot be routed to a fix node: ", "上报的缺陷无法路由到修复节点: ") + (detail or ""),
        Route.GENERATION_CAP: ui("A fix node reported a defect; a fix is never fixed automatically.",
                                 "修复节点自己又上报了缺陷;修复节点不会被自动再修。"),
        Route.RECURRED: ui("The same defect appeared again after its fix was merged.", "同一缺陷在修复合并后再次出现。"),
        Route.BUDGET_EXHAUSTED: ui("The automatic fix budget for this reporter or for the whole DAG is used up.",
                                   "该报告者或整个 DAG 的自动修复预算已用完。"),
    }
    return text[route]
