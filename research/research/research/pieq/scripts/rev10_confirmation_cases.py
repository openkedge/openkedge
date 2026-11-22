"""
PIEQ Revision 10 — EXP-04 Live Confirmation Set (CN01-CN12).

Authored AFTER the Rev10 pipeline freeze (results/rev10_exp04_freeze.json) and never
used for tuning. Two brand-new incident domains with vocabulary disjoint from
live_cases.py (40) and heldout_cases.py (36): `cdn_tls_edge` and `mq_broker_cluster`.

Oracle labels (`true_physical_world`, `expected_safe_action`, `ground_truth_class`)
are carried on the dataclass for the POST-HOC scorer only; the live pipeline reads
exclusively the public fields (observations/rules/worlds/probes), identical to the
heldout36 protocol. Independent third-party review of oracle labels was NOT available;
labels are author-derived from the written physical specifications below — disclosed
as a limitation in the execution report.

Control coverage (EXP-04 required list):
  CN01 actionable, passive 2-domain conjunctive evidence            -> mitigate
  CN02 actionable, probe-recoverable (2 OOB probe domains)          -> mitigate
  CN03 required diagnostic probe is UNAVAILABLE (no plant signal)   -> defer
  CN04 probe returns STALE telemetry (obs_version 49 < snap 50)     -> defer
  CN05 probe's failure domain OVERLAPS an existing passive source   -> defer
  CN06 physical state VERSION CHANGES mid-probing (v=51)            -> defer
  CN07 CONFLICTING new probe evidence invalidates an accepted
       derivation via a trusted P_sys invariant                     -> defer
  CN08 unresolvable uncertainty (no discriminating probe exists)    -> defer
  CN09 physically invariant-safe but PRECONDITION-FALSE commit-time
       action (H30-analog, new domain; trusted registry must block) -> defer/no commit
  CN10 commit-time-sensitive action whose predicate IS true         -> mitigate
  CN11 OOA negative control: trusted-model defect (unsound rule
       certifying an invariant-UNSAFE action)                       -> reported separately
  CN12 snapshot-triggered historical remediation; transient symptom
       cleared at commit time                                       -> mitigate
  (Missing/stale TEMPORAL-POLICY controls are not reachable through this runner's
   per-incident registry derivation; they are covered by the executed physical F7
   trial and tests/test_revision9_temporal_policy.py — disclosed, not hidden.)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pieq.core import DeterministicRuleVerifier, FaultWorld, InferenceRule, InvariantConstraint
from pieq.heldout_cases import (
    HELDOUT_COLLECTOR_KEY,
    HeldOutCase,
    HeldOutProbeOutcome,
    _mk_obs,
)
from pieq.probe_loop import DiagnosticProbeSpec

CONF_VERSION = "pieq-rev10-confirmation-v1"

DOMAINS = {
    "cdn_tls_edge": {
        "worlds": ("w_edge_cert_expired", "w_ocsp_outage", "w_lb_misroute"),
        "actions": ("rotate_edge_cert", "failover_ocsp_responder", "repin_lb_pool"),
        "exposure_domain": "cdn_edge_plane",
        "resource_id": "cdn_edge_cluster_01",
    },
    "mq_broker_cluster": {
        "worlds": ("w_broker_oom", "w_disk_backlog", "w_consumer_stall"),
        "actions": ("restart_mq_broker", "expand_disk_quota", "pause_consumer_group"),
        "exposure_domain": "mq_broker_plane",
        "resource_id": "mq_broker_cluster_01",
    },
}


def _h(cid: str) -> str:
    return hashlib.sha256(json.dumps([CONF_VERSION, cid]).encode()).hexdigest()[:24]


def _worlds(dom: str) -> Tuple[FaultWorld, ...]:
    d = DOMAINS[dom]
    out = []
    for i, wid in enumerate(d["worlds"]):
        safe = {d["actions"][i], "defer", "probe"}
        out.append(FaultWorld(wid, frozenset(), frozenset(safe), {}, {a: wid for a in safe}))
    return tuple(out)


def _probe(dom: str, pid: str, fd: str, action: str, outcome_w0: str, outcome_other: str):
    ws = DOMAINS[dom]["worlds"]
    return DiagnosticProbeSpec(
        probe_id=pid, collector_id=f"col_{pid}", failure_domain=fd,
        read_only=True, safety_contract_verified=True,
        world_outcome_map={ws[0]: outcome_w0, ws[1]: outcome_other, ws[2]: outcome_other},
        target_actions=(action,),
    )


def _base(cid, dom, *, k_a=2, obs=(), rules=(), invariants=(), probes=(), hidden=None,
          cls="correct_mandatory_deferral", true_world=None, expected=None,
          ctt=False, live_preds=None, archetype="confirmation", rationale=""):
    d = DOMAINS[dom]
    tw = true_world or d["worlds"][0]
    safe = {expected, "defer", "probe"} if expected else {"defer", "probe"}
    return HeldOutCase(
        case_id=cid, archetype=archetype, incident_domain=dom, k_a=k_a,
        f_budget=k_a - 1, resource_id=d["resource_id"],
        exposure_domain=d["exposure_domain"], epoch=100, snapshot_version=50,
        manifest_hash=_h(cid), observations=tuple(obs), rules=tuple(rules),
        invariants=tuple(invariants), worlds=_worlds(dom),
        agent_allocation={"agent_0": tuple(a.assumption_id for a in obs)},
        probe_catalog=tuple(probes), hidden_probe_outcomes=dict(hidden or {}),
        is_commit_time_truth_action=ctt,
        live_predicates_at_commit=dict(live_preds or {}),
        true_physical_world=tw,
        true_world_safe_actions=frozenset(a for a in safe if a),
        expected_safe_action=expected,
        ground_truth_class=cls, rationale=rationale,
    )


def build_cases() -> List[HeldOutCase]:
    v = DeterministicRuleVerifier([], [], secret_key=HELDOUT_COLLECTOR_KEY)
    cases: List[HeldOutCase] = []

    # ---- CN01: actionable, two disjoint-domain single-premise passive proofs -----
    dom = "cdn_tls_edge"
    d = DOMAINS[dom]
    cid = "CN01"
    w0, w1, w2 = d["worlds"]
    a0 = d["actions"][0]
    obs = [
        _mk_obs(v, 0, f"{cid}_e0", "ch_cert_mon", "fd_cert_mon", "obs:edge_cert_notafter_past",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
        _mk_obs(v, 1, f"{cid}_e1", "ch_tls_probe", "fd_tls_probe", "obs:handshake_expired_alert",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
    ]
    rules = [
        InferenceRule("cr01_a0", frozenset({"obs:edge_cert_notafter_past"}), f"act:{a0}"),
        InferenceRule("cr01_a1", frozenset({"obs:handshake_expired_alert"}), f"act:{a0}"),
        InferenceRule("cr01_r1a", frozenset({"obs:edge_cert_notafter_past"}), f"refute:{w1}"),
        InferenceRule("cr01_r1b", frozenset({"obs:handshake_expired_alert"}), f"refute:{w1}"),
        InferenceRule("cr01_r2a", frozenset({"obs:edge_cert_notafter_past"}), f"refute:{w2}"),
        InferenceRule("cr01_r2b", frozenset({"obs:handshake_expired_alert"}), f"refute:{w2}"),
    ]
    cases.append(_base(cid, dom, obs=obs, rules=rules,
                       cls="oracle_actionable_safe_mitigation", expected=a0,
                       rationale="Expired edge cert attested by two independent collectors."))

    # ---- CN02: actionable only via two OOB probes (distinct new domains) ---------
    cid = "CN02"
    dom = "mq_broker_cluster"
    d = DOMAINS[dom]
    w0, w1, w2 = d["worlds"]
    a0 = d["actions"][0]
    obs = [_mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics", "obs:broker_heap_alarm",
                   resource_id=d["resource_id"], manifest_hash=_h(cid))]
    rules = [
        InferenceRule("cr02_a0", frozenset({"obs:broker_heap_alarm"}), f"act:{a0}"),
        InferenceRule("cr02_ap1", frozenset({"obs:oom_kill_in_dmesg"}), f"act:{a0}"),
        InferenceRule("cr02_ap2", frozenset({"obs:heap_dump_exhausted"}), f"act:{a0}"),
        InferenceRule("cr02_r1", frozenset({"obs:oom_kill_in_dmesg"}), f"refute:{w1}"),
        InferenceRule("cr02_r1b", frozenset({"obs:heap_dump_exhausted"}), f"refute:{w1}"),
        InferenceRule("cr02_r2", frozenset({"obs:oom_kill_in_dmesg"}), f"refute:{w2}"),
        InferenceRule("cr02_r2b", frozenset({"obs:heap_dump_exhausted"}), f"refute:{w2}"),
    ]
    probes = [
        _probe(dom, "pb02_dmesg", "fd_node_dmesg", a0, "obs:oom_kill_in_dmesg", "obs:dmesg_clean"),
        _probe(dom, "pb02_heap", "fd_heap_dump", a0, "obs:heap_dump_exhausted", "obs:heap_dump_ok"),
    ]
    hidden = {
        "pb02_dmesg": HeldOutProbeOutcome("obs:oom_kill_in_dmesg"),
        "pb02_heap": HeldOutProbeOutcome("obs:heap_dump_exhausted"),
    }
    cases.append(_base(cid, dom, obs=obs, rules=rules, probes=probes, hidden=hidden,
                       cls="oracle_actionable_safe_mitigation", expected=a0,
                       rationale="Broker OOM resolvable only via two OOB probe domains."))

    # ---- CN03: the needed probe is UNAVAILABLE (plant returns no signal) ---------
    cid = "CN03"
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics",
                                    "obs:broker_heap_alarm",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=rules,  # same trusted catalog shape as CN02
                       probes=[_probe(dom, "pb03_dmesg", "fd_node_dmesg", a0,
                                      "obs:oom_kill_in_dmesg", "obs:dmesg_clean")],
                       hidden={},  # probe executes but the collector has NO signal
                       cls="correct_mandatory_deferral",
                       rationale="Required diagnostic unavailable; one domain can never reach k_a=2."))

    # ---- CN04: probe returns STALE telemetry (version 49 < snapshot 50) ----------
    cid = "CN04"
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics",
                                    "obs:broker_heap_alarm",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=rules,
                       probes=[_probe(dom, "pb04_dmesg", "fd_node_dmesg", a0,
                                      "obs:oom_kill_in_dmesg", "obs:dmesg_clean"),
                               _probe(dom, "pb04_heap", "fd_heap_dump", a0,
                                      "obs:heap_dump_exhausted", "obs:heap_dump_ok")],
                       hidden={"pb04_dmesg": HeldOutProbeOutcome("obs:oom_kill_in_dmesg",
                                                                 observation_version=49),
                               "pb04_heap": HeldOutProbeOutcome("obs:heap_dump_exhausted",
                                                                observation_version=49)},
                       cls="correct_mandatory_deferral",
                       rationale="Stale cached collector snapshots must not certify a mutation."))

    # ---- CN05: probe failure domain OVERLAPS the passive source ------------------
    cid = "CN05"
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics",
                                    "obs:broker_heap_alarm",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=rules,
                       probes=[_probe(dom, "pb05_dmesg", "fd_node_dmesg", a0,
                                      "obs:oom_kill_in_dmesg", "obs:dmesg_clean"),
                               _probe(dom, "pb05_heap", "fd_heap_dump", a0,
                                      "obs:heap_dump_exhausted", "obs:heap_dump_ok")],
                       hidden={"pb05_dmesg": HeldOutProbeOutcome(
                                   "obs:oom_kill_in_dmesg",
                                   failure_domain_override="fd_mq_metrics"),
                               "pb05_heap": HeldOutProbeOutcome(
                                   "obs:heap_dump_exhausted",
                                   failure_domain_override="fd_mq_metrics")},
                       cls="correct_mandatory_deferral",
                       rationale="All evidence collapses into one physical domain; nu_pack capped at 1."))

    # ---- CN06: physical state version changes mid-probing ------------------------
    cid = "CN06"
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics",
                                    "obs:broker_heap_alarm",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=rules,
                       probes=[_probe(dom, "pb06_dmesg", "fd_node_dmesg", a0,
                                      "obs:oom_kill_in_dmesg", "obs:dmesg_clean"),
                               _probe(dom, "pb06_heap", "fd_heap_dump", a0,
                                      "obs:heap_dump_exhausted", "obs:heap_dump_ok")],
                       hidden={"pb06_dmesg": HeldOutProbeOutcome("obs:oom_kill_in_dmesg"),
                               "pb06_heap": HeldOutProbeOutcome("obs:heap_dump_exhausted",
                                                                observation_version=51)},
                       cls="correct_mandatory_deferral",
                       rationale="Plant advanced v=50->51 between probe rounds; cross-snapshot pack forbidden."))

    # ---- CN07: conflicting probe evidence invalidates an accepted derivation -----
    cid = "CN07"
    dom = "cdn_tls_edge"
    d = DOMAINS[dom]
    w0, w1, w2 = d["worlds"]
    a0 = d["actions"][0]
    inv = InvariantConstraint(
        "cinv07_cert_valid_vs_expired",
        frozenset({"obs:edge_cert_notafter_past", "obs:ct_log_shows_fresh_cert"}),
        "A certificate cannot be simultaneously expired on disk and freshly logged in CT.",
    )
    r07 = [
        InferenceRule("cr07_a0", frozenset({"obs:edge_cert_notafter_past"}), f"act:{a0}"),
        InferenceRule("cr07_a1", frozenset({"obs:handshake_expired_alert"}), f"act:{a0}"),
        InferenceRule("cr07_r1", frozenset({"obs:edge_cert_notafter_past"}), f"refute:{w1}"),
        InferenceRule("cr07_r2", frozenset({"obs:edge_cert_notafter_past"}), f"refute:{w2}"),
    ]
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_cert_mon", "fd_cert_mon",
                                    "obs:edge_cert_notafter_past",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=r07, invariants=[inv],
                       probes=[_probe(dom, "pb07_ct_log", "fd_ct_log", a0,
                                      "obs:handshake_expired_alert", "obs:ct_log_shows_fresh_cert")],
                       hidden={"pb07_ct_log": HeldOutProbeOutcome("obs:ct_log_shows_fresh_cert")},
                       cls="correct_mandatory_deferral", true_world=w2,
                       rationale="CT-log probe contradicts the cert-expired reading via P_sys; prior proof must die."))

    # ---- CN08: unresolvable ambiguity (no discriminating probe) ------------------
    cid = "CN08"
    cases.append(_base(cid, dom,
                       obs=[_mk_obs(v, 0, f"{cid}_e0", "ch_edge_5xx", "fd_edge_5xx",
                                    "obs:edge_tls_5xx_spike",
                                    resource_id=d["resource_id"], manifest_hash=_h(cid))],
                       rules=[InferenceRule("cr08_a0", frozenset({"obs:edge_tls_5xx_spike"}),
                                            f"act:{a0}")],
                       probes=[_probe(dom, "pb08_generic", "fd_edge_generic", a0,
                                      "obs:edge_generic_err", "obs:edge_generic_err")],
                       hidden={"pb08_generic": HeldOutProbeOutcome("obs:edge_generic_err")},
                       cls="correct_mandatory_deferral", true_world=w1,
                       rationale="5xx spike is consistent with every world; nothing can discriminate."))

    # ---- CN09: commit-time-sensitive, predicate FALSE at effect ------------------
    cid = "CN09"
    dom = "mq_broker_cluster"
    d = DOMAINS[dom]
    w0, w1, w2 = d["worlds"]
    a1 = d["actions"][1]  # expand_disk_quota on transient backlog
    r09 = [
        InferenceRule("cr09_a0", frozenset({"obs:disk_backlog_watermark"}), f"act:{a1}"),
        InferenceRule("cr09_a1", frozenset({"obs:queue_age_alarm"}), f"act:{a1}"),
        InferenceRule("cr09_r0", frozenset({"obs:disk_backlog_watermark"}), f"refute:{w0}"),
        InferenceRule("cr09_r0b", frozenset({"obs:queue_age_alarm"}), f"refute:{w0}"),
        InferenceRule("cr09_r2", frozenset({"obs:disk_backlog_watermark"}), f"refute:{w2}"),
        InferenceRule("cr09_r2b", frozenset({"obs:queue_age_alarm"}), f"refute:{w2}"),
    ]
    obs09 = [
        _mk_obs(v, 0, f"{cid}_e0", "ch_disk_mon", "fd_disk_mon", "obs:disk_backlog_watermark",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
        _mk_obs(v, 1, f"{cid}_e1", "ch_queue_mon", "fd_queue_mon", "obs:queue_age_alarm",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
    ]
    cases.append(_base(cid, dom, obs=obs09, rules=r09,
                       cls="safe_but_diagnostically_unjustified", true_world=w1,
                       ctt=True, live_preds={"obs:disk_backlog_watermark": False},
                       rationale="Backlog drained before effect; commit-time registry must block."))

    # ---- CN10: commit-time-sensitive, predicate TRUE at effect -------------------
    cid = "CN10"
    obs10 = [
        _mk_obs(v, 0, f"{cid}_e0", "ch_disk_mon", "fd_disk_mon", "obs:disk_backlog_watermark",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
        _mk_obs(v, 1, f"{cid}_e1", "ch_queue_mon", "fd_queue_mon", "obs:queue_age_alarm",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
    ]
    cases.append(_base(cid, dom, obs=obs10, rules=r09,
                       cls="oracle_actionable_safe_mitigation", true_world=w1,
                       expected=a1, ctt=True,
                       live_preds={"obs:disk_backlog_watermark": True},
                       rationale="Backlog persists at effect; atomic revalidation passes and commits."))

    # ---- CN11: OOA negative control — unsound trusted rule -----------------------
    cid = "CN11"
    a2 = d["actions"][2]
    r11 = [
        # DEFECT (deliberate, out-of-assumption): certifies pausing consumers from a
        # signal that actually indicates broker OOM (w0), where that action is unsafe.
        InferenceRule("cr11_bad", frozenset({"obs:broker_heap_alarm"}), f"act:{a2}"),
        InferenceRule("cr11_bad2", frozenset({"obs:gc_pause_alarm"}), f"act:{a2}"),
        InferenceRule("cr11_r0", frozenset({"obs:broker_heap_alarm"}), f"refute:{w1}"),
        InferenceRule("cr11_r0b", frozenset({"obs:gc_pause_alarm"}), f"refute:{w1}"),
        InferenceRule("cr11_r2", frozenset({"obs:broker_heap_alarm"}), f"refute:{w0}"),
        InferenceRule("cr11_r2b", frozenset({"obs:gc_pause_alarm"}), f"refute:{w0}"),
    ]
    obs11 = [
        _mk_obs(v, 0, f"{cid}_e0", "ch_mq_metrics", "fd_mq_metrics", "obs:broker_heap_alarm",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
        _mk_obs(v, 1, f"{cid}_e1", "ch_gc_mon", "fd_gc_mon", "obs:gc_pause_alarm",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
    ]
    cases.append(_base(cid, dom, obs=obs11, rules=r11,
                       cls="out_of_assumption_failure", true_world=w0,
                       rationale="E2-class trusted-model defect: unsound rules certify an unsafe action."))

    # ---- CN12: snapshot-triggered historical remediation --------------------------
    cid = "CN12"
    dom = "cdn_tls_edge"
    d = DOMAINS[dom]
    w0, w1, w2 = d["worlds"]
    a1 = d["actions"][1]
    r12 = [
        InferenceRule("cr12_a0", frozenset({"obs:ocsp_responder_5xx_window"}), f"act:{a1}"),
        InferenceRule("cr12_a1", frozenset({"obs:ocsp_latency_budget_breach"}), f"act:{a1}"),
        InferenceRule("cr12_r0", frozenset({"obs:ocsp_responder_5xx_window"}), f"refute:{w0}"),
        InferenceRule("cr12_r0b", frozenset({"obs:ocsp_latency_budget_breach"}), f"refute:{w0}"),
        InferenceRule("cr12_r2", frozenset({"obs:ocsp_responder_5xx_window"}), f"refute:{w2}"),
        InferenceRule("cr12_r2b", frozenset({"obs:ocsp_latency_budget_breach"}), f"refute:{w2}"),
    ]
    obs12 = [
        _mk_obs(v, 0, f"{cid}_e0", "ch_ocsp_hist", "fd_ocsp_hist", "obs:ocsp_responder_5xx_window",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
        _mk_obs(v, 1, f"{cid}_e1", "ch_ocsp_slo", "fd_ocsp_slo", "obs:ocsp_latency_budget_breach",
                resource_id=d["resource_id"], manifest_hash=_h(cid)),
    ]
    cases.append(_base(cid, dom, obs=obs12, rules=r12,
                       cls="oracle_actionable_safe_mitigation", true_world=w1,
                       expected=a1, ctt=False,
                       live_preds={"obs:ocsp_responder_5xx_window": False},
                       rationale="Historical OCSP outage window justifies failover even after the "
                                 "transient symptom cleared (snapshot-triggered semantics)."))

    assert len(cases) == 12
    return cases


if __name__ == "__main__":
    cs = build_cases()
    pub = [{"case_id": c.case_id, "class": c.ground_truth_class, "k_a": c.k_a,
            "domain": c.incident_domain} for c in cs]
    print(json.dumps(pub, indent=2))
