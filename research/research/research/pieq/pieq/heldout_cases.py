"""
PIEQ Revision 8 P0.2 — Independent Held-Out Incident Benchmark (36 Disjoint Cases, H01-H36).

Strictly disjoint from the original 40 cases (`pieq/live_cases.py`) across:
  - 4 brand-new incident domains (`bgp_route_leak`, `storage_quorum_fence`,
    `k8s_control_plane_drain`, `tls_ca_rotation`),
  - disjoint world IDs, mutation names, predicates, rule IDs, and multi-premise /
    multi-domain dependency graphs,
  - independent physical-specification ground-truth labels in the 4-way taxonomy:
      1. `oracle_actionable_safe_mitigation`
      2. `safe_but_diagnostically_unjustified`
      3. `correct_mandatory_deferral`
      4. `out_of_assumption_failure`
    (NEVER computed by invoking PIEQ's ATMS/packing engine!),
  - leak-free domain-level probe catalogs fixed strictly by incident domain schema
    (hidden plant outcomes are queried ONLY AFTER probe selection).

Covers all 9 required P0.2 incident archetypes (4 cases per archetype):
  - H01-H04: Recoverable with sufficient independent passive evidence
  - H05-H08: Recoverable requiring bounded read-only OOB diagnostic probes
  - H09-H12: Diagnostic uncertainty that cannot safely be resolved
  - H13-H16: Correlated & misclassified telemetry (H13-H15 within assumptions; H16 misclassified h_man)
  - H17-H20: Valid evidence that does NOT justify the requested mutation (false precondition)
  - H21-H24: True preconditions with insufficient execution authorization
  - H25-H28: Concurrent operations and stale certificates
  - H29-H32: Transient preconditions changing between observation and execution (+ mid-probe drift)
  - H33-H36: Trusted-model-defect negative controls (E1 omitted world, E2 unsound rule,
             E5 >f faulty domains, E6 unmediated out-of-band bypass)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    DeterministicRuleVerifier,
    FaultWorld,
    InferenceRule,
    InvariantConstraint,
)
from pieq.probe_loop import DiagnosticProbeSpec

HELDOUT_VERSION = "pieq-rev8-heldout-v1"
HELDOUT_COLLECTOR_KEY = b"pieq-rev8-heldout-collector-key-v1"

GROUND_TRUTH_CLASSES: Tuple[str, ...] = (
    "oracle_actionable_safe_mitigation",
    "safe_but_diagnostically_unjustified",
    "correct_mandatory_deferral",
    "out_of_assumption_failure",
)

# 4 brand-new incident domains completely disjoint from live_cases.py
# (live_cases.py uses db_failover, retry_storm, cred_revocation).
HELDOUT_DOMAINS: Dict[str, Dict[str, Any]] = {
    "bgp_route_leak": {
        "brief": "Edge anycast POPs show asymmetric ingress loss after peer policy convergence.",
        "worlds": ("w_bgp_hijack", "w_transit_flap", "w_rpki_lag"),
        "actions": ("withdraw_bgp_prefix", "shift_egress_pop", "pin_rpki_cache"),
        "exposure_domain": "edge_bgp_plane",
        "resource_id": "edge_bgp_router_01",
    },
    "storage_quorum_fence": {
        "brief": "NVMe-oF storage cluster reports stalled writes across multipath initiators.",
        "worlds": ("w_initiator_wedged", "w_fabric_partition", "w_media_degraded"),
        "actions": ("fence_storage_initiator", "rebalance_io_queue", "quarantine_osd_pool"),
        "exposure_domain": "san_quorum_pool",
        "resource_id": "san_storage_pool_01",
    },
    "k8s_control_plane_drain": {
        "brief": "Kubernetes worker pool exhibits pod scheduling timeouts and API latency spikes.",
        "worlds": ("w_kubelet_wedge", "w_cni_split", "w_apiserver_burst"),
        "actions": ("cordon_and_drain_node", "restart_cni_daemon", "throttle_apiserver_writes"),
        "exposure_domain": "k8s_worker_pool",
        "resource_id": "k8s_cluster_prod_01",
    },
    "tls_ca_rotation": {
        "brief": "mTLS handshakes report unexpected intermediate chain failures across edge gateways.",
        "worlds": ("w_ca_key_compromise", "w_ocsp_outage", "w_ntp_stratum_skew"),
        "actions": ("revoke_intermediate_ca", "switch_ocsp_mirror", "resync_ntp_stratum"),
        "exposure_domain": "pki_ca_authority",
        "resource_id": "pki_ca_root_01",
    },
}


def _canonical_hash(obj: Any) -> str:
    def _norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _norm(val) for k, val in sorted(v.items())}
        if isinstance(v, (set, frozenset)):
            return sorted(_norm(x) for x in v)
        if isinstance(v, (tuple, list)):
            return [_norm(x) for x in v]
        if isinstance(v, bytes):
            return v.hex()
        return v

    raw = json.dumps(_norm(obj), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class HeldOutProbeOutcome:
    """Hidden physical plant response returned ONLY AFTER a probe is selected."""

    proposition: str
    observation_version: int = 50
    epoch_revision: int = 100
    manifest_hash_override: Optional[str] = None
    failure_domain_override: Optional[str] = None


@dataclass(frozen=True)
class HeldOutCase:
    case_id: str  # H01 .. H36
    archetype: str
    incident_domain: str
    k_a: int
    f_budget: int
    resource_id: str
    exposure_domain: str
    epoch: int
    snapshot_version: int
    manifest_hash: str
    observations: Tuple[BoundPrimitiveAssumption, ...]
    rules: Tuple[InferenceRule, ...]
    invariants: Tuple[InvariantConstraint, ...]
    worlds: Tuple[FaultWorld, ...]
    agent_allocation: Dict[str, Tuple[str, ...]]
    # Leak-free domain-level probe catalog (never leaks hidden plant truth):
    probe_catalog: Tuple[DiagnosticProbeSpec, ...]
    # Hidden plant responses queried ONLY when a probe executes:
    hidden_probe_outcomes: Dict[str, HeldOutProbeOutcome]
    # Execution-boundary & authorization environment controls:
    auth_epoch_cert: int = 5
    auth_epoch_target_min: int = 5
    auth_valid_until: Optional[int] = 1000
    now_time: Optional[int] = 500
    target_min_valid_epoch: int = 100
    target_manifest_hash: Optional[str] = None
    refinement_regime: str = "R1_ATOMIC"
    complete_writer_mediation: bool = True
    concurrent_cas_stale: bool = False
    exposure_cap: int = 1
    preexisting_unknown_exposure: int = 0
    required_predecessor_op_ids: Tuple[str, ...] = ()
    settled_predecessor_op_ids: Tuple[str, ...] = ()
    # Transient precondition controls (P0.2 / P1.2):
    live_predicates_at_commit: Dict[str, bool] = field(default_factory=dict)
    required_commit_predicates: Tuple[str, ...] = ()
    is_commit_time_truth_action: bool = False
    # Independent physical-spec ground truth (NEVER computed by PIEQ!):
    true_physical_world: str = ""
    true_world_safe_actions: FrozenSet[str] = frozenset()
    expected_safe_action: Optional[str] = None
    precondition_holds_at_snap: bool = True
    precondition_holds_at_commit: bool = True
    ground_truth_class: str = "correct_mandatory_deferral"
    violated_assumption_stratum: Optional[str] = None  # e.g., "E1", "E2", "E3", "E5", "E6"
    requires_probes_to_resolve: bool = False
    rationale: str = ""

    def build_atms(self) -> Tuple[BoundedATMSEngine, DeterministicRuleVerifier, Dict[str, BoundPrimitiveAssumption]]:
        verifier = DeterministicRuleVerifier(list(self.rules), list(self.invariants), secret_key=HELDOUT_COLLECTOR_KEY)
        atms = BoundedATMSEngine(
            verifier,
            expected_epoch=self.epoch,
            expected_resource_id=self.resource_id,
            expected_manifest_hash=self.manifest_hash,
        )
        by_id: Dict[str, BoundPrimitiveAssumption] = {}
        for obs in self.observations:
            # Register valid bound assumptions; stale/mismatched ones fail fail-closed as intended
            atms.register_assumption(obs)
            by_id[obs.assumption_id] = obs
        # Deterministically register any observed passive P_sys nogoods:
        for inv in self.invariants:
            matching = [
                a for a in atms.assumptions_by_bit.values() if a.proposition in inv.conflict_predicates
            ]
            if {a.proposition for a in matching} == set(inv.conflict_predicates):
                atms.register_nogood("collector", sum(1 << a.bit_index for a in matching), inv.invariant_id)
        return atms, verifier, by_id

    def make_leak_free_probe_executor(
        self, verifier: DeterministicRuleVerifier
    ) -> Callable[[DiagnosticProbeSpec, int], BoundPrimitiveAssumption]:
        """Query hidden plant state ONLY AFTER probe selection."""

        def _exec(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
            outcome = self.hidden_probe_outcomes.get(spec.probe_id)
            if outcome is None:
                prop = f"obs:no_signal_{spec.probe_id}"
                obs_ver = self.snapshot_version
                ep = self.epoch
                mh = self.manifest_hash
                dom = spec.failure_domain
            else:
                prop = outcome.proposition
                obs_ver = outcome.observation_version
                ep = outcome.epoch_revision
                mh = outcome.manifest_hash_override or self.manifest_hash
                dom = outcome.failure_domain_override or spec.failure_domain
            sig = verifier.sign_telemetry(
                assumption_id=f"probe_obs_{spec.probe_id}_{next_bit}",
                channel_id=spec.collector_id,
                failure_domain=dom,
                epoch_revision=ep,
                proposition=prop,
                observation_version=obs_ver,
                bit_index=next_bit,
                resource_id=self.resource_id,
                manifest_hash=mh,
            )
            return BoundPrimitiveAssumption(
                bit_index=next_bit,
                assumption_id=f"probe_obs_{spec.probe_id}_{next_bit}",
                channel_id=spec.collector_id,
                failure_domain=dom,
                epoch_revision=ep,
                proposition=prop,
                signature=sig,
                observation_version=obs_ver,
                resource_id=self.resource_id,
                manifest_hash=mh,
            )

        return _exec

    def public_spec_hash(self) -> str:
        """Hash of model-visible artifacts only (excludes private ground truth & hidden probe outcomes)."""
        return _canonical_hash(
            {
                "case_id": self.case_id,
                "incident_domain": self.incident_domain,
                "k_a": self.k_a,
                "f_budget": self.f_budget,
                "resource_id": self.resource_id,
                "epoch": self.epoch,
                "snapshot_version": self.snapshot_version,
                "manifest_hash": self.manifest_hash,
                "observations": [asdict(o) for o in self.observations],
                "rules": [asdict(r) for r in self.rules],
                "invariants": [asdict(i) for i in self.invariants],
                "world_ids": [w.world_id for w in self.worlds],
                "probe_ids": [p.probe_id for p in self.probe_catalog],
            }
        )


def _mk_obs(
    verifier: DeterministicRuleVerifier,
    bit: int,
    aid: str,
    ch: str,
    dom: str,
    prop: str,
    *,
    resource_id: str,
    manifest_hash: str,
    epoch: int = 100,
    obs_ver: int = 50,
) -> BoundPrimitiveAssumption:
    sig = verifier.sign_telemetry(
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=epoch,
        proposition=prop,
        observation_version=obs_ver,
        bit_index=bit,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
    )
    return BoundPrimitiveAssumption(
        bit_index=bit,
        assumption_id=aid,
        channel_id=ch,
        failure_domain=dom,
        epoch_revision=epoch,
        proposition=prop,
        signature=sig,
        observation_version=obs_ver,
        resource_id=resource_id,
        manifest_hash=manifest_hash,
    )


def _std_worlds(domain: str, *, helly: bool = False, extra_safe_in_w0: Sequence[str] = ()) -> Tuple[FaultWorld, ...]:
    d = HELDOUT_DOMAINS[domain]
    ws = d["worlds"]
    acts = d["actions"]
    out: List[FaultWorld] = []
    for i, wid in enumerate(ws):
        if helly:
            safe = {acts[0], acts[1]} if i == 0 else ({acts[1], acts[2]} if i == 1 else {acts[0], acts[2]})
        else:
            safe = {acts[i]}
            if i == 0 and extra_safe_in_w0:
                safe |= set(extra_safe_in_w0)
        safe |= {"defer", "probe"}
        out.append(FaultWorld(wid, frozenset(), frozenset(safe), {}, {a: wid for a in safe}))
    return tuple(out)


def _domain_probe_catalog(domain: str) -> Tuple[DiagnosticProbeSpec, ...]:
    """
    Leak-free public probe catalog fixed strictly by incident domain schema.
    Includes 3 independent OOB read-only probes, 1 shared-domain probe, 1 noisy/irrelevant
    read-only probe, and 1 non-read-only unsafe probe control.
    """
    d = HELDOUT_DOMAINS[domain]
    w0, w1, w2 = d["worlds"]
    a0, a1, a2 = d["actions"]
    return (
        DiagnosticProbeSpec(
            probe_id=f"oob_{domain}_primary_oob_a",
            collector_id=f"col_oob_{domain}_a",
            failure_domain=f"fd_oob_{domain}_a",
            read_only=True,
            safety_contract_verified=True,
            cost_units=1.0,
            estimated_latency_ms=14.0,
            world_outcome_map={
                w0: f"obs:{domain}_oob_a_confirm_w0",
                w1: f"obs:{domain}_oob_a_confirm_w1",
                w2: f"obs:{domain}_oob_a_confirm_w2",
            },
            target_actions=(a0, a1, a2),
        ),
        DiagnosticProbeSpec(
            probe_id=f"oob_{domain}_primary_oob_b",
            collector_id=f"col_oob_{domain}_b",
            failure_domain=f"fd_oob_{domain}_b",
            read_only=True,
            safety_contract_verified=True,
            cost_units=1.2,
            estimated_latency_ms=18.0,
            world_outcome_map={
                w0: f"obs:{domain}_oob_b_confirm_w0",
                w1: f"obs:{domain}_oob_b_confirm_w1",
                w2: f"obs:{domain}_oob_b_confirm_w2",
            },
            target_actions=(a0, a1, a2),
        ),
        DiagnosticProbeSpec(
            probe_id=f"oob_{domain}_primary_oob_c",
            collector_id=f"col_oob_{domain}_c",
            failure_domain=f"fd_oob_{domain}_c",
            read_only=True,
            safety_contract_verified=True,
            cost_units=1.4,
            estimated_latency_ms=22.0,
            world_outcome_map={
                w0: f"obs:{domain}_oob_c_confirm_w0",
                w1: f"obs:{domain}_oob_c_confirm_w1",
                w2: f"obs:{domain}_oob_c_confirm_w2",
            },
            target_actions=(a0, a1, a2),
        ),
        DiagnosticProbeSpec(
            probe_id=f"probe_{domain}_shared_inband",
            collector_id=f"col_shared_{domain}",
            failure_domain=f"fd_{domain}_shared_fabric",
            read_only=True,
            safety_contract_verified=True,
            cost_units=0.6,
            estimated_latency_ms=6.0,
            world_outcome_map={
                w0: f"obs:{domain}_shared_sig_w0",
                w1: f"obs:{domain}_shared_sig_w1",
                w2: f"obs:{domain}_shared_sig_w2",
            },
            target_actions=(a0,),
        ),
        DiagnosticProbeSpec(
            probe_id=f"probe_{domain}_mutative_unsafe",
            collector_id=f"col_mut_{domain}",
            failure_domain=f"fd_mut_{domain}",
            read_only=False,  # Fail-closed guard check
            safety_contract_verified=True,
            cost_units=0.2,
            estimated_latency_ms=4.0,
            world_outcome_map={w0: "obs:mut_0", w1: "obs:mut_1", w2: "obs:mut_2"},
            target_actions=(a0,),
        ),
    )


def _domain_oob_rules(domain: str) -> List[InferenceRule]:
    """Symmetric, world-neutral M_sys Horn rules for every OOB probe outcome across all 3 worlds."""
    d = HELDOUT_DOMAINS[domain]
    ws = d["worlds"]
    acts = d["actions"]
    rules: List[InferenceRule] = []
    for tag in ("a", "b", "c"):
        for idx, wid in enumerate(ws):
            prop = f"obs:{domain}_oob_{tag}_confirm_w{idx}"
            rules.append(InferenceRule(f"hr_{domain}_oob_{tag}_act_{idx}", frozenset({prop}), f"act:{acts[idx]}"))
            for other_idx, other_w in enumerate(ws):
                if other_idx != idx:
                    rules.append(
                        InferenceRule(
                            f"hr_{domain}_oob_{tag}_ref_{idx}_vs_{other_idx}",
                            frozenset({prop}),
                            f"refute:{other_w}",
                        )
                    )
    # Shared inband probe rules (same failure domain as passive shared fabric):
    rules.append(
        InferenceRule(f"hr_{domain}_shared_act0", frozenset({f"obs:{domain}_shared_sig_w0"}), f"act:{acts[0]}")
    )
    rules.append(
        InferenceRule(f"hr_{domain}_shared_ref1", frozenset({f"obs:{domain}_shared_sig_w0"}), f"refute:{ws[1]}")
    )
    rules.append(
        InferenceRule(f"hr_{domain}_shared_ref2", frozenset({f"obs:{domain}_shared_sig_w0"}), f"refute:{ws[2]}")
    )
    return rules


def _alloc_round_robin(obs_list: Sequence[BoundPrimitiveAssumption], extra_agent_grants: Optional[Dict[str, Sequence[str]]] = None) -> Dict[str, Tuple[str, ...]]:
    alloc: Dict[str, List[str]] = {f"agent_{i}": [] for i in range(5)}
    for idx, o in enumerate(obs_list):
        alloc[f"agent_{idx % 5}"].append(o.assumption_id)
    if extra_agent_grants:
        for aid, ids in extra_agent_grants.items():
            for oid in ids:
                if oid not in alloc[aid]:
                    alloc[aid].append(oid)
    return {k: tuple(v) for k, v in alloc.items()}


def build_heldout_cases() -> List[HeldOutCase]:
    """Construct the frozen 36-case independent held-out benchmark (H01..H36)."""
    cases: List[HeldOutCase] = []
    domains = ("bgp_route_leak", "storage_quorum_fence", "k8s_control_plane_drain", "tls_ca_rotation")
    v_tmp = DeterministicRuleVerifier([], [], secret_key=HELDOUT_COLLECTOR_KEY)

    # =========================================================================
    # ARCHETYPE 1 (H01-H04): Recoverable with sufficient independent passive evidence
    # Includes multi-premise conjunctive rules (H01, H03, k_a=2) and 3-domain quorum (H02, H04, k_a=3),
    # plus 1 harmless faulty domain (<= f) emitting false refutation to exercise fault tolerance.
    # =========================================================================
    for idx, dom in enumerate(domains):
        cid = f"H{idx + 1:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0 = d["actions"][0]
        k_a = 2 if idx % 2 == 0 else 3
        f_b = k_a - 1
        rules: List[InferenceRule] = []
        obs_list: List[BoundPrimitiveAssumption] = []
        grants: Dict[str, List[str]] = {}

        if k_a == 2:
            # 2-premise conjunctive proofs across 4 distinct domains (2 disjoint pairs)
            for j in range(4):
                obs_list.append(
                    _mk_obs(
                        v_tmp, j, f"{cid}_e{j}", f"ch_{dom}_{j}", f"fd_{dom}_{j}",
                        f"obs:{dom}_passive_sig_{j}", resource_id=d["resource_id"], manifest_hash=mhash,
                    )
                )
            rules.extend([
                InferenceRule(f"hr_{cid}_act_p0", frozenset({f"obs:{dom}_passive_sig_0", f"obs:{dom}_passive_sig_1"}), f"act:{a0}"),
                InferenceRule(f"hr_{cid}_act_p1", frozenset({f"obs:{dom}_passive_sig_2", f"obs:{dom}_passive_sig_3"}), f"act:{a0}"),
                InferenceRule(f"hr_{cid}_ref1_p0", frozenset({f"obs:{dom}_passive_sig_0", f"obs:{dom}_passive_sig_1"}), f"refute:{w1}"),
                InferenceRule(f"hr_{cid}_ref1_p1", frozenset({f"obs:{dom}_passive_sig_2", f"obs:{dom}_passive_sig_3"}), f"refute:{w1}"),
                InferenceRule(f"hr_{cid}_ref2_p0", frozenset({f"obs:{dom}_passive_sig_0", f"obs:{dom}_passive_sig_1"}), f"refute:{w2}"),
                InferenceRule(f"hr_{cid}_ref2_p1", frozenset({f"obs:{dom}_passive_sig_2", f"obs:{dom}_passive_sig_3"}), f"refute:{w2}"),
            ])
            # Ensure agent_0 holds pair (0,1) and agent_2 holds pair (2,3) for partitioned multi-agent synthesis
            grants = {"agent_0": [f"{cid}_e0", f"{cid}_e1"], "agent_2": [f"{cid}_e2", f"{cid}_e3"]}
            # Add 1 faulty domain emitting spurious refutation of w0 (<= f=1 -> must be tolerated!)
            obs_list.append(
                _mk_obs(
                    v_tmp, 4, f"{cid}_e4_noise", f"ch_{dom}_noise", f"fd_{dom}_noise",
                    f"obs:{dom}_spurious_ref_w0", resource_id=d["resource_id"], manifest_hash=mhash,
                )
            )
            rules.append(InferenceRule(f"hr_{cid}_noise", frozenset({f"obs:{dom}_spurious_ref_w0"}), f"refute:{w0}"))
        else:
            # 3 independent single-premise proofs + 2 faulty domains (<= f=2)
            for j in range(3):
                obs_list.append(
                    _mk_obs(
                        v_tmp, j, f"{cid}_e{j}", f"ch_{dom}_{j}", f"fd_{dom}_{j}",
                        f"obs:{dom}_passive_sig_{j}", resource_id=d["resource_id"], manifest_hash=mhash,
                    )
                )
                rules.append(InferenceRule(f"hr_{cid}_act_{j}", frozenset({f"obs:{dom}_passive_sig_{j}"}), f"act:{a0}"))
                rules.append(InferenceRule(f"hr_{cid}_ref1_{j}", frozenset({f"obs:{dom}_passive_sig_{j}"}), f"refute:{w1}"))
                rules.append(InferenceRule(f"hr_{cid}_ref2_{j}", frozenset({f"obs:{dom}_passive_sig_{j}"}), f"refute:{w2}"))
            for j in range(2):
                obs_list.append(
                    _mk_obs(
                        v_tmp, 3 + j, f"{cid}_noise_{j}", f"ch_{dom}_n{j}", f"fd_{dom}_noise_{j}",
                        f"obs:{dom}_spurious_ref_{j}", resource_id=d["resource_id"], manifest_hash=mhash,
                    )
                )
                rules.append(InferenceRule(f"hr_{cid}_noise_{j}", frozenset({f"obs:{dom}_spurious_ref_{j}"}), f"refute:{w0}"))

        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="1_passive_recoverable",
                incident_domain=dom,
                k_a=k_a,
                f_budget=f_b,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules + _domain_oob_rules(dom)),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list, grants),
                probe_catalog=_domain_probe_catalog(dom),
                hidden_probe_outcomes={
                    f"oob_{dom}_primary_oob_a": HeldOutProbeOutcome(f"obs:{dom}_oob_a_confirm_w0"),
                    f"oob_{dom}_primary_oob_b": HeldOutProbeOutcome(f"obs:{dom}_oob_b_confirm_w0"),
                    f"oob_{dom}_primary_oob_c": HeldOutProbeOutcome(f"obs:{dom}_oob_c_confirm_w0"),
                },
                true_physical_world=w0,
                true_world_safe_actions=frozenset({a0, "defer", "probe"}),
                expected_safe_action=a0,
                precondition_holds_at_snap=True,
                precondition_holds_at_commit=True,
                ground_truth_class="oracle_actionable_safe_mitigation",
                requires_probes_to_resolve=False,
                rationale=f"Recoverable {dom} incident with {k_a} disjoint passive proofs + {f_b} tolerated faulty domains.",
            )
        )

    # =========================================================================
    # ARCHETYPE 2 (H05-H08): Recoverable requiring bounded diagnostic probes
    # Passive telemetry only provides 1 domain (insufficient for k_a=2 or k_a=3) or
    # ambiguous symptom; leak-free OOB probes reveal w1 (NOT always w0!) only after selection!
    # =========================================================================
    for idx, dom in enumerate(domains):
        cid = f"H{idx + 5:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        # Vary true world across w1 / w2 so probes aren't always targeting w0!
        true_idx = 1 if idx % 2 == 0 else 2
        true_w = d["worlds"][true_idx]
        true_act = d["actions"][true_idx]
        k_a = 2 if idx < 2 else 3
        f_b = k_a - 1

        # Passive telemetry gives 1 ambiguous symptom + 1 valid domain supporting true_act (short of k_a)
        obs_list = [
            _mk_obs(
                v_tmp, 0, f"{cid}_e0", f"ch_{dom}_p0", f"fd_{dom}_passive_0",
                f"obs:{dom}_ambig_symptom", resource_id=d["resource_id"], manifest_hash=mhash,
            ),
            _mk_obs(
                v_tmp, 1, f"{cid}_e1", f"ch_{dom}_p1", f"fd_{dom}_passive_1",
                f"obs:{dom}_partial_hint_w{true_idx}", resource_id=d["resource_id"], manifest_hash=mhash,
            ),
        ]
        # Pad harmless liveness observations so all 5 agents have a handle
        for j in range(2, 5):
            obs_list.append(
                _mk_obs(
                    v_tmp, j, f"{cid}_e{j}", f"ch_{dom}_live_{j}", f"fd_{dom}_live_{j}",
                    f"obs:{dom}_collector_alive_{j}", resource_id=d["resource_id"], manifest_hash=mhash,
                )
            )
        passive_rules = [
            InferenceRule(f"hr_{cid}_part_act", frozenset({f"obs:{dom}_partial_hint_w{true_idx}"}), f"act:{true_act}"),
        ]
        for other_w in d["worlds"]:
            if other_w != true_w:
                passive_rules.append(
                    InferenceRule(f"hr_{cid}_part_ref_{other_w}", frozenset({f"obs:{dom}_partial_hint_w{true_idx}"}), f"refute:{other_w}")
                )

        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="2_probe_recoverable",
                incident_domain=dom,
                k_a=k_a,
                f_budget=f_b,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(passive_rules + _domain_oob_rules(dom)),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list),
                probe_catalog=_domain_probe_catalog(dom),
                hidden_probe_outcomes={
                    f"oob_{dom}_primary_oob_a": HeldOutProbeOutcome(f"obs:{dom}_oob_a_confirm_w{true_idx}"),
                    f"oob_{dom}_primary_oob_b": HeldOutProbeOutcome(f"obs:{dom}_oob_b_confirm_w{true_idx}"),
                    f"oob_{dom}_primary_oob_c": HeldOutProbeOutcome(f"obs:{dom}_oob_c_confirm_w{true_idx}"),
                    f"probe_{dom}_shared_inband": HeldOutProbeOutcome(f"obs:{dom}_shared_sig_w{true_idx}"),
                },
                true_physical_world=true_w,
                true_world_safe_actions=frozenset({true_act, "defer", "probe"}),
                expected_safe_action=true_act,
                precondition_holds_at_snap=True,
                precondition_holds_at_commit=True,
                ground_truth_class="oracle_actionable_safe_mitigation",
                requires_probes_to_resolve=True,
                rationale=f"Passive telemetry has nu_pack=1 < k_a={k_a}; leak-free OOB probes confirm {true_w} -> {true_act}.",
            )
        )

    # =========================================================================
    # ARCHETYPE 3 (H09-H12): Diagnostic uncertainty cannot safely be resolved
    # H09: Helly 3-world pairwise overlap with empty global intersection, uninformative probes
    # H10: 2 conflicting worlds with identical probe outcomes (indistinguishable)
    # H11: Probe cost exceeds probe budget (budget exhaustion -> DEFER_ESCALATE)
    # H12: Single-domain evidence + contradictory passive nogood deactivating proof
    # =========================================================================
    for idx, dom in enumerate(domains):
        cid = f"H{idx + 9:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0, a1, a2 = d["actions"]

        obs_list = [
            _mk_obs(v_tmp, 0, f"{cid}_e0", f"ch_{dom}_0", f"fd_{dom}_0", f"obs:{dom}_sym_0", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 1, f"{cid}_e1", f"ch_{dom}_1", f"fd_{dom}_1", f"obs:{dom}_sym_1", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 2, f"{cid}_e2", f"ch_{dom}_2", f"fd_{dom}_2", f"obs:{dom}_sym_2", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 3, f"{cid}_e3", f"ch_{dom}_3", f"fd_{dom}_3", f"obs:{dom}_sym_3", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 4, f"{cid}_e4", f"ch_{dom}_4", f"fd_{dom}_4", f"obs:{dom}_sym_4", resource_id=d["resource_id"], manifest_hash=mhash),
        ]
        rules: List[InferenceRule] = []
        invs: List[InvariantConstraint] = []
        probes: Tuple[DiagnosticProbeSpec, ...] = ()
        hidden_out: Dict[str, HeldOutProbeOutcome] = {}
        worlds = _std_worlds(dom, helly=(idx == 0))

        if idx == 0:
            # H09: Helly 3-world pairwise overlap, positive packing for a0 exists, yet all 3 worlds survive!
            for j in range(3):
                rules.append(InferenceRule(f"hr_{cid}_a0_{j}", frozenset({f"obs:{dom}_sym_{j}"}), f"act:{a0}"))
            probes = (
                DiagnosticProbeSpec(
                    probe_id=f"probe_{cid}_uninformative",
                    collector_id="col_uninf",
                    failure_domain="fd_uninf",
                    read_only=True,
                    safety_contract_verified=True,
                    cost_units=1.0,
                    world_outcome_map={w0: "obs:same", w1: "obs:same", w2: "obs:same"},
                ),
            )
            hidden_out = {f"probe_{cid}_uninformative": HeldOutProbeOutcome("obs:same")}
            true_w = w2  # a0 is safe in w0,w2? Wait: in helly w0={a0,a1}, w1={a1,a2}, w2={a0,a2}; pick true_w=w1 where a0 is UNSAFE!
            true_w = w1
            true_safe = frozenset({a1, a2, "defer", "probe"})
            rat = "Helly 3-world pairwise overlap with empty global intersection and uninformative probe; a0 unsafe in true_world."
        elif idx == 1:
            # H10: Indistinguishable worlds: probes return ambiguous 'no_conclusive_reading'
            probes = _domain_probe_catalog(dom)
            rules = _domain_oob_rules(dom)
            hidden_out = {
                p.probe_id: HeldOutProbeOutcome(f"obs:{dom}_inconclusive_{p.probe_id}")
                for p in probes
            }
            true_w = w1
            true_safe = frozenset({a1, "defer", "probe"})
            rat = "Probes return inconclusive readings matching no refutation/action rule; uncertainty unresolvable."
        elif idx == 2:
            # H11: Informative OOB probes exist in theory, but cost_units exceeds ProbeBudget!
            probes = (
                DiagnosticProbeSpec(
                    probe_id=f"probe_{cid}_too_expensive",
                    collector_id="col_exp",
                    failure_domain="fd_exp",
                    read_only=True,
                    safety_contract_verified=True,
                    cost_units=50.0,  # exceeds budget max_cost_units=6.0
                    world_outcome_map={w0: f"obs:{dom}_oob_a_confirm_w0", w1: f"obs:{dom}_oob_a_confirm_w1", w2: f"obs:{dom}_oob_a_confirm_w2"},
                    target_actions=(a0, a1, a2),
                ),
            )
            rules = _domain_oob_rules(dom)
            hidden_out = {f"probe_{cid}_too_expensive": HeldOutProbeOutcome(f"obs:{dom}_oob_a_confirm_w0")}
            true_w = w0
            true_safe = frozenset({a0, "defer", "probe"})
            rat = "Diagnostic probe exceeds cost budget -> fail-closed DEFER_ESCALATE."
        else:
            # H12: Observed P_sys contradiction (nogood) invalidates conjunctive proof
            rules = [
                InferenceRule(f"hr_{cid}_c0", frozenset({f"obs:{dom}_sym_0", f"obs:{dom}_sym_1"}), f"act:{a0}"),
                InferenceRule(f"hr_{cid}_c1", frozenset({f"obs:{dom}_sym_2"}), f"act:{a0}"),
                InferenceRule(f"hr_{cid}_r1", frozenset({f"obs:{dom}_sym_0", f"obs:{dom}_sym_1"}), f"refute:{w1}"),
                InferenceRule(f"hr_{cid}_r2", frozenset({f"obs:{dom}_sym_0", f"obs:{dom}_sym_1"}), f"refute:{w2}"),
            ]
            invs = [InvariantConstraint(f"inv_{cid}_conflict", frozenset({f"obs:{dom}_sym_0", f"obs:{dom}_sym_1"}), "Mutually contradictory sensor readings")]
            true_w = w1
            true_safe = frozenset({a1, "defer", "probe"})
            rat = "Observed P_sys nogood deactivates contradictory derivation; insufficient remaining proofs."

        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="3_unresolvable_uncertainty",
                incident_domain=dom,
                k_a=2,
                f_budget=1,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules),
                invariants=tuple(invs),
                worlds=worlds,
                agent_allocation=_alloc_round_robin(obs_list, {"agent_0": [f"{cid}_e0", f"{cid}_e1"]}),
                probe_catalog=probes,
                hidden_probe_outcomes=hidden_out,
                true_physical_world=true_w,
                true_world_safe_actions=true_safe,
                expected_safe_action=None,
                precondition_holds_at_snap=False,
                precondition_holds_at_commit=False,
                ground_truth_class="correct_mandatory_deferral",
                rationale=rat,
            )
        )

    # =========================================================================
    # ARCHETYPE 4 (H13-H16): Correlated and misclassified telemetry
    # H13: 4 collectors sharing authentic failure domain `fd_shared_tor_west` -> MUST defer
    # H14: Conjunctive overlap {e0,e1},{e1,e2},{e0,e2} (minCut=3, pack=1 < k_a=2) -> MUST defer
    # H15: Passive sensor + OOB probe sharing `fd_k8s_control_plane_drain_shared_fabric` -> MUST defer
    # H16: MISCLASSIFIED failure domain in h_man (1 faulty power bus split into 3 fake FDs -> E3 violation!)
    # =========================================================================
    for idx, dom in enumerate(domains):
        cid = f"H{idx + 13:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0, a1, _ = d["actions"]
        rules: List[InferenceRule] = []
        obs_list: List[BoundPrimitiveAssumption] = []
        grants: Dict[str, List[str]] = {}
        probes: Tuple[DiagnosticProbeSpec, ...] = ()
        hidden_out: Dict[str, HeldOutProbeOutcome] = {}
        gt_class = "correct_mandatory_deferral"
        violated_stratum: Optional[str] = None

        if idx == 0:
            # H13: 4 collectors sharing ONE labeled failure domain claiming w0 when true world is w1!
            for j in range(4):
                obs_list.append(
                    _mk_obs(v_tmp, j, f"{cid}_e{j}", f"ch_tor_{j}", "fd_shared_tor_west", f"obs:{dom}_tor_sig_{j}", resource_id=d["resource_id"], manifest_hash=mhash)
                )
                rules.append(InferenceRule(f"hr_{cid}_act_{j}", frozenset({f"obs:{dom}_tor_sig_{j}"}), f"act:{a0}"))
                rules.append(InferenceRule(f"hr_{cid}_r1_{j}", frozenset({f"obs:{dom}_tor_sig_{j}"}), f"refute:{w1}"))
                rules.append(InferenceRule(f"hr_{cid}_r2_{j}", frozenset({f"obs:{dom}_tor_sig_{j}"}), f"refute:{w2}"))
            obs_list.append(_mk_obs(v_tmp, 4, f"{cid}_e4", "ch_live", "fd_live", f"obs:{dom}_live", resource_id=d["resource_id"], manifest_hash=mhash))
            rat = "4 correlated collectors share fd_shared_tor_west; nu_pack=1 < k_a=2 -> mandatory deferral."
        elif idx == 1:
            # H14: Conjunctive overlap trap {e0,e1},{e1,e2},{e0,e2} -> Menger min-cut=3, exact pack=1!
            for j in range(3):
                obs_list.append(
                    _mk_obs(v_tmp, j, f"{cid}_e{j}", f"ch_ov_{j}", f"fd_ov_{j}", f"obs:{dom}_ov_{j}", resource_id=d["resource_id"], manifest_hash=mhash)
                )
            for j in range(3, 5):
                obs_list.append(
                    _mk_obs(v_tmp, j, f"{cid}_e{j}", f"ch_pad_{j}", f"fd_pad_{j}", f"obs:{dom}_pad_{j}", resource_id=d["resource_id"], manifest_hash=mhash)
                )
            for p_i, (u, v) in enumerate(((0, 1), (1, 2), (0, 2))):
                ants = frozenset({f"obs:{dom}_ov_{u}", f"obs:{dom}_ov_{v}"})
                rules.append(InferenceRule(f"hr_{cid}_ov_act_{p_i}", ants, f"act:{a0}"))
                rules.append(InferenceRule(f"hr_{cid}_ov_r1_{p_i}", ants, f"refute:{w1}"))
                rules.append(InferenceRule(f"hr_{cid}_ov_r2_{p_i}", ants, f"refute:{w2}"))
            grants = {
                "agent_0": [f"{cid}_e0", f"{cid}_e1"],
                "agent_1": [f"{cid}_e1", f"{cid}_e2"],
                "agent_2": [f"{cid}_e0", f"{cid}_e2"],
            }
            rat = "Conjunctive overlap {e0,e1},{e1,e2},{e0,e2}: Menger min-cut=3 yet exact packing=1 < k_a=2."
        elif idx == 2:
            # H15: Passive observation + separate read-only probe BOTH in fd_k8s_control_plane_drain_shared_fabric
            obs_list = [
                _mk_obs(v_tmp, 0, f"{cid}_e0", "ch_fab_0", f"fd_{dom}_shared_fabric", f"obs:{dom}_fab_0", resource_id=d["resource_id"], manifest_hash=mhash),
                _mk_obs(v_tmp, 1, f"{cid}_e1", "ch_pad_1", "fd_pad_1", f"obs:{dom}_pad_1", resource_id=d["resource_id"], manifest_hash=mhash),
                _mk_obs(v_tmp, 2, f"{cid}_e2", "ch_pad_2", "fd_pad_2", f"obs:{dom}_pad_2", resource_id=d["resource_id"], manifest_hash=mhash),
                _mk_obs(v_tmp, 3, f"{cid}_e3", "ch_pad_3", "fd_pad_3", f"obs:{dom}_pad_3", resource_id=d["resource_id"], manifest_hash=mhash),
                _mk_obs(v_tmp, 4, f"{cid}_e4", "ch_pad_4", "fd_pad_4", f"obs:{dom}_pad_4", resource_id=d["resource_id"], manifest_hash=mhash),
            ]
            rules = [
                InferenceRule(f"hr_{cid}_p0_act", frozenset({f"obs:{dom}_fab_0"}), f"act:{a0}"),
                InferenceRule(f"hr_{cid}_p0_r1", frozenset({f"obs:{dom}_fab_0"}), f"refute:{w1}"),
                InferenceRule(f"hr_{cid}_p0_r2", frozenset({f"obs:{dom}_fab_0"}), f"refute:{w2}"),
            ] + _domain_oob_rules(dom)
            # Only shared-domain probe available (plus unsafe mutative probe):
            probes = tuple(p for p in _domain_probe_catalog(dom) if "shared_inband" in p.probe_id or not p.read_only)
            hidden_out = {f"probe_{dom}_shared_inband": HeldOutProbeOutcome(f"obs:{dom}_shared_sig_w0")}
            rat = "Separate read-only probe shares physical failure domain with passive sensor -> nu_pack stays 1 -> mandatory defer."
        else:
            # H16: MISCLASSIFIED failure domain in h_man (1 faulty PSU mislabeled as 3 independent domains -> E3 violation!)
            for j in range(3):
                obs_list.append(
                    _mk_obs(v_tmp, j, f"{cid}_e{j}", f"ch_mis_{j}", f"fd_mislabeled_{j}", f"obs:{dom}_mis_{j}", resource_id=d["resource_id"], manifest_hash=mhash)
                )
                rules.append(InferenceRule(f"hr_{cid}_mis_act_{j}", frozenset({f"obs:{dom}_mis_{j}"}), f"act:{a0}"))
                rules.append(InferenceRule(f"hr_{cid}_mis_r1_{j}", frozenset({f"obs:{dom}_mis_{j}"}), f"refute:{w1}"))
                rules.append(InferenceRule(f"hr_{cid}_mis_r2_{j}", frozenset({f"obs:{dom}_mis_{j}"}), f"refute:{w2}"))
            for j in range(3, 5):
                obs_list.append(_mk_obs(v_tmp, j, f"{cid}_e{j}", f"ch_pad_{j}", f"fd_pad_{j}", f"obs:{dom}_pad_{j}", resource_id=d["resource_id"], manifest_hash=mhash))
            gt_class = "out_of_assumption_failure"
            violated_stratum = "E3"
            rat = "Trusted-assumption violation (E3): h_man misclassifies 3 sensors on 1 faulty power bus as disjoint domains."

        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="4_correlated_and_misclassified",
                incident_domain=dom,
                k_a=2,
                f_budget=1,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list, grants),
                probe_catalog=probes,
                hidden_probe_outcomes=hidden_out,
                true_physical_world=w1,
                true_world_safe_actions=frozenset({a1, "defer", "probe"}),
                expected_safe_action=None,
                precondition_holds_at_snap=False,
                precondition_holds_at_commit=False,
                ground_truth_class=gt_class,
                violated_assumption_stratum=violated_stratum,
                rationale=rat,
            )
        )

    # =========================================================================
    # ARCHETYPE 5 (H17-H20): Valid evidence that does NOT justify the requested mutation
    # World w0 is cleanly isolated (>= k_a disjoint refutations of w1, w2).
    # Action a0 IS invariant-safe in w0 (no invariant violation!), YET diagnostic precondition
    # c_{a0} is physically FALSE (supported by only 1 <= f faulty sensor, or valid telemetry
    # supports only world isolation without entailing a0's precondition).
    # Directly tests positive proof packing vs shield-only (Condition D vs E)!
    # =========================================================================
    for idx, dom in enumerate(domains):
        cid = f"H{idx + 17:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0 = d["actions"][0]
        k_a = 2
        obs_list = [
            _mk_obs(v_tmp, 0, f"{cid}_e0", "ch_iso_0", f"fd_{dom}_iso_0", f"obs:{dom}_benign_confirm_0", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 1, f"{cid}_e1", "ch_iso_1", f"fd_{dom}_iso_1", f"obs:{dom}_benign_confirm_1", resource_id=d["resource_id"], manifest_hash=mhash),
            # 1 single faulty sensor (<= f=1) claiming false precondition for a0:
            _mk_obs(v_tmp, 2, f"{cid}_e2", "ch_spurious", f"fd_{dom}_faulty_single", f"obs:{dom}_false_precond_trigger", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 3, f"{cid}_e3", "ch_pad_3", f"fd_{dom}_pad_3", f"obs:{dom}_pad_3", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 4, f"{cid}_e4", "ch_pad_4", f"fd_{dom}_pad_4", f"obs:{dom}_pad_4", resource_id=d["resource_id"], manifest_hash=mhash),
        ]
        rules = [
            InferenceRule(f"hr_{cid}_r1_0", frozenset({f"obs:{dom}_benign_confirm_0"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r1_1", frozenset({f"obs:{dom}_benign_confirm_1"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r2_0", frozenset({f"obs:{dom}_benign_confirm_0"}), f"refute:{w2}"),
            InferenceRule(f"hr_{cid}_r2_1", frozenset({f"obs:{dom}_benign_confirm_1"}), f"refute:{w2}"),
            InferenceRule(f"hr_{cid}_spurious_a0", frozenset({f"obs:{dom}_false_precond_trigger"}), f"act:{a0}"),
        ]
        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="5_valid_evidence_unjustified_mutation",
                incident_domain=dom,
                k_a=k_a,
                f_budget=1,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list),
                probe_catalog=(),
                hidden_probe_outcomes={},
                true_physical_world=w0,
                true_world_safe_actions=frozenset({a0, "defer", "probe"}),
                expected_safe_action=None,
                precondition_holds_at_snap=False,
                precondition_holds_at_commit=False,
                ground_truth_class="safe_but_diagnostically_unjustified",
                rationale=f"World {w0} isolated by 2 disjoint refutations (a0 preserves K), yet c_a is FALSE (only 1 faulty sensor -> nu_pack=1 < k_a=2).",
            )
        )

    # =========================================================================
    # ARCHETYPE 6 (H21-H24): True preconditions but insufficient execution authorization
    # Epistemically 100% valid (nu_pack=2 >= k_a, unique safe world w0), BUT:
    #   H21: auth_epoch revoked at target (target_min=7 > cert=5)
    #   H22: auth_valid_until expired (now_time=1200 > 1000)
    #   H23: missing trusted target clock (now_time=None)
    #   H24: UNVERIFIED_ASYNC target refinement attestation
    # =========================================================================
    auth_faults = [
        ("revoked_auth_epoch", {"auth_epoch_cert": 5, "auth_epoch_target_min": 7}),
        ("expired_auth_ttl", {"auth_valid_until": 1000, "now_time": 1200}),
        ("missing_auth_clock", {"auth_valid_until": 1000, "now_time": None}),
        ("unverified_async_refinement", {"refinement_regime": "UNVERIFIED_ASYNC"}),
    ]
    for idx, (dom, (fname, kwargs)) in enumerate(zip(domains, auth_faults)):
        cid = f"H{idx + 21:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0 = d["actions"][0]
        obs_list = [
            _mk_obs(v_tmp, 0, f"{cid}_e0", "ch_0", f"fd_{dom}_0", f"obs:{dom}_ok_0", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 1, f"{cid}_e1", "ch_1", f"fd_{dom}_1", f"obs:{dom}_ok_1", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 2, f"{cid}_e2", "ch_2", f"fd_{dom}_2", f"obs:{dom}_pad_2", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 3, f"{cid}_e3", "ch_3", f"fd_{dom}_3", f"obs:{dom}_pad_3", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 4, f"{cid}_e4", "ch_4", f"fd_{dom}_4", f"obs:{dom}_pad_4", resource_id=d["resource_id"], manifest_hash=mhash),
        ]
        rules = [
            InferenceRule(f"hr_{cid}_a0", frozenset({f"obs:{dom}_ok_0"}), f"act:{a0}"),
            InferenceRule(f"hr_{cid}_a1", frozenset({f"obs:{dom}_ok_1"}), f"act:{a0}"),
            InferenceRule(f"hr_{cid}_r1_0", frozenset({f"obs:{dom}_ok_0"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r1_1", frozenset({f"obs:{dom}_ok_1"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r2_0", frozenset({f"obs:{dom}_ok_0"}), f"refute:{w2}"),
            InferenceRule(f"hr_{cid}_r2_1", frozenset({f"obs:{dom}_ok_1"}), f"refute:{w2}"),
        ]
        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="6_insufficient_execution_authorization",
                incident_domain=dom,
                k_a=2,
                f_budget=1,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list),
                probe_catalog=(),
                hidden_probe_outcomes={},
                true_physical_world=w0,
                true_world_safe_actions=frozenset({a0, "defer", "probe"}),
                expected_safe_action=None,
                precondition_holds_at_snap=True,
                precondition_holds_at_commit=True,
                ground_truth_class="correct_mandatory_deferral",
                rationale=f"Epistemic quorum valid, but execution authorization fails ({fname}) -> mandatory fail-closed rejection.",
                **kwargs,
            )
        )

    # =========================================================================
    # ARCHETYPE 7 (H25-H28): Concurrent operations and stale certificates
    #   H25: Stale per-domain admission revision CAS
    #   H26: Cross-root exposure cap saturated by preexisting UNKNOWN op in I_U
    #   H27: Missing Mode S predecessor frontier Pred(a)
    #   H28: Stale admission_epoch certificate rejected atomically at Tier E2 SQLite WAL
    # =========================================================================
    conc_faults = [
        ("stale_admission_rev_cas", {"concurrent_cas_stale": True}),
        ("exposure_cap_saturated_by_unknown", {"exposure_cap": 1, "preexisting_unknown_exposure": 1}),
        ("missing_predecessor_frontier", {"required_predecessor_op_ids": ("op_pred_unsettled",)}),
        ("stale_cert_admission_epoch", {"target_min_valid_epoch": 105}),
    ]
    for idx, (dom, (fname, kwargs)) in enumerate(zip(domains, conc_faults)):
        cid = f"H{idx + 25:02d}"
        d = HELDOUT_DOMAINS[dom]
        mhash = _canonical_hash([HELDOUT_VERSION, cid])
        w0, w1, w2 = d["worlds"]
        a0 = d["actions"][0]
        obs_list = [
            _mk_obs(v_tmp, 0, f"{cid}_e0", "ch_0", f"fd_{dom}_0", f"obs:{dom}_ok_0", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 1, f"{cid}_e1", "ch_1", f"fd_{dom}_1", f"obs:{dom}_ok_1", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 2, f"{cid}_e2", "ch_2", f"fd_{dom}_2", f"obs:{dom}_pad_2", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 3, f"{cid}_e3", "ch_3", f"fd_{dom}_3", f"obs:{dom}_pad_3", resource_id=d["resource_id"], manifest_hash=mhash),
            _mk_obs(v_tmp, 4, f"{cid}_e4", "ch_4", f"fd_{dom}_4", f"obs:{dom}_pad_4", resource_id=d["resource_id"], manifest_hash=mhash),
        ]
        rules = [
            InferenceRule(f"hr_{cid}_a0", frozenset({f"obs:{dom}_ok_0"}), f"act:{a0}"),
            InferenceRule(f"hr_{cid}_a1", frozenset({f"obs:{dom}_ok_1"}), f"act:{a0}"),
            InferenceRule(f"hr_{cid}_r1_0", frozenset({f"obs:{dom}_ok_0"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r1_1", frozenset({f"obs:{dom}_ok_1"}), f"refute:{w1}"),
            InferenceRule(f"hr_{cid}_r2_0", frozenset({f"obs:{dom}_ok_0"}), f"refute:{w2}"),
            InferenceRule(f"hr_{cid}_r2_1", frozenset({f"obs:{dom}_ok_1"}), f"refute:{w2}"),
        ]
        cases.append(
            HeldOutCase(
                case_id=cid,
                archetype="7_concurrent_and_stale_certificates",
                incident_domain=dom,
                k_a=2,
                f_budget=1,
                resource_id=d["resource_id"],
                exposure_domain=d["exposure_domain"],
                epoch=100,
                snapshot_version=50,
                manifest_hash=mhash,
                observations=tuple(obs_list),
                rules=tuple(rules),
                invariants=(),
                worlds=_std_worlds(dom),
                agent_allocation=_alloc_round_robin(obs_list),
                probe_catalog=(),
                hidden_probe_outcomes={},
                true_physical_world=w0,
                true_world_safe_actions=frozenset({a0, "defer", "probe"}),
                expected_safe_action=None,
                precondition_holds_at_snap=True,
                precondition_holds_at_commit=True,
                ground_truth_class="correct_mandatory_deferral",
                rationale=f"Concurrent/stale execution hazard ({fname}) must be blocked at ledger CAS or Tier E2 boundary.",
                **kwargs,
            )
        )

    # =========================================================================
    # ARCHETYPE 8 (H29-H32): Transient preconditions changing between observation and execution
    #   H29 (k8s): Transient load clears before commit; opt-in Tier E2 revalidation enabled -> rejects cleanly!
    #   H30 (storage): Transient I/O spike clears before commit; opt-in Tier E2 revalidation OMITTED
    #       -> exposes exact snapshot-vs-commit gap (commits stale transient precondition)!
    #   H31 (bgp): Mid-probe plant state change (passive obs at v=50, OOB probe arrives at v=51 after plant changed!)
    #       -> snapshot consistency broken! (Exposes Rev7 probe_loop.py cross-version bug; repaired in P1.3!)
    #   H32 (tls): Historical-snapshot trigger (CA compromise verified at v=50; attacker stops before t_eff)
    #       -> intentionally omits commit-time revalidation and MUST safely commit!
    # =========================================================================
    # H29: k8s transient precondition WITH Tier E2 atomic revalidation
    dom29 = "k8s_control_plane_drain"
    d29 = HELDOUT_DOMAINS[dom29]
    mh29 = _canonical_hash([HELDOUT_VERSION, "H29"])
    w0, w1, w2 = d29["worlds"]
    a2 = d29["actions"][2]  # throttle_apiserver_writes (transient action on w_apiserver_burst)
    obs29 = [
        _mk_obs(v_tmp, 0, "H29_e0", "ch_0", "fd_k8s_0", "obs:apiserver_inflight_high_0", resource_id=d29["resource_id"], manifest_hash=mh29),
        _mk_obs(v_tmp, 1, "H29_e1", "ch_1", "fd_k8s_1", "obs:apiserver_inflight_high_1", resource_id=d29["resource_id"], manifest_hash=mh29),
        _mk_obs(v_tmp, 2, "H29_e2", "ch_2", "fd_k8s_2", "obs:k8s_pad_2", resource_id=d29["resource_id"], manifest_hash=mh29),
        _mk_obs(v_tmp, 3, "H29_e3", "ch_3", "fd_k8s_3", "obs:k8s_pad_3", resource_id=d29["resource_id"], manifest_hash=mh29),
        _mk_obs(v_tmp, 4, "H29_e4", "ch_4", "fd_k8s_4", "obs:k8s_pad_4", resource_id=d29["resource_id"], manifest_hash=mh29),
    ]
    rules29 = [
        InferenceRule("hr_H29_a0", frozenset({"obs:apiserver_inflight_high_0"}), f"act:{a2}"),
        InferenceRule("hr_H29_a1", frozenset({"obs:apiserver_inflight_high_1"}), f"act:{a2}"),
        InferenceRule("hr_H29_r0_0", frozenset({"obs:apiserver_inflight_high_0"}), f"refute:{w0}"),
        InferenceRule("hr_H29_r0_1", frozenset({"obs:apiserver_inflight_high_1"}), f"refute:{w0}"),
        InferenceRule("hr_H29_r1_0", frozenset({"obs:apiserver_inflight_high_0"}), f"refute:{w1}"),
        InferenceRule("hr_H29_r1_1", frozenset({"obs:apiserver_inflight_high_1"}), f"refute:{w1}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H29",
            archetype="8_transient_preconditions",
            incident_domain=dom29,
            k_a=2,
            f_budget=1,
            resource_id=d29["resource_id"],
            exposure_domain=d29["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh29,
            observations=tuple(obs29),
            rules=tuple(rules29),
            invariants=(),
            worlds=_std_worlds(dom29),
            agent_allocation=_alloc_round_robin(obs29),
            probe_catalog=(),
            hidden_probe_outcomes={},
            live_predicates_at_commit={"obs:apiserver_inflight_saturated": False},
            required_commit_predicates=("obs:apiserver_inflight_saturated",),
            is_commit_time_truth_action=True,
            true_physical_world=w2,
            true_world_safe_actions=frozenset({a2, "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=True,
            precondition_holds_at_commit=False,
            ground_truth_class="correct_mandatory_deferral",
            rationale="Transient API-server spike cleared before commit; Tier E2 atomic revalidation rejects stale precondition.",
        )
    )

    # H30: storage transient I/O spike WITHOUT Tier E2 revalidation (commit-time precondition false!)
    dom30 = "storage_quorum_fence"
    d30 = HELDOUT_DOMAINS[dom30]
    mh30 = _canonical_hash([HELDOUT_VERSION, "H30"])
    w0, w1, w2 = d30["worlds"]
    a1 = d30["actions"][1]  # rebalance_io_queue
    obs30 = [
        _mk_obs(v_tmp, 0, "H30_e0", "ch_0", "fd_san_0", "obs:io_queue_spike_0", resource_id=d30["resource_id"], manifest_hash=mh30),
        _mk_obs(v_tmp, 1, "H30_e1", "ch_1", "fd_san_1", "obs:io_queue_spike_1", resource_id=d30["resource_id"], manifest_hash=mh30),
        _mk_obs(v_tmp, 2, "H30_e2", "ch_2", "fd_san_2", "obs:san_pad_2", resource_id=d30["resource_id"], manifest_hash=mh30),
        _mk_obs(v_tmp, 3, "H30_e3", "ch_3", "fd_san_3", "obs:san_pad_3", resource_id=d30["resource_id"], manifest_hash=mh30),
        _mk_obs(v_tmp, 4, "H30_e4", "ch_4", "fd_san_4", "obs:san_pad_4", resource_id=d30["resource_id"], manifest_hash=mh30),
    ]
    rules30 = [
        InferenceRule("hr_H30_a0", frozenset({"obs:io_queue_spike_0"}), f"act:{a1}"),
        InferenceRule("hr_H30_a1", frozenset({"obs:io_queue_spike_1"}), f"act:{a1}"),
        InferenceRule("hr_H30_r0_0", frozenset({"obs:io_queue_spike_0"}), f"refute:{w0}"),
        InferenceRule("hr_H30_r0_1", frozenset({"obs:io_queue_spike_1"}), f"refute:{w0}"),
        InferenceRule("hr_H30_r2_0", frozenset({"obs:io_queue_spike_0"}), f"refute:{w2}"),
        InferenceRule("hr_H30_r2_1", frozenset({"obs:io_queue_spike_1"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H30",
            archetype="8_transient_preconditions",
            incident_domain=dom30,
            k_a=2,
            f_budget=1,
            resource_id=d30["resource_id"],
            exposure_domain=d30["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh30,
            observations=tuple(obs30),
            rules=tuple(rules30),
            invariants=(),
            worlds=_std_worlds(dom30),
            agent_allocation=_alloc_round_robin(obs30),
            probe_catalog=(),
            hidden_probe_outcomes={},
            live_predicates_at_commit={"obs:io_queue_spike": False},
            required_commit_predicates=(),  # Deliberately omitted to test snapshot-vs-commit truth boundary!
            is_commit_time_truth_action=True,
            true_physical_world=w1,
            true_world_safe_actions=frozenset({a1, "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=True,
            precondition_holds_at_commit=False,
            ground_truth_class="safe_but_diagnostically_unjustified",
            rationale="Transient I/O spike cleared before commit without E2 revalidation -> demonstrates snapshot truth != commit-time truth.",
        )
    )

    # H31: BGP mid-probe plant version advancement (passive at v=50, OOB probe observes v=51!)
    dom31 = "bgp_route_leak"
    d31 = HELDOUT_DOMAINS[dom31]
    mh31 = _canonical_hash([HELDOUT_VERSION, "H31"])
    w0, w1, w2 = d31["worlds"]
    a0 = d31["actions"][0]
    obs31 = [
        _mk_obs(v_tmp, 0, "H31_e0", "ch_0", "fd_bgp_0", "obs:bgp_hijack_passive_0", resource_id=d31["resource_id"], manifest_hash=mh31, obs_ver=50),
        _mk_obs(v_tmp, 1, "H31_e1", "ch_1", "fd_bgp_1", "obs:bgp_pad_1", resource_id=d31["resource_id"], manifest_hash=mh31, obs_ver=50),
        _mk_obs(v_tmp, 2, "H31_e2", "ch_2", "fd_bgp_2", "obs:bgp_pad_2", resource_id=d31["resource_id"], manifest_hash=mh31, obs_ver=50),
        _mk_obs(v_tmp, 3, "H31_e3", "ch_3", "fd_bgp_3", "obs:bgp_pad_3", resource_id=d31["resource_id"], manifest_hash=mh31, obs_ver=50),
        _mk_obs(v_tmp, 4, "H31_e4", "ch_4", "fd_bgp_4", "obs:bgp_pad_4", resource_id=d31["resource_id"], manifest_hash=mh31, obs_ver=50),
    ]
    rules31 = [
        InferenceRule("hr_H31_a0", frozenset({"obs:bgp_hijack_passive_0"}), f"act:{a0}"),
        InferenceRule("hr_H31_r1", frozenset({"obs:bgp_hijack_passive_0"}), f"refute:{w1}"),
        InferenceRule("hr_H31_r2", frozenset({"obs:bgp_hijack_passive_0"}), f"refute:{w2}"),
    ] + _domain_oob_rules(dom31)
    # Only 1 OOB probe returns at v=51 (plant state mutated to w_transit_flap while probe ran,
    # yet probe_a returns a stale/cross-version reading at v=51 while passive was v=50!):
    probes31 = (
        _domain_probe_catalog(dom31)[0],  # oob_bgp_route_leak_primary_oob_a
    )
    cases.append(
        HeldOutCase(
            case_id="H31",
            archetype="8_transient_preconditions",
            incident_domain=dom31,
            k_a=2,
            f_budget=1,
            resource_id=d31["resource_id"],
            exposure_domain=d31["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh31,
            observations=tuple(obs31),
            rules=tuple(rules31),
            invariants=(),
            worlds=_std_worlds(dom31),
            agent_allocation=_alloc_round_robin(obs31),
            probe_catalog=probes31,
            hidden_probe_outcomes={
                f"oob_{dom31}_primary_oob_a": HeldOutProbeOutcome(
                    f"obs:{dom31}_oob_a_confirm_w0", observation_version=51
                ),
            },
            true_physical_world=w1,  # Plant shifted to w_transit_flap; combining v=50 and v=51 is invalid!
            true_world_safe_actions=frozenset({d31["actions"][1], "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=False,
            precondition_holds_at_commit=False,
            ground_truth_class="correct_mandatory_deferral",
            requires_probes_to_resolve=False,
            rationale="Mid-probe plant version jump (v=50 passive + v=51 probe): must NOT combine cross-snapshot proofs across rounds!",
        )
    )

    # H32: TLS historical-snapshot trigger (compromise at v=50; attacker stops before t_eff -> MUST revoke CA!)
    dom32 = "tls_ca_rotation"
    d32 = HELDOUT_DOMAINS[dom32]
    mh32 = _canonical_hash([HELDOUT_VERSION, "H32"])
    w0, w1, w2 = d32["worlds"]
    a0 = d32["actions"][0]  # revoke_intermediate_ca
    obs32 = [
        _mk_obs(v_tmp, 0, "H32_e0", "ch_0", "fd_tls_0", "obs:ca_key_exfil_0", resource_id=d32["resource_id"], manifest_hash=mh32),
        _mk_obs(v_tmp, 1, "H32_e1", "ch_1", "fd_tls_1", "obs:ca_key_exfil_1", resource_id=d32["resource_id"], manifest_hash=mh32),
        _mk_obs(v_tmp, 2, "H32_e2", "ch_2", "fd_tls_2", "obs:tls_pad_2", resource_id=d32["resource_id"], manifest_hash=mh32),
        _mk_obs(v_tmp, 3, "H32_e3", "ch_3", "fd_tls_3", "obs:tls_pad_3", resource_id=d32["resource_id"], manifest_hash=mh32),
        _mk_obs(v_tmp, 4, "H32_e4", "ch_4", "fd_tls_4", "obs:tls_pad_4", resource_id=d32["resource_id"], manifest_hash=mh32),
    ]
    rules32 = [
        InferenceRule("hr_H32_a0", frozenset({"obs:ca_key_exfil_0"}), f"act:{a0}"),
        InferenceRule("hr_H32_a1", frozenset({"obs:ca_key_exfil_1"}), f"act:{a0}"),
        InferenceRule("hr_H32_r1_0", frozenset({"obs:ca_key_exfil_0"}), f"refute:{w1}"),
        InferenceRule("hr_H32_r1_1", frozenset({"obs:ca_key_exfil_1"}), f"refute:{w1}"),
        InferenceRule("hr_H32_r2_0", frozenset({"obs:ca_key_exfil_0"}), f"refute:{w2}"),
        InferenceRule("hr_H32_r2_1", frozenset({"obs:ca_key_exfil_1"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H32",
            archetype="8_transient_preconditions",
            incident_domain=dom32,
            k_a=2,
            f_budget=1,
            resource_id=d32["resource_id"],
            exposure_domain=d32["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh32,
            observations=tuple(obs32),
            rules=tuple(rules32),
            invariants=(),
            worlds=_std_worlds(dom32),
            agent_allocation=_alloc_round_robin(obs32),
            probe_catalog=(),
            hidden_probe_outcomes={},
            live_predicates_at_commit={"obs:active_exfil_stream": False},
            required_commit_predicates=(),  # Historical-snapshot semantics: intentionally no commit revalidation!
            is_commit_time_truth_action=False,
            true_physical_world=w0,
            true_world_safe_actions=frozenset({a0, "defer", "probe"}),
            expected_safe_action=a0,
            precondition_holds_at_snap=True,
            precondition_holds_at_commit=True,  # Historical compromise persists semantically even if exfil stopped
            ground_truth_class="oracle_actionable_safe_mitigation",
            rationale="Historical-snapshot trigger (compromised CA at v=50): intentionally omits commit revalidation and safely revokes CA.",
        )
    )

    # =========================================================================
    # ARCHETYPE 9 (H33-H36): Trusted-model-defect negative controls (out_of_assumption_failure)
    #   H33 (bgp): E1 omitted true world (w_unmodeled_blackhole not in S_cand)
    #   H34 (storage): E2 unsound Horn rule in M_sys deriving unsafe fence from healthy signal
    #   H35 (k8s): E5 >f simultaneous faulty failure domains (3 faulty domains when f=2)
    #   H36 (tls): E6 unmediated out-of-band writer bypass (complete_writer_mediation=False,
    #              modeled as an attested-true but physically bypassed mediation violation!)
    # =========================================================================
    # H33: E1 omitted world
    dom33 = "bgp_route_leak"
    d33 = HELDOUT_DOMAINS[dom33]
    mh33 = _canonical_hash([HELDOUT_VERSION, "H33"])
    w0, w1, w2 = d33["worlds"]
    a0 = d33["actions"][0]
    obs33 = [
        _mk_obs(v_tmp, 0, "H33_e0", "ch_0", "fd_0", "obs:bgp_loss_0", resource_id=d33["resource_id"], manifest_hash=mh33),
        _mk_obs(v_tmp, 1, "H33_e1", "ch_1", "fd_1", "obs:bgp_loss_1", resource_id=d33["resource_id"], manifest_hash=mh33),
        _mk_obs(v_tmp, 2, "H33_e2", "ch_2", "fd_2", "obs:pad_2", resource_id=d33["resource_id"], manifest_hash=mh33),
        _mk_obs(v_tmp, 3, "H33_e3", "ch_3", "fd_3", "obs:pad_3", resource_id=d33["resource_id"], manifest_hash=mh33),
        _mk_obs(v_tmp, 4, "H33_e4", "ch_4", "fd_4", "obs:pad_4", resource_id=d33["resource_id"], manifest_hash=mh33),
    ]
    rules33 = [
        InferenceRule("hr_H33_a0", frozenset({"obs:bgp_loss_0"}), f"act:{a0}"),
        InferenceRule("hr_H33_a1", frozenset({"obs:bgp_loss_1"}), f"act:{a0}"),
        InferenceRule("hr_H33_r1_0", frozenset({"obs:bgp_loss_0"}), f"refute:{w1}"),
        InferenceRule("hr_H33_r1_1", frozenset({"obs:bgp_loss_1"}), f"refute:{w1}"),
        InferenceRule("hr_H33_r2_0", frozenset({"obs:bgp_loss_0"}), f"refute:{w2}"),
        InferenceRule("hr_H33_r2_1", frozenset({"obs:bgp_loss_1"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H33",
            archetype="9_trusted_model_defect_controls",
            incident_domain=dom33,
            k_a=2,
            f_budget=1,
            resource_id=d33["resource_id"],
            exposure_domain=d33["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh33,
            observations=tuple(obs33),
            rules=tuple(rules33),
            invariants=(),
            worlds=_std_worlds(dom33),
            agent_allocation=_alloc_round_robin(obs33),
            probe_catalog=(),
            hidden_probe_outcomes={},
            true_physical_world="w_unmodeled_anycast_blackhole",  # NOT in S_cand!
            true_world_safe_actions=frozenset({"defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=False,
            precondition_holds_at_commit=False,
            ground_truth_class="out_of_assumption_failure",
            violated_assumption_stratum="E1",
            rationale="Negative control (E1): true physical world w_unmodeled_anycast_blackhole omitted from S_cand.",
        )
    )

    # H34: E2 unsound Horn rule in M_sys
    dom34 = "storage_quorum_fence"
    d34 = HELDOUT_DOMAINS[dom34]
    mh34 = _canonical_hash([HELDOUT_VERSION, "H34"])
    w0, w1, w2 = d34["worlds"]
    a0, a1 = d34["actions"][0], d34["actions"][1]
    obs34 = [
        _mk_obs(v_tmp, 0, "H34_e0", "ch_0", "fd_0", "obs:normal_scrub_0", resource_id=d34["resource_id"], manifest_hash=mh34),
        _mk_obs(v_tmp, 1, "H34_e1", "ch_1", "fd_1", "obs:normal_scrub_1", resource_id=d34["resource_id"], manifest_hash=mh34),
        _mk_obs(v_tmp, 2, "H34_e2", "ch_2", "fd_2", "obs:pad_2", resource_id=d34["resource_id"], manifest_hash=mh34),
        _mk_obs(v_tmp, 3, "H34_e3", "ch_3", "fd_3", "obs:pad_3", resource_id=d34["resource_id"], manifest_hash=mh34),
        _mk_obs(v_tmp, 4, "H34_e4", "ch_4", "fd_4", "obs:pad_4", resource_id=d34["resource_id"], manifest_hash=mh34),
    ]
    # Unsound authored M_sys rules erroneously map benign background scrub to fence_storage_initiator!
    rules34 = [
        InferenceRule("hr_H34_unsound_a0", frozenset({"obs:normal_scrub_0"}), f"act:{a0}"),
        InferenceRule("hr_H34_unsound_a1", frozenset({"obs:normal_scrub_1"}), f"act:{a0}"),
        InferenceRule("hr_H34_unsound_r1_0", frozenset({"obs:normal_scrub_0"}), f"refute:{w1}"),
        InferenceRule("hr_H34_unsound_r1_1", frozenset({"obs:normal_scrub_1"}), f"refute:{w1}"),
        InferenceRule("hr_H34_unsound_r2_0", frozenset({"obs:normal_scrub_0"}), f"refute:{w2}"),
        InferenceRule("hr_H34_unsound_r2_1", frozenset({"obs:normal_scrub_1"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H34",
            archetype="9_trusted_model_defect_controls",
            incident_domain=dom34,
            k_a=2,
            f_budget=1,
            resource_id=d34["resource_id"],
            exposure_domain=d34["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh34,
            observations=tuple(obs34),
            rules=tuple(rules34),
            invariants=(),
            worlds=_std_worlds(dom34),
            agent_allocation=_alloc_round_robin(obs34),
            probe_catalog=(),
            hidden_probe_outcomes={},
            true_physical_world=w1,
            true_world_safe_actions=frozenset({a1, "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=False,
            precondition_holds_at_commit=False,
            ground_truth_class="out_of_assumption_failure",
            violated_assumption_stratum="E2",
            rationale="Negative control (E2): unsound M_sys Horn rule derives unsafe storage fence from benign background scrub.",
        )
    )

    # H35: E5 >f simultaneous faulty failure domains (3 faulty domains when f=2, k_a=3)
    dom35 = "k8s_control_plane_drain"
    d35 = HELDOUT_DOMAINS[dom35]
    mh35 = _canonical_hash([HELDOUT_VERSION, "H35"])
    w0, w1, w2 = d35["worlds"]
    a0, a1 = d35["actions"][0], d35["actions"][1]
    obs35 = [
        _mk_obs(v_tmp, 0, "H35_e0", "ch_0", "fd_0", "obs:k8s_corrupt_0", resource_id=d35["resource_id"], manifest_hash=mh35),
        _mk_obs(v_tmp, 1, "H35_e1", "ch_1", "fd_1", "obs:k8s_corrupt_1", resource_id=d35["resource_id"], manifest_hash=mh35),
        _mk_obs(v_tmp, 2, "H35_e2", "ch_2", "fd_2", "obs:k8s_corrupt_2", resource_id=d35["resource_id"], manifest_hash=mh35),
        _mk_obs(v_tmp, 3, "H35_e3", "ch_3", "fd_3", "obs:pad_3", resource_id=d35["resource_id"], manifest_hash=mh35),
        _mk_obs(v_tmp, 4, "H35_e4", "ch_4", "fd_4", "obs:pad_4", resource_id=d35["resource_id"], manifest_hash=mh35),
    ]
    rules35 = [
        InferenceRule("hr_H35_a0", frozenset({"obs:k8s_corrupt_0"}), f"act:{a0}"),
        InferenceRule("hr_H35_a1", frozenset({"obs:k8s_corrupt_1"}), f"act:{a0}"),
        InferenceRule("hr_H35_a2", frozenset({"obs:k8s_corrupt_2"}), f"act:{a0}"),
        InferenceRule("hr_H35_r1_0", frozenset({"obs:k8s_corrupt_0"}), f"refute:{w1}"),
        InferenceRule("hr_H35_r1_1", frozenset({"obs:k8s_corrupt_1"}), f"refute:{w1}"),
        InferenceRule("hr_H35_r1_2", frozenset({"obs:k8s_corrupt_2"}), f"refute:{w1}"),
        InferenceRule("hr_H35_r2_0", frozenset({"obs:k8s_corrupt_0"}), f"refute:{w2}"),
        InferenceRule("hr_H35_r2_1", frozenset({"obs:k8s_corrupt_1"}), f"refute:{w2}"),
        InferenceRule("hr_H35_r2_2", frozenset({"obs:k8s_corrupt_2"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H35",
            archetype="9_trusted_model_defect_controls",
            incident_domain=dom35,
            k_a=3,
            f_budget=2,
            resource_id=d35["resource_id"],
            exposure_domain=d35["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh35,
            observations=tuple(obs35),
            rules=tuple(rules35),
            invariants=(),
            worlds=_std_worlds(dom35),
            agent_allocation=_alloc_round_robin(obs35),
            probe_catalog=(),
            hidden_probe_outcomes={},
            true_physical_world=w1,
            true_world_safe_actions=frozenset({a1, "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=False,
            precondition_holds_at_commit=False,
            ground_truth_class="out_of_assumption_failure",
            violated_assumption_stratum="E5",
            rationale="Negative control (E5): 3 simultaneous faulty failure domains exceed fault budget f=2 -> unsafe admission.",
        )
    )

    # H36: E6 unmediated external writer / stale-attestation mediation violation
    # Attestation falsely claims complete_writer_mediation=True while an unmediated external
    # operator mutated the physical plant from w0 -> w1 between v_snap and t_eff without bumping epoch!
    dom36 = "tls_ca_rotation"
    d36 = HELDOUT_DOMAINS[dom36]
    mh36 = _canonical_hash([HELDOUT_VERSION, "H36"])
    w0, w1, w2 = d36["worlds"]
    a0, a1 = d36["actions"][0], d36["actions"][1]
    obs36 = [
        _mk_obs(v_tmp, 0, "H36_e0", "ch_0", "fd_0", "obs:ca_alert_0", resource_id=d36["resource_id"], manifest_hash=mh36),
        _mk_obs(v_tmp, 1, "H36_e1", "ch_1", "fd_1", "obs:ca_alert_1", resource_id=d36["resource_id"], manifest_hash=mh36),
        _mk_obs(v_tmp, 2, "H36_e2", "ch_2", "fd_2", "obs:pad_2", resource_id=d36["resource_id"], manifest_hash=mh36),
        _mk_obs(v_tmp, 3, "H36_e3", "ch_3", "fd_3", "obs:pad_3", resource_id=d36["resource_id"], manifest_hash=mh36),
        _mk_obs(v_tmp, 4, "H36_e4", "ch_4", "fd_4", "obs:pad_4", resource_id=d36["resource_id"], manifest_hash=mh36),
    ]
    rules36 = [
        InferenceRule("hr_H36_a0", frozenset({"obs:ca_alert_0"}), f"act:{a0}"),
        InferenceRule("hr_H36_a1", frozenset({"obs:ca_alert_1"}), f"act:{a0}"),
        InferenceRule("hr_H36_r1_0", frozenset({"obs:ca_alert_0"}), f"refute:{w1}"),
        InferenceRule("hr_H36_r1_1", frozenset({"obs:ca_alert_1"}), f"refute:{w1}"),
        InferenceRule("hr_H36_r2_0", frozenset({"obs:ca_alert_0"}), f"refute:{w2}"),
        InferenceRule("hr_H36_r2_1", frozenset({"obs:ca_alert_1"}), f"refute:{w2}"),
    ]
    cases.append(
        HeldOutCase(
            case_id="H36",
            archetype="9_trusted_model_defect_controls",
            incident_domain=dom36,
            k_a=2,
            f_budget=1,
            resource_id=d36["resource_id"],
            exposure_domain=d36["exposure_domain"],
            epoch=100,
            snapshot_version=50,
            manifest_hash=mh36,
            observations=tuple(obs36),
            rules=tuple(rules36),
            invariants=(),
            worlds=_std_worlds(dom36),
            agent_allocation=_alloc_round_robin(obs36),
            probe_catalog=(),
            hidden_probe_outcomes={},
            complete_writer_mediation=True,  # Attested true, but physically bypassed out-of-band!
            true_physical_world=w1,  # External unmediated writer flipped plant state without bumping epoch
            true_world_safe_actions=frozenset({a1, "defer", "probe"}),
            expected_safe_action=None,
            precondition_holds_at_snap=True,
            precondition_holds_at_commit=False,
            ground_truth_class="out_of_assumption_failure",
            violated_assumption_stratum="E6",
            rationale="Negative control (E6): unmediated out-of-band writer silently rotated plant state without bumping target epoch.",
        )
    )

    assert len(cases) == 36
    return cases


def build_heldout_manifest(cases: Optional[Sequence[HeldOutCase]] = None) -> Dict[str, Any]:
    cases = build_heldout_cases() if cases is None else list(cases)
    by_class: Dict[str, List[str]] = {c: [] for c in GROUND_TRUTH_CLASSES}
    by_arch: Dict[str, List[str]] = {}
    for c in cases:
        by_class[c.ground_truth_class].append(c.case_id)
        by_arch.setdefault(c.archetype, []).append(c.case_id)
    return {
        "heldout_version": HELDOUT_VERSION,
        "case_count": len(cases),
        "incident_domains": sorted(HELDOUT_DOMAINS.keys()),
        "dataset_public_hash": _canonical_hash([c.public_spec_hash() for c in cases]),
        "dataset_full_hash": _canonical_hash(
            [(c.case_id, c.ground_truth_class, c.true_physical_world, c.expected_safe_action, c.public_spec_hash()) for c in cases]
        ),
        "ground_truth_class_counts": {k: len(v) for k, v in by_class.items()},
        "ground_truth_class_ids": by_class,
        "archetype_ids": by_arch,
        "disjointness_guarantee": "100% disjoint from pieq/live_cases.py in incident domains, worlds, actions, rules, and predicates.",
    }
