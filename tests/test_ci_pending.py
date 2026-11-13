"""A pending CI check is observed once per tick: it neither blocks the Loop nor counts as a failure (设计 §7.3)."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omac.core.manifest import Manifest, Node, local_runtime_file, write_local_runtime
from omac.core.workitem import PullRequestCheckResult
from omac.engines.codehost import CodeHost
from omac.pipeline import delivery
from omac.pipeline.decide_ci import ci_wait_expired


@pytest.mark.parametrize("waited,timeout,expired", [(0, 60, False), (59.9, 60, False), (60, 60, True), (500, 60, True)])
def test_the_wait_expires_at_the_configured_timeout(waited, timeout, expired):
    assert ci_wait_expired(waited, timeout) is expired


class _Host(CodeHost):
    def __init__(self, result):
        self.result, self.calls = result, []

    def check_pull_request(self, pr_url, command, timeout_seconds, pending_exit_code=None):
        self.calls.append((command, pending_exit_code))
        return self.result

    def observe_pull_request(self, _url):
        from omac.core.workitem import PullRequestObservation, PullRequestState
        return PullRequestObservation(PullRequestState.OPEN)

    request_pull_request_merge = read_pull_request_readiness = None


class _Store:
    def __init__(self):
        self.item = SimpleNamespace(
            artifacts={"pr_url": "https://github.com/acme/repo/pull/1"}, bounces=SimpleNamespace(ci=0))

    def get_work_item(self, _id):
        return self.item

    def __getattr__(self, name):                 # any platform write would be a state change a pending check must not make
        raise AssertionError(f"pending check must not call store.{name}")


CI = {"ci": {"check_command": "sh ci.sh {pr_url}", "pending_exit_code": 8, "timeout_minutes": 1}}
PENDING = PullRequestCheckResult(False, 8, "build running", pending=True)


def _advance(host, manifest, path):
    return delivery.advance_delivery(
        CI, manifest, "n", _Store(), None, host, {"ci": 2}, manifest_path=path)


@pytest.fixture
def manifest(tmp_path):
    path = str(tmp_path / "m.yaml")
    return Manifest(meta={}, nodes={"n": Node(id="n", worker="w", work_item_id="i1", status="in_progress")}), path


def test_a_pending_check_changes_nothing_and_passes_the_configured_exit_code(manifest):
    m, path = manifest
    host = _Host(PENDING)

    assert _advance(host, m, path) == "pending"

    assert m.nodes["n"].status == "in_progress" and host.calls == [("sh ci.sh {pr_url}", 8)]


def test_a_pending_check_past_its_timeout_bounces_as_timed_out(manifest, monkeypatch):
    m, path = manifest
    _advance(_Host(PENDING), m, path)                       # records the first pending observation
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    write_local_runtime(path, "ci-wait", {"i1@": long_ago})
    seen = []
    monkeypatch.setattr(delivery, "apply", lambda plan, *a, **k: seen.append(plan))

    outcome = _advance(_Host(PENDING), m, path)

    assert outcome == "bounce" and any("timed out" in str(p) for p in seen)
    assert "i1@" not in json.load(open(local_runtime_file(path, "ci-wait")))


def test_a_green_result_clears_the_recorded_wait(manifest):
    m, path = manifest
    _advance(_Host(PENDING), m, path)

    class Store(_Store):
        def __getattr__(self, name):
            return lambda *a, **k: None

    outcome = delivery.advance_delivery(
        CI, m, "n", Store(), None, _Host(PullRequestCheckResult(True, 0, "ok")), {"ci": 2}, manifest_path=path)

    assert outcome == "pass"
    assert "i1@" not in json.load(open(local_runtime_file(path, "ci-wait")))


@pytest.mark.parametrize("passed,conflicting,expected", [
    (False, True, True), (False, False, False), (True, True, False), (True, False, False)])
def test_only_an_unpassed_check_on_a_conflicting_pr_is_a_conflict(passed, conflicting, expected):
    from omac.pipeline.decide_ci import ci_unpassed_is_conflict
    assert ci_unpassed_is_conflict(passed=passed, conflicting=conflicting) is expected


@pytest.mark.parametrize("check", [
    PENDING, PullRequestCheckResult(False, 1, "no checks reported on the 'x' branch")])
def test_a_conflicting_pr_bounces_to_the_worker_with_a_rebase_instruction_instead_of_waiting(manifest, check):
    from omac.core.workitem import PullRequestObservation, PullRequestState
    m, path = manifest
    host = _Host(check)
    host.observe_pull_request = lambda _url: PullRequestObservation(PullRequestState.OPEN, conflicting=True)
    store, comments = _Store(), []
    store.add_comment = lambda _id, text: comments.append(text)
    store.__class__ = type("S", (_Store,), {"__getattr__": lambda self, n: (lambda *a, **k: None)})
    out = delivery.advance_delivery(CI, m, "n", store, None, host, {"ci": 2}, manifest_path=path)
    assert out == "bounce"
    assert any("rebase" in c.lower() or "Rebase" in c for c in comments)


def test_a_non_conflicting_pending_check_still_waits(manifest):
    from omac.core.workitem import PullRequestObservation, PullRequestState
    m, path = manifest
    host = _Host(PENDING)
    host.observe_pull_request = lambda _url: PullRequestObservation(PullRequestState.OPEN)
    assert _advance(host, m, path) == "pending"


def test_a_lost_wait_record_restarts_the_clock_instead_of_expiring_or_never_expiring(manifest):
    m, path = manifest
    write_local_runtime(path, "ci-wait", {"i1@": "not-a-timestamp"})
    assert _advance(_Host(PENDING), m, path) == "pending"
    assert datetime.fromisoformat(json.load(open(local_runtime_file(path, "ci-wait")))["i1@"]).tzinfo is not None


def test_a_wait_left_by_an_earlier_head_does_not_time_out_a_new_delivery(manifest):
    m, path = manifest
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    write_local_runtime(path, "ci-wait", {"i1@old": long_ago})
    store = _Store()
    store.item.artifacts["head_sha"] = "new"
    out = delivery.advance_delivery(CI, m, "n", store, None, _Host(PENDING), {"ci": 2}, manifest_path=path)
    assert out == "pending"


def test_a_blocking_custom_command_keeps_its_verdict_on_a_conflicting_pr(manifest):
    from omac.core.workitem import PullRequestObservation, PullRequestState
    m, path = manifest
    host = _Host(PullRequestCheckResult(False, 1, "lint failed"))
    host.observe_pull_request = lambda _url: PullRequestObservation(PullRequestState.OPEN, conflicting=True)
    store, comments = _Store(), []
    store.add_comment = lambda _id, text: comments.append(text)
    store.__class__ = type("S", (_Store,), {"__getattr__": lambda self, n: (lambda *a, **k: None)})
    custom = {"ci": {"check_command": "sh ci.sh {pr_url}", "timeout_minutes": 1}}
    assert delivery.advance_delivery(custom, m, "n", store, None, host, {"ci": 2}, manifest_path=path) == "bounce"
    assert any("lint failed" in c for c in comments) and not any("conflict" in c.lower() for c in comments)
