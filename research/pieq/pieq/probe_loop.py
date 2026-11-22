"""
Revision 7 P1 + Revision 8 P1.3 — Bounded Read-Only Active Diagnostic Probe Loop
with Multi-Round Temporal & Snapshot Consistency Auditing.

Turns conservative uncertainty into an actionable diagnostic workflow without weakening
any deterministic verification, snapshot-consistency, or failure-domain provenance invariant:
  1. Identifies unresolved plausible worlds hat{B}_{tau,v}^{(f)} and missing positive quorums.
  2. Establishes an immutable snapshot anchor (epoch, observation_version, resource_id,
     manifest_hash) and optional concurrent-commit / manifest observers across probing rounds.
  3. Filters candidate probes through strict read-only, load-cap, and safety-contract checks.
  4. Ranks probes via transparent deterministic worst-case world reduction / partition
     discrimination (or calibrated EVSI when calibrated priors & utilities are supplied).
  5. Executes the selected probe through a trusted read-only collector interface.
  6. Audits temporal/snapshot consistency across all 7 P1.3 boundaries:
       (1) plant changes between initial telemetry and a probe,
       (2) concurrent commits during probe execution,
       (3) stale observation handles reused after version advancement,
       (4) different collectors observing different consistent snapshots,
       (5) probe retries returning values from different state epochs,
       (6) topology-manifest or failure-domain changes, and
       (7) previously verified proofs invalidated by newly observed P_sys conflicts (nogoods).
  7. Registers collector-signed BoundPrimitiveAssumption with authentic failure_domain,
     checks P_sys invariants to register newly triggered nogoods, and fires only
     snapshot-consistent M_sys rules.
  8. Preserves recursive failure-domain provenance (a probe sharing a failure domain with an
     existing passive sensor or earlier probe NEVER inflates nu_pack).
  9. Re-evaluates ATMS, plausible-world belief set, positive packing, and compound safety gate
     with strict cross-proof snapshot consistency (failing closed conservatively if snapshot
     consistency cannot be established).
 10. Returns a structured ADMIT_MUTATION or DEFER_ESCALATE outcome with full temporal audit log.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import time
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    FaultWorld,
    PlausibleWorldGate,
    compute_disjoint_justification_packing_bounded,
    filter_snapshot_consistent_witnesses,
)


@dataclass(frozen=True)
class DiagnosticProbeSpec:
    """Trusted specification and safety contract for a read-only diagnostic probe."""

    probe_id: str
    collector_id: str
    failure_domain: str  # True physical failure domain (never spoofed as independent!)
    read_only: bool
    safety_contract_verified: bool
    cost_units: float = 1.0
    estimated_latency_ms: float = 15.0
    load_weight: int = 1
    # Maps world_id -> proposition expected if executed in that world (for partition ranking)
    world_outcome_map: Mapping[str, str] = field(default_factory=dict)
    # Actions whose positive preconditions this probe can help witness
    target_actions: Tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ProbeBudget:
    """Strict termination bounds for active diagnostic probing."""

    max_invocations: int = 3
    max_cost_units: float = 5.0
    max_time_ms: float = 500.0
    max_load_units: int = 4


@dataclass(frozen=True)
class TemporalAuditRecord:
    """Per-round temporal & snapshot-consistency audit record (Revision 8 P1.3)."""

    round_index: int
    probe_id: str
    anchor_snapshot: Optional[Tuple[int, int, str, str]]
    observed_snapshot: Optional[Tuple[int, int, str, str]]
    commit_version_before: Optional[int]
    commit_version_after: Optional[int]
    manifest_before: Optional[str]
    manifest_after: Optional[str]
    consistent: bool
    violation_reason: Optional[str]
    newly_registered_nogoods: Tuple[str, ...] = ()
    invalidated_prior_proofs: int = 0


@dataclass(frozen=True)
class ProbeExecutionRecord:
    probe_id: str
    failure_domain: str
    collector_id: str
    proposition: str
    assumption_id: str
    accepted_by_atms: bool
    cost_units: float
    latency_ms: float
    worlds_before: Tuple[str, ...]
    worlds_after: Tuple[str, ...]
    max_packing_before: int
    max_packing_after: int
    was_effective: bool
    derived_consequents: Tuple[str, ...]
    ranking_score: float


@dataclass(frozen=True)
class ProbeLoopResult:
    outcome: str  # "ADMIT_MUTATION" or "DEFER_ESCALATE"
    admitted_action: Optional[str]
    escalation_reason: Optional[str]
    surviving_worlds: Tuple[str, ...]
    action_packing: Dict[str, int]
    refutation_packing: Dict[str, int]
    executed_probes: Tuple[ProbeExecutionRecord, ...]
    rejected_unsafe_probes: Tuple[Tuple[str, str], ...]
    probes_executed: int
    probes_effective: int
    total_cost_units: float
    total_load_units: int
    elapsed_ms: float
    budget_exhausted_reason: Optional[str]
    packing_budget_truncated: bool
    temporal_consistency_ok: bool = True
    temporal_audit_log: Tuple[TemporalAuditRecord, ...] = ()
    temporal_violation_reasons: Tuple[str, ...] = ()


def _snapshot_sig_of_assumption(a: BoundPrimitiveAssumption) -> Tuple[int, int, str, str]:
    return (
        a.epoch_revision,
        a.observation_version,
        getattr(a, "resource_id", ""),
        getattr(a, "manifest_hash", ""),
    )


def _derive_anchor_snapshot(
    atms: BoundedATMSEngine,
    expected_snapshot_version: Optional[int],
    require_consistent_observation_version: bool,
) -> Tuple[Optional[Tuple[int, int, str, str]], Optional[str]]:
    """
    Determine the consistent active snapshot `(epoch, obs_version, resource_id, manifest_hash)`
    from already-registered ATMS assumptions and configured bindings.
    Returns `(anchor, violation_reason)`. If existing passive observations already span
    multiple snapshots/epochs/manifests under strict snapshot checking, fails closed.
    """
    existing = list(atms.assumptions_by_bit.values())
    if not existing:
        if expected_snapshot_version is not None and atms.expected_epoch is not None:
            return (
                (
                    atms.expected_epoch,
                    expected_snapshot_version,
                    atms.expected_resource_id or "",
                    atms.expected_manifest_hash or "",
                ),
                None,
            )
        return None, None

    sigs = {_snapshot_sig_of_assumption(a) for a in existing}
    if require_consistent_observation_version:
        if len(sigs) > 1:
            return None, "TEMPORAL_VIOLATION_PRE_PROBE_MIXED_SNAPSHOTS"
        anchor = next(iter(sigs))
        if expected_snapshot_version is not None and anchor[1] != expected_snapshot_version:
            return anchor, "TEMPORAL_VIOLATION_PRE_PROBE_VERSION_MISMATCH"
        return anchor, None

    # Non-strict legacy mode (e.g., offline r4 replay): anchor epoch/resource/manifest if uniform
    anchor = next(iter(sigs)) if len(sigs) == 1 else None
    return anchor, None


def _count_all_active_proofs(atms: BoundedATMSEngine) -> int:
    return sum(len(atms.active_witness_justifications(c)) for c in atms.justifications)


def _check_and_register_probe_nogoods(
    atms: BoundedATMSEngine,
    obs: BoundPrimitiveAssumption,
    *,
    require_consistent_observation_version: bool,
) -> Tuple[Tuple[str, ...], int, int]:
    """
    Revision 8 P1.3 Boundary #7:
    After registering a new probe observation `obs`, deterministically evaluate all
    trusted `P_sys` conflict invariants involving `obs.proposition` over snapshot-
    consistent registered assumptions.
      1. Registers every verified `NogoodCertificate` in `atms`, purging conflicting
         superset environments via Theorem 2(2).
      2. Computes `contested_leaf_mask`: the bitmask of prior leaves directly paired
         with `obs` in a newly verified conflict nogood (since `obs` and `e_prev`
         cannot simultaneously hold in one physical snapshot, prior proofs relying
         on `e_prev` are conservatively invalidated for positive admission and world
         pruning until uncontested independent evidence resolves the conflict).
    Returns `(newly_registered_invariant_ids, invalidated_prior_proofs, contested_leaf_mask)`.
    """
    if not atms.verifier.invariants:
        return (), 0, 0
    obs_sig = _snapshot_sig_of_assumption(obs)
    by_prop: Dict[str, List[BoundPrimitiveAssumption]] = {}
    for a in atms.assumptions_by_bit.values():
        if require_consistent_observation_version and _snapshot_sig_of_assumption(a) != obs_sig:
            continue
        by_prop.setdefault(a.proposition, []).append(a)

    # Snapshot active proof masks prior to registering probe nogoods:
    prior_active_masks: List[int] = [
        j.env_mask
        for cons in atms.justifications
        for j in atms.active_witness_justifications(cons)
    ]
    registered_ids: List[str] = []
    contested_mask = 0
    for inv in sorted(atms.verifier.invariants.values(), key=lambda x: x.invariant_id):
        if obs.proposition not in inv.conflict_predicates:
            continue
        if not all(p in by_prop for p in inv.conflict_predicates):
            continue
        other_props = sorted(p for p in inv.conflict_predicates if p != obs.proposition)
        if not other_props:
            cert = atms.register_nogood("probe_tcb_audit", 1 << obs.bit_index, inv.invariant_id)
            if cert is not None:
                registered_ids.append(inv.invariant_id)
                contested_mask |= 1 << obs.bit_index
            continue
        combos: List[List[BoundPrimitiveAssumption]] = [[obs]]
        for p in other_props:
            next_combos: List[List[BoundPrimitiveAssumption]] = []
            for base in combos:
                for cand in by_prop[p]:
                    next_combos.append(base + [cand])
            combos = next_combos
        for combo in combos:
            ng_mask = 0
            for item in combo:
                ng_mask |= 1 << item.bit_index
            cert = atms.register_nogood("probe_tcb_audit", ng_mask, inv.invariant_id)
            if cert is not None:
                registered_ids.append(inv.invariant_id)
                contested_mask |= ng_mask

    invalidated = sum(
        1 for m in prior_active_masks
        if (not atms.is_consistent(m)) or ((m & contested_mask) != 0)
    )
    return tuple(sorted(set(registered_ids))), invalidated, contested_mask


def _evaluate_gate_state(
    *,
    atms: BoundedATMSEngine,
    worlds: Sequence[FaultWorld],
    candidate_actions: Sequence[str],
    incident_class: str,
    f_fault_budget: int,
    k_a: int,
    require_positive_packing: bool = True,
    require_consistent_observation_version: bool = True,
    expected_snapshot_version: Optional[int] = None,
    packing_expansions: int = 10000,
    inflight_mutations: Sequence = (),
    contested_leaf_mask: int = 0,
) -> Tuple[List[FaultWorld], Dict[str, int], Dict[str, int], List[str], bool, bool]:
    """
    Run the unchanged core TCB PlausibleWorldGate + bounded disjoint packing,
    enforcing Revision 8 P1.3 snapshot consistency across BOTH refutation packing
    AND positive proof packing whenever `require_consistent_observation_version=True`,
    and conservatively excluding any proofs whose leaves are contested by a newly
    registered probe-triggered `P_sys` conflict nogood.
    Returns `(survivors, act_pack, ref_pack, eligible, truncated, snapshot_inconsistent)`.
    """
    gate = PlausibleWorldGate(worlds, ["defer", "probe"])

    def _uncontested(wits: Sequence) -> List:
        if contested_leaf_mask == 0:
            return list(wits)
        return [j for j in wits if (j.env_mask & contested_leaf_mask) == 0]

    refs = {
        w.world_id: _uncontested(atms.active_witness_justifications(f"refute:{w.world_id}"))
        for w in worlds
    }
    check_snap = require_consistent_observation_version or (expected_snapshot_version is not None)
    survivors = gate.compute_fault_tolerant_belief_set(
        refs,
        atms.assumptions_by_bit,
        fault_budget_f=f_fault_budget,
        budget_exceeded=atms.budget_exceeded,
        max_packing_expansions=packing_expansions,
        expected_snapshot_version=expected_snapshot_version,
        require_consistent_observation_version=require_consistent_observation_version,
        expected_epoch=atms.expected_epoch if check_snap else None,
        expected_resource_id=atms.expected_resource_id if check_snap else None,
        expected_manifest_hash=atms.expected_manifest_hash if check_snap else None,
    )
    truncated = atms.budget_exceeded
    snapshot_inconsistent = False
    ref_pack: Dict[str, int] = {}
    for wid, witnesses in refs.items():
        filtered_refs = list(witnesses)
        if check_snap:
            filtered_refs, inc_r, _, _ = filter_snapshot_consistent_witnesses(
                filtered_refs,
                atms.assumptions_by_bit,
                expected_snapshot_version=expected_snapshot_version,
                expected_epoch=atms.expected_epoch,
                expected_resource_id=atms.expected_resource_id,
                expected_manifest_hash=atms.expected_manifest_hash,
            )
            snapshot_inconsistent |= inc_r
        cnt, _, ex = compute_disjoint_justification_packing_bounded(
            filtered_refs, atms.assumptions_by_bit, max_expansions=packing_expansions
        )
        truncated |= ex
        ref_pack[wid] = cnt

    act_pack: Dict[str, int] = {}
    eligible: List[str] = []
    for action in sorted(candidate_actions):
        if action in {"defer", "probe"}:
            continue
        pos_wits = _uncontested(atms.active_witness_justifications(f"act:{action}"))
        pos_inc = False
        if check_snap:
            pos_wits, pos_inc, _, _ = filter_snapshot_consistent_witnesses(
                pos_wits,
                atms.assumptions_by_bit,
                expected_snapshot_version=expected_snapshot_version,
                expected_epoch=atms.expected_epoch,
                expected_resource_id=atms.expected_resource_id,
                expected_manifest_hash=atms.expected_manifest_hash,
            )
            snapshot_inconsistent |= pos_inc
        cnt, _, ex = compute_disjoint_justification_packing_bounded(
            pos_wits,
            atms.assumptions_by_bit,
            max_expansions=packing_expansions,
        )
        truncated |= ex
        act_pack[action] = cnt
        ok, _ = gate.evaluate_concurrent_compound_safety(
            action, incident_class, 0, survivors, inflight_mutations
        )
        pos_ok = (cnt >= k_a) if require_positive_packing else True
        if not ex and not pos_inc and ok and pos_ok:
            eligible.append(action)

    return survivors, act_pack, ref_pack, eligible, truncated, snapshot_inconsistent


def score_probe_worst_case_reduction(
    probe: DiagnosticProbeSpec,
    surviving_worlds: Sequence[FaultWorld],
    existing_domains: FrozenSet[str],
    action_packing: Mapping[str, int],
    k_a: int,
    action_witnessed_domains: Optional[Mapping[str, FrozenSet[str]]] = None,
) -> Tuple[float, bool]:
    """
    Transparent deterministic probe ranking criterion (when calibrated priors are absent):
      1. Domain novelty: probes from a fresh physical failure_domain strictly outrank
         redundant probes sharing an already-observed failure domain.
      2. Worst-case world reduction: |B| - max_o |{s in B : outcome(probe, s) == o}|.
      3. Positive-quorum completion bonus: if a single world survives (or target_actions
         are safe in some surviving world) and act_pack < k_a, probes witnessing that
         action from a failure domain not yet witnessing that action earn positive utility.
      4. Penalize cost_units so cheaper equally informative probes run first.
    Returns (score, has_diagnostic_value).
    """
    n_surv = len(surviving_worlds)
    is_fresh_domain = probe.failure_domain not in existing_domains

    # Partition discrimination over currently surviving worlds:
    buckets: Counter[str] = Counter()
    for w in surviving_worlds:
        outcome = probe.world_outcome_map.get(w.world_id, "__unobserved__")
        buckets[outcome] += 1
    max_bucket = max(buckets.values()) if buckets else n_surv
    worst_case_reduction = max(0, n_surv - max_bucket)
    distinct_buckets = len([k for k in buckets if k != "__unobserved__"])

    # Does the probe help close an incomplete positive quorum or world refutation?
    surv_safe_union: Set[str] = set()
    for w in surviving_worlds:
        surv_safe_union |= set(w.safe_mutations) - {"defer", "probe"}
    needs_positive_fresh = any(
        (a in surv_safe_union)
        and (action_packing.get(a, 0) < k_a)
        and (
            probe.failure_domain not in action_witnessed_domains.get(a, frozenset())
            if action_witnessed_domains is not None
            else is_fresh_domain
        )
        for a in probe.target_actions
    )
    can_refute_surviving = distinct_buckets >= 1 and n_surv > 1

    has_value = (worst_case_reduction > 0) or needs_positive_fresh or (
        can_refute_surviving and is_fresh_domain
    )
    if not has_value:
        return (-1e9, False)

    domain_bonus = 10.0 if (is_fresh_domain or needs_positive_fresh) else 0.0
    positive_bonus = 4.0 if needs_positive_fresh else 0.0
    refute_bonus = 2.0 if (can_refute_surviving and is_fresh_domain) else 0.0
    score = (
        domain_bonus
        + 5.0 * float(worst_case_reduction)
        + 1.0 * float(distinct_buckets)
        + positive_bonus
        + refute_bonus
        - 0.25 * float(probe.cost_units)
        - 0.001 * float(probe.estimated_latency_ms)
    )
    return (score, True)


def score_probe_calibrated_evsi(
    probe: DiagnosticProbeSpec,
    surviving_worlds: Sequence[FaultWorld],
    calibrated_priors: Mapping[str, float],
    action_utilities: Mapping[Tuple[str, str], float],
    candidate_actions: Sequence[str],
    existing_domains: FrozenSet[str],
) -> Tuple[float, bool]:
    """
    Calibrated Expected Value of Sample Information (EVSI):
      EVSI(p) = sum_o P(o) * max_a E[U(a, s) | o] - max_a E[U(a, s)] - cost(p).
    Strictly validates calibrated priors on surviving worlds before use.
    """
    surv_ids = [w.world_id for w in surviving_worlds]
    raw_weights = [float(calibrated_priors.get(wid, 0.0)) for wid in surv_ids]
    total_w = sum(raw_weights)
    if total_w <= 0.0 or not all(math.isfinite(x) and x >= 0.0 for x in raw_weights):
        raise ValueError("calibrated_priors must provide finite non-negative mass on surviving worlds")
    priors = {wid: w / total_w for wid, w in zip(surv_ids, raw_weights)}

    actions = list(candidate_actions)
    baseline_eu = max(
        sum(priors[wid] * float(action_utilities.get((a, wid), 0.0)) for wid in surv_ids)
        for a in actions
    )

    by_outcome: Dict[str, List[str]] = {}
    for wid in surv_ids:
        o = probe.world_outcome_map.get(wid, "__unobserved__")
        by_outcome.setdefault(o, []).append(wid)

    posterior_eu = 0.0
    for o, wids in by_outcome.items():
        p_o = sum(priors[wid] for wid in wids)
        if p_o <= 0.0:
            continue
        best_a_eu = max(
            sum((priors[wid] / p_o) * float(action_utilities.get((a, wid), 0.0)) for wid in wids)
            for a in actions
        )
        posterior_eu += p_o * best_a_eu

    evsi = (posterior_eu - baseline_eu) - float(probe.cost_units)
    if probe.failure_domain in existing_domains and evsi <= 0.0:
        return (evsi, False)
    return (evsi, evsi > 0.0)


def run_active_diagnostic_probe_loop(
    *,
    atms: BoundedATMSEngine,
    worlds: Sequence[FaultWorld],
    candidate_actions: Sequence[str],
    incident_class: str,
    f_fault_budget: int,
    k_a: int,
    available_probes: Sequence[DiagnosticProbeSpec],
    probe_executor: Callable[[DiagnosticProbeSpec, int], BoundPrimitiveAssumption],
    budget: ProbeBudget = ProbeBudget(),
    require_positive_packing: bool = True,
    require_consistent_observation_version: bool = True,
    expected_snapshot_version: Optional[int] = None,
    packing_expansions: int = 10000,
    ranking_mode: str = "worst_case_reduction",
    calibrated_priors: Optional[Mapping[str, float]] = None,
    action_utilities: Optional[Mapping[Tuple[str, str], float]] = None,
    inflight_mutations: Sequence = (),
    version_observer: Optional[Callable[[], int]] = None,
    manifest_observer: Optional[Callable[[], str]] = None,
) -> ProbeLoopResult:
    """
    Execute the bounded 10-step active diagnostic probe workflow with Revision 8 P1.3
    cross-round temporal & snapshot-consistency auditing.

    Conservative rule: if snapshot consistency cannot be established (plant version drift,
    concurrent commits during probing, stale handle reuse, mixed collector snapshots,
    retry epoch drift, manifest/domain rotation), fail closed (`DEFER_ESCALATE`) and
    retain potentially true worlds rather than inferring false certainty.
    """
    t0 = time.perf_counter()
    executed_records: List[ProbeExecutionRecord] = []
    temporal_audit_log: List[TemporalAuditRecord] = []
    temporal_violations: List[str] = []
    rejected_unsafe: List[Tuple[str, str]] = []
    executed_ids: Set[str] = set()
    total_cost = 0.0
    total_load = 0
    budget_exhausted_reason: Optional[str] = None
    contested_leaf_mask: int = 0

    # Pre-screen probe safety contracts fail-closed:
    safe_probes: List[DiagnosticProbeSpec] = []
    for p in available_probes:
        if not p.read_only:
            rejected_unsafe.append((p.probe_id, "REJECTED_NON_READ_ONLY_PROBE"))
            continue
        if not p.safety_contract_verified:
            rejected_unsafe.append((p.probe_id, "REJECTED_UNVERIFIED_PROBE_SAFETY_CONTRACT"))
            continue
        if not math.isfinite(p.cost_units) or p.cost_units < 0 or p.load_weight < 0:
            rejected_unsafe.append((p.probe_id, "REJECTED_INVALID_PROBE_COST_OR_LOAD"))
            continue
        safe_probes.append(p)

    # Establish snapshot anchor + initial target commit version & manifest hash:
    anchor_snapshot, pre_violation = _derive_anchor_snapshot(
        atms, expected_snapshot_version, require_consistent_observation_version
    )
    effective_expected_ver = (
        expected_snapshot_version
        if expected_snapshot_version is not None
        else (anchor_snapshot[1] if (anchor_snapshot is not None and require_consistent_observation_version) else None)
    )
    initial_commit_ver = version_observer() if version_observer is not None else None
    initial_manifest = manifest_observer() if manifest_observer is not None else (
        anchor_snapshot[3] if anchor_snapshot is not None else atms.expected_manifest_hash
    )

    if pre_violation is not None:
        temporal_violations.append(pre_violation)
        temporal_audit_log.append(
            TemporalAuditRecord(
                round_index=0,
                probe_id="__pre_probe_audit__",
                anchor_snapshot=anchor_snapshot,
                observed_snapshot=None,
                commit_version_before=initial_commit_ver,
                commit_version_after=initial_commit_ver,
                manifest_before=initial_manifest,
                manifest_after=initial_manifest,
                consistent=False,
                violation_reason=pre_violation,
            )
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return ProbeLoopResult(
            outcome="DEFER_ESCALATE",
            admitted_action=None,
            escalation_reason=f"DEFER_SNAPSHOT_INCONSISTENT:{pre_violation}",
            surviving_worlds=tuple(sorted(w.world_id for w in worlds)),
            action_packing={a: 0 for a in sorted(candidate_actions) if a not in {"defer", "probe"}},
            refutation_packing={w.world_id: 0 for w in worlds},
            executed_probes=(),
            rejected_unsafe_probes=tuple(rejected_unsafe),
            probes_executed=0,
            probes_effective=0,
            total_cost_units=0.0,
            total_load_units=0,
            elapsed_ms=elapsed_ms,
            budget_exhausted_reason=None,
            packing_budget_truncated=atms.budget_exceeded,
            temporal_consistency_ok=False,
            temporal_audit_log=tuple(temporal_audit_log),
            temporal_violation_reasons=tuple(temporal_violations),
        )

    # Step 1: Evaluate current passive ATMS state before probing
    survivors, act_pack, ref_pack, eligible, truncated, snap_inc = _evaluate_gate_state(
        atms=atms,
        worlds=worlds,
        candidate_actions=candidate_actions,
        incident_class=incident_class,
        f_fault_budget=f_fault_budget,
        k_a=k_a,
        require_positive_packing=require_positive_packing,
        require_consistent_observation_version=require_consistent_observation_version,
        expected_snapshot_version=effective_expected_ver,
        packing_expansions=packing_expansions,
        inflight_mutations=inflight_mutations,
    )
    if snap_inc:
        temporal_violations.append("TEMPORAL_VIOLATION_PASSIVE_PROOF_SNAPSHOT_INCONSISTENT")

    if len(eligible) == 1 and not truncated and not temporal_violations:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return ProbeLoopResult(
            outcome="ADMIT_MUTATION",
            admitted_action=eligible[0],
            escalation_reason=None,
            surviving_worlds=tuple(sorted(w.world_id for w in survivors)),
            action_packing=act_pack,
            refutation_packing=ref_pack,
            executed_probes=(),
            rejected_unsafe_probes=tuple(rejected_unsafe),
            probes_executed=0,
            probes_effective=0,
            total_cost_units=0.0,
            total_load_units=0,
            elapsed_ms=elapsed_ms,
            budget_exhausted_reason=None,
            packing_budget_truncated=truncated,
            temporal_consistency_ok=True,
            temporal_audit_log=tuple(temporal_audit_log),
            temporal_violation_reasons=(),
        )

    # Iterative bounded probing loop (Steps 2-9)
    while True:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if len(executed_records) >= budget.max_invocations:
            budget_exhausted_reason = "MAX_INVOCATIONS_REACHED"
            break
        if elapsed_ms >= budget.max_time_ms:
            budget_exhausted_reason = "MAX_TIME_MS_REACHED"
            break

        # Check concurrent commit or manifest rotation BEFORE dispatching next probe:
        commit_before = version_observer() if version_observer is not None else initial_commit_ver
        manifest_before = manifest_observer() if manifest_observer is not None else initial_manifest
        if initial_commit_ver is not None and commit_before != initial_commit_ver:
            v_reason = "TEMPORAL_VIOLATION_CONCURRENT_COMMIT_DURING_PROBE"
            temporal_violations.append(v_reason)
            temporal_audit_log.append(
                TemporalAuditRecord(
                    round_index=len(executed_records) + 1,
                    probe_id="__pre_dispatch_check__",
                    anchor_snapshot=anchor_snapshot,
                    observed_snapshot=None,
                    commit_version_before=initial_commit_ver,
                    commit_version_after=commit_before,
                    manifest_before=initial_manifest,
                    manifest_after=manifest_before,
                    consistent=False,
                    violation_reason=v_reason,
                )
            )
            break
        if initial_manifest is not None and manifest_before != initial_manifest:
            v_reason = "TEMPORAL_VIOLATION_MANIFEST_ROTATED_DURING_PROBE"
            temporal_violations.append(v_reason)
            temporal_audit_log.append(
                TemporalAuditRecord(
                    round_index=len(executed_records) + 1,
                    probe_id="__pre_dispatch_check__",
                    anchor_snapshot=anchor_snapshot,
                    observed_snapshot=None,
                    commit_version_before=initial_commit_ver,
                    commit_version_after=commit_before,
                    manifest_before=initial_manifest,
                    manifest_after=manifest_before,
                    consistent=False,
                    violation_reason=v_reason,
                )
            )
            break

        # Domains already consumed by active verified justifications (plus already-probed domains):
        witnessed_domains_set: Set[str] = {r.failure_domain for r in executed_records}
        action_witnessed: Dict[str, Set[str]] = {}
        for cons, just_list in atms.justifications.items():
            for just in just_list:
                if atms.is_consistent(just.env_mask):
                    m = just.env_mask
                    while m:
                        lsb = m & -m
                        bit = lsb.bit_length() - 1
                        if bit in atms.assumptions_by_bit:
                            dom = atms.assumptions_by_bit[bit].failure_domain
                            witnessed_domains_set.add(dom)
                            if cons.startswith("act:"):
                                action_witnessed.setdefault(cons[4:], set()).add(dom)
                        m ^= lsb
        existing_domains = frozenset(witnessed_domains_set)
        action_witnessed_frozen = {k: frozenset(v) for k, v in action_witnessed.items()}

        # Filter remaining safe probes fitting inside remaining cost/load budgets:
        ranked: List[Tuple[float, str, DiagnosticProbeSpec]] = []
        any_over_budget = False
        for p in safe_probes:
            if p.probe_id in executed_ids:
                continue
            if total_cost + p.cost_units > budget.max_cost_units + 1e-12:
                any_over_budget = True
                continue
            if total_load + p.load_weight > budget.max_load_units:
                any_over_budget = True
                continue
            if ranking_mode == "calibrated_evsi":
                if calibrated_priors is None or action_utilities is None:
                    raise ValueError("calibrated_evsi mode requires calibrated_priors and action_utilities")
                score, valuable = score_probe_calibrated_evsi(
                    p, survivors, calibrated_priors, action_utilities, candidate_actions, existing_domains
                )
            else:
                score, valuable = score_probe_worst_case_reduction(
                    p, survivors, existing_domains, act_pack, k_a, action_witnessed_frozen
                )
            if valuable:
                ranked.append((score, p.probe_id, p))

        if not ranked:
            if any_over_budget:
                budget_exhausted_reason = "MAX_COST_OR_LOAD_REACHED"
            break

        # Deterministic selection: highest score, tie-break alphabetically by probe_id
        ranked.sort(key=lambda item: (-item[0], item[1]))
        chosen_score, _, chosen = ranked[0]
        executed_ids.add(chosen.probe_id)

        # Allocate next free bit index (< 64) for the new authenticated observation:
        used_bits = set(atms.assumptions_by_bit.keys())
        free_bits = [b for b in range(atms.max_assumptions) if b not in used_bits]
        if not free_bits:
            budget_exhausted_reason = "ATMS_BIT_CAPACITY_EXHAUSTED"
            break
        next_bit = free_bits[0]

        worlds_before = tuple(sorted(w.world_id for w in survivors))
        max_pack_before = max(act_pack.values()) if act_pack else 0

        # Step 5: Execute trusted read-only probe
        p_t0 = time.perf_counter()
        obs = probe_executor(chosen, next_bit)
        p_lat = (time.perf_counter() - p_t0) * 1000.0 + float(chosen.estimated_latency_ms)
        total_cost += float(chosen.cost_units)
        total_load += int(chosen.load_weight)

        commit_after = version_observer() if version_observer is not None else initial_commit_ver
        manifest_after = manifest_observer() if manifest_observer is not None else initial_manifest
        obs_sig = _snapshot_sig_of_assumption(obs)

        # If no passive observations existed yet, anchor to the first consistent probe snapshot:
        if anchor_snapshot is None:
            anchor_snapshot = obs_sig
            if require_consistent_observation_version and effective_expected_ver is None:
                effective_expected_ver = obs_sig[1]

        # Step 6: Revision 8 P1.3 Temporal & Provenance Consistency Audit across all 7 boundaries
        round_violation: Optional[str] = None
        if initial_commit_ver is not None and commit_after != initial_commit_ver:
            round_violation = "TEMPORAL_VIOLATION_CONCURRENT_COMMIT_DURING_PROBE"
        elif initial_manifest is not None and manifest_after != initial_manifest:
            round_violation = "TEMPORAL_VIOLATION_MANIFEST_ROTATED_DURING_PROBE"
        elif obs.failure_domain != chosen.failure_domain:
            round_violation = "TEMPORAL_VIOLATION_FAILURE_DOMAIN_CHANGED"
        elif anchor_snapshot is not None and obs_sig[3] != anchor_snapshot[3]:
            round_violation = "TEMPORAL_VIOLATION_MANIFEST_HASH_CHANGED"
        elif anchor_snapshot is not None and obs_sig[2] != anchor_snapshot[2]:
            round_violation = "TEMPORAL_VIOLATION_RESOURCE_ID_MISMATCH"
        elif anchor_snapshot is not None and obs_sig[0] != anchor_snapshot[0]:
            round_violation = "TEMPORAL_VIOLATION_EPOCH_DRIFT"
        elif (
            require_consistent_observation_version
            and anchor_snapshot is not None
            and obs_sig[1] != anchor_snapshot[1]
        ):
            round_violation = "TEMPORAL_VIOLATION_OBSERVATION_VERSION_DRIFT"
        elif (
            effective_expected_ver is not None
            and require_consistent_observation_version
            and obs_sig[1] != effective_expected_ver
        ):
            round_violation = "TEMPORAL_VIOLATION_OBSERVATION_VERSION_DRIFT"

        derived_consequents: List[str] = []
        new_nogoods: Tuple[str, ...] = ()
        invalidated_proofs = 0

        if round_violation is not None:
            accepted_by_atms = False
            temporal_violations.append(round_violation)
            temporal_audit_log.append(
                TemporalAuditRecord(
                    round_index=len(executed_records) + 1,
                    probe_id=chosen.probe_id,
                    anchor_snapshot=anchor_snapshot,
                    observed_snapshot=obs_sig,
                    commit_version_before=commit_before,
                    commit_version_after=commit_after,
                    manifest_before=manifest_before,
                    manifest_after=manifest_after,
                    consistent=False,
                    violation_reason=round_violation,
                )
            )
        else:
            # Register authenticated observation in ATMS (checks signature, epoch, resource, manifest)
            accepted_by_atms = atms.register_assumption(obs)
            if accepted_by_atms:
                # Boundary #7: Deterministically check P_sys invariants so any newly observed
                # contradiction immediately registers a NogoodCertificate and deactivates prior proofs!
                new_nogoods, invalidated_proofs, round_contested = _check_and_register_probe_nogoods(
                    atms, obs, require_consistent_observation_version=require_consistent_observation_version
                )
                contested_leaf_mask |= round_contested

                # Boundary #3: Deterministically fire trusted M_sys rules ONLY combining `obs`
                # with assumptions matching the EXACT SAME consistent snapshot signature!
                by_prop: Dict[str, List[BoundPrimitiveAssumption]] = {}
                for a in atms.assumptions_by_bit.values():
                    if require_consistent_observation_version and _snapshot_sig_of_assumption(a) != obs_sig:
                        continue
                    by_prop.setdefault(a.proposition, []).append(a)

                for rule in sorted(atms.verifier.rules.values(), key=lambda r: r.rule_id):
                    if obs.proposition not in rule.antecedents:
                        continue
                    if not all(ant in by_prop for ant in rule.antecedents):
                        continue
                    chosen_assumps: List[BoundPrimitiveAssumption] = [obs]
                    used_doms = {obs.failure_domain}
                    complete = True
                    for ant in sorted(rule.antecedents):
                        if ant == obs.proposition:
                            continue
                        candidates = sorted(
                            by_prop[ant],
                            key=lambda x: (x.failure_domain in used_doms, x.bit_index),
                        )
                        if not candidates:
                            complete = False
                            break
                        pick = candidates[0]
                        chosen_assumps.append(pick)
                        used_doms.add(pick.failure_domain)
                    if not complete:
                        continue
                    env_mask = 0
                    for ca in chosen_assumps:
                        env_mask |= 1 << ca.bit_index
                    cert = atms.add_verified_justification(
                        f"probe:{chosen.probe_id}",
                        f"probe:{chosen.probe_id}:step_{len(executed_records)}",
                        rule.consequent,
                        env_mask,
                        rule.rule_id,
                    )
                    if cert is not None:
                        derived_consequents.append(rule.consequent)

            temporal_audit_log.append(
                TemporalAuditRecord(
                    round_index=len(executed_records) + 1,
                    probe_id=chosen.probe_id,
                    anchor_snapshot=anchor_snapshot,
                    observed_snapshot=obs_sig,
                    commit_version_before=commit_before,
                    commit_version_after=commit_after,
                    manifest_before=manifest_before,
                    manifest_after=manifest_after,
                    consistent=True,
                    violation_reason=None,
                    newly_registered_nogoods=new_nogoods,
                    invalidated_prior_proofs=invalidated_proofs,
                )
            )

        # Step 7 & 8: Re-evaluate belief set, positive packing, and safety shield
        survivors, act_pack, ref_pack, eligible, truncated, snap_inc = _evaluate_gate_state(
            atms=atms,
            worlds=worlds,
            candidate_actions=candidate_actions,
            incident_class=incident_class,
            f_fault_budget=f_fault_budget,
            k_a=k_a,
            require_positive_packing=require_positive_packing,
            require_consistent_observation_version=require_consistent_observation_version,
            expected_snapshot_version=effective_expected_ver,
            packing_expansions=packing_expansions,
            inflight_mutations=inflight_mutations,
            contested_leaf_mask=contested_leaf_mask,
        )
        if snap_inc and "TEMPORAL_VIOLATION_CROSS_SNAPSHOT_PACKING" not in temporal_violations:
            temporal_violations.append("TEMPORAL_VIOLATION_CROSS_SNAPSHOT_PACKING")

        worlds_after = tuple(sorted(w.world_id for w in survivors))
        max_pack_after = max(act_pack.values()) if act_pack else 0
        was_effective = (len(worlds_after) < len(worlds_before)) or (max_pack_after > max_pack_before)

        executed_records.append(
            ProbeExecutionRecord(
                probe_id=chosen.probe_id,
                failure_domain=chosen.failure_domain,
                collector_id=chosen.collector_id,
                proposition=obs.proposition,
                assumption_id=obs.assumption_id,
                accepted_by_atms=accepted_by_atms,
                cost_units=float(chosen.cost_units),
                latency_ms=p_lat,
                worlds_before=worlds_before,
                worlds_after=worlds_after,
                max_packing_before=max_pack_before,
                max_packing_after=max_pack_after,
                was_effective=was_effective,
                derived_consequents=tuple(sorted(set(derived_consequents))),
                ranking_score=chosen_score,
            )
        )

        # Fail closed immediately if temporal/snapshot inconsistency occurred during probing:
        if temporal_violations:
            break

        if len(eligible) == 1 and not truncated:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ProbeLoopResult(
                outcome="ADMIT_MUTATION",
                admitted_action=eligible[0],
                escalation_reason=None,
                surviving_worlds=worlds_after,
                action_packing=act_pack,
                refutation_packing=ref_pack,
                executed_probes=tuple(executed_records),
                rejected_unsafe_probes=tuple(rejected_unsafe),
                probes_executed=len(executed_records),
                probes_effective=sum(1 for r in executed_records if r.was_effective),
                total_cost_units=total_cost,
                total_load_units=total_load,
                elapsed_ms=elapsed_ms,
                budget_exhausted_reason=None,
                packing_budget_truncated=truncated,
                temporal_consistency_ok=True,
                temporal_audit_log=tuple(temporal_audit_log),
                temporal_violation_reasons=(),
            )

    # Step 10: Return structured DEFER_ESCALATE if safe mitigation remains uncertifiable
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if temporal_violations:
        esc_reason = f"DEFER_SNAPSHOT_INCONSISTENT:{temporal_violations[0]}"
        # Conservative retention: when snapshot consistency cannot be established, retain all worlds!
        final_worlds = tuple(sorted(w.world_id for w in worlds))
    elif truncated:
        esc_reason = "DEFER_COMPUTATION_BUDGET_EXHAUSTED"
        final_worlds = tuple(sorted(w.world_id for w in survivors))
    elif len(eligible) > 1:
        esc_reason = "DEFER_MULTIPLE_ELIGIBLE_ACTIONS"
        final_worlds = tuple(sorted(w.world_id for w in survivors))
    elif budget_exhausted_reason is not None:
        esc_reason = f"DEFER_PROBE_BUDGET_EXHAUSTED:{budget_exhausted_reason}"
        final_worlds = tuple(sorted(w.world_id for w in survivors))
    elif len(survivors) > 1:
        esc_reason = "DEFER_AMBIGUOUS_WORLDS_UNRESOLVED"
        final_worlds = tuple(sorted(w.world_id for w in survivors))
    else:
        esc_reason = "DEFER_INSUFFICIENT_INDEPENDENT_POSITIVE_QUORUM"
        final_worlds = tuple(sorted(w.world_id for w in survivors))

    return ProbeLoopResult(
        outcome="DEFER_ESCALATE",
        admitted_action=None,
        escalation_reason=esc_reason,
        surviving_worlds=final_worlds,
        action_packing=act_pack,
        refutation_packing=ref_pack,
        executed_probes=tuple(executed_records),
        rejected_unsafe_probes=tuple(rejected_unsafe),
        probes_executed=len(executed_records),
        probes_effective=sum(1 for r in executed_records if r.was_effective),
        total_cost_units=total_cost,
        total_load_units=total_load,
        elapsed_ms=elapsed_ms,
        budget_exhausted_reason=budget_exhausted_reason,
        packing_budget_truncated=truncated,
        temporal_consistency_ok=(len(temporal_violations) == 0),
        temporal_audit_log=tuple(temporal_audit_log),
        temporal_violation_reasons=tuple(temporal_violations),
    )
