"""Commands a scripted agent runs inside a Run. They read the task with `omac work show`, exactly as the
issue text tells a real agent to, and submit through `omac work submit` (OMAC is the only write path).

usage: tools.py worker-submit [--no-push] [--empty-commands] <issue> | reviewer-submit [--repeat] <verdict> <issue> | dump <issue>
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


def worker_submit(issue, push=True, empty_commands=False):
    """--no-push keeps the PR head where it was (a revision on the same commit); --empty-commands submits a verification
    that lists no command, which the ordinary evidence gate must refuse."""
    info = show(issue)
    contract = _load_contract(info["context"]["contract"])
    verification = verification_for(contract, info["task"]["dag_key"])
    if empty_commands:
        verification = {**verification, "commands": [], "integration_gates": []}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(verification, fh)
    pr_url = f"https://github.com/example/repo/pull/{info['task']['issue_key'].split('-')[-1]}"
    gh_env = {**os.environ, "PATH": os.environ["OMAC_FAKE_BIN"] + os.pathsep + os.environ["PATH"]}
    if push or not _has_pushed(gh_env, pr_url):
        subprocess.run(["gh", "__push", pr_url], env=gh_env, check=True)  # a submit normally follows a push: the PR head moves
    done = omac("work", "submit", issue, "--pr-url", pr_url, "--verification-file", fh.name)
    sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


def _has_pushed(env, pr_url):
    """The PR must exist for `work submit`; a first submit with --no-push still needs one push."""
    state = Path(env["OMAC_GH_STATE"], "prs.json")
    return state.exists() and json.loads(state.read_text()).get(pr_url, {}).get("pushes", 0) > 0


def reviewer_submit(issue, verdict, repeat=False):
    info = show(issue)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(review_report_for(info["context"], verdict, info["task"]["bounces"]["review_bounce"] + 1, repeat=repeat), fh)
    done = omac("work", "submit", issue, "--verdict", verdict, "--report-file", fh.name)
    sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


def dump(issue):
    """Write `work show` for this issue to $OMAC_FAKE_DUMP/<issue>.json (for tests that inspect what an agent sees)."""
    Path(os.environ["OMAC_FAKE_DUMP"], f"{issue}.json").write_text(json.dumps(show(issue)))
    return 0


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "reviewer-submit":  # reviewer-submit [--repeat] <verdict> <issue>
        sys.exit(reviewer_submit(rest[-1], [part for part in rest[:-1] if part != "--repeat"][0], repeat="--repeat" in rest))
    if command == "worker-submit":  # worker-submit [--no-push] [--empty-commands] <issue>
        sys.exit(worker_submit(rest[-1], push="--no-push" not in rest, empty_commands="--empty-commands" in rest))
    sys.exit(dump(rest[0]))
