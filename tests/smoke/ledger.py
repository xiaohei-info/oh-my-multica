"""Monthly spend guard for the weekly smoke run (PLAN phase 10).

The hard limit is a metered API key with a monthly cap set in the provider console. This ledger is the second,
local layer: it lives outside the repository (default ~/.omac-smoke/ledger.jsonl, override OMAC_SMOKE_LEDGER),
refuses to start a run once the month's recorded spend reaches the cap, and is fed from the Agent's own cost
reports. When a Run reports no cost, a flat estimate is recorded instead and marked as such.

    python tests/smoke/ledger.py check [--expected-usd 4]     exit 0 = go, 1 = refuse
    python tests/smoke/ledger.py record --usd 1.37 [--estimated] [--runs 3]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_CAP_USD = 20.0


def ledger_path() -> Path:
    return Path(os.environ.get("OMAC_SMOKE_LEDGER") or Path.home() / ".omac-smoke" / "ledger.jsonl")


def cap_usd() -> float:
    return float(os.environ.get("OMAC_SMOKE_MONTHLY_CAP_USD", DEFAULT_CAP_USD))


def month_of(timestamp: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(timestamp))


def month_total(path: Path, now: float) -> float:
    if not path.exists():
        return 0.0
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return sum(float(r["usd"]) for r in rows if month_of(r["at"]) == month_of(now))


def decide(path: Path, now: float, cap: float, expected_usd: float = 0.0) -> tuple[bool, str]:
    spent = month_total(path, now)
    if spent + expected_usd >= cap:
        return False, (f"Smoke run refused: ${spent:.2f} already spent this month (+ ${expected_usd:.2f} expected) "
                       f"reaches the ${cap:.2f} cap. Raise OMAC_SMOKE_MONTHLY_CAP_USD, or wait for next month; "
                       f"the ledger is {path}.")
    return True, f"Smoke run allowed: ${spent:.2f} of ${cap:.2f} spent this month."


def record(path: Path, now: float, usd: float, *, estimated: bool = False, runs: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"at": now, "usd": usd, "estimated": estimated, "runs": runs}) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--expected-usd", type=float, default=0.0)
    rec = sub.add_parser("record")
    rec.add_argument("--usd", type=float, required=True)
    rec.add_argument("--estimated", action="store_true")
    rec.add_argument("--runs", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "check":
        go, message = decide(ledger_path(), time.time(), cap_usd(), args.expected_usd)
        print(message, file=sys.stdout if go else sys.stderr)
        return 0 if go else 1
    record(ledger_path(), time.time(), args.usd, estimated=args.estimated, runs=args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
