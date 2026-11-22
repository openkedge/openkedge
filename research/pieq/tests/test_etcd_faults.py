"""Real etcd quorum tests; containers and local processes are distinct evidence."""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cluster():
    from scripts.run_etcd_fault_eval import EtcdCluster

    if not (os.environ.get("PIEQ_ETCD_BIN") or os.environ.get("PIEQ_ETCD_ENDPOINTS")):
        pytest.skip("set PIEQ_ETCD_BIN or isolated PIEQ_ETCD_ENDPOINTS")
    with EtcdCluster() as cluster:
        yield cluster


@pytest.mark.parametrize("schedule", [
    "stale_resume", "simultaneous_drains", "duplicate_replay",
    "predecessor_frontier", "leader_failure", "quorum_loss", "client_isolation",
])
def test_committed_revisions_preserve_fence_cap_and_receipts(cluster, schedule):
    # Removing any epoch/revision/cap comparison or treating failure as permission
    # breaks these assertions against durable etcd keys, independent of logs.
    from scripts.run_etcd_fault_eval import run_schedule

    for repetition in range(int(os.environ.get("PIEQ_ETCD_REPETITIONS", "20"))):
        result = run_schedule(cluster, schedule, repetition)
        assert result["checks"]["all_passed"], result
        assert result["effect_count"] <= 1
        assert len(result["cluster_member_ids"]) == 3
        assert len(set(result["client_targets"])) == 2
        assert result["processes_reaped"]


def test_receipt_replay_binds_payload_and_revocation_is_monotonic(cluster):
    from pieq.etcd_boundary import EtcdBoundary

    boundary = EtcdBoundary(cluster.endpoints[0])
    boundary.initialize()
    first = boundary.execute("op", 10, "node_1")
    assert first["status"] == "COMMITTED"
    assert boundary.execute("op", 10, "node_2")["status"] == "REJECTED_OP_ID_CONFLICT"
    assert boundary.revoke(11)["status"] == "REVOKED"
    assert boundary.revoke(9)["status"] == "ALREADY_REVOKED"
    snapshot = boundary.snapshot()
    assert snapshot["values"]["epoch"] == 11
    assert snapshot["revisions"]["nodes/node_1"] == snapshot["revisions"]["receipts/op"]


def test_second_operation_cannot_repeat_a_drain_on_the_same_resource(cluster):
    from pieq.etcd_boundary import EtcdBoundary

    boundary = EtcdBoundary(cluster.endpoints[0])
    boundary.initialize()
    assert boundary.execute("first", 10, "node_1")["status"] == "COMMITTED"
    second = boundary.execute("second", 10, "node_1")
    assert second["status"] == "REJECTED_ALREADY_DRAINED"
    snapshot = boundary.snapshot()
    assert "receipts/second" not in snapshot["values"]
    assert snapshot["values"]["frontier"] == ["first"]


def test_isolation_cuts_a_previously_working_route_to_a_real_member(cluster):
    from scripts.run_etcd_fault_eval import run_schedule

    result = run_schedule(cluster, "client_isolation")
    assert result["isolation_before"]["values"]["epoch"] == 10
    assert result["receipts"][0]["status"] == "UNKNOWN"
    assert result["receipts"][1]["status"] == "COMMITTED"
    assert result["final_state"]["values"]["nodes/node_1"] == "ACTIVE"
    assert result["checks"]["all_passed"]


def test_external_fault_target_must_be_the_bundled_isolated_compose(monkeypatch, tmp_path):
    from scripts.run_etcd_fault_eval import EtcdCluster

    monkeypatch.setattr(EtcdCluster, "wait", lambda self: pytest.fail("network was reached"))
    unrelated = tmp_path / "unrelated-compose.yml"
    unrelated.write_text("services: {}\n")
    monkeypatch.setenv("PIEQ_ETCD_COMPOSE", str(unrelated))
    monkeypatch.setenv(
        "PIEQ_ETCD_ENDPOINTS",
        "http://127.0.0.1:12379,http://127.0.0.1:22379,http://127.0.0.1:32379",
    )
    with pytest.raises(ValueError, match="isolated"):
        with EtcdCluster():
            pytest.fail("an unrelated compose project was accepted for fault injection")


def test_external_fault_target_rejects_nonstandard_endpoints_before_network(monkeypatch):
    from scripts.run_etcd_fault_eval import EtcdCluster

    monkeypatch.setattr(EtcdCluster, "wait", lambda self: pytest.fail("network was reached"))
    bundled = Path(__file__).resolve().parents[1] / "containers" / "etcd-compose.yml"
    monkeypatch.setenv("PIEQ_ETCD_COMPOSE", str(bundled))
    monkeypatch.setenv(
        "PIEQ_ETCD_ENDPOINTS",
        "http://127.0.0.1:1,http://127.0.0.1:2,http://127.0.0.1:3",
    )
    with pytest.raises(ValueError, match="endpoints"):
        with EtcdCluster():
            pytest.fail("nonstandard endpoints were accepted for fault injection")


def test_cluster_records_distinct_member_id_for_each_client_endpoint(cluster):
    routes = cluster.member_routes
    assert len(routes) == 3
    assert {route["endpoint"] for route in routes} == set(cluster.endpoints)
    assert len({route["member_id"] for route in routes}) == 3
    assert {route["name"] for route in routes} == {"pieq0", "pieq1", "pieq2"}


@pytest.mark.parametrize("schedule,stops", [("leader_failure", 1), ("quorum_loss", 2)])
def test_member_failure_receipts_record_fault_timing_and_topology(cluster, schedule, stops):
    from scripts.run_etcd_fault_eval import run_schedule

    result = run_schedule(cluster, schedule)
    events = result["fault_schedule"]
    stopped = [event for event in events if event["event"] == "member_stop"]
    restarted = [event for event in events if event["event"] == "member_restart"]
    assert len(stopped) == stops
    assert len(restarted) == stops
    assert all(event["signal"] == "SIGKILL" and event["member_id"] for event in stopped)
    assert all(event["timestamp_ns"] > 0 for event in stopped + restarted)
    assert any(event["event"] == "post_fault_health" for event in events)
    assert result["leader_before_fault"]["member_id"] in {
        route["member_id"] for route in result["member_routes"]
    }
    assert result["checks"]["all_passed"]
