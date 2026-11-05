"""The one decision table of node hold / release: manifest facts in, a plan of manifest writes out.

No I/O and no platform access (enforced by tests/test_architecture.py). A held node stays ``todo`` with ``held`` set:
``core.graph.ready_nodes`` skips it and its downstream never sees a satisfied dependency, so neither is dispatched and
nothing cascades. Only a ``todo`` node can be held: a node that is running, waiting for a decision, or closed has a
platform side that a manifest flag would not stop.
"""
from __future__ import annotations

from ..core import graph
from ..i18n import ui
from .decide_effects import Plan, SaveState, SetNode


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
