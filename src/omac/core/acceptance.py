"""验收文档结构 —— plan 阶段(P3)产出,总控验收(P4)共用同一份 schema。

flows: [{id, name, actions: [{step, how, expected}]}]

plan 阶段把业务流程拆成 flows(每条 flow 一个可验收的端到端路径),
总控验收按 flow 逐项走查、记录 pass/fail。两边对齐同一个 id,故漏项可被
左移门机器校验打回(见 core.evidence.validate_acceptance_results)。
"""
from dataclasses import dataclass
from collections import defaultdict

import yaml


@dataclass
class Action:
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
    flows: list  # list[Flow]

    @property
    def flow_ids(self) -> list:
        return [flow.id for flow in self.flows]


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


def _load_action(raw) -> Action:
    if not isinstance(raw, dict):
        raise ValueError(f"action must be an object, got {type(raw).__name__}")
    step = raw.get("step")
    if not isinstance(step, str) or not step.strip():
        raise ValueError("action.step is required")
    how = raw.get("how")
    if not isinstance(how, str) or not how.strip():
        raise ValueError(f"action {step!r} how is required")
    expected = raw.get("expected")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError(f"action {step!r} expected is required")
    return Action(step=step, how=how, expected=expected)


def load_acceptance_doc(raw) -> AcceptanceDoc:
    """从 yaml.safe_load 后的 dict 构造 AcceptanceDoc;结构不全则报错。"""
    if not isinstance(raw, dict):
        raise ValueError(f"acceptance doc must be a mapping, got {type(raw).__name__}")
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
        flows.append(Flow(
            id=flow_id,
            name=name,
            actions=[_load_action(a) for a in actions_raw],
        ))
    _validate_expected_specificity(flows)
    return AcceptanceDoc(flows=flows)


def load_acceptance_doc_file(path: str) -> AcceptanceDoc:
    with open(path, encoding="utf-8") as fh:
        return load_acceptance_doc(yaml.safe_load(fh))
