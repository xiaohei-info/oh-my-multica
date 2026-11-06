"""The one decision table of node hold / release: manifest facts in, a plan of manifest writes out.

No I/O and no platform access (enforced by tests/test_architecture.py). A held node stays ``todo`` with ``held`` set:
``core.graph.ready_nodes`` skips it and its downstream never sees a satisfied dependency, so neither is dispatched and
nothing cascades. Only a ``todo`` node can be held: a node that is running, waiting for a decision, or closed has a
platform side that a manifest flag would not stop.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from ..core import graph
from ..core.manifest import EvidenceMode
from ..i18n import ui
from .decide_effects import Plan, SaveState, SetDagFact, SetNode

AUTO_HELD_KEY = "auto_held"   # _dag.yaml fact: nodes the code has held once; a released node is never held again


class HoldRefused(Exception):
    """The hold or release does not apply to this node; the message says why and what to do instead."""


def _refusal_for(status: str, node_key: str) -> str:
    if status in graph.RUNNING:
        return ui(
            f"Node {node_key} is {status}: a node already in progress cannot be held. Let it finish, or stop it "
            "explicitly with `omac node abandon`; hold applies to nodes that have not been dispatched.",
            f"节点 {node_key} 处于 {status}:进行中的节点不能挂起。等它结束,或用 `omac node abandon` 显式放弃;"
            "hold 只适用于尚未派发的节点。")
    if status in graph.NEEDS_DECISION:
        return ui(
            f"Node {node_key} is {status} and already waits for a decision. Resolve it with `omac node retry` "
            "first; a retried (todo) node can then be held.",
            f"节点 {node_key} 处于 {status},已在等待决策。请先 `omac node retry`;重试成 todo 后才能挂起。")
    return ui(
        f"Node {node_key} is {status}: there is nothing left to hold.",
        f"节点 {node_key} 处于 {status}:没有可挂起的内容。")


def plan_hold(node_key: str, status: str, reason: str | None) -> Plan:
    """Hold a todo node (holding a held node again only replaces its reason)."""
    if status != "todo":
        raise HoldRefused(_refusal_for(status, node_key))
    return Plan((SetNode(node_key, {"held": True, "hold_reason": reason or None}), SaveState()), "held")


def plan_release(node_key: str, held: bool) -> Plan:
    if not held:
        raise HoldRefused(ui(
            f"Node {node_key} is not held; nothing to release.", f"节点 {node_key} 未被挂起,无需 release。"))
    return Plan((SetNode(node_key, {"held": False, "hold_reason": None}), SaveState()), "released")


def plan_auto_hold_live(ready: Iterable[str], nodes: Mapping[str, object], already_held: Iterable[str]) -> Plan | None:
    """Hold each ready node whose contract needs live evidence, once (``dispatch.hold_live``); None when there is none.

    A live node needs something the code cannot prepare (an account, a running service), so a person releases it. The
    node is remembered in ``auto_held``: after ``omac node release`` (or a later retry) it dispatches like any other.
    """
    seen = set(already_held)
    live = [
        key for key in ready
        if key not in seen and getattr(getattr(nodes[key], "contract", None), "evidence_mode", None) is EvidenceMode.LIVE]
    if not live:
        return None
    reason = ui(
        "contract.evidence_mode is live: prepare the live environment, then release (dispatch.hold_live)",
        "contract.evidence_mode 为 live:先准备好真实环境再 release(dispatch.hold_live)")
    return Plan((
        *(SetNode(key, {"held": True, "hold_reason": reason}) for key in live),
        SetDagFact(AUTO_HELD_KEY, sorted(seen | set(live))),
        SaveState(),
    ), "auto-held")
