"""logsetup:进度事件流的格式解析 + structlog 配置 + 事件名常量。

纪律:进度事件走 stderr(不污染 stdout 数据线);默认人类文本,--json-logs/
OMAC_LOG_FORMAT=json 出机器可解析的扁平 JSON-lines。
"""
import json

import pytest
import structlog

from omac.core import logsetup


# ==================== 格式解析优先级:flag > env > 默认 ====================

class TestResolveLogFormat:
    def test_default_is_text(self, monkeypatch):
        monkeypatch.delenv("OMAC_LOG_FORMAT", raising=False)
        assert logsetup.resolve_log_format(None) == logsetup.TEXT

    def test_env_json_overrides_default(self, monkeypatch):
        monkeypatch.setenv("OMAC_LOG_FORMAT", "json")
        assert logsetup.resolve_log_format(None) == logsetup.JSON

    def test_flag_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OMAC_LOG_FORMAT", "json")
        # 显式 flag 压过环境变量
        assert logsetup.resolve_log_format("text") == logsetup.TEXT

    def test_unknown_falls_back_to_text(self, monkeypatch):
        monkeypatch.setenv("OMAC_LOG_FORMAT", "garbage")
        assert logsetup.resolve_log_format(None) == logsetup.TEXT


# ==================== 事件名常量:机器契约,测试锁定 ====================

def test_event_names_are_stable_contract():
    # 上层机器/LLM 依赖这些名字解析事件流,改名即破坏契约
    assert logsetup.EVT_DISPATCH == "dispatch"
    assert logsetup.EVT_VERDICT == "verdict"
    assert logsetup.EVT_REVISION == "revision"
    assert logsetup.EVT_NODE_DONE == "node_done"
    assert logsetup.EVT_NODE_FAILED == "node_failed"
    assert logsetup.EVT_HUMAN_GATE_WAIT == "human_gate_wait"
    assert logsetup.EVT_CONVERGED == "converged"
    assert logsetup.EVT_NEEDS_DECISION == "needs_decision"
    assert logsetup.EVT_CONFIG_SYNCED == "config_synced"


# ==================== configure:两渲染器,都走 stderr ====================

class TestConfigure:
    def test_json_format_emits_flat_jsonl_to_stderr(self, capsys, monkeypatch):
        monkeypatch.delenv("OMAC_LOG_FORMAT", raising=False)
        # 先消费掉前一个 configure 留下的捕获流,确保本测试独立
        capsys.readouterr()
        logsetup.configure_logging(logsetup.JSON)
        log = logsetup.get_logger()
        log.info(logsetup.EVT_DISPATCH, kind="plan", id="AITEAM-696",
                 worker="hermes-architect")
        cap = capsys.readouterr()
        assert cap.out == ""  # stdout 数据线不被污染
        line = cap.err.strip().splitlines()[-1]
        obj = json.loads(line)  # 机器可解析
        assert obj["event"] == "dispatch"
        assert obj["kind"] == "plan"
        assert obj["id"] == "AITEAM-696"
        assert obj["worker"] == "hermes-architect"

    def test_text_format_is_human_readable_on_stderr(self, capsys, monkeypatch):
        monkeypatch.delenv("OMAC_LOG_FORMAT", raising=False)
        logsetup.configure_logging(logsetup.TEXT)
        log = logsetup.get_logger()
        log.info(logsetup.EVT_NODE_DONE, kind="plan", id="AITEAM-696")
        cap = capsys.readouterr()
        assert cap.out == ""
        # 人类文本:含事件名与字段,但不是 JSON
        assert "node_done" in cap.err
        assert "AITEAM-696" in cap.err
        with pytest.raises(json.JSONDecodeError):
            json.loads(cap.err.strip().splitlines()[-1])
