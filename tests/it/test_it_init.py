"""`omac init` (flags, wizard and --check) against a real Multica: the workspace, project and agent pool come from the server."""
import builtins
import sys

import pytest
import yaml

from omac.cli import exit_codes
from omac.cli.main import main
from omac.engines.models import WorkspaceInfo


@pytest.fixture()
def here(tmp_path, monkeypatch, cli_env):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def config_of(path):
    return yaml.safe_load((path / ".omac" / "config.yaml").read_text())


def flags(stack, **over):
    values = {"--engine": "multica", "--workspace": stack.workspace_id, "--project": stack.project_id, "--planner": "alice",
              "--orchestrator": "bob", "--workers": "charlie", "--reviewers": "alice,bob", **over}
    return ["init", *[part for pair in values.items() for part in pair if part is not None]]


def test_the_server_lists_its_workspaces(store, stack):
    infos = store.list_workspaces()

    assert infos and isinstance(infos[0], WorkspaceInfo) and any(w.id == stack.workspace_id for w in infos)


def test_flags_write_a_valid_config_and_the_check_passes(here, stack, capsys):
    code = main(flags(stack, **{"--max-parallel": "7", "--retry-ci": "1", "--retry-review": "2", "--retry-merge": "0"}))

    cfg = config_of(here)
    assert code == exit_codes.OK and (cfg["engine"], cfg["workspace"], cfg["project"]) == ("multica", stack.workspace_id, stack.project_id)
    assert (cfg["roles"]["planner"], cfg["roles"]["orchestrator"], cfg["roles"]["workers"], cfg["roles"]["reviewers"]) == (
        "alice", "bob", ["charlie"], ["alice", "bob"])
    assert "acceptor" not in cfg["roles"] and cfg["defaults"]["max_parallel"] == 7 and cfg["retry"] == {"worker": 3, "ci": 1, "review": 2, "merge": 0}
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.OK


def test_an_acceptor_is_optional_and_kept_when_given(here, stack):
    assert main(flags(stack, **{"--acceptor": "bob"})) == exit_codes.OK

    assert config_of(here)["roles"]["acceptor"] == "bob"


def test_a_role_outside_the_agent_pool_is_refused_and_nothing_is_written(here, stack, capsys):
    assert main(flags(stack, **{"--planner": "ghost"})) == exit_codes.VALIDATION

    err = capsys.readouterr().err
    assert "ghost" in err and "agent pool" in err and not (here / ".omac" / "config.yaml").exists()


@pytest.mark.parametrize(("config", "expected"), [
    ({"roles.workers": '["zoe"]'}, ("zoe", "workspace pool")),
    ({"workspace": "no-such-ws", "roles.workers": '["alice"]'}, ("no-such-ws", "")),
])
def test_check_names_what_the_server_does_not_have(here, stack, capsys, config, expected):
    main(["config", "set", "engine", "multica"])
    main(["config", "set", "workspace", stack.workspace_id])
    main(["config", "set", "project", stack.project_id])
    for key, value in config.items():
        main(["config", "set", key, value])
    capsys.readouterr()

    assert main(["init", "--check"]) == exit_codes.VALIDATION

    err = capsys.readouterr().err
    assert all(part in err for part in expected)


# ---- the wizard -----------------------------------------------------------------------------------------------------------------

class Tty:
    def isatty(self):
        return True


def answers(*seq):
    it = iter(seq)
    return lambda prompt="": next(it, "")


@pytest.fixture()
def wizard(here, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty())

    def feed(*seq):
        monkeypatch.setattr(builtins, "input", answers(*seq))
    return feed


def test_the_wizard_defaults_make_a_config_the_check_accepts(here, stack, store, wizard, capsys):
    first = store.list_members(stack.workspace_id)[0]
    wizard("", "multica", stack.workspace_id, stack.project_id, "", "", "", "", "", "", "6", "3", "1", "2", "0", "", "", "")

    assert main(["init"]) == exit_codes.OK

    cfg = config_of(here)
    assert (cfg["engine"], cfg["language"], cfg["roles"]["planner"], cfg["roles"]["workers"]) == ("multica", "en", first, [first])
    assert "acceptor" not in cfg["roles"] and cfg["defaults"]["max_parallel"] == 6 and cfg["retry"] == {"worker": 3, "ci": 1, "review": 2, "merge": 0}
    assert cfg["workflow"] == {"human_in_loop": True, "review": True, "acceptance_doc": True, "goal_required": False}
    capsys.readouterr()
    assert main(["init", "--check"]) == exit_codes.OK


def test_the_wizard_takes_agent_names_as_well_as_numbers(here, stack, wizard):
    wizard("en", "multica", stack.workspace_id, stack.project_id, "", "alice", "bob", "charlie", "bob,charlie", "")

    assert main(["init"]) == exit_codes.OK

    cfg = config_of(here)
    assert cfg["roles"]["reviewers"] == ["bob", "charlie"] and cfg["workflow"]["human_in_loop"] is True


def test_the_wizard_refuses_a_planner_that_is_not_in_the_pool(here, stack, wizard, capsys):
    wizard("en", "multica", stack.workspace_id, stack.project_id, "", "ghost")

    assert main(["init"]) == exit_codes.VALIDATION

    assert "ghost" in capsys.readouterr().err


def test_the_wizard_can_create_a_native_profile_agent_and_map_it_as_a_worker(here, stack, wizard, capsys):
    name = "template-backend"
    wizard("en", "multica", stack.workspace_id, stack.project_id, "y", "backend-eng", name, "", "", "alice", "bob", name, "carol", "")

    assert main(["init"]) == exit_codes.OK

    out = capsys.readouterr().out
    assert config_of(here)["roles"]["workers"] == [name] and "Existing Agents" in out and f"Agent created: {name}" in out


def test_a_flag_run_persists_english_and_prints_no_chinese(here, stack, capsys):
    import re

    assert main(flags(stack)) == exit_codes.OK

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert config_of(here)["language"] == "en" and "Configuration written" in rendered
    assert re.search(r"[\u4e00-\u9fff]", rendered) is None


def test_init_writes_the_retry_acceptance_workflow_and_reconcile_defaults(here, stack):
    from omac.core import config as config_mod

    assert main(flags(stack)) == exit_codes.OK

    cfg = config_of(here)
    assert cfg["retry"] == {"worker": 3, "ci": 3, "review": 3, "merge": 3}
    assert cfg["acceptance"] == {"max_rounds": config_mod.DEFAULT_MAX_ROUNDS} and cfg["acceptance"]["max_rounds"] == 3
    assert cfg["workflow"] == config_mod.DEFAULT_WORKFLOW and cfg["reconcile"] == config_mod.DEFAULT_RECONCILE
