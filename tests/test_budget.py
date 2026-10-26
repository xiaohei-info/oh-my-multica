import pytest

from _support import budget

OK = dict(seconds=0.01, tmp_bytes=0, session_tmp_bytes=0, rss_before=0, rss_after=0, kind="unit")


def test_within_budget_passes():
    assert budget.check_call(**OK) == []


def test_a_unit_test_is_held_to_its_cpu_time_not_to_how_busy_the_machine_is():
    assert budget.check_call(**{**OK, "seconds": 0.5, "cpu_seconds": 0.25})            # 0.25 s of work is over the 0.2 s budget
    assert budget.check_call(**{**OK, "seconds": 0.19, "cpu_seconds": 0.19}) == []
    assert budget.check_call(**{**OK, "seconds": 0.6, "cpu_seconds": 0.02}) == []      # a stalled machine does not fail a cheap test
    assert budget.check_call(**{**OK, "seconds": 1.5, "cpu_seconds": 0.02})             # but a test that blocks does
    assert budget.check_call(**{**OK, "seconds": 0.5}) and budget.check_call(**{**OK, "seconds": 0.19}) == []   # no CPU figure: wall clock


def test_a_docker_test_gets_seconds_not_the_unit_budget():
    assert budget.check_call(**{**OK, "seconds": 0.5, "cpu_seconds": 0.5, "kind": "docker"}) == []
    assert budget.check_call(**{**OK, "seconds": 31, "kind": "docker"})


def test_integration_tests_get_seconds_not_minutes():
    assert budget.check_call(**{**OK, "seconds": 3, "kind": "integration"}) == []
    assert budget.check_call(**{**OK, "seconds": 6, "kind": "integration"})


def test_budget_class_comes_from_markers():
    assert budget.kind_of([]) == "unit"
    assert budget.kind_of(["integration"]) == "integration"
    assert budget.kind_of(["integration", "docker"]) == "docker"


@pytest.mark.parametrize("overage", [{"tmp_bytes": 20 * budget.MB}, {"session_tmp_bytes": 201 * budget.MB}])
def test_temp_files_over_the_per_test_or_session_limit_fail(overage):
    assert budget.check_call(**{**OK, **overage})


def test_rss_blames_only_the_test_that_crosses_the_limit():
    over = dict(OK, rss_before=budget.UNIT_RSS - 1, rss_after=budget.UNIT_RSS + 1)
    assert budget.check_call(**over)
    assert budget.check_call(**dict(over, rss_before=budget.UNIT_RSS + 1, rss_after=budget.UNIT_RSS + 2)) == []


def test_tree_bytes_counts_nested_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_bytes(b"x" * 10)
    (tmp_path / "g").write_bytes(b"y" * 5)
    assert budget.tree_bytes(tmp_path) == 15
