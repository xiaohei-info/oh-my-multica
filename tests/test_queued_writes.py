"""cli.queued_writes: with a `dag run` holding the manifest lock, node/defect writes queue and the runner applies them
at its tick boundary (design §4.5). The runner is played by the test: it holds the lock and drains the queue."""
import json
import threading
import time

import pytest
from _support.state import save_literal

from omac.cli import exit_codes
from omac.cli.commands.dag import _apply_queued_writes
from omac.cli.main import main
from omac.cli.queued_writes import drain_queued_writes
from omac.core import commandqueue
from omac.core.manifest import load_manifest, manifest_write_lock


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    for name in ("OMAC_ENGINE", "OMAC_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def path(tmp_path):
    return save_literal(tmp_path / "m.yaml", {"meta": {}, "nodes": [
        {"id": "root", "worker": "alice", "status": "done", "work_item_id": "1"},
        {"id": "n", "worker": "bob", "blocked_by": ["root"], "status": "todo"},
    ]})


def _held(path):
    return load_manifest(path).nodes["n"].held


def test_a_free_lock_applies_directly_as_before(path):
    assert main(["node", "hold", path, "n"]) == exit_codes.OK
    assert _held(path) is True
    assert commandqueue.pending(path) == []


def test_no_wait_queues_and_the_runner_applies_at_the_tick_boundary(path, capsys):
    with manifest_write_lock(path):
        assert main(["node", "hold", path, "n", "--reason", "why", "--no-wait"]) == exit_codes.OK
        queued = json.loads(capsys.readouterr().out)
        assert queued["state"] == "pending" and _held(path) is False
        assert drain_queued_writes(path) == 1
    assert (_held(path), load_manifest(path).nodes["n"].hold_reason) == (True, "why")
    assert commandqueue.pending(path) == []
    assert commandqueue.read_receipt(path, queued["request_id"])["state"] == "done"


def test_a_waiting_command_gets_the_runners_result_and_exit_code(path, capsys):
    ready = threading.Event()

    def runner():
        with manifest_write_lock(path):
            ready.set()
            while not commandqueue.pending(path):
                time.sleep(0.05)
            drain_queued_writes(path)

    thread = threading.Thread(target=runner)
    thread.start()
    ready.wait()
    code = main(["node", "hold", path, "n"])
    thread.join()

    assert code == exit_codes.OK
    assert json.loads(capsys.readouterr().out)["held"] is True and _held(path) is True


def test_a_queued_write_is_revalidated_when_applied_and_fails_with_the_commands_own_error(path, capsys):
    with manifest_write_lock(path):
        main(["node", "release", path, "n", "--no-wait"])          # n is not held: refused when applied
        request_id = json.loads(capsys.readouterr().out)["request_id"]
        drain_queued_writes(path)
    # the command side replays the failure: same exit code as the direct command
    assert commandqueue.read_receipt(path, request_id)["result"]["exit_code"] == exit_codes.VALIDATION

    ready = threading.Event()

    def runner():
        with manifest_write_lock(path):
            ready.set()
            while not commandqueue.pending(path):
                time.sleep(0.05)
            drain_queued_writes(path)

    thread = threading.Thread(target=runner)
    thread.start()
    ready.wait()
    assert main(["node", "release", path, "n"]) == exit_codes.VALIDATION
    thread.join()
    assert "n" in capsys.readouterr().err                      # the command's own refusal text reaches the caller


def test_a_runner_that_exited_leaves_the_write_to_the_waiting_command(path, capsys):
    """The lock frees without anyone draining: the waiting command takes the lock itself and applies the queue."""
    ready = threading.Event()

    def runner():
        with manifest_write_lock(path):
            ready.set()
            while not commandqueue.pending(path):
                time.sleep(0.05)

    thread = threading.Thread(target=runner)
    thread.start()
    ready.wait()
    code = main(["node", "hold", path, "n"])
    thread.join()

    assert code == exit_codes.OK and _held(path) is True


def test_a_timeout_exits_10_and_leaves_the_write_queued(path, capsys):
    with manifest_write_lock(path):
        code = main(["node", "hold", path, "n", "--wait-timeout", "0"])
        assert len(commandqueue.pending(path)) == 1
    assert code == exit_codes.IN_PROGRESS and _held(path) is False


def test_writes_apply_in_arrival_order(path, capsys):
    with manifest_write_lock(path):
        main(["node", "hold", path, "n", "--no-wait"])
        main(["node", "release", path, "n", "--no-wait"])
        assert drain_queued_writes(path) == 2
    assert _held(path) is False


def test_an_interrupted_application_is_reported_unknown_and_never_replayed(path):
    with manifest_write_lock(path):
        request_id = commandqueue.enqueue(path, "node", {"action": "hold", "manifest": path, "node_key": "n"}, ".")
        commandqueue._write_receipt(path, request_id, "applying")     # the runner died after the intent, before the result
        calls = []
        commandqueue.drain(path, lambda request: calls.append(request) or {})
    receipt = commandqueue.read_receipt(path, request_id)
    assert calls == [] and _held(path) is False
    assert receipt["result"]["error"]["class"] == "unknown"
    assert commandqueue.pending(path) == []


def test_a_finished_receipt_with_a_surviving_request_is_not_applied_twice(path):
    with manifest_write_lock(path):
        request_id = commandqueue.enqueue(path, "node", {"action": "hold", "manifest": path, "node_key": "n"}, ".")
        commandqueue._write_receipt(path, request_id, "done", {"exit_code": 0})
        calls = []
        commandqueue.drain(path, lambda request: calls.append(request) or {})
    assert calls == [] and commandqueue.pending(path) == []


def test_the_runner_reloads_its_manifest_after_applying_a_queued_write(path):
    with manifest_write_lock(path):
        stale = load_manifest(path)
        main(["node", "hold", path, "n", "--no-wait"])
        fresh = _apply_queued_writes(stale, path)
        assert fresh is not stale and fresh.nodes["n"].held is True
        assert _apply_queued_writes(fresh, path) is fresh               # nothing queued: no reload
