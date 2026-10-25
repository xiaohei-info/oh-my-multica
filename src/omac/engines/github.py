"""GitHub adapter of the CodeHost port: ``gh`` for PR facts, configured shell commands for checks/merges."""
from __future__ import annotations

import json
import subprocess

from ..core.workitem import (
    MergeCommandResult,
    PullRequestCheckResult,
    PullRequestObservation,
    PullRequestReadiness,
    PullRequestReadinessFailure,
    PullRequestReadinessFailureKind,
    PullRequestState,
)
from .codehost import CodeHost

PR_VIEW_FIELDS = "state,mergedAt,autoMergeRequest,mergeStateStatus,headRefOid"


class GitHubCodeHost(CodeHost):
    def request_pull_request_merge(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> MergeCommandResult:
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return MergeCommandResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return MergeCommandResult(False, None, str(exc))
        return MergeCommandResult(
            proc.returncode == 0, proc.returncode,
            (proc.stdout or "") + (proc.stderr or ""))

    def observe_pull_request(self, pr_url: str) -> PullRequestObservation:
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", pr_url, "--json",
                 PR_VIEW_FIELDS],
                capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return PullRequestObservation(PullRequestState.UNKNOWN, detail=str(exc))
        if proc.returncode != 0:
            return PullRequestObservation(
                PullRequestState.UNKNOWN, detail=(proc.stderr or proc.stdout or "").strip())
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            return PullRequestObservation(PullRequestState.UNKNOWN, detail=str(exc))
        state = str(payload.get("state") or "").upper()
        merged_at = payload.get("mergedAt")
        if state == "MERGED":
            return PullRequestObservation(PullRequestState.MERGED, merged_at=merged_at)
        if state == "OPEN":
            merge_state = str(payload.get("mergeStateStatus") or "").upper()
            if payload.get("autoMergeRequest") or merge_state == "QUEUED":
                return PullRequestObservation(PullRequestState.PENDING)
            if merge_state == "UNKNOWN" or not merge_state:
                return PullRequestObservation(
                    PullRequestState.UNKNOWN,
                    detail="missing or unknown mergeStateStatus")
            return PullRequestObservation(PullRequestState.OPEN)
        if state == "CLOSED":
            return PullRequestObservation(PullRequestState.CLOSED_UNMERGED)
        return PullRequestObservation(PullRequestState.UNKNOWN, detail=f"unexpected PR state: {state}")

    def check_pull_request(
        self, pr_url: str, command: str, timeout_seconds: int,
    ) -> PullRequestCheckResult:
        try:
            proc = subprocess.run(
                command.replace("{pr_url}", pr_url), shell=True,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            output = "".join(
                stream.decode("utf-8", errors="replace")
                if isinstance(stream, bytes) else stream or ""
                for stream in (exc.stdout, exc.stderr))
            return PullRequestCheckResult(False, None, output, timed_out=True)
        except FileNotFoundError as exc:
            return PullRequestCheckResult(False, None, str(exc))
        return PullRequestCheckResult(
            proc.returncode == 0, proc.returncode,
            (proc.stdout or "") + (proc.stderr or ""))

    def read_pull_request_readiness(
        self, pr_url: str,
    ) -> PullRequestReadiness | PullRequestReadinessFailure:
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", pr_url, "--json", "isDraft,state,headRefOid"],
                capture_output=True, text=True, timeout=30)
        except FileNotFoundError as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MISSING_CLI, str(exc))
        except subprocess.TimeoutExpired as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.TIMEOUT, str(exc))
        if proc.returncode != 0:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.COMMAND,
                (proc.stderr or proc.stdout or "").strip())
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED, str(exc))
        if not isinstance(payload, dict):
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED, "readiness payload is not an object")
        is_draft = payload.get("isDraft")
        state = payload.get("state")
        head_sha = payload.get("headRefOid")
        if (
            not isinstance(is_draft, bool)
            or not isinstance(state, str) or not state
            or not isinstance(head_sha, str) or not head_sha
        ):
            return PullRequestReadinessFailure(
                PullRequestReadinessFailureKind.MALFORMED,
                "readiness payload is missing typed isDraft/state/headRefOid fields")
        return PullRequestReadiness(is_draft, state, head_sha=head_sha)
