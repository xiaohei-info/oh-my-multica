"""A working repository driven only through the `omac` CLI against the private Multica (tests/it)."""
import os
import subprocess
import sys
from pathlib import Path

import yaml

from _support.it_stack import Stack
from _support.state import save_literal


class Project:
    """A working repository with a bare remote and a `.omac` config, driven only through the `omac` CLI."""

    def __init__(self, root: Path, stack: Stack):
        self.stack, self.work = stack, root / "work"
        remote = root / "remote.git"
        self.work.mkdir()
        for args, cwd in ((["init", "--bare", "-q", "-b", "main", str(remote)], root),
                          (["init", "-q", "-b", "main"], self.work),
                          (["config", "user.email", "it@example.com"], self.work),
                          (["config", "user.name", "it"], self.work),
                          (["remote", "add", "origin", str(remote)], self.work)):
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        (self.work / ".omac").mkdir()
        (self.work / ".omac" / "config.yaml").write_text(yaml.safe_dump({
            "language": "en", "engine": "multica", "workspace": stack.workspace_id, "project": stack.project_id,
            "roles": {"planner": "alice", "orchestrator": "bob", "workers": ["alice", "bob"], "reviewers": ["bob", "carol"]},
            "defaults": {"max_parallel": 4, "poll_interval": 1, "coverage_gate": 90},
            "workflow": {"human_in_loop": False, "review": True, "acceptance_doc": False, "goal_required": False},
        }))
        subprocess.run("git add -A && git commit -qm init && git push -q -u origin main", shell=True, cwd=self.work,
                       check=True, capture_output=True)

    def configure(self, **updates) -> None:
        """Merge ``updates`` (nested dicts) into .omac/config.yaml; omac commits and pushes it before dispatching."""
        path = self.work / ".omac" / "config.yaml"
        config = yaml.safe_load(path.read_text())
        for key, value in updates.items():
            config[key] = {**config.get(key, {}), **value} if isinstance(value, dict) else value
        path.write_text(yaml.safe_dump(config))

    def manifest(self, name: str, nodes: list[dict]) -> str:
        path = self.work / ".omac" / f"{name}.yaml"
        save_literal(path, {"meta": {"name": name}, "nodes": nodes})
        return str(path)

    def omac(self, *args: str, env: dict | None = None, server_url: str | None = None, timeout: float = 180):
        full = {**os.environ, **self.stack.cli_env(server_url), **self.stack.omac_env(), **(env or {})}
        return subprocess.run([sys.executable, "-m", "omac", *args], cwd=self.work, env=full,
                              capture_output=True, text=True, timeout=timeout)


def contract(**extra) -> dict:
    return {"objective": "o", "acceptance": ["it works"], "non_goals": ["n"], "verification_commands": ["true"],
            "integration_gates": [{
                "name": "gate", "layer": "L1", "delivery_goal": "delivered", "source_of_truth": ["docs/a.md"],
                "covers": ["a"], "acceptance_refs": ["it works"], "commands": ["true"],
                "required_metrics": {"route_coverage": 100}, "artifacts": ["coverage.xml"]}],
            "pr_base": "main", **extra}
