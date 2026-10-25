import pytest

from omac.core.taskmeta import WorkerHandoffIntent, parse_worker_handoff


def test_transient_retries_round_trip_and_stay_out_of_the_stored_form_while_zero():
    fresh = WorkerHandoffIntent(schema="omac.worker-handoff/v1")
    assert "transient_retries" not in fresh.as_dict()
    spent = WorkerHandoffIntent(schema="omac.worker-handoff/v1", transient_retries=1)
    assert spent.as_dict()["transient_retries"] == 1
    assert parse_worker_handoff(spent.as_dict()).transient_retries == 1
    assert parse_worker_handoff(fresh.as_dict()).transient_retries == 0


@pytest.mark.parametrize("raw", [-3, "2", True, None, 1.5])
def test_malformed_transient_retries_never_grant_extra_reruns(raw):
    parsed = parse_worker_handoff({"schema": "omac.worker-handoff/v1", "transient_retries": raw})
    assert parsed.transient_retries == 0 or raw is True and parsed.transient_retries == 0
