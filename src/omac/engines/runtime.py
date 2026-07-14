"""AgentRuntime — 引擎执行面抽象接口:「用这个载荷唤醒这个 agent」。

数据面(WorkItemStore)保证工单与证据的读写;执行面保证被指派的 agent
真的会醒来干活。二者在某些平台上由同一系统一体承担(此时 wake 可能是
确认性 no-op),在纯 issue 平台上则需要独立的运行时方案(设计文档 §12.3)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .models import AgentInfo, AgentProvisionSpec, RuntimeTarget


class AgentRuntime(ABC):
    """执行面接口。措辞保持平台中立(§12.4)。"""

    @abstractmethod
    def wake(self, item_id: str, agent: str, role: str) -> None:
        """确保 agent 被唤醒处理该工作单元。

        契约:
        - 调用前数据面的 assign_work_item 已完成(载荷、metadata 就位);
        - 本方法返回即视为「唤醒已达成或已在途」,幂等——重复调用无副作用;
        - 阶段交接(评审/回退)同样经 assign + wake,同一工作单元反复唤醒
          不同 assignee 必须可行。
        - 无法达成唤醒时抛 PlatformError,编排层据此把节点标 blocked。
        """

    @abstractmethod
    def list_targets(self) -> List[RuntimeTarget]:
        """列出用户创建 Agent 时可选择的运行时目标。"""

    @abstractmethod
    def provision_agent(self, spec: AgentProvisionSpec) -> AgentInfo:
        """上传缺失 Skill、创建 Agent 并绑定 Skill；不得覆盖同名 Agent。"""

    @abstractmethod
    def describe(self) -> str:
        """一句话说明该运行时的唤醒机制(体检/诊断输出用)。"""
