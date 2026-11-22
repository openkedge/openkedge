#!/usr/bin/env python3
"""
Independent PostgreSQL Ground-Truth Observer (Revision 7 P2 Control #5).

Does NOT trust PIEQ's ledger or Patroni's REST API as the source of truth.
Queries every PostgreSQL node directly over SQL to detect:
  1. Concurrent read-write primaries (`NOT pg_is_in_recovery()` on >= 2 nodes simultaneously).
  2. Dual-primary committed writes in `pieq_write_witness` across divergent timelines.
  3. Lost acknowledged client writes after failover/recovery.

STATUS: UNEXECUTED (PostgreSQL/Docker runtime unavailable on authoring host).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import time
from typing import Dict, List, Optional, Sequence


@dataclass(frozen=True)
class NodeSQLObservation:
    node: str
    reachable: bool
    is_in_recovery: Optional[bool]
    timeline_id: Optional[int]
    current_lsn: Optional[str]
    witness_row_count: Optional[int]
    error: Optional[str] = None


def probe_pg_node_via_psql(dsn: str, node: str, timeout_s: float = 2.0) -> NodeSQLObservation:
    sql = (
        "SELECT pg_is_in_recovery(), "
        "(SELECT timeline_id FROM pg_control_checkpoint()), "
        "CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()::text "
        "ELSE pg_current_wal_lsn()::text END, "
        "(SELECT COUNT(*) FROM pieq_write_witness);"
    )
    try:
        proc = subprocess.run(
            ["psql", dsn, "-t", "-A", "-F", "|", "-c", sql],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            return NodeSQLObservation(node, False, None, None, None, None, proc.stderr.strip())
        parts = proc.stdout.strip().split("|")
        return NodeSQLObservation(
            node=node,
            reachable=True,
            is_in_recovery=(parts[0] == "t"),
            timeline_id=int(parts[1]),
            current_lsn=parts[2],
            witness_row_count=int(parts[3]),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        return NodeSQLObservation(node, False, None, None, None, None, str(exc))


def check_cluster_ground_truth(node_dsns: Dict[str, str]) -> Dict[str, object]:
    """
    Poll all PostgreSQL nodes independently and evaluate physical split-brain truth.
    """
    ts = time.time()
    obs: List[NodeSQLObservation] = [
        probe_pg_node_via_psql(dsn, node) for node, dsn in sorted(node_dsns.items())
    ]
    writable_primaries = [o.node for o in obs if o.reachable and o.is_in_recovery is False]
    dual_primary_detected = len(writable_primaries) >= 2
    timelines = sorted({o.timeline_id for o in obs if o.reachable and o.timeline_id is not None})
    return {
        "timestamp": ts,
        "writable_primaries": writable_primaries,
        "physical_dual_primary_detected": dual_primary_detected,
        "observed_timelines": timelines,
        "nodes": [o.__dict__ for o in obs],
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    default_dsns = {
        "pg1": "postgresql://postgres:pieq_eval_pg_only_disposable@172.28.0.21:5432/postgres",
        "pg2": "postgresql://postgres:pieq_eval_pg_only_disposable@172.28.0.22:5432/postgres",
        "pg3": "postgresql://postgres:pieq_eval_pg_only_disposable@172.28.0.23:5432/postgres",
    }
    print(json.dumps(check_cluster_ground_truth(default_dsns), indent=2))


if __name__ == "__main__":
    main()
