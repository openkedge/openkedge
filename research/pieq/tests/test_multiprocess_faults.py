"""Real POSIX pauses; evidence is single-host SQLite, not distributed consensus."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX signals")


def test_stopped_control_worker_does_not_cancel_target_or_release_unknown():
    from pieq import fault_worker

    result = fault_worker.run_schedule("e1_unknown", seed=7)
    assert result["checks"]["all_passed"]
    assert result["pause_snapshot"]["ledger"]["original"] == "UNKNOWN"
    assert result["pause_snapshot"]["lease_expired"]
    assert result["pause_snapshot"]["lease_expiry_mode"] == "elapsed_while_stopped"
    assert result["pause_snapshot"]["stop_preceded_lease_expiry"]
    assert result["pause_snapshot"]["stop_timestamp_ns"] < result["pause_snapshot"]["lease_deadline_ns"]
    assert result["pause_snapshot"]["observed_expiry_ns"] > result["pause_snapshot"]["lease_deadline_ns"]
    assert result["pause_snapshot"]["conflict_status"] == "BLOCKED_UNKNOWN"
    assert result["pause_snapshot"]["target_completed_while_control_stopped"]
    assert result["rows"]["cluster_nodes"][0][1] == "DRAINED"


@pytest.mark.parametrize("schedule", [
    "e2_after_prepare", "e2_after_target_read", "e2_before_effect",
    "e2_after_effect_before_ack", "mode_s", "simultaneous_drains",
    "duplicate_op_id", "worker_crash_restart", "worker_crash_unknown",
])
def test_named_fault_schedules_twenty_repetitions(schedule):
    from pieq import fault_worker

    for repetition in range(20):
        result = fault_worker.run_schedule(schedule, seed=1000 + repetition)
        assert result["checks"]["all_passed"], result
        assert result["evidence_scope"] == "multi-process single-host SQLite"
        assert result["events"]
        assert result["receipts"]
        assert result["processes_reaped"]
        if schedule == "worker_crash_unknown":
            assert result["pause_snapshot"]["restart_state"] == "UNKNOWN"
            assert result["pause_snapshot"]["conflict_status"] == "BLOCKED_UNKNOWN"
            assert result["pause_snapshot"]["lease_expiry_mode"] == "elapsed_while_stopped"
        if schedule == "mode_s":
            assert sum(event["receipt"]["status"] == "REJECTED_MISSING_PREDECESSOR_FRONTIER"
                       for event in result["events"]
                       if event["event"] == "effect_completed") == 2


def test_e1_unknown_twenty_repetitions():
    from pieq import fault_worker

    for repetition in range(20):
        result = fault_worker.run_schedule("e1_unknown", seed=2000 + repetition)
        assert result["checks"]["all_passed"], result
        assert result["liveness_lost_to_unknown"]
