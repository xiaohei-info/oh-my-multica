import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_multica: marks tests that require a real multica CLI + daemon (opt-in, default skip)",
    )
