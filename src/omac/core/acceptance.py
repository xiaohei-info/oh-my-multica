"""验收文档结构 —— plan 阶段(P3)产出,总控验收(P4)共用同一份 schema。

flows: [{id, name, actions: [{step, how, expected}]}]

plan 阶段把业务流程拆成 flows(每条 flow 一个可验收的端到端路径),
总控验收按 flow 逐项走查、记录 pass/fail。两边对齐同一个 id,故漏项可被
左移门机器校验打回(见 core.evidence.validate_acceptance_results)。
"""
from dataclasses import dataclass
from collections import defaultdict
import re

import yaml


@dataclass
class Action:
    id: str
    step: str
    how: str
    expected: str


@dataclass
class Flow:
    id: str
    name: str
    actions: list  # list[Action]


@dataclass
class AcceptanceDoc:
    schema: str
    flows: list  # list[Flow]

    @property
    def flow_ids(self) -> list:
        return [flow.id for flow in self.flows]

    @property
    def action_ids_by_flow(self) -> dict[str, list[str]]:
        return {
            flow.id: [action.id for action in flow.actions]
            for flow in self.flows
        }


_EMBEDDED_ACTION_ID = re.compile(r"Action ID=`([^`]+)`")


def _action_id(raw, flow_id: str, index: int) -> str:
    explicit = raw.get("id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    embedded = []
    for field in ("how", "expected"):
        value = raw.get(field)
        if isinstance(value, str):
            embedded.extend(_EMBEDDED_ACTION_ID.findall(value))
    identities = list(dict.fromkeys(embedded))
    if len(identities) > 1:
        raise ValueError(
            f"flow {flow_id} action {index} contains conflicting Action IDs: "
            + ", ".join(identities))
    if identities:
        return identities[0]
    # Legacy omac.acceptance/v1 documents did not require action.id. Keep them
    # readable with a deterministic migration identity instead of silently
    # dropping action-level responsibility.
    return f"{flow_id}/STEP-{index:02d}"


def _validate_expected_specificity(flows: list[Flow]) -> None:
    """拒绝用一个通用 expected 覆盖大多数不同 action。

    单纯非空不足以保证可执行性。若同一判据覆盖了大型文档中至少 30% 的
    actions，执行者只能回头解释 step，违反 action 自包含约束。
    """
    expected_steps = defaultdict(list)
    total = 0
    for flow in flows:
        for action in flow.actions:
            total += 1
            normalized = " ".join(action.expected.split())
            expected_steps[normalized].append(action.step)

    if total < 20:
        return

    repeated = max(expected_steps.values(), key=len)
    threshold = max(12, (total * 3 + 9) // 10)
    if len(repeated) < threshold:
        return

    raise ValueError(
        "action.expected is reused by "
        f"{len(repeated)}/{total} actions; each action expected must state "
        "its own observable outcome and failure criterion"
    )


def _load_action(raw, *, flow_id: str, index: int, require_id: bool) -> Action:
    if not isinstance(raw, dict):
        raise ValueError(f"action must be an object, got {type(raw).__name__}")
    if require_id and not (
        isinstance(raw.get("id"), str) and raw["id"].strip()
    ):
        raise ValueError(
            f"flow {flow_id} action {index} id is required by omac.acceptance/v2")
    step = raw.get("step")
    if not isinstance(step, str) or not step.strip():
        raise ValueError("action.step is required")
    how = raw.get("how")
    if not isinstance(how, str) or not how.strip():
        raise ValueError(f"action {step!r} how is required")
    expected = raw.get("expected")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError(f"action {step!r} expected is required")
    return Action(
        id=_action_id(raw, flow_id, index),
        step=step,
        how=how,
        expected=expected,
    )


def load_acceptance_doc(raw) -> AcceptanceDoc:
    """从 yaml.safe_load 后的 dict 构造 AcceptanceDoc;结构不全则报错。"""
    if not isinstance(raw, dict):
        raise ValueError(f"acceptance doc must be a mapping, got {type(raw).__name__}")
    schema = raw.get("schema", "omac.acceptance/v1")
    if schema not in {"omac.acceptance/v1", "omac.acceptance/v2"}:
        raise ValueError(f"unsupported acceptance schema: {schema}")
    flows_raw = raw.get("flows")
    if not isinstance(flows_raw, list) or not flows_raw:
        raise ValueError("acceptance doc flows must be a non-empty list")

    seen_ids = set()
    flows = []
    for f in flows_raw:
        if not isinstance(f, dict):
            raise ValueError("each flow must be an object")
        flow_id = f.get("id")
        if not isinstance(flow_id, str) or not flow_id.strip():
            raise ValueError("flow.id is required")
        if flow_id in seen_ids:
            raise ValueError(f"duplicate flow id: {flow_id}")
        seen_ids.add(flow_id)

        name = f.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"flow {flow_id} name is required")

        actions_raw = f.get("actions")
        if not isinstance(actions_raw, list) or not actions_raw:
            raise ValueError(f"flow {flow_id} actions must be a non-empty list")
        actions = [
            _load_action(
                action,
                flow_id=flow_id,
                index=index,
                require_id=schema == "omac.acceptance/v2",
            )
            for index, action in enumerate(actions_raw, start=1)
        ]
        action_ids = [action.id for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError(f"flow {flow_id} contains duplicate action ids")
        flows.append(Flow(
            id=flow_id,
            name=name,
            actions=actions,
        ))
    _validate_expected_specificity(flows)
    return AcceptanceDoc(schema=schema, flows=flows)


def load_acceptance_doc_file(path: str) -> AcceptanceDoc:
    with open(path, encoding="utf-8") as fh:
        return load_acceptance_doc(yaml.safe_load(fh))
