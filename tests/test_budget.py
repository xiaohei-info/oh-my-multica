from _support import budget

OK = dict(seconds=0.01, tmp_bytes=0, session_tmp_bytes=0, rss_before=0, rss_after=0, kind="unit")


def test_within_budget_passes():
    assert budget.check_call(**OK) == []


def test_slow_unit_test_fails_but_docker_test_gets_more_time():
    assert budget.check_call(**{**OK, "seconds": 0.5})
    assert budget.check_call(**{**OK, "seconds": 0.3}) == []  # inside the 2x gate band (target stays 200 ms)
    assert budget.check_call(**{**OK, "seconds": 0.5, "kind": "docker"}) == []
    assert budget.check_call(**{**OK, "seconds": 31, "kind": "docker"})


def test_integration_tests_get_seconds_not_minutes():
    assert budget.check_call(**{**OK, "seconds": 3, "kind": "integration"}) == []
    assert budget.check_call(**{**OK, "seconds": 6, "kind": "integration"})


def test_budget_class_comes_from_markers():
    assert budget.kind_of([]) == "unit"
    assert budget.kind_of(["integration"]) == "integration"
    assert budget.kind_of(["integration", "docker"]) == "docker"


def test_twenty_megabytes_in_tmp_path_fails():
    assert budget.check_call(**{**OK, "tmp_bytes": 20 * budget.MB})


def test_session_temp_total_fails():
    assert budget.check_call(**{**OK, "session_tmp_bytes": 201 * budget.MB})


def test_rss_blames_only_the_test_that_crosses_the_limit():
    over = dict(OK, rss_before=budget.UNIT_RSS - 1, rss_after=budget.UNIT_RSS + 1)
    assert budget.check_call(**over)
    assert budget.check_call(**dict(over, rss_before=budget.UNIT_RSS + 1, rss_after=budget.UNIT_RSS + 2)) == []


def test_tree_bytes_counts_nested_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_bytes(b"x" * 10)
    (tmp_path / "g").write_bytes(b"y" * 5)
    assert budget.tree_bytes(tmp_path) == 15
