"""Every adapter implements its whole port; the pipeline-facing ports stay separate."""
import inspect

import pytest

from omac.engines import create_engine
from omac.engines.codehost import CodeHost
from omac.engines.github import GitHubCodeHost
from omac.engines.models import EngineConfig
from omac.engines.runtime import AgentRuntime
from omac.engines.store import WorkItemStore


def test_engine_combines_one_complete_adapter_per_port():
    engine = create_engine("multica", EngineConfig(engine_type="multica", workspace_id="ws"))
    assert isinstance(engine.store, WorkItemStore) and not inspect.isabstract(type(engine.store))
    assert isinstance(engine.runtime, AgentRuntime) and not inspect.isabstract(type(engine.runtime))
    assert isinstance(engine.code_host, CodeHost) and not inspect.isabstract(type(engine.code_host))


def test_ports_do_not_overlap():
    code_host = {n for n, _ in inspect.getmembers(CodeHost, inspect.isfunction)}
    store = {n for n, _ in inspect.getmembers(WorkItemStore, inspect.isfunction)}
    runtime = {n for n, _ in inspect.getmembers(AgentRuntime, inspect.isfunction)}
    assert not code_host & store and not code_host & runtime and not store & runtime


def test_github_adapter_is_a_code_host():
    assert issubclass(GitHubCodeHost, CodeHost)
