"""Commands a scripted agent runs inside a Run. They read the task with `omac work show`, exactly as the
issue text tells a real agent to, and submit through `omac work submit` (OMAC is the only write path).

usage: tools.py worker-submit <issue> | reviewer-submit <verdict> <issue> | dump <issue>
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # tests/ -> _support

import yaml  # noqa: E402

from _support.deliveries import review_report_for, verification_for  # noqa: E402
from omac.core.manifest import _load_contract  # noqa: E402


def omac(*args):
    # The daemon puts its own directory ahead of ours on PATH; the shim dir must win so `gh` is the test shim.
    env = {**os.environ, "PATH": os.environ["OMAC_FAKE_BIN"] + os.pathsep + os.environ["PATH"]}
    return subprocess.run([sys.executable, "-m", "omac", *args], capture_output=True, text=True, env=env)


def show(issue):
    done = omac("work", "show", issue, "--output", "json")
    if done.returncode:
        sys.exit(f"work show failed: {done.stderr[-400:]}")
    return json.loads(done.stdout)


def worker_submit(issue):
    info = show(issue)
    contract = _load_contract(info["context"]["contract"])
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(verification_for(contract, info["task"]["dag_key"]), fh)
    pr_url = f"https://github.com/example/repo/pull/{info['task']['issue_key'].split('-')[-1]}"
    gh_env = {**os.environ, "PATH": os.environ["OMAC_FAKE_BIN"] + os.pathsep + os.environ["PATH"]}
    subprocess.run(["gh", "__push", pr_url], env=gh_env, check=True)  # every submit follows a push: the PR head moves
    done = omac("work", "submit", issue, "--pr-url", pr_url, "--verification-file", fh.name)
    sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


def reviewer_submit(issue, verdict):
    info = show(issue)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(review_report_for(info["context"], verdict, info["task"]["bounces"]["review_bounce"] + 1), fh)
    done = omac("work", "submit", issue, "--verdict", verdict, "--report-file", fh.name)
    sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


def dump(issue):
    """Write `work show` for this issue to $OMAC_FAKE_DUMP/<issue>.json (for tests that inspect what an agent sees)."""
    Path(os.environ["OMAC_FAKE_DUMP"], f"{issue}.json").write_text(json.dumps(show(issue)))
    return 0


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "reviewer-submit":  # reviewer-submit <verdict> <issue>
        sys.exit(reviewer_submit(rest[1], rest[0]))
    sys.exit({"worker-submit": worker_submit, "dump": dump}[command](rest[0]))
