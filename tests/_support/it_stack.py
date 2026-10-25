"""A private Multica for integration tests: compose project + daemon with a scripted Agent, torn down afterwards.

Isolation (PLAN 6.3.5): own compose project name and label ``omac.it=1``, random loopback port, random secrets,
a private HOME (so the ``multica`` CLI never reads ``~/.multica``), a daemon under its own profile and id.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IT_DIR = Path(__file__).resolve().parent.parent / "it"
COMPOSE = IT_DIR / "compose.yml"
LABEL = "omac.it=1"


class StackError(RuntimeError):
    pass


def require_docker() -> None:
    """Integration tests need Docker; fail with the fix instead of skipping silently."""
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=30)
        subprocess.run(["docker", "compose", "version"], check=True, capture_output=True, timeout=30)
        shutil.which("multica") or (_ for _ in ()).throw(FileNotFoundError("multica"))
    except (OSError, subprocess.SubprocessError) as exc:
        raise StackError(
            "Integration tests (tests/it) need Docker with the compose plugin and the `multica` CLI on PATH. "
            "Start Docker (or OrbStack) and install the CLI, then rerun `python -m pytest tests/it`. "
            f"Unavailable: {exc}") from exc


class Stack:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.run_id = secrets.token_hex(3)
        self.project = f"omac-it-{os.getpid()}-{self.run_id}"
        self.home = self.root / "home"
        self.env_secret, self.code = secrets.token_hex(16), f"{secrets.randbelow(10**6):06d}"
        self.url = ""
        self.token = ""
        self.workspace_id = ""
        self.project_id = ""
        self.daemon: subprocess.Popen | None = None
        self.agents: dict[str, str] = {}
        self.scenarios: dict[str, Path] = {}
        self.fake_log = self.root / "fake-agent.log"
        self.profile = f"it{self.run_id}"
        self.bin_dir = self.root / "fakebin"
        self.gh_state = self.root / "gh"
        self.dump_dir = self.root / "dump"

    # ---- compose
    def _compose(self, *args: str, check: bool = True):
        env = {**os.environ, "OMAC_IT_SECRET": self.env_secret, "OMAC_IT_CODE": self.code}
        return subprocess.run(["docker", "compose", "-p", self.project, "-f", str(COMPOSE), *args],
                              env=env, check=check, capture_output=True, text=True, timeout=300)

    def up(self) -> None:
        self.home.mkdir(parents=True)
        try:
            self._compose("up", "-d", "--wait", "--wait-timeout", "120")
        except subprocess.CalledProcessError as exc:
            self.down()
            raise StackError(f"docker compose up failed:\n{exc.stderr[-2000:]}") from exc
        self.url = "http://" + self._compose("port", "backend", "8080").stdout.strip()
        self.token = self.login()
        self.workspace_id = self.api("POST", "/api/workspaces", {"name": "it-main", "slug": "it-main"})["id"]
        self._write_cli_config()
        self.project_id = json.loads(self.multica("project", "create", "--title", "it-project", "--output", "json"))["id"]
        self._start_daemon()

    def down(self) -> None:
        if self.daemon and self.daemon.poll() is None:
            os.killpg(self.daemon.pid, signal.SIGTERM)
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.daemon.pid, signal.SIGKILL)
        self._compose("down", "-v", "--remove-orphans", check=False)

    # ---- HTTP API (only for arranging the instance; OMAC itself goes through the adapter)
    def api(self, method: str, path: str, body: dict | None = None, *, token: str | None = None, base: str | None = None):
        request = urllib.request.Request(
            (base or self.url) + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", **({"Authorization": f"Bearer {token or self.token}"} if (token or self.token) else {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise StackError(f"{method} {path} -> {exc.code}: {exc.read()[:300]!r}") from exc
        return json.loads(payload) if payload else None

    def login(self) -> str:
        email = f"it-{secrets.token_hex(4)}@example.com"  # send-code is rate limited per address
        self.api("POST", "/auth/send-code", {"email": email}, token="")
        return self.api("POST", "/auth/verify-code", {"email": email, "code": self.code}, token="")["token"]

    # ---- CLI environment used by OMAC (never ~/.multica)
    def cli_env(self, server_url: str | None = None) -> dict[str, str]:
        return {"HOME": str(self.home), "MULTICA_CONFIG_PATH": str(self.home / ".multica" / "config.json"),
                "MULTICA_SERVER_URL": server_url or self.url, "MULTICA_WORKSPACE_ID": self.workspace_id}

    def omac_env(self) -> dict[str, str]:
        """Environment under which `python -m omac` talks to this instance (also given to the scripted agents)."""
        src = str(Path(__file__).resolve().parent.parent.parent / "src")
        return {"OMAC_ENGINE": "multica", "OMAC_WORKSPACE_ID": self.workspace_id,
                "OMAC_PROJECT_ID": self.project_id, "PYTHONPATH": src,
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}", "OMAC_GH_STATE": str(self.gh_state),
                "OMAC_FAKE_BIN": str(self.bin_dir), "OMAC_FAKE_DUMP": str(self.dump_dir)}

    def _write_cli_config(self) -> None:
        for directory in (self.home / ".multica", self.home / ".multica" / "profiles" / self.profile):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "config.json").write_text(json.dumps(
                {"server_url": self.url, "token": self.token, "workspace_id": self.workspace_id}))

    def multica(self, *args: str, profile: bool = False) -> str:
        cmd = ["multica", *(["--profile", self.profile] if profile else []), *args]
        done = subprocess.run(cmd, env={**os.environ, **self.cli_env()}, capture_output=True, text=True, timeout=60)
        if done.returncode:
            raise StackError(f"{' '.join(cmd)} failed: {done.stderr[-500:]}")
        return done.stdout

    # ---- daemon + scripted agents
    def _start_daemon(self) -> None:
        self.bin_dir.mkdir()
        self.gh_state.mkdir()
        self.dump_dir.mkdir()
        for shim in ("claude", "gh"):
            target = self.bin_dir / shim
            target.write_text((IT_DIR / "agent" / shim).read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
            target.chmod(0o755)
        env = {**os.environ, **self.cli_env(), **self.omac_env(),
               "MULTICA_DAEMON_ID": f"omac-it-{self.run_id}", "MULTICA_DAEMON_POLL_INTERVAL": "200ms",
               "MULTICA_WORKSPACES_ROOT": str(self.root / "runs"), "OMAC_FAKE_LOG": str(self.fake_log)}
        self.daemon = subprocess.Popen(
            ["multica", "--profile", self.profile, "daemon", "start", "--foreground", "--no-auto-update", "--no-auto-reload"],
            env=env, stdout=(self.root / "daemon.log").open("w"), stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            runtimes = json.loads(self.multica("runtime", "list", "--output", "json", profile=True) or "[]")
            claude = [r for r in runtimes if "claude" in (r.get("launch_header") or "")]
            if claude:
                self.runtime_id = claude[0]["id"]
                return
            time.sleep(0.5)
        raise StackError(f"The Multica daemon did not register the scripted agent runtime; see {self.root / 'daemon.log'}")

    def agent(self, name: str, scenario: list[list[dict]] | None = None) -> str:
        """Create (or return) an agent whose Runs execute ``scenario`` (a list of attempts, each a list of steps)."""
        path = self.root / f"scenario-{name}.json"
        path.write_text(json.dumps({"attempts": scenario or []}))
        for counter in (Path(str(path) + ".count"),):
            counter.unlink(missing_ok=True)
        self.scenarios[name] = path
        if name not in self.agents:
            created = json.loads(self.multica(
                "agent", "create", "--name", name, "--runtime-id", self.runtime_id,
                "--custom-env", json.dumps({"OMAC_SCENARIO": str(path)}), "--output", "json"))
            self.agents[name] = created["id"]
        return self.agents[name]

    def set_scenario(self, name: str, attempts: list[list[dict]]) -> None:
        self.scenarios[name].write_text(json.dumps({"attempts": attempts}))
        Path(str(self.scenarios[name]) + ".count").unlink(missing_ok=True)

    def tool(self, *args: str) -> dict:
        """A scenario step that runs a scripted-agent command (tests/it/agent/tools.py); {issue} is filled by the Run."""
        return {"run": [sys.executable, str(IT_DIR / "agent" / "tools.py"), *args, "{issue}"]}

    def review(self, verdict: str) -> dict:
        return self.tool("reviewer-submit", verdict)

    def gh_calls(self, verb: str) -> list[list[str]]:
        """Arguments of every `gh pr <verb>` OMAC ran, oldest first."""
        path = self.gh_state / "calls.log"
        rows = [json.loads(line)["args"] for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row[:2] == ["pr", verb]]

    def gh_behavior(self, **behavior) -> None:
        (self.gh_state / "behavior.json").write_text(json.dumps(behavior))

    def fake_runs(self) -> list[dict]:
        return [json.loads(line) for line in self.fake_log.read_text().splitlines()] if self.fake_log.exists() else []
