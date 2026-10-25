"""Fixtures for tests/it: one private Multica per pytest process, scripted agents, an isolated CLI environment."""
from pathlib import Path

import pytest

from _support.it_project import Project
from _support.it_stack import Stack, require_docker
from omac.engines import Engine, code_host_for, create_engine
from omac.engines.models import EngineConfig


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.docker)


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    require_docker()
    instance = Stack(tmp_path_factory.mktemp("it"))
    try:
        instance.up()
        for name in ("alice", "bob", "carol"):
            instance.agent(name)
        yield instance
    finally:
        instance.down()


@pytest.fixture()
def cli_env(stack, monkeypatch):
    """Point the `multica` CLI and the adapter's direct HTTP calls at the private instance only."""
    for key, value in stack.cli_env().items():
        monkeypatch.setenv(key, value)
    return stack.cli_env()


@pytest.fixture()
def engine(stack, cli_env) -> Engine:
    real = create_engine("multica", EngineConfig(engine_type="multica", workspace_id=stack.workspace_id))
    return Engine(store=real.store, runtime=real.runtime, code_host=code_host_for("multica"))


@pytest.fixture()
def store(engine):
    """The real store; every work item the test creates is cancelled afterwards."""
    real, created = engine.store, []
    real.created_ids = created
    original = real.create_work_item

    def tracking(*args, **kwargs):
        item = original(*args, **kwargs)
        created.append(item.id)
        return item

    real.create_work_item = tracking
    try:
        yield real
    finally:
        for item_id in created:
            for cleanup in (engine.runtime.cancel, real.cancel_work_item):
                try:
                    cleanup(item_id)
                except Exception:
                    pass


@pytest.fixture()
def project(stack, tmp_path) -> Project:
    return Project(tmp_path, stack)


@pytest.fixture()
def kit(stack, engine, store, tmp_path, monkeypatch):
    """The Loop driven in-process against the private Multica: real store, runtime and gh shim (tests/_support/loopkit.py)."""
    from _support.loopkit import Kit

    return Kit(stack, engine, tmp_path, monkeypatch)
