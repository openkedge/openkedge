# REVISION 11 — Reproducibility

**Published repository & commit:** `https://github.com/openkedge/openkedge`, authoritative path `research/pieq/`, verified commit `525ff84eb0b786fa3479673a71915db3d5de84c1`. Pre-experiment SHA-256 freezes: `results/rev11/rev11_phase0_freeze.json` (36 files, includes EXP-05/06/09 preregistrations), `results/rev10_frozen_hashes.json`, `results/rev10_exp04_freeze.json`, `results/rev9_frozen_hashes.json` (historical, unmodified).
**Independently rerun GitHub Actions CI:** `https://github.com/openkedge/openkedge/actions/runs/45072074097` (`.github/workflows/pieq-evaluation.yml` on `ubuntu-latest`, Python 3.13.18, `pytest 9.0.3`, NumPy 2.5.1, 3-member containerized `etcd` via `containers/etcd-compose.yml`): **242 pytest cases passed, 0 failed, 0 skipped in 170.35 s** (superseding prior local runs without `etcd`: 229 passed, 13 skipped) and **140 distributed `etcd` fault trials passed** (uploaded as CI evidence artifact `pieq-etcd-evidence`; not double-counted as 382 independent experiments). Note: CI is independently rerun execution of the authors' published implementation, not a third-party scientific replication.
**Authoring runtime (EXP-05..09):** macOS (Apple M-series), Python 3.15.2 (`.venv-rev9`: pytest 9.2.0, numpy, scipy, psycopg2 inside spilo containers), pdflatex (TeX Live), colima VM (4 vCPU/8 GB) + Docker 29.5.2, images `ghcr.io/zalando/spilo-16:3.3-p1`, `quay.io/coreos/etcd:v3.7.3`, psql 19 client.
**Model identity attestation:** NONE pinned. `claude` CLI 2.1.330 reports `claude-sonnet-5-5` (and haiku/opus variants) without immutable snapshots; `codex` CLI 0.171 (Bedrock `openai.gpt-5.6-sol`, reasoning high) reports no model ID (config-declared). Sampling parameters are NOT enforced through either CLI (declared-only; no seeds applicable). Bedrock usage is not billed in reported costs.

## Commands (from `research/pieq/` in `openkedge/openkedge`)
```bash
python3 -m pip install -r requirements-eval.txt
python3 -m pytest -q tests/                                        # 242 passed with containerized etcd (229 passed, 13 skipped locally without etcd)
python3 scripts/run_etcd_fault_eval.py --repetitions 20 --output results/ci_etcd_evidence.json  # 140 etcd fault trials
# EXP-05 (20 live reps, ~$5.5):
for i in $(seq -w 1 20); do ./.venv-rev9/bin/python scripts/run_rev10_hardened_live_eval.py \
  --condition S1 --cases-module rev10_confirmation_cases --trace results/rev11/exp05/run_$i.jsonl; done
./.venv-rev9/bin/python scripts/analyze_rev11_exp05.py
# EXP-06:
./.venv-rev9/bin/python scripts/run_rev11_exp06.py --condition C1   # paired replay (deterministic)
./.venv-rev9/bin/python scripts/run_rev11_exp06.py --condition C2
./.venv-rev9/bin/python scripts/run_rev11_exp06.py --condition C3 --runs 3   # live
# EXP-07 (requires the physical cluster):
cd experiments/pg_patroni_failover && docker compose -f docker-compose.rev10.yml up -d
../../.venv-rev9/bin/python analyze_rev11_exp07a.py
../../.venv-rev9/bin/python rev10_physical_harness.py f1-p0|f1-p1|f1-p4|f2-p4|f5|f7|restore
# EXP-08:
docker cp rev11_exp08_e2_adapter.py pieq-pg1:/tmp/exp08.py
docker exec pieq-pg1 python3 /tmp/exp08.py <leader_ip>
# EXP-09 (~$45, ~5 h):
bash scripts/run_rev11_exp09.sh && ./.venv-rev9/bin/python scripts/analyze_rev11_exp09.py
```

## Limitations and unexecuted work
Pinned multi-provider API evaluation: UNEXECUTED (`results/rev10_pinned_preflight.json`, credentials absent). All benchmark labels author-created; no external review. Physical testbed single-VM (common cause). EXP-09 ran 5 paired repetitions (preregistered budget deviation from the 20-target, disclosed). Live reproduction is inherently nondeterministic (unpinned models, uncontrolled sampling); deterministic replays (EXP-06 C1/C2) and all software suites are exactly reproducible from the frozen hashes.

Machine-readable headline-number → artifact map: `results/rev11/REVISION11_EVIDENCE_MANIFEST.json`.
