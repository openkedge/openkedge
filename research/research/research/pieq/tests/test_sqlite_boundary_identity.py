"""Durable target identity and domain checks for the SQLite E2 boundary."""
import pytest

from pieq.core import EpistemicLeaseCertificate, SQLiteProtectedResourceBoundary


def _certificate(action="drain_node", epoch=10, signature="permit-v1"):
    return EpistemicLeaseCertificate(action, 1, epoch, 1, (), signature)


def test_replayed_op_id_must_match_full_operation_identity(tmp_path):
    boundary = SQLiteProtectedResourceBoundary(str(tmp_path / "target.sqlite"), max_drained_nodes=2)
    original = _certificate()
    assert boundary.execute_fenced_mutation(original, target_node="node_1", op_id="op").status == "COMMITTED"
    assert boundary.execute_fenced_mutation(original, target_node="node_1", op_id="op").status == "DUPLICATE_IDEMPOTENT_COMMITTED"
    mismatches = [
        (_certificate("drain_other"), "node_1", ()),
        (original, "node_2", ()),
        (_certificate(epoch=11), "node_1", ()),
        (_certificate(signature="different-permit"), "node_1", ()),
        (original, "node_1", ("prior-op",)),
    ]
    for certificate, node, predecessors in mismatches:
        receipt = boundary.execute_fenced_mutation(
            certificate, target_node=node, op_id="op",
            required_predecessor_op_ids=predecessors,
        )
        assert receipt.status == "REJECTED_OP_ID_CONFLICT"
        assert not receipt.committed
    assert boundary.current_seq() == 1
    assert boundary.conn.execute("SELECT COUNT(*) FROM committed_ops").fetchone()[0] == 1


def test_drain_requires_known_active_target_and_receipt_effect_match(tmp_path):
    boundary = SQLiteProtectedResourceBoundary(str(tmp_path / "target.sqlite"), max_drained_nodes=2)
    absent = boundary.execute_fenced_mutation(_certificate(), target_node="unknown", op_id="absent")
    assert absent.status == "REJECTED_OUTSIDE_DOMAIN"
    assert not absent.committed
    assert boundary.current_seq() == 0
    assert boundary.conn.execute("SELECT COUNT(*) FROM committed_ops").fetchone()[0] == 0
    assert boundary.execute_fenced_mutation(_certificate(), target_node="node_1", op_id="first").committed
    repeated = boundary.execute_fenced_mutation(_certificate(), target_node="node_1", op_id="second")
    assert repeated.status == "REJECTED_ALREADY_DRAINED"
    assert not repeated.committed
    assert boundary.current_seq() == 1
    assert boundary.conn.execute("SELECT status FROM cluster_nodes WHERE node_id='node_1'").fetchone()[0] == "DRAINED"


def test_drain_cap_is_durable_shared_configuration(tmp_path):
    path = str(tmp_path / "target.sqlite")
    first = SQLiteProtectedResourceBoundary(path, max_drained_nodes=1)
    with pytest.raises(ValueError, match="cap"):
        SQLiteProtectedResourceBoundary(path, max_drained_nodes=2)
    second = SQLiteProtectedResourceBoundary(path, max_drained_nodes=1)
    assert first.execute_fenced_mutation(_certificate(), target_node="node_1", op_id="first").committed
    denied = second.execute_fenced_mutation(_certificate(), target_node="node_2", op_id="second")
    assert denied.status == "REJECTED_EXPOSURE_CAP"
    assert not denied.committed


def test_legacy_unbound_receipt_migrates_fail_closed(tmp_path):
    path = str(tmp_path / "legacy.sqlite")
    original = SQLiteProtectedResourceBoundary(path)
    original.conn.execute("DROP TABLE committed_ops")
    original.conn.execute(
        "CREATE TABLE committed_ops (op_id TEXT PRIMARY KEY, action TEXT NOT NULL, "
        "commit_version INTEGER NOT NULL, status TEXT NOT NULL)"
    )
    original.conn.execute(
        "INSERT INTO committed_ops VALUES ('legacy-op', 'drain_node', 1, 'COMMITTED')"
    )
    original.conn.close()
    migrated = SQLiteProtectedResourceBoundary(path)
    receipt = migrated.execute_fenced_mutation(
        _certificate(), target_node="node_1", op_id="legacy-op"
    )
    assert receipt.status == "REJECTED_OP_ID_CONFLICT"
    assert not receipt.committed
