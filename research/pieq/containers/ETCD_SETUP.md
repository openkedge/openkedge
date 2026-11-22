Etcd fault evaluation uses etcd 3.7.3 and the Python 3.13.5 standard-library
urllib JSON gateway client (no third-party etcd client package). Test runner:
pytest 9.0.3. The compose image is version and manifest-digest pinned to
gcr.io/etcd-development/etcd:v3.7.3 at
sha256:0d27b9478551f431f82d1e0ddb6c87ad07ebae8b904c637f3a99213349523910.
The public registry returned that digest on 2026-11-17.

Run from research/pieq:

```sh
python -m pip install -r requirements-eval.txt
docker compose -f containers/etcd-compose.yml up -d
export PIEQ_ETCD_COMPOSE="$PWD/containers/etcd-compose.yml"
export PIEQ_ETCD_ENDPOINTS=http://127.0.0.1:12379,http://127.0.0.1:22379,http://127.0.0.1:32379
python -m pytest tests/test_etcd_faults.py -q
python scripts/run_etcd_fault_eval.py --repetitions 20 --output /tmp/pieq-etcd-container-evidence.json
docker compose -f containers/etcd-compose.yml down
```

Use this dedicated compose project only: tests kill and restart its members.
No host directories or persistent volumes are mounted. Every trial receives a
random versioned key namespace. The runner rejects any Compose path other than
the bundled `containers/etcd-compose.yml` and any endpoint list other than the
three loopback mappings above before it contacts or stops a member. It then
checks that those endpoints reach three distinct member IDs named `pieq0`,
`pieq1`, and `pieq2`. Never point these fault tests at an ordinary developer
cluster or reuse this dedicated Compose project for other data.

Each archived trial records the verified endpoint-to-member-ID mapping. Leader
and quorum-loss trials additionally record the leader ID and term before the
fault, SIGKILL stop/restart monotonic timestamps, and the survivor's
post-fault maintenance status and write result. A reachable status response
without a leader during quorum loss does not establish quorum health; the
write outcome is explicitly `UNKNOWN`.

For local diagnostics set PIEQ_ETCD_BIN to an etcd 3.7.3 executable and unset
PIEQ_ETCD_ENDPOINTS/PIEQ_ETCD_COMPOSE. Three independent processes use temporary
data directories and allocated loopback ports. Local process evidence does not
satisfy container-backed acceptance. The verified macOS ARM archive SHA256 is
efd1fc11ea761c9c39c9011fda9c92a9eaecf91be3b7b3198afe4dc786435e28.

E2 here means the drain effect is the node-state mutation inside the multi-key
etcd transaction. These tests do not claim atomicity for an external drain RPC.
Client isolation cuts a per-client HTTP relay after proving its unchanged
endpoint reaches a real etcd member; the peer's independent route remains
healthy. It does not simulate packet loss after an accepted transaction. Timeout returns
UNKNOWN and requires receipt reconciliation before choosing another operation.
