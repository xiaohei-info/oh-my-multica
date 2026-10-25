"""The smoke run's local spend guard: refuses at the cap, counts only the current month."""
import importlib.util
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("smoke_ledger", Path(__file__).parent / "smoke" / "ledger.py")
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

JAN = time.mktime(time.strptime("2026-01-15 12:00", "%Y-%m-%d %H:%M")) - time.timezone
FEB = time.mktime(time.strptime("2026-02-15 12:00", "%Y-%m-%d %H:%M")) - time.timezone


def test_allows_a_run_while_under_the_cap(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.record(path, JAN, 5.0)
    assert ledger.decide(path, JAN, 20.0)[0] is True


def test_refuses_once_the_month_reaches_the_cap_and_says_how_to_continue(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.record(path, JAN, 12.0)
    ledger.record(path, JAN, 8.0)
    allowed, message = ledger.decide(path, JAN, 20.0)
    assert allowed is False and "OMAC_SMOKE_MONTHLY_CAP_USD" in message


def test_the_expected_cost_of_this_run_counts_before_it_starts(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.record(path, JAN, 17.0)
    assert ledger.decide(path, JAN, 20.0, expected_usd=4.0)[0] is False


def test_a_new_month_starts_from_zero(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.record(path, JAN, 19.99)
    assert ledger.decide(path, FEB, 20.0)[0] is True


def test_a_tiny_cap_refuses_even_an_empty_ledger(tmp_path):
    assert ledger.decide(tmp_path / "none.jsonl", JAN, 0.01, expected_usd=1.0)[0] is False


def test_command_line_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("OMAC_SMOKE_LEDGER", str(tmp_path / "l.jsonl"))
    monkeypatch.setenv("OMAC_SMOKE_MONTHLY_CAP_USD", "0.01")
    assert ledger.main(["check", "--expected-usd", "1"]) == 1
    monkeypatch.setenv("OMAC_SMOKE_MONTHLY_CAP_USD", "20")
    assert ledger.main(["check"]) == 0
    assert ledger.main(["record", "--usd", "25", "--runs", "3"]) == 0
    assert ledger.main(["check"]) == 1
