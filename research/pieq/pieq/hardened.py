"""
Revision 7 P1 — Hardened Reference Configuration and Contract-to-Implementation Matrix.

Separates the hardened evaluation profile (all theorem contracts unconditionally enabled)
from legacy unit-test configurations (which retain backwards-compatible default flags).

Explicitly identifies which protections are supplied by:
  - Core Python (`pieq/core.py` — in-memory admission/ATMS/ledger; NO crash durability by itself)
  - SQLite WAL adapter (`SQLiteProtectedResourceBoundary` — single-host durable WAL + Tier E2)
  - etcd adapter (`pieq/etcd_boundary.py` — Raft-replicated linearizable Tier E2 KV transactions)
  - External physical target (complete writer mediation, physical Refines_D(R1/R2), STONITH/IAM)
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    CrossRootExposureLedger,
    DeterministicRuleVerifier,
    EpistemicLeaseCertificate,
    FaultWorld,
    InferenceRule,
    InvariantConstraint,
    PlausibleWorldGate,
    SQLiteProtectedResourceBoundary,
    SnapshotContext,
    TargetExecutionReceipt,
    compute_disjoint_justification_packing_bounded,
    filter_snapshot_consistent_witnesses,
)
from pieq.probe_loop import DiagnosticProbeSpec, ProbeBudget, ProbeLoopResult, run_active_diagnostic_probe_loop


# Explicit contract-to-implementation matrix (Revision 7 Deliverable 4):
CONTRACT_IMPLEMENTATION_MATRIX: Tuple[Dict[str, str], ...] = (
    {
        "contract": "Deterministic Horn/nogood proof & collector HMAC verification (Thm 2)",
        "core_python": "ENFORCED (DeterministicRuleVerifier + BoundedATMSEngine + TypedCertificateBuilder)",
        "sqlite_wal": "N/A (verifies issued EpistemicLeaseCertificate HMAC)",
        "etcd_adapter": "NOT_IN_ADAPTER (control-plane verifies before issuing txn)",
        "external_target": "Trusted collector signing keys & uncompromised telemetry root",
    },
    {
        "contract": "Exact disjoint complete-justification packing & budget fail-closed (Thm 3)",
        "core_python": "ENFORCED (64-bit branch-and-bound + budget_exceeded fail-closed)",
        "sqlite_wal": "N/A (receives signed packing_number in certificate)",
        "etcd_adapter": "N/A",
        "external_target": "Physical failure-domain independence D(e) must hold",
    },
    {
        "contract": "Resource/epoch/observation-version/manifest binding (no torn snapshots)",
        "core_python": "ENFORCED IN HARDENED PROFILE (expected_epoch/resource/manifest + require_consistent_observation_version + require_contiguous_versions)",
        "sqlite_wal": "ENFORCED (expected_manifest_hash + min_valid_epoch inside BEGIN IMMEDIATE)",
        "etcd_adapter": "PARTIAL (epoch enforced atomically; manifest_hash/auth_epoch handled by control plane unless extended)",
        "external_target": "Collectors must stamp authentic snapshot version and manifest hash",
    },
    {
        "contract": "Conservative f-fault-tolerant world retention & safety shield (Lem 4)",
        "core_python": "ENFORCED (PlausibleWorldGate; budget exhaustion retains all worlds)",
        "sqlite_wal": "N/A",
        "etcd_adapter": "N/A",
        "external_target": "World-completeness (S_true in W) & safe-action specification A_safe(s)",
    },
    {
        "contract": "Per-domain admission revision CAS & exposure accounting (Thm 5)",
        "core_python": "IN-MEMORY ONLY (CrossRootExposureLedger enforces CAS + exposure caps in RAM; NO crash durability!)",
        "sqlite_wal": "DURABLE SINGLE-HOST (WAL + synchronous=FULL exposure cap & committed_ops)",
        "etcd_adapter": "DURABLE REPLICATED (linearizable mod_revision CAS across cap, nodes, frontier)",
        "external_target": "Must not mutate shared exposure domain out-of-band",
    },
    {
        "contract": "Durable op_id idempotency, identity binding, & Mode S predecessor frontier",
        "core_python": "IN-MEMORY ONLY (issuance_dedup + predecessor tracking in RAM)",
        "sqlite_wal": "DURABLE SINGLE-HOST (committed_ops PK + identity_json conflict check + required_predecessor_op_ids)",
        "etcd_adapter": "DURABLE REPLICATED (receipts/<op_id> VERSION==0 + identity match + frontier check)",
        "external_target": "External non-KV targets must enforce durable op_id & frontier or remain R2-fenced",
    },
    {
        "contract": "UNKNOWN outcome retention until verified commit or fenced no-effect",
        "core_python": "IN-MEMORY ONLY (CrossRootExposureLedger retains I_U in RAM)",
        "sqlite_wal": "DURABLE SINGLE-HOST (record_fenced_no_effect returns False if already COMMITTED)",
        "etcd_adapter": "DURABLE REPLICATED (timeout returns UNKNOWN; caller reconciles receipts/<op_id>)",
        "external_target": "Non-cancellable ops require definitive barrier (STONITH/IAM/storage fence)",
    },
    {
        "contract": "Execution-time authorization freshness (Cor 1 E2) vs post-FenceAck exclusion (E1+)",
        "core_python": "ENFORCED IN MODEL (TargetResourceBoundary distinguishes E2 atomic vs E1+-quiescence pre-FenceAck window; fail-closed clock)",
        "sqlite_wal": "ENFORCED TIER E2 (min_auth_epoch + auth_valid_until checked atomically inside BEGIN IMMEDIATE)",
        "etcd_adapter": "NOT_IN_ADAPTER (epoch fenced; auth_epoch/TTL currently control-plane-side)",
        "external_target": "Complete writer mediation & authoritative auth store at effect boundary",
    },
    {
        "contract": "Commit-time precondition revalidation CommitPreconditionValid(a, t_eff) (Rev7 P0.2)",
        "core_python": "SEPARATED (Theorem 3 certifies SnapshotPreconditionValid(a, v_snap) only)",
        "sqlite_wal": "OPT-IN TIER E2 (required_commit_predicates / commit_precondition_check inside BEGIN IMMEDIATE)",
        "etcd_adapter": "OPT-IN VIA TXN COMPARE (caller can include live-predicate key compares)",
        "external_target": "Only needed for transient-state actions; historical-snapshot actions omit it",
    },
    {
        "contract": "Target capability & physical refinement attestation (Refines_D R1/R2)",
        "core_python": "ENFORCED IN HARDENED PROFILE (TargetRefinementAttestation HMAC check; UNVERIFIED_ASYNC rejected fail-closed)",
        "sqlite_wal": "SQLite WAL provides single-host R1 serializability for local DB rows",
        "etcd_adapter": "etcd provides linearizable R1 KV transitions for keys under prefix",
        "external_target": "TRUSTED CONTRACT — physical external devices/cloud APIs not proven by finite model check",
    },
)


@dataclass(frozen=True)
class TargetRefinementAttestation:
    """Signed attestation declaring a target's verified enforcement tier and refinement regime."""

    resource_id: str
    tier: str  # "E2_SHARED_ATOMIC" or "E1_PLUS_QUIESCENCE_BARRIER"
    refinement_regime: str  # "R1_ATOMIC" or "R2_PHASED" (UNVERIFIED_ASYNC always rejected!)
    manifest_hash: str
    complete_writer_mediation: bool
    signature: str = ""

    def compute_signature(self, secret_key: bytes) -> str:
        payload = json.dumps(
            {
                "resource_id": self.resource_id,
                "tier": self.tier,
                "refinement_regime": self.refinement_regime,
                "manifest_hash": self.manifest_hash,
                "complete_writer_mediation": self.complete_writer_mediation,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(secret_key, payload, hashlib.sha256).hexdigest()

    def with_signature(self, secret_key: bytes) -> "TargetRefinementAttestation":
        return TargetRefinementAttestation(
            resource_id=self.resource_id,
            tier=self.tier,
            refinement_regime=self.refinement_regime,
            manifest_hash=self.manifest_hash,
            complete_writer_mediation=self.complete_writer_mediation,
            signature=self.compute_signature(secret_key),
        )


@dataclass(frozen=True)
class HardenedPIEQConfig:
    """
    Hardened evaluation configuration enforcing every Theorem 3-5 & Corollary 1 contract.
    Rejects weakened parameter combinations at construction time.
    """

    resource_id: str
    epoch: int
    manifest_hash: str
    auth_epoch: int
    signing_key: bytes
    f_fault_budget: int
    k_a: int
    packing_expansions: int = 10000
    require_positive_packing: bool = True
    require_consistent_observation_version: bool = True
    require_versioned_commits: bool = True
    require_contiguous_versions: bool = True
    require_admission_rev_cas: bool = True
    require_auth_clock: bool = True
    allowed_tiers: FrozenSet[str] = frozenset({"E2_SHARED_ATOMIC", "E1_PLUS_QUIESCENCE_BARRIER"})
    allowed_refinement_regimes: FrozenSet[str] = frozenset({"R1_ATOMIC", "R2_PHASED"})

    def __post_init__(self) -> None:
        if not self.resource_id or not self.manifest_hash:
            raise ValueError("HardenedPIEQConfig requires non-empty resource_id and manifest_hash")
        if len(self.signing_key) < 8:
            raise ValueError("HardenedPIEQConfig requires signing_key >= 8 bytes")
        if self.k_a < self.f_fault_budget + 1:
            raise ValueError("HardenedPIEQConfig requires k_a >= f + 1")
        if not (
            self.require_consistent_observation_version
            and self.require_versioned_commits
            and self.require_contiguous_versions
            and self.require_admission_rev_cas
            and self.require_auth_clock
        ):
            raise ValueError("HardenedPIEQConfig forbids relaxing binding, CAS, or clock invariants")
        if "E0_PROXY_ONLY" in self.allowed_tiers or "E1_ASYNC_FENCE" in self.allowed_tiers:
            raise ValueError("HardenedPIEQConfig forbids admitting onto E0 or E1_ASYNC_FENCE targets")
        if "UNVERIFIED_ASYNC" in self.allowed_refinement_regimes:
            raise ValueError("HardenedPIEQConfig forbids admitting onto UNVERIFIED_ASYNC targets")


@dataclass(frozen=True)
class HardenedAdmissionDecision:
    admitted: bool
    action: str
    reason: str
    certificate: Optional[EpistemicLeaseCertificate]
    execution_receipt: Optional[TargetExecutionReceipt]
    surviving_worlds: Tuple[str, ...]
    action_packing: Dict[str, int]
    probe_result: Optional[ProbeLoopResult] = None


class HardenedAdmissionController:
    """
    End-to-end hardened PIEQ admission + execution orchestrator.
    Enforces every check fail-closed before minting an HMAC-signed EpistemicLeaseCertificate
    and executing on a durable Tier E2 SQLite WAL boundary.
    """

    def __init__(
        self,
        config: HardenedPIEQConfig,
        attestation: TargetRefinementAttestation,
        ledger: CrossRootExposureLedger,
        target_boundary: SQLiteProtectedResourceBoundary,
    ) -> None:
        self.config = config
        self.attestation = attestation
        self.ledger = ledger
        self.target = target_boundary

    def verify_target_capability(self) -> Tuple[bool, str]:
        att = self.attestation
        if att.resource_id != self.config.resource_id:
            return False, "REJECTED_TARGET_RESOURCE_MISMATCH"
        if att.manifest_hash != self.config.manifest_hash:
            return False, "REJECTED_TARGET_MANIFEST_MISMATCH"
        if not att.complete_writer_mediation:
            return False, "REJECTED_INCOMPLETE_WRITER_MEDIATION"
        if att.tier not in self.config.allowed_tiers:
            return False, "REJECTED_UNSUPPORTED_ENFORCEMENT_TIER"
        if att.refinement_regime not in self.config.allowed_refinement_regimes:
            return False, "REJECTED_UNVERIFIED_PHYSICAL_REFINEMENT"
        expected_sig = att.compute_signature(self.config.signing_key)
        if not hmac.compare_digest(att.signature, expected_sig):
            return False, "REJECTED_INVALID_TARGET_ATTESTATION_SIGNATURE"
        return True, "TARGET_VERIFIED"

    def evaluate_and_execute(
        self,
        *,
        atms: BoundedATMSEngine,
        worlds: Sequence[FaultWorld],
        candidate_actions: Sequence[str],
        incident_class: str,
        exposure_domain: str,
        exposure_cost: int,
        op_id: str,
        issuance_key: str,
        snapshot_context: SnapshotContext,
        now_time: Optional[int],
        auth_valid_until: Optional[int],
        target_node: str = "node_1",
        required_predecessor_op_ids: Sequence[str] = (),
        required_commit_predicates: Sequence[str] = (),
        available_probes: Sequence[DiagnosticProbeSpec] = (),
        probe_executor=None,
        probe_budget: Optional[ProbeBudget] = None,
        override_expected_admission_revs: Optional[Dict[str, int]] = None,
    ) -> HardenedAdmissionDecision:
        # 1. Fail-closed target capability & physical refinement attestation check:
        cap_ok, cap_reason = self.verify_target_capability()
        if not cap_ok:
            return HardenedAdmissionDecision(False, "defer", cap_reason, None, None, (), {})

        # 2. Fail-closed auth clock & TTL check:
        if auth_valid_until is None or now_time is None:
            return HardenedAdmissionDecision(
                False, "defer", "REJECTED_MISSING_AUTH_CLOCK_OR_TTL", None, None, (), {}
            )
        if now_time > auth_valid_until:
            return HardenedAdmissionDecision(
                False, "defer", "REJECTED_AUTH_TTL_EXPIRED_AT_ADMISSION", None, None, (), {}
            )

        # 3. Enforce strict snapshot contiguity & versioned-commit requirements:
        if not snapshot_context.require_versioned_commits or not snapshot_context.require_contiguous_versions:
            return HardenedAdmissionDecision(
                False, "defer", "REJECTED_RELAXED_SNAPSHOT_CONTEXT", None, None, (), {}
            )
        if (
            snapshot_context.min_obs_version is not None
            and snapshot_context.max_obs_version is not None
            and snapshot_context.min_obs_version != snapshot_context.max_obs_version
        ):
            return HardenedAdmissionDecision(
                False, "defer", "REJECTED_TORN_SNAPSHOT_CONTEXT", None, None, (), {}
            )

        # 4. Enforce ATMS strict binding configuration:
        if (
            atms.expected_epoch != self.config.epoch
            or atms.expected_resource_id != self.config.resource_id
            or atms.expected_manifest_hash != self.config.manifest_hash
        ):
            return HardenedAdmissionDecision(
                False, "defer", "REJECTED_ATMS_BINDING_MISCONFIGURED", None, None, (), {}
            )

        # Capture current per-domain admission revisions for mandatory CAS:
        observed_revs = (
            dict(override_expected_admission_revs)
            if override_expected_admission_revs is not None
            else self.ledger.admission_revisions([exposure_domain])
        )
        inflight = self.ledger.active_operations(exposure_domain)

        # 5. Epistemic certification + optional bounded active diagnostic probe loop:
        probe_res: Optional[ProbeLoopResult] = None
        if available_probes and probe_executor is not None:
            probe_res = run_active_diagnostic_probe_loop(
                atms=atms,
                worlds=worlds,
                candidate_actions=candidate_actions,
                incident_class=incident_class,
                f_fault_budget=self.config.f_fault_budget,
                k_a=self.config.k_a,
                available_probes=available_probes,
                probe_executor=probe_executor,
                budget=probe_budget or ProbeBudget(),
                require_positive_packing=self.config.require_positive_packing,
                require_consistent_observation_version=self.config.require_consistent_observation_version,
                expected_snapshot_version=snapshot_context.snapshot_version,
                packing_expansions=self.config.packing_expansions,
                inflight_mutations=inflight,
                version_observer=self.target.current_seq,
            )
            if probe_res.outcome != "ADMIT_MUTATION" or probe_res.admitted_action is None:
                return HardenedAdmissionDecision(
                    False,
                    "defer",
                    probe_res.escalation_reason or "DEFER_ESCALATE",
                    None,
                    None,
                    probe_res.surviving_worlds,
                    probe_res.action_packing,
                    probe_result=probe_res,
                )
            chosen_action = probe_res.admitted_action
            surv_ids = probe_res.surviving_worlds
            act_pack = probe_res.action_packing
        else:
            gate = PlausibleWorldGate(worlds, ["defer", "probe"])
            refs = {w.world_id: atms.active_witness_justifications(f"refute:{w.world_id}") for w in worlds}
            survivors = gate.compute_fault_tolerant_belief_set(
                refs,
                atms.assumptions_by_bit,
                fault_budget_f=self.config.f_fault_budget,
                budget_exceeded=atms.budget_exceeded,
                max_packing_expansions=self.config.packing_expansions,
                expected_snapshot_version=snapshot_context.snapshot_version,
                require_consistent_observation_version=self.config.require_consistent_observation_version,
                expected_epoch=self.config.epoch,
                expected_resource_id=self.config.resource_id,
                expected_manifest_hash=self.config.manifest_hash,
            )
            surv_ids = tuple(sorted(w.world_id for w in survivors))
            act_pack = {}
            eligible: List[str] = []
            truncated = atms.budget_exceeded
            for action in sorted(candidate_actions):
                if action in {"defer", "probe"}:
                    continue
                pos_wits, pos_inc, _, _ = filter_snapshot_consistent_witnesses(
                    atms.active_witness_justifications(f"act:{action}"),
                    atms.assumptions_by_bit,
                    expected_snapshot_version=snapshot_context.snapshot_version,
                    expected_epoch=self.config.epoch,
                    expected_resource_id=self.config.resource_id,
                    expected_manifest_hash=self.config.manifest_hash,
                )
                cnt, _, ex = compute_disjoint_justification_packing_bounded(
                    pos_wits,
                    atms.assumptions_by_bit,
                    max_expansions=self.config.packing_expansions,
                )
                truncated |= ex
                act_pack[action] = cnt
                ok, _ = gate.evaluate_concurrent_compound_safety(
                    action, incident_class, 0, survivors, inflight, snapshot_context=snapshot_context
                )
                pos_ok = (cnt >= self.config.k_a) if self.config.require_positive_packing else True
                if not ex and not pos_inc and ok and pos_ok:
                    eligible.append(action)
            if truncated:
                return HardenedAdmissionDecision(
                    False, "defer", "REJECTED_COMPUTATION_BUDGET_EXHAUSTED", None, None, surv_ids, act_pack
                )
            if len(eligible) != 1:
                return HardenedAdmissionDecision(
                    False, "defer", "DEFER_NO_UNIQUE_CERTIFIED_SAFE_ACTION", None, None, surv_ids, act_pack
                )
            chosen_action = eligible[0]

        # 6. Mandatory per-domain admission revision CAS + exposure reservation:
        ok_res, _, res_reason = self.ledger.admit_and_reserve(
            issuance_key=issuance_key,
            op_id=op_id,
            action=chosen_action,
            domain=exposure_domain,
            exposure_cost=exposure_cost,
            predecessor_op_ids=tuple(required_predecessor_op_ids),
            expected_admission_revs=observed_revs,
        )
        if not ok_res:
            return HardenedAdmissionDecision(
                False, "defer", f"REJECTED_LEDGER_ADMISSION:{res_reason}", None, None, surv_ids, act_pack, probe_res
            )

        # 7. Compute deterministic proof_manifest_digest & mint HMAC-signed EpistemicLeaseCertificate:
        witnesses = atms.active_witness_justifications(f"act:{chosen_action}")
        support_mask = 0
        for w in witnesses:
            support_mask |= w.env_mask
        digest_payload = json.dumps(
            {
                "action": chosen_action,
                "witness_masks": sorted(w.env_mask for w in witnesses),
                "surviving_worlds": list(surv_ids),
                "snapshot_version": snapshot_context.snapshot_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        proof_digest = hashlib.sha256(digest_payload).hexdigest()

        unsigned_cert = EpistemicLeaseCertificate(
            action=chosen_action,
            support_mask=support_mask,
            admission_epoch=self.config.epoch,
            packing_number=act_pack.get(chosen_action, 0),
            plausible_worlds=surv_ids,
            signature="",
            auth_epoch=self.config.auth_epoch,
            auth_valid_until=auth_valid_until,
            resource_id=self.config.resource_id,
            manifest_hash=self.config.manifest_hash,
            snapshot_version=snapshot_context.snapshot_version,
            proof_manifest_digest=proof_digest,
            admission_revisions=tuple(sorted(observed_revs.items())),
        )
        signed_cert = EpistemicLeaseCertificate(
            action=unsigned_cert.action,
            support_mask=unsigned_cert.support_mask,
            admission_epoch=unsigned_cert.admission_epoch,
            packing_number=unsigned_cert.packing_number,
            plausible_worlds=unsigned_cert.plausible_worlds,
            signature=unsigned_cert.compute_signature(self.config.signing_key),
            auth_epoch=unsigned_cert.auth_epoch,
            auth_valid_until=unsigned_cert.auth_valid_until,
            resource_id=unsigned_cert.resource_id,
            manifest_hash=unsigned_cert.manifest_hash,
            snapshot_version=unsigned_cert.snapshot_version,
            proof_manifest_digest=unsigned_cert.proof_manifest_digest,
            admission_revisions=unsigned_cert.admission_revisions,
        )

        # 8. Execute atomically on Tier E2 durable SQLite WAL target boundary:
        receipt = self.target.execute_fenced_mutation(
            signed_cert,
            target_node=target_node,
            op_id=op_id,
            required_predecessor_op_ids=required_predecessor_op_ids,
            now_time=now_time,
            required_commit_predicates=required_commit_predicates,
        )
        if receipt.committed:
            self.ledger.confirm_commit(exposure_domain, op_id, commit_version=receipt.commit_version)
            return HardenedAdmissionDecision(
                True, chosen_action, "COMMITTED", signed_cert, receipt, surv_ids, act_pack, probe_res
            )

        # If target cleanly rejected with zero physical effect, mark fenced no-effect & release:
        # (Whereas timeouts/unknown outcomes must retain UNKNOWN in the ledger!)
        fenced_ok = self.target.record_fenced_no_effect(op_id, chosen_action)
        if fenced_ok:
            self.ledger.release_on_verified_no_effect(exposure_domain, op_id, has_fenced_no_effect_proof=True)
        else:
            self.ledger.mark_timeout_or_cancel_race(exposure_domain, op_id, state="UNKNOWN")

        return HardenedAdmissionDecision(
            False, "defer", f"TARGET_REJECTED:{receipt.status}", signed_cert, receipt, surv_ids, act_pack, probe_res
        )
