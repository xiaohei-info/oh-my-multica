"""Commands a scripted agent runs inside a Run. They read the task with `omac work show`, exactly as the
issue text tells a real agent to, and submit through `omac work submit` (OMAC is the only write path).

usage: tools.py worker-submit <issue>
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # tests/ -> _support

import yaml  # noqa: E402

from _support.deliveries import verification_for  # noqa: E402
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
    done = omac("work", "submit", issue, "--pr-url", f"https://github.com/example/repo/pull/{info['task']['issue_key'].split('-')[-1]}",
                "--verification-file", fh.name)
    sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


if __name__ == "__main__":
    command, issue = sys.argv[1:3]
    sys.exit({"worker-submit": worker_submit}[command](issue))
