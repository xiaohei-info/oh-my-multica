"""CodeHost — port for the code-hosting side of delivery: pull-request facts, CI checks and merging.

Kept separate from ``WorkItemStore`` so a work-item platform (Multica, later Linear or Jira) and a
code host (GitHub, later others) can be combined freely. Commands configured for checks and merges
run only inside adapters; the pipeline decides from the returned results and never shells out.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.workitem import (
    MergeCommandResult,
    PullRequestCheckResult,
    PullRequestObservation,
    PullRequestReadiness,
    PullRequestReadinessFailure,
)


class CodeHost(ABC):
    @abstractmethod
    def request_pull_request_merge(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> MergeCommandResult:
        """向远端发起一次合并请求。

        返回的是本地命令结果，不能据此推导 PR 已合入；调用方必须随后调用
        ``observe_pull_request``。命令执行属于适配器，pipeline/CLI 不得直调平台 CLI。
        """

    @abstractmethod
    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        """读取远端 PR 当前事实；不可确认时返回 ``UNKNOWN``，不得臆测已合入。"""

    @abstractmethod
    def check_pull_request(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> PullRequestCheckResult:
        """执行一次 PR 检查命令并返回本地结果；pipeline 只依据结果推进 CI 门。"""

    @abstractmethod
    def read_pull_request_readiness(
        self, pr_url: str,
    ) -> PullRequestReadiness | PullRequestReadinessFailure:
        """读取 PR draft/open 事实，供 develop authoring 的交付前置门使用。"""
