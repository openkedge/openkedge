"""
Core formal primitives and runtime admission engine for
Provenance-Isolated Epistemic Quorums (PIEQ).

Implements:
- Theorem 1 (Conformity Contraction): Exact eigenvalue evolution under temporally white
  innovations and explicit separation of Effective Sample Size (N_eff) vs. Participation Ratio (r_PR).
- Theorem 2 (Sound Conflict Elimination): Deterministic Rule/Invariant Verifier and Resource-Bounded
  64-bit ATMS preserving minimal consistent subsets of true assumption environments.
- Theorem 3 (Disjoint Complete-Justification Set Packing vs. Menger Min-Cut):
  Exact 64-bit bitmask branch-and-bound set-packing solver for complete proof supports D(J),
  plus characterization of the unit-premise fragment where Menger vertex min-cut is exact.
- Theorem 4 (Fault-Tolerant Plausible-World Shielding under Bounded Computation):
  Constructs the conservative f-fault-tolerant belief overapproximation hat{B}_tau^{(f)}
  (requiring k_a = f + 1 disjoint refutations to eliminate any world) and enforces
  a in cap_{s in hat{B}_tau^{(f)}} A_safe(s).
- Theorem 5 (Compositional Safety under Concurrent Mutations & Aggregate Exposure):
  Enforces compound reachable-state safety and KedgeFlow Cross-Root Exposure Conservation.
- Proposition 6 (Probabilistic Unsafe-Commit Bound & Enforcement Tiers E0, E1, E2):
  Separates Proxy-Only (E0), Asynchronous Push Fence (E1), Two-Phase Quiescence Barrier (E1+),
  and Shared-State Atomic Revision Validation (E2), plus real SQLite WAL resource fencing.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import sqlite3
from collections import deque
from dataclasses import dataclass, field
import itertools
import json
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np


# =============================================================================
# 1. Covariance Metrics & Conformity Dynamics (Theorem 1)
# =============================================================================

def effective_sample_size_equicorr(n_agents: int, rho: float) -> float:
    """
    Effective sample size of the ensemble mean under equicorrelation rho:
        N_eff = N / (1 + (N - 1) * rho)
    """
    if n_agents <= 0:
        raise ValueError("n_agents must be positive")
    denom = 1.0 + (n_agents - 1) * rho
    if denom <= 0:
        raise ValueError("Invalid equicorrelation rho for given n_agents")
    return float(n_agents) / denom


def participation_ratio_equicorr(n_agents: int, rho: float) -> float:
    """
    Covariance participation ratio (effective rank) under equicorrelation rho:
        r_PR = (tr Sigma)^2 / ||Sigma||_F^2 = N / (1 + (N - 1) * rho^2)
    Note: The closed-form in terms of bar_rho holds under equicorrelation;
    for general heterogeneous covariance matrices, use `covariance_metrics_from_matrix`.
    """
    if n_agents <= 0:
        raise ValueError("n_agents must be positive")
    denom = 1.0 + (n_agents - 1) * (rho ** 2)
    return float(n_agents) / denom


def covariance_metrics_from_matrix(sigma: np.ndarray) -> Tuple[float, float, float]:
    """
    Given a general N x N covariance matrix Sigma, return:
      (mean_off_diag_corr_rho, N_eff, r_PR)
    where r_PR = (tr Sigma)^2 / ||Sigma||_F^2 is computed directly from the full matrix.
    """
    n = sigma.shape[0]
    diag = np.diag(sigma)
    std = np.sqrt(np.maximum(diag, 1e-15))
    corr = sigma / np.outer(std, std)
    if n > 1:
        off_diag_sum = np.sum(corr) - np.trace(corr)
        bar_rho = float(off_diag_sum / (n * (n - 1)))
    else:
        bar_rho = 0.0
    n_eff = float(n / (1.0 + (n - 1) * bar_rho))
    tr_sigma = float(np.trace(sigma))
    frob_sq = float(np.sum(sigma ** 2))
    r_pr = (tr_sigma ** 2) / max(frob_sq, 1e-15)
    return bar_rho, n_eff, r_pr


def equicorrelated_covariance(n_agents: int, sigma_sq: float, rho: float) -> np.ndarray:
    """Construct an N x N equicorrelated covariance matrix."""
    mat = np.full((n_agents, n_agents), sigma_sq * rho, dtype=float)
    np.fill_diagonal(mat, sigma_sq)
    return mat


def propagate_conformity_covariance(
    n_agents: int,
    sigma0_sq: float,
    rho0: float,
    gamma: float,
    rounds: int,
    tau_dec_sq: float = 0.0,
    rho_model: float = 0.0,
) -> np.ndarray:
    """
    Propagate covariance Sigma^{(t)} under linear averaging dynamics over UNBOUNDED
    latent score / log-odds residuals epsilon^{(t)} in R^N:
        epsilon^{(t+1)} = ((1 - gamma) I + (gamma / N) 1 1^T) epsilon^{(t)} + eta^{(t)}
    assuming temporally white innovations Cov(eta^{(t)}, eta^{(s)}) = 0 (t != s)
    that are orthogonal to current residual state Cov(epsilon^{(t)}, eta^{(t)}) = 0.

    Revision 6 (Priority 0.B): Probability-valued beliefs y_i^{(t)} = psi(z_i^{(t)}) in [0, 1]
    are a separate nonlinear readout whose marginal variance is bounded by 1/4
    (forcing lambda_parallel <= N / 4); persistent-innovation growth lambda_parallel = Theta(t)
    applies strictly to the unbounded latent score residual model, not to [0, 1] probabilities.
    """
    sigma = equicorrelated_covariance(n_agents, sigma0_sq, rho0)
    w = (1.0 - gamma) * np.eye(n_agents) + (gamma / n_agents) * np.ones((n_agents, n_agents))
    q_eta = equicorrelated_covariance(n_agents, tau_dec_sq, rho_model) if tau_dec_sq > 0 else np.zeros_like(sigma)
    for _ in range(rounds):
        sigma = w @ sigma @ w.T + q_eta
    return sigma


def binomial_tail_risk(m_channels: int, k_quorum: int, p_false: float) -> float:
    """
    Exact probability that at least k_quorum out of m_channels independent failure domains
    simultaneously suffer false evidence:
        sum_{j=k}^M C(M, j) p^j (1 - p)^{M - j}
    """
    if k_quorum <= 0:
        return 1.0
    if k_quorum > m_channels:
        return 0.0
    total = 0.0
    for j in range(k_quorum, m_channels + 1):
        total += math.comb(m_channels, j) * (p_false ** j) * ((1.0 - p_false) ** (m_channels - j))
    return total


def residual_unsafe_admit_bound(
    m_channels: int,
    k_quorum: int,
    p_sensor: float,
    delta_model: float,
    delta_common: float,
    delta_freshness: float,
    delta_rules: float = 0.0,
    delta_domain_class: float = 0.0,
    delta_drift: float = 0.0,
    delta_concurrent: float = 0.0,
) -> float:
    """
    Proposition 6 (Revision 3) residual unsafe-commit bound using mutually exclusive
    sequential conditioning over the 8 separated failure events:
      1. E_univ (delta_model): Incomplete candidate fault-world universe (S_tau not in S_cand)
      2. E_rule (delta_rules): Unsound trusted diagnostic, safety, or transition rules
      3. E_dom (delta_domain_class): Incorrect failure-domain classification in manifest
      4. E_common (delta_common): Cross-domain common-cause faults (on E_univ^c cap E_rule^c cap E_dom^c)
      5. E_sensor (sensor_tail): >= k_a independent failure-domain faults (conditioned on E_dom^c cap E_common^c)
      6. E_drift (delta_drift): Unobserved physical-state changes / unmediated external writers
      7. E_fresh^T (delta_freshness): Target enforcement and revocation-freshness race under tier T
      8. E_conc (delta_concurrent): Concurrent mutation ordering or envelope violation
    Under deterministic TCB assumptions (Assumptions 1-3), delta_rules = delta_domain_class = delta_concurrent = 0.
    """
    sensor_tail = binomial_tail_risk(m_channels, k_quorum, p_sensor)
    total = (
        delta_model
        + delta_rules
        + delta_domain_class
        + delta_common
        + sensor_tail
        + delta_drift
        + delta_freshness
        + delta_concurrent
    )
    return min(1.0, total)


def disjoint_stratum_unsafe_bound(
    m_domains: int,
    k_quorum: int,
    p_domain_fault: float,
    p_univ: float,
    p_rules_given_prev: float = 0.0,
    p_dom_given_prev: float = 0.0,
    p_common_given_prev: float = 0.0,
    p_drift_given_prev: float = 0.0,
    p_fresh_given_prev: float = 0.0,
    p_conc_given_prev: float = 0.0,
) -> Dict[str, float]:
    """
    Computes the exact non-double-counting sequential stratum decomposition:
        P(bigcup_{i=1}^8 F_i) = sum_{i=1}^8 P(F_i cap bigcap_{j < i} F_j^c)
                              = sum_{i=1}^8 P(F_i | bigcap_{j < i} F_j^c) * prod_{j < i} (1 - P(F_j | ...))
    Demonstrates that conditional probabilities over disjoint strata never double-count
    overlapping events (e.g., E_common and E_sensor are partitioned by E_common vs E_common^c),
    and returns both the exact stratum sum and the linear upper bound.
    """
    sensor_tail = binomial_tail_risk(m_domains, k_quorum, p_domain_fault)
    cond_probs = [
        ("E1_incomplete_universe", p_univ),
        ("E2_unsound_rules", p_rules_given_prev),
        ("E3_domain_misclassification", p_dom_given_prev),
        ("E4_common_cause_fault", p_common_given_prev),
        ("E5_independent_sensor_tail", sensor_tail),
        ("E6_unobserved_state_drift", p_drift_given_prev),
        ("E7_target_freshness_race", p_fresh_given_prev),
        ("E8_concurrent_coordination", p_conc_given_prev),
    ]
    surviving_mass = 1.0
    exact_disjoint_sum = 0.0
    strata: Dict[str, float] = {}
    for name, p_cond in cond_probs:
        p_clamped = max(0.0, min(1.0, p_cond))
        stratum_prob = surviving_mass * p_clamped
        strata[name] = stratum_prob
        exact_disjoint_sum += stratum_prob
        surviving_mass *= (1.0 - p_clamped)
    strata["exact_disjoint_total"] = exact_disjoint_sum
    strata["additive_upper_bound"] = min(1.0, sum(p for _, p in cond_probs))
    return strata


# =============================================================================
# 2. Trusted Computing Base, Primitive Assumptions & Deterministic Verifier
# =============================================================================

@dataclass(frozen=True)
class PrimitiveAssumption:
    """
    Cryptographically signed telemetry observation registered in the control-plane ledger.
    A valid signature certifies collector provenance and payload integrity,
    NOT physical ground truth or diagnostic sufficiency.
    """
    bit_index: int              # 0 .. 63 in the bounded epoch window
    assumption_id: str          # e.g., "e_http_timeout"
    channel_id: str             # e.g., "ch_http_probe"
    failure_domain: str         # e.g., "fd_inband_net"
    epoch_revision: int         # etcd revision at collection
    proposition: str            # atomic telemetry predicate, e.g., "obs:http_timeout"
    signature: str              # HMAC/Ed25519 collector signature
    observation_version: int = 0  # target plant snapshot version observed by collector

    @property
    def bitmask(self) -> int:
        return 1 << self.bit_index


@dataclass(frozen=True)
class BoundPrimitiveAssumption(PrimitiveAssumption):
    """
    PrimitiveAssumption additionally cryptographically bound to target resource scope
    `resource_id` and topology manifest digest `manifest_hash` (P2 evidence binding).
    """
    resource_id: str = ""
    manifest_hash: str = ""


@dataclass(frozen=True)
class InferenceRule:
    """
    Deterministic Horn rule in the trusted domain model M_sys:
        antecedents (conjunction of atomic predicates) |- consequent
    """
    rule_id: str
    antecedents: FrozenSet[str]
    consequent: str


@dataclass(frozen=True)
class InvariantConstraint:
    """
    Deterministic conflict invariant in P_sys:
        if all predicates in conflict_predicates simultaneously hold, derive bottom (nogood).
    """
    invariant_id: str
    conflict_predicates: FrozenSet[str]
    description: str = ""


@dataclass(frozen=True)
class SignedJustification:
    """
    Proof-carrying positive or refutation derivation-DAG certificate proposed by an agent
    and verified deterministically by V_rule.
    `env_mask` ALWAYS stores the complete recursive union bitmask of all ancestor primitive
    telemetry leaves E_leaf(Pi). Single-step justifications have `sub_justifications=()`.
    """
    agent_id: str
    context_id: str
    consequent: str
    env_mask: int
    rule_id: str
    merkle_hash: str
    sub_justifications: Tuple["SignedJustification", ...] = ()
    direct_env_mask: Optional[int] = None


@dataclass(frozen=True)
class NogoodCertificate:
    """
    Minimal conflict certificate nogood(E_bot) verified against P_sys.
    """
    issuer_id: str
    env_mask: int
    invariant_id: str
    merkle_hash: str


class DeterministicRuleVerifier:
    """
    Trusted deterministic verifier V_rule inside the control-plane TCB.
    LLMs propose candidate explanations and assumption citations outside the TCB;
    V_rule deterministically verifies collector signatures, single-step or multi-step
    Horn derivation DAGs in M_sys, and minimal conflict invariants in P_sys.
    """

    def __init__(
        self,
        rules: Sequence[InferenceRule],
        invariants: Sequence[InvariantConstraint],
        secret_key: bytes = b"kf-proxy-tcb-key",
    ) -> None:
        self.rules: Dict[str, InferenceRule] = {r.rule_id: r for r in rules}
        self.invariants: Dict[str, InvariantConstraint] = {inv.invariant_id: inv for inv in invariants}
        self.secret_key = secret_key

    def sign_telemetry(
        self,
        assumption_id: str,
        channel_id: str,
        failure_domain: str,
        epoch_revision: int,
        proposition: str,
        observation_version: int = 0,
        bit_index: int = 0,
        resource_id: str = "",
        manifest_hash: str = "",
    ) -> str:
        fields: List[object] = [
            bit_index, assumption_id, channel_id, failure_domain,
            epoch_revision, proposition, observation_version,
        ]
        if resource_id or manifest_hash:
            fields.extend([resource_id, manifest_hash])
        payload = json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hmac.new(self.secret_key, payload, hashlib.sha256).hexdigest()[:16]

    def verify_assumption_signature(self, a: PrimitiveAssumption) -> bool:
        expected = self.sign_telemetry(
            a.assumption_id, a.channel_id, a.failure_domain, a.epoch_revision,
            a.proposition, a.observation_version, a.bit_index,
            resource_id=getattr(a, "resource_id", ""),
            manifest_hash=getattr(a, "manifest_hash", ""),
        )
        return hmac.compare_digest(a.signature, expected)

    def _compute_justification_digest(
        self,
        agent_id: str,
        context_id: str,
        consequent: str,
        env_mask: int,
        rule_id: str,
        child_hashes: Tuple[str, ...] = (),
        direct_env_mask: Optional[int] = None,
    ) -> str:
        if not child_hashes:
            raw = f"{agent_id}:{context_id}:{consequent}:{env_mask}:{rule_id}"
        else:
            d_mask = env_mask if direct_env_mask is None else direct_env_mask
            raw = (
                f"dag:{agent_id}:{context_id}:{consequent}:{d_mask}:{env_mask}:"
                f"{rule_id}:{','.join(child_hashes)}"
            )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _reverify_certificate(
        self,
        cert: SignedJustification,
        assumptions_by_bit: Dict[int, PrimitiveAssumption],
        active_stack: Set[int],
    ) -> bool:
        node_key = id(cert)
        if node_key in active_stack:
            return False  # Cycle detected -> reject non-DAG!
        active_stack.add(node_key)
        try:
            rule = self.rules.get(cert.rule_id)
            if rule is None or rule.consequent != cert.consequent:
                return False
            d_mask = cert.env_mask if cert.direct_env_mask is None else cert.direct_env_mask
            if type(d_mask) is not int or not 0 <= d_mask < (1 << 64):
                return False

            props: Set[str] = set()
            mask = d_mask
            direct_count = 0
            while mask:
                lsb = mask & -mask
                bit_idx = lsb.bit_length() - 1
                assump = assumptions_by_bit.get(bit_idx)
                if assump is None or not self.verify_assumption_signature(assump):
                    return False
                props.add(assump.proposition)
                direct_count += 1
                mask ^= lsb

            recursive_mask = d_mask
            child_hashes: List[str] = []
            for child in cert.sub_justifications:
                if not self._reverify_certificate(child, assumptions_by_bit, active_stack):
                    return False
                props.add(child.consequent)
                recursive_mask |= child.env_mask
                child_hashes.append(child.merkle_hash)

            if recursive_mask == 0 or recursive_mask != cert.env_mask:
                return False
            if not rule.antecedents.issubset(props) or (direct_count + len(cert.sub_justifications)) != len(rule.antecedents):
                return False

            expected_digest = self._compute_justification_digest(
                cert.agent_id,
                cert.context_id,
                cert.consequent,
                recursive_mask,
                cert.rule_id,
                tuple(child_hashes),
                direct_env_mask=d_mask if child_hashes else None,
            )
            return hmac.compare_digest(cert.merkle_hash, expected_digest)
        finally:
            active_stack.remove(node_key)

    def verify_justification(
        self,
        agent_id: str,
        context_id: str,
        consequent: str,
        env_mask: int,
        rule_id: str,
        assumptions_by_bit: Dict[int, PrimitiveAssumption],
    ) -> Optional[SignedJustification]:
        if type(env_mask) is not int or not 0 < env_mask < (1 << 64):
            return None
        rule = self.rules.get(rule_id)
        if rule is None or rule.consequent != consequent:
            return None

        props: Set[str] = set()
        mask = env_mask
        bit_count = 0
        while mask:
            lsb = mask & -mask
            bit_idx = lsb.bit_length() - 1
            assump = assumptions_by_bit.get(bit_idx)
            if assump is None or not self.verify_assumption_signature(assump):
                return None
            props.add(assump.proposition)
            bit_count += 1
            mask ^= lsb

        if not rule.antecedents.issubset(props) or bit_count != len(rule.antecedents):
            return None

        digest = self._compute_justification_digest(agent_id, context_id, consequent, env_mask, rule_id)
        return SignedJustification(
            agent_id=agent_id,
            context_id=context_id,
            consequent=consequent,
            env_mask=env_mask,
            rule_id=rule_id,
            merkle_hash=digest,
        )

    def verify_derivation_dag(
        self,
        agent_id: str,
        context_id: str,
        consequent: str,
        direct_leaf_mask: int,
        child_justifications: Sequence[SignedJustification],
        rule_id: str,
        assumptions_by_bit: Dict[int, PrimitiveAssumption],
    ) -> Optional[SignedJustification]:
        """
        Verify a finite acyclic multi-step Horn derivation DAG rooted at `consequent`.
        Each child justification is recursively verified down to signed primitive telemetry
        leaves; the returned certificate carries `env_mask = E_leaf(Pi)` (the recursive union
        of all ancestor primitive leaf bits) and preserves `sub_justifications` for recursive
        provenance-footprint computation.
        """
        if not child_justifications:
            return self.verify_justification(
                agent_id, context_id, consequent, direct_leaf_mask, rule_id, assumptions_by_bit
            )
        if type(direct_leaf_mask) is not int or not 0 <= direct_leaf_mask < (1 << 64):
            return None
        rule = self.rules.get(rule_id)
        if rule is None or rule.consequent != consequent:
            return None

        props: Set[str] = set()
        mask = direct_leaf_mask
        direct_count = 0
        while mask:
            lsb = mask & -mask
            bit_idx = lsb.bit_length() - 1
            assump = assumptions_by_bit.get(bit_idx)
            if assump is None or not self.verify_assumption_signature(assump):
                return None
            props.add(assump.proposition)
            direct_count += 1
            mask ^= lsb

        recursive_mask = direct_leaf_mask
        verified_children: List[SignedJustification] = []
        child_hashes: List[str] = []
        for child in child_justifications:
            if not self._reverify_certificate(child, assumptions_by_bit, set()):
                return None
            props.add(child.consequent)
            recursive_mask |= child.env_mask
            verified_children.append(child)
            child_hashes.append(child.merkle_hash)

        if recursive_mask == 0:
            return None
        if not rule.antecedents.issubset(props) or (direct_count + len(verified_children)) != len(rule.antecedents):
            return None

        digest = self._compute_justification_digest(
            agent_id,
            context_id,
            consequent,
            recursive_mask,
            rule_id,
            tuple(child_hashes),
            direct_env_mask=direct_leaf_mask,
        )
        return SignedJustification(
            agent_id=agent_id,
            context_id=context_id,
            consequent=consequent,
            env_mask=recursive_mask,
            rule_id=rule_id,
            merkle_hash=digest,
            sub_justifications=tuple(verified_children),
            direct_env_mask=direct_leaf_mask,
        )

    def verify_nogood(
        self,
        issuer_id: str,
        env_mask: int,
        invariant_id: str,
        assumptions_by_bit: Dict[int, PrimitiveAssumption],
    ) -> Optional[NogoodCertificate]:
        if type(env_mask) is not int or not 0 <= env_mask < (1 << 64):
            return None
        inv = self.invariants.get(invariant_id)
        if inv is None or env_mask == 0:
            return None

        props: Set[str] = set()
        mask = env_mask
        bit_count = 0
        while mask:
            lsb = mask & -mask
            bit_idx = lsb.bit_length() - 1
            assump = assumptions_by_bit.get(bit_idx)
            if assump is None or not self.verify_assumption_signature(assump):
                return None
            props.add(assump.proposition)
            bit_count += 1
            mask ^= lsb

        if not inv.conflict_predicates.issubset(props) or bit_count != len(inv.conflict_predicates):
            return None

        digest = hashlib.sha256(
            f"nogood:{issuer_id}:{env_mask}:{invariant_id}".encode("utf-8")
        ).hexdigest()[:16]
        return NogoodCertificate(
            issuer_id=issuer_id,
            env_mask=env_mask,
            invariant_id=invariant_id,
            merkle_hash=digest,
        )


# =============================================================================
# 3. Resource-Bounded 64-Bit ATMS Engine (Theorem 2)
# =============================================================================

class BoundedATMSEngine:
    """
    Bounded online ATMS lattice maintaining minimal consistent assumption environments
    represented as 64-bit integers.

    Theorem 2 (Minimal Consistent Subset Preservation):
    If a true assumption set E^* derives c (via a minimal sub-environment E_min <= E^*)
    and resource limits (d_max, L_max) are not exceeded, then L^{(t)}(c) continuously
    contains at least one minimal consistent subset E_sub <= E^* across all rounds t >= 1.
    """

    def __init__(
        self,
        verifier: DeterministicRuleVerifier,
        max_assumptions: int = 64,
        max_env_cardinality: int = 6,
        max_environments_per_node: int = 64,
        max_hitting_set_expansions: int = 256,
        expected_epoch: Optional[int] = None,
        expected_resource_id: Optional[str] = None,
        expected_manifest_hash: Optional[str] = None,
    ) -> None:
        if max_assumptions > 64:
            raise ValueError("Hardware bitmask ATMS requires max_assumptions <= 64")
        self.verifier = verifier
        self.max_assumptions = max_assumptions
        self.max_env_cardinality = max_env_cardinality
        self.max_environments_per_node = max_environments_per_node
        self.max_hitting_set_expansions = max_hitting_set_expansions
        self.expected_epoch = expected_epoch
        self.expected_resource_id = expected_resource_id
        self.expected_manifest_hash = expected_manifest_hash

        self.assumptions_by_bit: Dict[int, PrimitiveAssumption] = {}
        self.labels: Dict[str, List[int]] = {}  # consequent -> minimal consistent env bitmasks
        self.justifications: Dict[str, List[SignedJustification]] = {}
        self.nogoods: List[int] = []            # minimal nogood bitmasks
        self.nogood_certs: List[NogoodCertificate] = []
        self.budget_exceeded: bool = False

    def register_assumption(self, assump: PrimitiveAssumption) -> bool:
        if type(assump.bit_index) is not int or assump.bit_index < 0 or assump.bit_index >= self.max_assumptions:
            raise ValueError(f"bit_index {assump.bit_index} outside [0, {self.max_assumptions})")
        if self.expected_epoch is not None and assump.epoch_revision != self.expected_epoch:
            return False
        if self.expected_resource_id is not None and getattr(assump, "resource_id", "") != self.expected_resource_id:
            return False
        if self.expected_manifest_hash is not None and getattr(assump, "manifest_hash", "") != self.expected_manifest_hash:
            return False
        if not self.verifier.verify_assumption_signature(assump):
            return False
        existing = self.assumptions_by_bit.get(assump.bit_index)
        if existing is not None:
            return existing == assump
        if any(other.assumption_id == assump.assumption_id
               for other in self.assumptions_by_bit.values()):
            return False
        self.assumptions_by_bit[assump.bit_index] = assump
        return True

    def is_consistent(self, env_mask: int) -> bool:
        for ng in self.nogoods:
            if (env_mask & ng) == ng:
                return False
        return True

    def has_minimal_consistent_subset(self, consequent: str, superset_mask: int) -> bool:
        """
        Check whether L(consequent) contains at least one minimal consistent environment
        E_sub that is a subset of `superset_mask` ((E_sub & superset_mask) == E_sub).
        """
        for env_mask in self.labels.get(consequent, []):
            if (env_mask & superset_mask) == env_mask and self.is_consistent(env_mask):
                return True
        return False

    def add_verified_justification(
        self,
        agent_id: str,
        context_id: str,
        consequent: str,
        env_mask: int,
        rule_id: str,
    ) -> Optional[SignedJustification]:
        if type(env_mask) is not int or not 0 <= env_mask < (1 << self.max_assumptions):
            return None
        if env_mask.bit_count() > self.max_env_cardinality:
            self.budget_exceeded = True
            return None

        cert = self.verifier.verify_justification(
            agent_id, context_id, consequent, env_mask, rule_id, self.assumptions_by_bit
        )
        if cert is None:
            return None

        self.justifications.setdefault(consequent, []).append(cert)

        if not self.is_consistent(env_mask):
            return cert

        current = self.labels.setdefault(consequent, [])
        # Minimality invariant: if an existing environment is already a subset of env_mask, retain existing
        for existing in current:
            if (existing & env_mask) == existing:
                return cert

        # Remove any existing strict supersets of env_mask
        pruned = [existing for existing in current if (existing & env_mask) != env_mask]
        if len(pruned) >= self.max_environments_per_node:
            self.budget_exceeded = True
            return cert

        pruned.append(env_mask)
        self.labels[consequent] = pruned
        return cert

    def add_verified_derivation(
        self,
        agent_id: str,
        context_id: str,
        consequent: str,
        direct_leaf_mask: int,
        child_justifications: Sequence[SignedJustification],
        rule_id: str,
    ) -> Optional[SignedJustification]:
        """
        Verify and register a multi-step Horn derivation DAG certificate.
        Enforces |E_leaf(Pi)| <= max_env_cardinality (d_max) and updates ATMS labels
        using the complete recursive primitive-leaf environment E_leaf(Pi).
        """
        cert = self.verifier.verify_derivation_dag(
            agent_id,
            context_id,
            consequent,
            direct_leaf_mask,
            child_justifications,
            rule_id,
            self.assumptions_by_bit,
        )
        if cert is None:
            return None
        if cert.env_mask.bit_count() > self.max_env_cardinality:
            self.budget_exceeded = True
            return None

        self.justifications.setdefault(consequent, []).append(cert)

        if not self.is_consistent(cert.env_mask):
            return cert

        current = self.labels.setdefault(consequent, [])
        for existing in current:
            if (existing & cert.env_mask) == existing:
                return cert

        pruned = [existing for existing in current if (existing & cert.env_mask) != cert.env_mask]
        if len(pruned) >= self.max_environments_per_node:
            self.budget_exceeded = True
            return cert

        pruned.append(cert.env_mask)
        self.labels[consequent] = pruned
        return cert

    def register_nogood(self, issuer_id: str, env_mask: int, invariant_id: str) -> Optional[NogoodCertificate]:
        cert = self.verifier.verify_nogood(issuer_id, env_mask, invariant_id, self.assumptions_by_bit)
        if cert is None:
            return None

        for existing in self.nogoods:
            if (existing & env_mask) == existing:
                return cert
        self.nogoods = [ng for ng in self.nogoods if (ng & env_mask) != env_mask]
        self.nogoods.append(env_mask)
        self.nogood_certs.append(cert)

        for prop, envs in list(self.labels.items()):
            self.labels[prop] = [e for e in envs if (e & env_mask) != env_mask]

        return cert

    def minimal_hitting_sets(self) -> Tuple[List[int], bool]:
        if not self.nogoods:
            return [0], True

        sorted_nogoods = sorted(self.nogoods, key=lambda m: m.bit_count())
        candidates: List[int] = [0]
        expansions = 0

        for ng in sorted_nogoods:
            next_candidates: List[int] = []
            for cand in candidates:
                if (cand & ng) != 0:
                    next_candidates.append(cand)
                else:
                    mask = ng
                    while mask:
                        lsb = mask & -mask
                        expansions += 1
                        if expansions > self.max_hitting_set_expansions:
                            self.budget_exceeded = True
                            return [], False
                        new_cand = cand | lsb
                        if not any((existing & new_cand) == existing for existing in next_candidates):
                            next_candidates = [
                                existing for existing in next_candidates if (existing & new_cand) != new_cand
                            ]
                            next_candidates.append(new_cand)
                        mask ^= lsb
            candidates = next_candidates

        return candidates, not self.budget_exceeded

    def active_witness_justifications(self, consequent: str) -> List[SignedJustification]:
        return [
            j for j in self.justifications.get(consequent, [])
            if self.is_consistent(j.env_mask)
        ]


# =============================================================================
# 4. Complete-Justification Set Packing vs. Menger Min-Cut (Theorem 3)
# =============================================================================

class ProvenanceFlowGraph:
    """
    Vertex-capacitated Provenance Flow Graph G_prov = (V, E).
    Provides the polynomial-time Menger vertex-cut upper bound on disjoint proof packing,
    which is exact on the unit-premise fragment (|Premises(J)| == 1 for all J).
    """

    def __init__(self) -> None:
        self.adj: Dict[str, Dict[str, int]] = {}

    def _add_directed_edge(self, u: str, v: str, cap: int) -> None:
        self.adj.setdefault(u, {})[v] = self.adj.get(u, {}).get(v, 0) + cap
        self.adj.setdefault(v, {})[u] = self.adj.get(v, {}).get(u, 0)

    def add_unit_vertex(self, node_id: str) -> Tuple[str, str]:
        v_in = f"{node_id}::in"
        v_out = f"{node_id}::out"
        self._add_directed_edge(v_in, v_out, 1)
        return v_in, v_out

    def add_link(self, u_id: str, v_id: str, cap: int = 1) -> None:
        self._add_directed_edge(f"{u_id}::out", f"{v_id}::in", cap)

    def max_flow(self, source: str, sink: str) -> int:
        residual: Dict[str, Dict[str, int]] = {
            u: dict(edges) for u, edges in self.adj.items()
        }
        flow = 0
        while True:
            parent: Dict[str, str] = {source: source}
            queue = deque([source])
            while queue and sink not in parent:
                u = queue.popleft()
                for v, cap in residual.get(u, {}).items():
                    if cap > 0 and v not in parent:
                        parent[v] = u
                        queue.append(v)
            if sink not in parent:
                break
            bottleneck = 10 ** 9
            curr = sink
            while curr != source:
                prev = parent[curr]
                bottleneck = min(bottleneck, residual[prev][curr])
                curr = prev
            curr = sink
            while curr != source:
                prev = parent[curr]
                residual[prev][curr] -= bottleneck
                residual.setdefault(curr, {})[prev] = residual.get(curr, {}).get(prev, 0) + bottleneck
                curr = prev
            flow += bottleneck
        return flow


def compute_provenance_min_cut(
    witnesses: Sequence[SignedJustification],
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
) -> int:
    """
    Compute the vertex-capacitated Menger max-flow / min-cut upper bound on G_prov.
    Warning (Second Review Critical Issue #1): For multi-premise conjunctive justifications
    such as J_1={e_1,e_2}, J_2={e_2,e_3}, J_3={e_1,e_3}, Menger max-flow counts vertex-disjoint
    paths (3) rather than pairwise-disjoint complete justifications (1).
    Use `compute_disjoint_justification_packing` for exact complete-proof independence!
    """
    if not witnesses:
        return 0

    g = ProvenanceFlowGraph()
    src = "SUPER_SRC"
    sink = "SUPER_SINK"
    g.adj.setdefault(src, {})
    g.adj.setdefault(sink, {})

    seen_fd: Set[str] = set()
    seen_ch: Set[str] = set()
    seen_assump: Set[str] = set()
    seen_ctx: Set[str] = set()

    for idx, w in enumerate(witnesses):
        ctx_node = f"ctx:{w.context_id}"
        if ctx_node not in seen_ctx:
            seen_ctx.add(ctx_node)
            g.add_unit_vertex(ctx_node)

        w_node = f"wit:{idx}:{w.merkle_hash}"
        g.add_unit_vertex(w_node)
        g.add_link(ctx_node, w_node, cap=1)
        g._add_directed_edge(f"{w_node}::out", sink, 1)

        mask = w.env_mask
        while mask:
            lsb = mask & -mask
            bit_idx = lsb.bit_length() - 1
            assump = assumptions_by_bit.get(bit_idx)
            if assump is not None:
                fd_node = f"fd:{assump.failure_domain}"
                ch_node = f"ch:{assump.channel_id}"
                as_node = f"as:{assump.assumption_id}"

                if fd_node not in seen_fd:
                    seen_fd.add(fd_node)
                    fd_in, _ = g.add_unit_vertex(fd_node)
                    g._add_directed_edge(src, fd_in, 1)

                if ch_node not in seen_ch:
                    seen_ch.add(ch_node)
                    g.add_unit_vertex(ch_node)

                if as_node not in seen_assump:
                    seen_assump.add(as_node)
                    g.add_unit_vertex(as_node)

                g.add_link(fd_node, ch_node, cap=1)
                g.add_link(ch_node, as_node, cap=1)
                g.add_link(as_node, ctx_node, cap=1)
            mask ^= lsb

    return g.max_flow(src, sink)


def justification_dependency_footprint(
    j: SignedJustification,
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
) -> FrozenSet[str]:
    """
    Complete recursive dependency footprint Phi(Pi) of a verified derivation-DAG certificate Pi:
      - Leaf e_m: Phi(e_m) = {FailureDomain(e_m), Collector(e_m), AssumptionID(e_m)}
      - Internal rule node v: Phi(v) = {ctx(v)} union cup_{u in ch(v)} Phi(u)
    By structural induction over finite DAGs, Phi(Pi) equals the union of all ancestor
    proposer contexts plus all ancestor primitive telemetry domains, collectors, and IDs.
    Two complete proofs Pi_1, Pi_2 are structurally provenance-disjoint iff Phi(Pi_1) cap Phi(Pi_2) == empty.
    """
    deps: Set[str] = set()
    visited: Set[int] = set()
    stack: List[SignedJustification] = [j]

    while stack:
        node = stack.pop()
        node_key = id(node)
        if node_key in visited:
            continue
        visited.add(node_key)
        deps.add(f"ctx:{node.context_id}")

        mask = node.env_mask if not node.sub_justifications else (
            node.direct_env_mask if node.direct_env_mask is not None else node.env_mask
        )
        while mask:
            lsb = mask & -mask
            bit_idx = lsb.bit_length() - 1
            assump = assumptions_by_bit.get(bit_idx)
            if assump is not None:
                deps.add(f"fd:{assump.failure_domain}")
                deps.add(f"ch:{assump.channel_id}")
                deps.add(f"as:{assump.assumption_id}")
            else:
                deps.add(f"bit:{bit_idx}")
            mask ^= lsb

        for child in node.sub_justifications:
            stack.append(child)

    return frozenset(deps)


def compute_disjoint_justification_packing_bounded(
    witnesses: Sequence[SignedJustification],
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
    max_expansions: Optional[int] = 10000,
) -> Tuple[int, List[SignedJustification], bool]:
    """
    Theorem 3 (Bounded Exact Complete-Justification Set Packing Solver):
    Finds a maximum-cardinality family J_a subseteq W(a) of complete verified justifications
    such that for all distinct J_i, J_j in J_a:
        D(J_i) cap D(J_j) == empty.

    Uses 64-bit integer dependency bitmasks and branch-and-bound pruning with an explicit
    search-expansion budget `max_expansions`:
      - When `budget_exhausted == False`, returns the exact global optimum nu_pack(W(a)).
      - When `budget_exhausted == True`, returns the best valid disjoint subset found so far
        (a sound lower bound for positive admission, and triggers conservative world retention
        for refutation packing in Theorem 4).
    """
    if not witnesses:
        return 0, [], False

    # Map every distinct dependency token in union_J D(J) to a bit position
    dep_to_bit: Dict[str, int] = {}
    items: List[Tuple[int, SignedJustification]] = []
    seen_footprints: Set[int] = set()

    for w in witnesses:
        fp = justification_dependency_footprint(w, assumptions_by_bit)
        mask = 0
        for token in fp:
            if token not in dep_to_bit:
                dep_to_bit[token] = len(dep_to_bit)
            mask |= 1 << dep_to_bit[token]
        if mask not in seen_footprints:
            seen_footprints.add(mask)
            items.append((mask, w))

    items.sort(key=lambda pair: pair[0].bit_count())

    # Remove footprints that are strict supersets of another witness's footprint
    minimal_items: List[Tuple[int, SignedJustification]] = []
    for mask_i, w_i in items:
        if not any((mask_j & mask_i) == mask_j and mask_j != mask_i for mask_j, _ in minimal_items):
            minimal_items.append((mask_i, w_i))

    m = len(minimal_items)
    best_selection: List[SignedJustification] = []
    expansions = 0
    budget_exhausted = False

    def dfs(idx: int, used_mask: int, current: List[SignedJustification]) -> None:
        nonlocal best_selection, expansions, budget_exhausted
        if len(current) > len(best_selection):
            best_selection = list(current)
        if idx >= m:
            return
        if max_expansions is not None and expansions >= max_expansions:
            budget_exhausted = True
            return
        expansions += 1
        remaining_compatible = sum(
            1 for k in range(idx, m) if (minimal_items[k][0] & used_mask) == 0
        )
        if len(current) + remaining_compatible <= len(best_selection):
            return

        mask_i, w_i = minimal_items[idx]
        if (mask_i & used_mask) == 0:
            current.append(w_i)
            dfs(idx + 1, used_mask | mask_i, current)
            current.pop()
            if budget_exhausted:
                return
        dfs(idx + 1, used_mask, current)

    dfs(0, 0, [])
    return len(best_selection), best_selection, budget_exhausted


def proof_snapshot_signature(
    j: SignedJustification,
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
) -> Optional[Tuple[int, int, str, str]]:
    """
    Revision 8 P1.3 — Intra-proof snapshot signature audit.

    Returns `(epoch_revision, observation_version, resource_id, manifest_hash)` iff
    `j.env_mask` is non-empty, every leaf bit exists in `assumptions_by_bit`, and all
    leaves share a single consistent `(epoch_revision, observation_version, resource_id,
    manifest_hash)` snapshot tuple. Returns `None` if leaves mix snapshots/epochs/manifests.
    """
    mask = j.env_mask
    if mask == 0:
        return None
    sigs: Set[Tuple[int, int, str, str]] = set()
    while mask:
        lsb = mask & -mask
        bit_idx = lsb.bit_length() - 1
        assump = assumptions_by_bit.get(bit_idx)
        if assump is None:
            return None
        sigs.add(
            (
                assump.epoch_revision,
                assump.observation_version,
                getattr(assump, "resource_id", ""),
                getattr(assump, "manifest_hash", ""),
            )
        )
        mask ^= lsb
    if len(sigs) != 1:
        return None
    return next(iter(sigs))


def filter_snapshot_consistent_witnesses(
    witnesses: Sequence[SignedJustification],
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
    *,
    expected_snapshot_version: Optional[int] = None,
    expected_epoch: Optional[int] = None,
    expected_resource_id: Optional[str] = None,
    expected_manifest_hash: Optional[str] = None,
) -> Tuple[List[SignedJustification], bool, Optional[Tuple[int, int, str, str]], str]:
    """
    Revision 8 P1.3 — Cross-proof & multi-round snapshot consistency filter.

    Enforces existing snapshot Assumptions 1-3 across multi-round proof/refutation sets:
      1. Excludes any proof whose own leaves span mismatched `(epoch, obs_version, resource, manifest)`.
      2. Excludes any proof violating explicitly anchored `expected_*` bindings.
      3. When multiple distinct consistent snapshot signatures coexist across proofs (e.g.,
         collector A observed consistent snapshot v=50 while collector B or a later probe
         observed consistent snapshot v=51), NEVER packs proofs across different snapshots:
         fails closed (`inconsistency_detected=True`, returning `[]`) rather than inferring
         false certainty from temporally incompatible observations.
    """
    if not witnesses:
        return [], False, None, "NO_WITNESSES"

    consistent: List[Tuple[Tuple[int, int, str, str], SignedJustification]] = []
    inconsistency_detected = False
    first_reason = "SNAPSHOT_CONSISTENT"

    for w in witnesses:
        sig = proof_snapshot_signature(w, assumptions_by_bit)
        if sig is None:
            inconsistency_detected = True
            if first_reason == "SNAPSHOT_CONSISTENT":
                first_reason = "INTRA_PROOF_TORN_SNAPSHOT"
            continue
        ep, obs_ver, res_id, mhash = sig
        if expected_epoch is not None and ep != expected_epoch:
            inconsistency_detected = True
            if first_reason == "SNAPSHOT_CONSISTENT":
                first_reason = "EPOCH_MISMATCH"
            continue
        if expected_snapshot_version is not None and obs_ver != expected_snapshot_version:
            inconsistency_detected = True
            if first_reason == "SNAPSHOT_CONSISTENT":
                first_reason = "OBSERVATION_VERSION_MISMATCH"
            continue
        if expected_resource_id is not None and res_id != expected_resource_id:
            inconsistency_detected = True
            if first_reason == "SNAPSHOT_CONSISTENT":
                first_reason = "RESOURCE_ID_MISMATCH"
            continue
        if expected_manifest_hash is not None and mhash != expected_manifest_hash:
            inconsistency_detected = True
            if first_reason == "SNAPSHOT_CONSISTENT":
                first_reason = "MANIFEST_HASH_MISMATCH"
            continue
        consistent.append((sig, w))

    distinct_sigs = {sig for sig, _ in consistent}
    if len(distinct_sigs) > 1:
        # Cross-snapshot inconsistency: collectors/rounds observed different consistent snapshots.
        # Fail closed conservatives: refuse to pack across incompatible physical snapshots!
        return [], True, None, "CROSS_SNAPSHOT_PROOFS_INCOMPATIBLE"

    if not consistent:
        return [], inconsistency_detected, None, first_reason

    unique_sig = next(iter(distinct_sigs))
    return [w for _, w in consistent], inconsistency_detected, unique_sig, first_reason


def compute_disjoint_justification_packing(
    witnesses: Sequence[SignedJustification],
    assumptions_by_bit: Dict[int, PrimitiveAssumption],
    max_expansions: Optional[int] = 10000,
) -> Tuple[int, List[SignedJustification]]:
    """
    Convenience wrapper returning (packing_number, selected_witnesses).
    For budget-exhaustion status, call `compute_disjoint_justification_packing_bounded`.
    """
    count, selected, _ = compute_disjoint_justification_packing_bounded(
        witnesses, assumptions_by_bit, max_expansions=max_expansions
    )
    return count, selected


# =============================================================================
# 5. Fault-Tolerant Plausible-World Gate & Concurrent Exposure Gate (Theorems 4 & 5)
# =============================================================================

@dataclass(frozen=True)
class FaultWorld:
    """
    A candidate plant fault state s in S_cand.
    `viable=True` indicates s in Viab(K); `viable=False` marks a boundary/unsafe sink state.
    """
    world_id: str
    compatible_propositions: FrozenSet[str]
    safe_mutations: FrozenSet[str]
    probe_outcomes: Dict[str, str] = field(default_factory=dict)
    transition_map: Dict[str, str] = field(default_factory=dict)  # action -> post_world_id
    prior_weight: float = 1.0
    viable: bool = True


@dataclass(frozen=True)
class SnapshotContext:
    """
    Task 1: Explicit temporal and version semantics for belief snapshots and commit histories.
      - `snapshot_version`: Consistent target version watermark v_snap of hat{B}_{tau_obs}.
      - `min_obs_version` / `max_obs_version`: When telemetry spans mixed sensor versions [v_min, v_max],
        commits <= v_min are already reflected (never re-applied), commits > v_max are strictly
        post-snapshot (applied once in commit_version order), and commits in (v_min, v_max] are
        conservatively treated as snapshot-ambiguous (both skip and apply branches retained).
      - `require_versioned_commits`: When True, unversioned commits (commit_version is None)
        trigger the fail-closed unknown-order commit barrier.
      - `require_contiguous_versions`: Revision 6 (Theorem 5 Part 2 repair) — when True,
        requires every integer commit version in (v_min, v_head] to be present in committed_ops
        with no missing gap (catching unacknowledged I_H/I_U commits ordered before a known
        post-snapshot commit).
    """
    snapshot_version: int = 0
    min_obs_version: Optional[int] = None
    max_obs_version: Optional[int] = None
    require_versioned_commits: bool = True
    require_contiguous_versions: bool = False

    @property
    def effective_min_version(self) -> int:
        return self.snapshot_version if self.min_obs_version is None else self.min_obs_version

    @property
    def effective_max_version(self) -> int:
        return self.snapshot_version if self.max_obs_version is None else self.max_obs_version


@dataclass(frozen=True)
class InFlightMutation:
    """
    Represents an active committed (I_C), prepared/in-flight (I_H), or timed-out /
    cancellation-raced / epistemically UNKNOWN (I_U) operation u in U_tau
    for Theorem 5 (Hybrid Concurrent Mutation Safety & Cross-Root Exposure Conservation).
    """
    op_id: str
    action: str
    resource_group: str
    exposure_cost: int = 1
    lifecycle_state: str = "IN_FLIGHT"          # "COMMITTED" (I_C), "IN_FLIGHT"/"PREPARED" (I_H), "UNKNOWN"/"TIMED_OUT"/"CANCEL_RACING" (I_U)
    serialized_before_new: bool = False         # Mode S: resource-level ordering contract u <_ord a enforced at target
    commutes_with: FrozenSet[str] = frozenset() # Legacy metadata only; Mode C verifier never trusts unverified claims
    partial_effect_worlds: Tuple[str, ...] = () # Intermediate/partial-effect states Post_part(s, u)
    sequence_number: Optional[int] = None       # Monotonic resource sequence number for target order enforcement
    commit_version: Optional[int] = None        # Target version at which COMMITTED op took effect (Task 1)
    affected_domains: Tuple[str, ...] = ()      # Closed domain set D*(u) (Task 4)
    domain_costs: Dict[str, int] = field(default_factory=dict)  # Per-domain costs E(u, d)
    predecessor_op_ids: Tuple[str, ...] = ()    # Mode S predecessor-frontier Pred(u) (Task 3)
    intra_op_predecessor_ids: Tuple[str, ...] = ()  # P0.1: Intra-op phase dependencies for PHASE_DECOMPOSED refinement


class ConstraintDependencyGraph:
    """
    Task 4: Multi-resource constraint-domain closure D*(a).
    Maps direct resource domains to all coupled global/composite invariant domains
    (e.g., cross-group quorum caps, shared AZ availability limits) via fixed-point closure.
    """

    def __init__(self) -> None:
        self.edges: Dict[str, Set[str]] = {}
        self.global_members: Dict[str, Set[str]] = {}

    def add_coupling(self, domain_a: str, domain_b: str) -> None:
        self.edges.setdefault(domain_a, set()).add(domain_b)
        self.edges.setdefault(domain_b, set()).add(domain_a)

    def add_global_invariant(self, global_domain: str, member_domains: Iterable[str]) -> None:
        members = set(member_domains)
        self.global_members.setdefault(global_domain, set()).update(members)
        for m in members:
            self.edges.setdefault(m, set()).add(global_domain)

    def domain_closure(self, direct_domains: Iterable[str]) -> Tuple[str, ...]:
        visited: Set[str] = set()
        queue = deque(direct_domains)
        while queue:
            d = queue.popleft()
            if d in visited:
                continue
            visited.add(d)
            for nxt in self.edges.get(d, ()):
                if nxt not in visited:
                    queue.append(nxt)
            # If d is a global invariant domain, include all its member domains if coupled?
            # No: global invariant coupling flows from member -> global_domain, and global_domain
            # itself must be checked/reserved. Transitive invariant dependencies in `edges` are closed.
        return tuple(sorted(visited))

    def closure_costs(self, direct_costs: Dict[str, int]) -> Dict[str, int]:
        closed = self.domain_closure(direct_costs.keys())
        result: Dict[str, int] = {}
        for d in closed:
            if d in direct_costs:
                result[d] = direct_costs[d]
            elif d in self.global_members:
                # Aggregate exposure projected onto composite/global domain d
                result[d] = sum(direct_costs.get(m, 0) for m in self.global_members[d])
            else:
                result[d] = max(direct_costs.values(), default=1)
        return result


class CrossRootExposureLedger:
    """
    Implements KedgeFlow v0.91 Cross-Root Exposure Conservation (Section 7.3.1 & Theorem 5)
    with Task 1 commit-version tracking and Task 4 multi-domain closure reservations.
    Maintains pairwise-disjoint sets I_C(d), I_H(d), and I_U(d) per domain d.
    """

    def __init__(
        self,
        caps: Dict[str, int],
        dependency_graph: Optional[ConstraintDependencyGraph] = None,
        atomic_multidomain: bool = True,
    ) -> None:
        self.caps: Dict[str, int] = dict(caps)
        self.dependency_graph = dependency_graph
        self.atomic_multidomain = atomic_multidomain
        self.committed_ic: Dict[str, Dict[str, InFlightMutation]] = {d: {} for d in caps}
        self.holding_ih: Dict[str, Dict[str, InFlightMutation]] = {d: {} for d in caps}
        self.unknown_iu: Dict[str, Dict[str, InFlightMutation]] = {d: {} for d in caps}
        self.issuance_dedup: Dict[str, InFlightMutation] = {}
        self.last_sequence: Dict[str, int] = {d: 0 for d in caps}
        self.domain_admission_rev: Dict[str, int] = {d: 0 for d in caps}
        self.global_commit_version: int = 0
        self.external_drift_detected: bool = False

    def admission_revisions(self, domains: Sequence[str]) -> Dict[str, int]:
        """P1.3: Return current per-domain monotonic admission linearization revisions r_adm(d)."""
        return {d: self.domain_admission_rev.get(d, 0) for d in sorted(set(domains))}

    def domain_totals(self, domain: str) -> Tuple[int, int, int]:
        c_val = sum(op.domain_costs.get(domain, op.exposure_cost) for op in self.committed_ic.get(domain, {}).values())
        h_val = sum(op.domain_costs.get(domain, op.exposure_cost) for op in self.holding_ih.get(domain, {}).values())
        u_val = sum(op.domain_costs.get(domain, op.exposure_cost) for op in self.unknown_iu.get(domain, {}).values())
        return c_val, h_val, u_val

    def total_reserved_exposure(self, domain: str) -> int:
        c_val, h_val, u_val = self.domain_totals(domain)
        return c_val + h_val + u_val

    def active_operations(self, domain: str) -> List[InFlightMutation]:
        return (
            list(self.committed_ic.get(domain, {}).values())
            + list(self.holding_ih.get(domain, {}).values())
            + list(self.unknown_iu.get(domain, {}).values())
        )

    def all_active_operations(self) -> List[InFlightMutation]:
        seen: Dict[str, InFlightMutation] = {}
        for mapping in (self.committed_ic, self.holding_ih, self.unknown_iu):
            for dom_map in mapping.values():
                for op_id, op in dom_map.items():
                    seen[op_id] = op
        return list(seen.values())

    def _assert_disjoint_invariants(self, domain: str) -> None:
        ic = set(self.committed_ic.get(domain, {}).keys())
        ih = set(self.holding_ih.get(domain, {}).keys())
        iu = set(self.unknown_iu.get(domain, {}).keys())
        assert ic.isdisjoint(ih) and ih.isdisjoint(iu) and ic.isdisjoint(iu)

    def admit_and_reserve(
        self,
        issuance_key: str,
        op_id: str,
        action: str,
        domain: str,
        exposure_cost: int,
        serialized_before_new: bool = False,
        commutes_with: FrozenSet[str] = frozenset(),
        partial_effect_worlds: Tuple[str, ...] = (),
        predecessor_op_ids: Tuple[str, ...] = (),
        expected_admission_revs: Optional[Dict[str, int]] = None,
    ) -> Tuple[bool, Optional[InFlightMutation], str]:
        return self.admit_and_reserve_multi_domain(
            issuance_key=issuance_key,
            op_id=op_id,
            action=action,
            direct_domain_costs={domain: exposure_cost},
            serialized_before_new=serialized_before_new,
            commutes_with=commutes_with,
            partial_effect_worlds=partial_effect_worlds,
            predecessor_op_ids=predecessor_op_ids,
            expected_admission_revs=expected_admission_revs,
        )

    def admit_and_reserve_multi_domain(
        self,
        issuance_key: str,
        op_id: str,
        action: str,
        direct_domain_costs: Dict[str, int],
        serialized_before_new: bool = False,
        commutes_with: FrozenSet[str] = frozenset(),
        partial_effect_worlds: Tuple[str, ...] = (),
        predecessor_op_ids: Tuple[str, ...] = (),
        fail_on_domain: Optional[str] = None,
        crash_after_domain: Optional[str] = None,
        expected_admission_revs: Optional[Dict[str, int]] = None,
    ) -> Tuple[bool, Optional[InFlightMutation], str]:
        """
        Task 4 & P1.3: Atomic multi-domain closure reservation with admission-revision CAS.
        Computes closure D*(a) via `dependency_graph` (if present), sorts domains canonically,
        verifies per-domain admission revision CAS (when `expected_admission_revs` is provided)
        so a Mode C certificate verified against an earlier unresolved set U^(r)|_D cannot be
        reused after a concurrent admission modifies U|_D, and validates
        C(d) + H(d) + U(d) + E(a, d) <= B(d) across EVERY d in D*(a).
          - If `atomic_multidomain=False` and |D*(a)| > 1 with any overlapping unserialized ops,
            rejects unless serialized (conservative serialization fallback).
          - If `fail_on_domain` is triggered mid-transaction, rolls back pre-dispatch reservations
            on earlier domains cleanly (`ROLLBACK_PARTIAL_RESERVATION`).
          - If `crash_after_domain` is triggered, retains already-reserved domains conservatively in I_U
            and advances domain_admission_rev so concurrent certificates must revalidate
            (`COORDINATOR_CRASH_RETAINED_IN_IU`).
        """
        if self.dependency_graph is not None:
            domain_costs = self.dependency_graph.closure_costs(direct_domain_costs)
        else:
            domain_costs = dict(direct_domain_costs)

        if issuance_key in self.issuance_dedup:
            existing = self.issuance_dedup[issuance_key]
            if (
                existing.action != action
                or existing.domain_costs != domain_costs
                or existing.serialized_before_new != serialized_before_new
            ):
                return False, None, "REJECTED_ISSUANCE_KEY_CONFLICT"
            return True, existing, "DUPLICATE_IDEMPOTENT_REUSE"

        ordered_domains = tuple(sorted(domain_costs.keys()))
        if not ordered_domains:
            return False, None, "EMPTY_DOMAIN_CLOSURE"

        primary_domain = ordered_domains[0]
        primary_cost = domain_costs[primary_domain]

        if not self.atomic_multidomain and len(ordered_domains) > 1 and not serialized_before_new:
            return False, None, "MULTIDOMAIN_ATOMICITY_UNAVAILABLE_SERIALIZE_REQUIRED"

        # P1.3: Admission linearization CAS over every domain in closure D*(a)
        if expected_admission_revs is not None:
            for d in ordered_domains:
                if d not in expected_admission_revs or self.domain_admission_rev.get(d, 0) != expected_admission_revs[d]:
                    return False, None, "REJECTED_STALE_CONCURRENCY_CERTIFICATE_REVALIDATION_REQUIRED"

        # Pre-check all domains in closure atomically before mutating any domain
        for d in ordered_domains:
            cap = self.caps.get(d)
            if cap is not None and self.total_reserved_exposure(d) + domain_costs[d] > cap:
                return False, None, "EXPOSURE_BUDGET_EXCEEDED"

        seq = max((self.last_sequence.get(d, 0) for d in ordered_domains), default=0) + 1
        op = InFlightMutation(
            op_id=op_id,
            action=action,
            resource_group=primary_domain,
            exposure_cost=primary_cost,
            lifecycle_state="IN_FLIGHT",
            serialized_before_new=serialized_before_new,
            commutes_with=commutes_with,
            partial_effect_worlds=partial_effect_worlds,
            sequence_number=seq,
            affected_domains=ordered_domains,
            domain_costs=dict(domain_costs),
            predecessor_op_ids=predecessor_op_ids,
        )

        reserved_so_far: List[str] = []
        for d in ordered_domains:
            if fail_on_domain == d:
                # Partial reservation failure -> deterministic pre-dispatch rollback
                for prev_d in reserved_so_far:
                    self.holding_ih.get(prev_d, {}).pop(op_id, None)
                return False, None, "ROLLBACK_PARTIAL_RESERVATION"

            self.holding_ih.setdefault(d, {})[op_id] = op
            reserved_so_far.append(d)

            if crash_after_domain == d:
                # Coordinator crash after reserving d: move reserved_so_far conservatively to I_U!
                crashed_op = InFlightMutation(
                    op_id=op.op_id,
                    action=op.action,
                    resource_group=op.resource_group,
                    exposure_cost=op.exposure_cost,
                    lifecycle_state="UNKNOWN_COORDINATOR_CRASH",
                    serialized_before_new=op.serialized_before_new,
                    commutes_with=op.commutes_with,
                    partial_effect_worlds=op.partial_effect_worlds,
                    sequence_number=op.sequence_number,
                    affected_domains=op.affected_domains,
                    domain_costs=op.domain_costs,
                    predecessor_op_ids=op.predecessor_op_ids,
                )
                for prev_d in reserved_so_far:
                    self.holding_ih[prev_d].pop(op_id, None)
                    self.unknown_iu.setdefault(prev_d, {})[op_id] = crashed_op
                    self.domain_admission_rev[prev_d] = self.domain_admission_rev.get(prev_d, 0) + 1
                return False, crashed_op, "COORDINATOR_CRASH_RETAINED_IN_IU"

        for d in ordered_domains:
            self.last_sequence[d] = seq
            self.domain_admission_rev[d] = self.domain_admission_rev.get(d, 0) + 1
            self._assert_disjoint_invariants(d)
        self.issuance_dedup[issuance_key] = op
        return True, op, "RESERVED_IN_IH"

    def mark_timeout_or_cancel_race(self, domain: str, op_id: str, state: str = "UNKNOWN") -> bool:
        """
        Move an operation across all its affected domains from I_H(d) to I_U(d) on timeout,
        lost ack, or unconfirmed cancellation race. Preserves C(d) + H(d) + U(d) invariance.
        """
        old = self.holding_ih.get(domain, {}).get(op_id)
        if old is None:
            return False
        domains = old.affected_domains or (domain,)
        updated = InFlightMutation(
            op_id=old.op_id,
            action=old.action,
            resource_group=old.resource_group,
            exposure_cost=old.exposure_cost,
            lifecycle_state=state,
            serialized_before_new=old.serialized_before_new,
            commutes_with=old.commutes_with,
            partial_effect_worlds=old.partial_effect_worlds,
            sequence_number=old.sequence_number,
            affected_domains=old.affected_domains,
            domain_costs=old.domain_costs,
            predecessor_op_ids=old.predecessor_op_ids,
        )
        for d in domains:
            self.holding_ih.get(d, {}).pop(op_id, None)
            self.unknown_iu.setdefault(d, {})[op_id] = updated
            self.domain_admission_rev[d] = self.domain_admission_rev.get(d, 0) + 1
            self._assert_disjoint_invariants(d)
        return True

    def confirm_commit(
        self,
        domain: str,
        op_id: str,
        actual_cost: Optional[int] = None,
        commit_version: Optional[int] = None,
    ) -> bool:
        """Move operation from I_H or I_U to I_C across all its affected domains."""
        old = (
            self.holding_ih.get(domain, {}).get(op_id)
            or self.unknown_iu.get(domain, {}).get(op_id)
        )
        if old is None:
            return False
        if commit_version is None:
            self.global_commit_version += 1
            commit_version = self.global_commit_version
        else:
            self.global_commit_version = max(self.global_commit_version, commit_version)

        domains = old.affected_domains or (domain,)
        cost = old.exposure_cost if actual_cost is None else min(old.exposure_cost, actual_cost)
        new_domain_costs = dict(old.domain_costs)
        if actual_cost is not None:
            for d in domains:
                if d in new_domain_costs:
                    new_domain_costs[d] = min(new_domain_costs[d], actual_cost)
        committed = InFlightMutation(
            op_id=old.op_id,
            action=old.action,
            resource_group=old.resource_group,
            exposure_cost=cost,
            lifecycle_state="COMMITTED",
            serialized_before_new=True,
            commutes_with=old.commutes_with,
            partial_effect_worlds=(),
            sequence_number=old.sequence_number,
            commit_version=commit_version,
            affected_domains=old.affected_domains,
            domain_costs=new_domain_costs,
            predecessor_op_ids=old.predecessor_op_ids,
        )
        for d in domains:
            self.holding_ih.get(d, {}).pop(op_id, None)
            self.unknown_iu.get(d, {}).pop(op_id, None)
            self.committed_ic.setdefault(d, {})[op_id] = committed
            self.domain_admission_rev[d] = self.domain_admission_rev.get(d, 0) + 1
            self._assert_disjoint_invariants(d)
        return True

    def release_on_verified_no_effect(
        self, domain: str, op_id: str, has_fenced_no_effect_proof: bool
    ) -> bool:
        """
        Pre-effect release from I_H(d) or I_U(d) is permitted ONLY when accompanied by an
        authoritative fenced_no_effect receipt and target barrier excluding late execution.
        """
        if not has_fenced_no_effect_proof:
            return False
        old = (
            self.holding_ih.get(domain, {}).get(op_id)
            or self.unknown_iu.get(domain, {}).get(op_id)
        )
        if old is None:
            return False
        domains = old.affected_domains or (domain,)
        removed_any = False
        for d in domains:
            r_h = self.holding_ih.get(d, {}).pop(op_id, None)
            r_u = self.unknown_iu.get(d, {}).pop(op_id, None)
            if r_h is not None or r_u is not None:
                removed_any = True
                self.domain_admission_rev[d] = self.domain_admission_rev.get(d, 0) + 1
            self._assert_disjoint_invariants(d)
        return removed_any

    def reconcile_after_restart(
        self,
        domain: str,
        target_committed_versions: Dict[str, int],
        fenced_no_effect_ops: Set[str],
    ) -> Dict[str, str]:
        """
        Task 1: Reconcile I_H and I_U against durable target commit journal after gateway/target restart.
          - Ops in `target_committed_versions` move cleanly to I_C with durable commit_version.
          - Ops in `fenced_no_effect_ops` are released.
          - All remaining unresolved ops in I_H move conservatively to I_U (`UNKNOWN`) until resolved.
        """
        report: Dict[str, str] = {}
        for op_id in list(self.holding_ih.get(domain, {}).keys()) + list(self.unknown_iu.get(domain, {}).keys()):
            if op_id in target_committed_versions:
                self.confirm_commit(domain, op_id, commit_version=target_committed_versions[op_id])
                report[op_id] = "RECONCILED_COMMITTED"
            elif op_id in fenced_no_effect_ops:
                self.release_on_verified_no_effect(domain, op_id, has_fenced_no_effect_proof=True)
                report[op_id] = "RECONCILED_FENCED_NO_EFFECT"
            else:
                if op_id in self.holding_ih.get(domain, {}):
                    self.mark_timeout_or_cancel_race(domain, op_id, state="UNKNOWN")
                report[op_id] = "RETAINED_IN_IU_UNKNOWN"
        return report


@dataclass(frozen=True)
class CompatibilityCertificate:
    """
    Concrete machine-verifiable Mode C compatibility certificate.
    Issued ONLY by `ModeCCompatibilityVerifier` when physical execution refinement
    (R1 SERIALIZABLE_ATOMIC or R2 PHASE_DECOMPOSED) holds, all predecessor prefixes,
    all concurrent subsets (both containing and excluding the candidate action),
    all linear extensions respecting predecessor constraints, partial-effect
    transitions, and aggregate exposure bounds pass deterministic verification.
    Bound to per-domain admission linearization revisions `admission_revisions` (P1.3).
    """
    cert_id: str
    candidate_action: str
    domain_closure: Tuple[str, ...]
    base_world_ids: Tuple[str, ...]
    concurrent_op_ids: Tuple[str, ...]
    serialized_predecessor_ids: Tuple[str, ...]
    checked_schedule_count: int
    pairwise_commutativity_ok: bool
    all_intermediate_states_safe: bool
    partial_effect_safe: bool
    aggregate_exposure_ok: bool
    supported_class: str
    verifier_digest: str
    checked_absent_candidate_schedules: bool = True
    physical_refinement_mode: str = "SERIALIZABLE_ATOMIC"
    admission_revisions: Tuple[Tuple[str, int], ...] = ()


class ModeCCompatibilityVerifier:
    """
    Sound, conservative deterministic verifier for Mode S prefix safety and Mode C concurrency.
    Supported operational class:
      - Physical execution refinement explicitly attested as either:
          * SERIALIZABLE_ATOMIC (R1): target enforces serializable/linearizable physical effects
            at the modeled operation boundary; or
          * PHASE_DECOMPOSED (R2): transition model exposes internal read/write phases and
            intra-op phase dependencies so overlapping physical interleavings are checked directly.
        Targets declared UNVERIFIED_ASYNC fail closed immediately (P0.1).
      - Bounded number of unserialized concurrent operations (|U_C| <= MAX_CONCURRENT_MODE_C_OPS,
        or |P| <= MAX_PHASE_OPS in PHASE_DECOMPOSED mode) and bounded unordered serialized predecessors.
      - Every operation has modeled transition/safety semantics in the finite-state TCB
        model (S_cand, A_safe, Post_hat); any unmodeled transition fails closed.
    Exhaustively checks EVERY admissible subset — BOTH containing candidate and excluding candidate
    (covering canceled / timed-out / fenced_no_effect realizations) — across every linear
    extension respecting `predecessor_op_ids` and `intra_op_predecessor_ids`.
    """

    MAX_CONCURRENT_MODE_C_OPS = 4
    MAX_PHASE_OPS = 6
    VALID_PHYSICAL_REFINEMENT_MODES = ("SERIALIZABLE_ATOMIC", "PHASE_DECOMPOSED", "UNVERIFIED_ASYNC")

    @staticmethod
    def _is_intra_op_prefix_closed(
        subset: Sequence[InFlightMutation],
        all_op_ids: Set[str],
    ) -> bool:
        present = {op.op_id for op in subset}
        for op in subset:
            for pid in op.intra_op_predecessor_ids:
                if pid in all_op_ids and pid not in present:
                    return False
        return True

    @staticmethod
    def _enumerate_linear_extensions(
        ops: Sequence[InFlightMutation],
    ) -> Tuple[bool, List[Tuple[InFlightMutation, ...]]]:
        """
        Enumerate all linear extensions of `ops` respecting induced `predecessor_op_ids`
        and `intra_op_predecessor_ids` constraints among operations present in `ops`.
        Operations absent from `ops` (e.g., settled as `fenced_no_effect` or canceled)
        do not block successors. Returns `(False, [])` if the induced ordering contains a cycle.
        """
        n = len(ops)
        if n == 0:
            return True, [()]
        op_by_id = {op.op_id: idx for idx, op in enumerate(ops)}
        preds: List[Set[int]] = [set() for _ in range(n)]
        for idx, op in enumerate(ops):
            for pid in tuple(op.predecessor_op_ids) + tuple(op.intra_op_predecessor_ids):
                if pid in op_by_id:
                    p_idx = op_by_id[pid]
                    if p_idx == idx:
                        return False, []
                    preds[idx].add(p_idx)

        # Cycle check via Kahn's algorithm
        in_deg = [len(p) for p in preds]
        adj: List[List[int]] = [[] for _ in range(n)]
        for idx, p_set in enumerate(preds):
            for p_idx in p_set:
                adj[p_idx].append(idx)
        q = deque(i for i in range(n) if in_deg[i] == 0)
        visited_count = 0
        while q:
            u = q.popleft()
            visited_count += 1
            for v in adj[u]:
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    q.append(v)
        if visited_count != n:
            return False, []

        results: List[Tuple[InFlightMutation, ...]] = []
        chosen = [False] * n
        current: List[InFlightMutation] = []

        def backtrack() -> None:
            if len(current) == n:
                results.append(tuple(current))
                return
            for i in range(n):
                if not chosen[i] and all(chosen[p] for p in preds[i]):
                    chosen[i] = True
                    current.append(ops[i])
                    backtrack()
                    current.pop()
                    chosen[i] = False

        backtrack()
        return True, results

    @classmethod
    def verify_prefix_schedule_safety(
        cls,
        base_worlds: Sequence[FaultWorld],
        predecessor_ops: Sequence[InFlightMutation],
        worlds_by_id: Dict[str, FaultWorld],
    ) -> Tuple[bool, Dict[str, FaultWorld], str]:
        """
        P0-A Repair (Mode S / U_S Inductive Prefix Safety):
        Verify that EVERY admissible subset O <= predecessor_ops and EVERY linear extension
        pi in Lin(O, <_ord|_O) is sequentially safe from every s_0 in base_worlds:
          - Every starting, intermediate, and terminal state is modeled and viable.
          - Every executed predecessor step o_j belongs to A_safe(s_{j-1}).
          - Every full and declared partial post-state is modeled and viable.
        Returns `(True, reachable_states_by_id, "PREFIX_SAFE")` iff all prefixes pass.
        Fails closed if unordered predecessors exceed MAX_CONCURRENT_MODE_C_OPS.
        """
        if not base_worlds:
            return False, {}, "EMPTY_BELIEF_SET"
        for w in base_worlds:
            if not w.viable:
                return False, {}, "UNSAFE_BASE_STATE_NOT_VIABLE"

        reachable: Dict[str, FaultWorld] = {w.world_id: w for w in base_worlds}
        if not predecessor_ops:
            return True, reachable, "PREFIX_SAFE"

        # Check whether predecessors are totally ordered via predecessor_op_ids
        if len(predecessor_ops) > cls.MAX_CONCURRENT_MODE_C_OPS:
            ok_ord, exts = cls._enumerate_linear_extensions(predecessor_ops)
            if not ok_ord:
                return False, {}, "CYCLIC_PREDECESSOR_ORDER"
            if len(exts) > 1:
                return False, {}, "MODE_S_PREDECESSOR_CLASS_UNBOUNDED_FAIL_CLOSED"

        for r in range(1, len(predecessor_ops) + 1):
            for subset in itertools.combinations(predecessor_ops, r):
                ok_ord, exts = cls._enumerate_linear_extensions(subset)
                if not ok_ord:
                    return False, {}, "CYCLIC_PREDECESSOR_ORDER"
                for perm in exts:
                    frontier: Dict[str, FaultWorld] = {w.world_id: w for w in base_worlds}
                    for step_op in perm:
                        next_frontier: Dict[str, FaultWorld] = {}
                        for st in frontier.values():
                            if not st.viable or step_op.action not in st.safe_mutations:
                                return False, {}, "UNSAFE_PREDECESSOR_PREFIX_STATE"

                            post_id = st.transition_map.get(step_op.action)
                            if post_id is not None:
                                if post_id not in worlds_by_id:
                                    return False, {}, "MODE_C_REJECTED_UNMODELED_POST_STATE"
                                post_w = worlds_by_id[post_id]
                                if not post_w.viable:
                                    return False, {}, "UNSAFE_PREDECESSOR_PREFIX_STATE"
                                next_frontier[post_id] = post_w
                            else:
                                next_frontier[st.world_id] = st

                            for part_id in step_op.partial_effect_worlds:
                                if part_id not in worlds_by_id:
                                    return False, {}, "MODE_C_REJECTED_UNMODELED_PARTIAL_STATE"
                                part_w = worlds_by_id[part_id]
                                if not part_w.viable:
                                    return False, {}, "UNSAFE_PREDECESSOR_PREFIX_STATE"
                                next_frontier[part_id] = part_w

                        frontier = next_frontier
                        reachable.update(frontier)

        return True, reachable, "PREFIX_SAFE"

    @staticmethod
    def _compute_digest(
        candidate_action: str,
        domain_closure: Tuple[str, ...],
        base_world_ids: Tuple[str, ...],
        concurrent_op_ids: Tuple[str, ...],
        serialized_predecessor_ids: Tuple[str, ...],
        checked_schedule_count: int,
        pairwise_commutativity_ok: bool,
        checked_absent_candidate_schedules: bool = True,
        physical_refinement_mode: str = "SERIALIZABLE_ATOMIC",
        admission_revisions: Tuple[Tuple[str, int], ...] = (),
    ) -> str:
        rev_str = ",".join(f"{d}:{r}" for d, r in admission_revisions)
        payload = (
            f"{candidate_action}|{','.join(domain_closure)}|{','.join(base_world_ids)}|"
            f"{','.join(concurrent_op_ids)}|{','.join(serialized_predecessor_ids)}|"
            f"{checked_schedule_count}|{pairwise_commutativity_ok}|{checked_absent_candidate_schedules}|"
            f"{physical_refinement_mode}|{rev_str}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def verify_certificate_digest(cls, cert: CompatibilityCertificate) -> bool:
        expected = cls._compute_digest(
            cert.candidate_action,
            cert.domain_closure,
            cert.base_world_ids,
            cert.concurrent_op_ids,
            cert.serialized_predecessor_ids,
            cert.checked_schedule_count,
            cert.pairwise_commutativity_ok,
            cert.checked_absent_candidate_schedules,
            cert.physical_refinement_mode,
            cert.admission_revisions,
        )
        return (
            cert.all_intermediate_states_safe
            and cert.partial_effect_safe
            and cert.aggregate_exposure_ok
            and cert.checked_absent_candidate_schedules
            and cert.physical_refinement_mode in ("SERIALIZABLE_ATOMIC", "PHASE_DECOMPOSED")
            and hmac.compare_digest(cert.verifier_digest, expected)
        )

    @classmethod
    def verify(
        cls,
        candidate_action: str,
        domain_closure: Tuple[str, ...],
        base_worlds: Sequence[FaultWorld],
        unresolved_ops: Sequence[InFlightMutation],
        worlds_by_id: Dict[str, FaultWorld],
        aggregate_exposure_ok: bool = True,
        candidate_partial_effect_worlds: Tuple[str, ...] = (),
        candidate_predecessor_op_ids: Tuple[str, ...] = (),
        candidate_op_id: str = "__candidate__",
        physical_refinement_mode: str = "SERIALIZABLE_ATOMIC",
        candidate_phases: Sequence[InFlightMutation] = (),
        admission_revisions: Optional[Dict[str, int]] = None,
    ) -> Tuple[bool, Optional[CompatibilityCertificate], str]:
        if not aggregate_exposure_ok:
            return False, None, "EXPOSURE_BUDGET_EXCEEDED"
        if not base_worlds:
            return False, None, "EMPTY_BELIEF_SET"
        if (
            physical_refinement_mode == "UNVERIFIED_ASYNC"
            or physical_refinement_mode not in cls.VALID_PHYSICAL_REFINEMENT_MODES
        ):
            return False, None, "MODE_C_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED"

        serialized_ops = [op for op in unresolved_ops if op.serialized_before_new]
        concurrent_ops = [op for op in unresolved_ops if not op.serialized_before_new]
        rev_tuple = tuple(sorted((str(d), int(r)) for d, r in (admission_revisions or {}).items()))

        if physical_refinement_mode == "PHASE_DECOMPOSED":
            cand_phase_list = (
                list(candidate_phases)
                if candidate_phases
                else [
                    InFlightMutation(
                        op_id=candidate_op_id,
                        action=candidate_action,
                        resource_group=domain_closure[0],
                        partial_effect_worlds=candidate_partial_effect_worlds,
                        predecessor_op_ids=candidate_predecessor_op_ids,
                    )
                ]
            )
            all_phase_ops = list(concurrent_ops) + cand_phase_list
            if len(all_phase_ops) > cls.MAX_PHASE_OPS:
                return False, None, "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S"

            ok_pre, pre_states, pre_reason = cls.verify_prefix_schedule_safety(
                base_worlds, serialized_ops, worlds_by_id
            )
            if not ok_pre:
                return False, None, pre_reason

            all_phase_ids = {op.op_id for op in all_phase_ops}
            checked_schedules = 0
            for r in range(1, len(all_phase_ops) + 1):
                for subset in itertools.combinations(all_phase_ops, r):
                    if not cls._is_intra_op_prefix_closed(subset, all_phase_ids):
                        continue
                    ok_ord, exts = cls._enumerate_linear_extensions(subset)
                    if not ok_ord:
                        return False, None, "CYCLIC_PREDECESSOR_ORDER"
                    for perm in exts:
                        checked_schedules += 1
                        frontier: Dict[str, FaultWorld] = dict(pre_states)
                        for step_op in perm:
                            next_frontier: Dict[str, FaultWorld] = {}
                            for st in frontier.values():
                                if not st.viable or step_op.action not in st.safe_mutations:
                                    return False, None, "UNSAFE_PHASE_INTERLEAVING_EXITS_INVARIANT"
                                post_id = st.transition_map.get(step_op.action)
                                if post_id is not None:
                                    if post_id not in worlds_by_id or not worlds_by_id[post_id].viable:
                                        return False, None, "UNSAFE_PHASE_INTERLEAVING_EXITS_INVARIANT"
                                    next_frontier[post_id] = worlds_by_id[post_id]
                                else:
                                    next_frontier[st.world_id] = st
                                for part_id in step_op.partial_effect_worlds:
                                    if part_id not in worlds_by_id or not worlds_by_id[part_id].viable:
                                        return False, None, "UNSAFE_PARTIAL_EFFECT_STATE"
                                    next_frontier[part_id] = worlds_by_id[part_id]
                            frontier = next_frontier

            base_ids = tuple(sorted(w.world_id for w in base_worlds))
            conc_ids = tuple(op.op_id for op in concurrent_ops)
            ser_ids = tuple(op.op_id for op in serialized_ops)
            digest = cls._compute_digest(
                candidate_action,
                domain_closure,
                base_ids,
                conc_ids,
                ser_ids,
                checked_schedules,
                False,
                checked_absent_candidate_schedules=True,
                physical_refinement_mode="PHASE_DECOMPOSED",
                admission_revisions=rev_tuple,
            )
            cert = CompatibilityCertificate(
                cert_id=f"compat_{digest}",
                candidate_action=candidate_action,
                domain_closure=domain_closure,
                base_world_ids=base_ids,
                concurrent_op_ids=conc_ids,
                serialized_predecessor_ids=ser_ids,
                checked_schedule_count=checked_schedules,
                pairwise_commutativity_ok=False,
                all_intermediate_states_safe=True,
                partial_effect_safe=True,
                aggregate_exposure_ok=True,
                supported_class="BOUNDED_PHASE_DECOMPOSED",
                verifier_digest=digest,
                checked_absent_candidate_schedules=True,
                physical_refinement_mode="PHASE_DECOMPOSED",
                admission_revisions=rev_tuple,
            )
            return True, cert, "ADMIT_COMPOUND_SAFE"

        if len(concurrent_ops) > cls.MAX_CONCURRENT_MODE_C_OPS:
            return False, None, "MODE_C_REJECTED_OUTSIDE_BOUNDED_CLASS_FALLBACK_MODE_S"

        # P0-A: Verify full prefix sequential safety across ALL subsets & linear extensions of U_S
        ok_pre, pre_states, pre_reason = cls.verify_prefix_schedule_safety(
            base_worlds, serialized_ops, worlds_by_id
        )
        if not ok_pre:
            return False, None, pre_reason

        cand_op = InFlightMutation(
            op_id=candidate_op_id,
            action=candidate_action,
            resource_group=domain_closure[0],
            partial_effect_worlds=candidate_partial_effect_worlds,
            predecessor_op_ids=candidate_predecessor_op_ids,
        )
        checked_schedules = 0

        # P0-B: Check EVERY subset O' <= U_C union {a}, INCLUDING subsets EXCLUDING a
        # (covering canceled, timed-out, or fenced_no_effect candidate executions as well as
        # predecessors settled as fenced_no_effect), across every <_ord|_{O'}-linear extension!
        for r in range(0, len(concurrent_ops) + 1):
            for subset in itertools.combinations(concurrent_ops, r):
                # 1. Subsets EXCLUDING candidate action a (when subset is nonempty)
                if subset:
                    ok_ord_no_a, exts_no_a = cls._enumerate_linear_extensions(subset)
                    if not ok_ord_no_a:
                        return False, None, "CYCLIC_PREDECESSOR_ORDER"
                    for perm in exts_no_a:
                        checked_schedules += 1
                        frontier: Dict[str, FaultWorld] = dict(pre_states)
                        for step_op in perm:
                            next_frontier: Dict[str, FaultWorld] = {}
                            for st in frontier.values():
                                if not st.viable or step_op.action not in st.safe_mutations:
                                    return False, None, "UNSAFE_ABSENT_CANDIDATE_SCHEDULE"

                                post_id = st.transition_map.get(step_op.action)
                                if post_id is not None:
                                    if post_id not in worlds_by_id or not worlds_by_id[post_id].viable:
                                        return False, None, "UNSAFE_ABSENT_CANDIDATE_SCHEDULE"
                                    next_frontier[post_id] = worlds_by_id[post_id]
                                else:
                                    next_frontier[st.world_id] = st

                                for part_id in step_op.partial_effect_worlds:
                                    if part_id not in worlds_by_id or not worlds_by_id[part_id].viable:
                                        return False, None, "UNSAFE_ABSENT_CANDIDATE_SCHEDULE"
                                    next_frontier[part_id] = worlds_by_id[part_id]
                            frontier = next_frontier

                # 2. Subsets CONTAINING candidate action a
                ops_to_order = list(subset) + [cand_op]
                ok_ord_with_a, exts_with_a = cls._enumerate_linear_extensions(ops_to_order)
                if not ok_ord_with_a:
                    return False, None, "CYCLIC_PREDECESSOR_ORDER"
                for perm in exts_with_a:
                    checked_schedules += 1
                    cand_pos = perm.index(cand_op)
                    frontier = dict(pre_states)
                    for step_idx, step_op in enumerate(perm):
                        next_frontier = {}
                        for st in frontier.values():
                            if not st.viable or step_op.action not in st.safe_mutations:
                                if step_op.op_id == candidate_op_id:
                                    return False, None, "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE"
                                if step_idx > cand_pos:
                                    return False, None, "UNSAFE_REVERSE_INTERLEAVING_AFTER_CANDIDATE"
                                return False, None, "UNSAFE_INTERMEDIATE_STATE"

                            post_id = st.transition_map.get(step_op.action)
                            if post_id is not None:
                                if post_id not in worlds_by_id or not worlds_by_id[post_id].viable:
                                    return False, None, "UNSAFE_POST_STATE_EXITS_VIABILITY"
                                next_frontier[post_id] = worlds_by_id[post_id]
                            else:
                                next_frontier[st.world_id] = st

                            for part_id in step_op.partial_effect_worlds:
                                if part_id not in worlds_by_id or not worlds_by_id[part_id].viable:
                                    return False, None, "UNSAFE_PARTIAL_EFFECT_STATE"
                                next_frontier[part_id] = worlds_by_id[part_id]

                        frontier = next_frontier

        # Check pairwise commutativity explicitly on pairs not ordered by predecessor_op_ids
        pairwise_commutes = True
        for st in pre_states.values():
            for u_op in concurrent_ops:
                ordered_pair = (
                    candidate_op_id in u_op.predecessor_op_ids
                    or u_op.op_id in candidate_predecessor_op_ids
                )
                if ordered_pair:
                    continue
                s_a_id = st.transition_map.get(candidate_action, st.world_id)
                s_u_id = st.transition_map.get(u_op.action, st.world_id)
                s_a = worlds_by_id.get(s_a_id)
                s_u = worlds_by_id.get(s_u_id)
                if s_a is not None and s_u is not None:
                    ua_id = s_a.transition_map.get(u_op.action, s_a.world_id)
                    au_id = s_u.transition_map.get(candidate_action, s_u.world_id)
                    if ua_id != au_id:
                        return False, None, "NONCOMMUTATIVE_UNSERIALIZED_INTERLEAVING"

        base_ids = tuple(sorted(w.world_id for w in base_worlds))
        conc_ids = tuple(op.op_id for op in concurrent_ops)
        ser_ids = tuple(op.op_id for op in serialized_ops)
        digest = cls._compute_digest(
            candidate_action,
            domain_closure,
            base_ids,
            conc_ids,
            ser_ids,
            checked_schedules,
            pairwise_commutes,
            checked_absent_candidate_schedules=True,
            physical_refinement_mode="SERIALIZABLE_ATOMIC",
            admission_revisions=rev_tuple,
        )
        cert = CompatibilityCertificate(
            cert_id=f"compat_{digest}",
            candidate_action=candidate_action,
            domain_closure=domain_closure,
            base_world_ids=base_ids,
            concurrent_op_ids=conc_ids,
            serialized_predecessor_ids=ser_ids,
            checked_schedule_count=checked_schedules,
            pairwise_commutativity_ok=pairwise_commutes,
            all_intermediate_states_safe=True,
            partial_effect_safe=True,
            aggregate_exposure_ok=True,
            supported_class="BOUNDED_FINITE_STATE",
            verifier_digest=digest,
            checked_absent_candidate_schedules=True,
            physical_refinement_mode="SERIALIZABLE_ATOMIC",
            admission_revisions=rev_tuple,
        )
        return True, cert, "ADMIT_COMPOUND_SAFE"


class PlausibleWorldGate:
    """
    Enforces Theorem 4 (f-Fault-Tolerant Plausible-World Shielding under Bounded Computation)
    and Theorem 5 (Compositional Safety under Concurrent Mutations, Versioned Snapshots,
    Constraint-Domain Closure, and Mode C / Mode S Verification).
    """

    def __init__(
        self,
        candidate_worlds: Sequence[FaultWorld],
        safe_probes: Sequence[str],
        exposure_caps: Optional[Dict[str, int]] = None,
        dependency_graph: Optional[ConstraintDependencyGraph] = None,
    ) -> None:
        self.candidate_worlds: List[FaultWorld] = list(candidate_worlds)
        self.worlds_by_id: Dict[str, FaultWorld] = {w.world_id: w for w in candidate_worlds}
        self.safe_probes: List[str] = list(safe_probes)
        self.exposure_caps: Dict[str, int] = dict(exposure_caps or {})
        self.dependency_graph = dependency_graph
        self.last_compatibility_certificate: Optional[CompatibilityCertificate] = None

    def compute_fault_tolerant_belief_set(
        self,
        refutations_by_world: Dict[str, Sequence[SignedJustification]],
        assumptions_by_bit: Dict[int, PrimitiveAssumption],
        fault_budget_f: int = 2,
        budget_exceeded: bool = False,
        verified_Done_worlds: Optional[Set[str]] = None,
        max_packing_expansions: int = 10000,
        expected_snapshot_version: Optional[int] = None,
        require_consistent_observation_version: bool = False,
        expected_epoch: Optional[int] = None,
        expected_resource_id: Optional[str] = None,
        expected_manifest_hash: Optional[str] = None,
    ) -> List[FaultWorld]:
        """
        Construct the conservative f-fault-tolerant plausible belief set hat{B}_tau^{(f)}:
          - Starts from the complete candidate universe S_cand (conservative overapproximation).
          - A world s is pruned ONLY if exact set packing verifies >= f + 1 disjoint refutations
            WITHOUT hitting ATMS or set-packing budget exhaustion.
          - Revision 6 + Revision 8 P1.3 (Assumption 3 enforcement): when
            `require_consistent_observation_version=True` or any `expected_*` anchor is set,
            enforces BOTH intra-proof and cross-proof snapshot consistency via
            `filter_snapshot_consistent_witnesses` so proofs from different snapshots/epochs/
            manifests are NEVER packed together to prune a potentially true fault world.
        """
        required_disjoint_refutations = fault_budget_f + 1
        surviving: List[FaultWorld] = []
        check_obs_ver = (
            require_consistent_observation_version
            or (expected_snapshot_version is not None)
            or (expected_epoch is not None)
            or (expected_resource_id is not None)
            or (expected_manifest_hash is not None)
        )

        for w in self.candidate_worlds:
            if budget_exceeded and (verified_Done_worlds is None or w.world_id not in verified_Done_worlds):
                surviving.append(w)
                continue

            refs = list(refutations_by_world.get(w.world_id, []))
            if check_obs_ver:
                refs, snap_inconsistent, _, _ = filter_snapshot_consistent_witnesses(
                    refs,
                    assumptions_by_bit,
                    expected_snapshot_version=expected_snapshot_version,
                    expected_epoch=expected_epoch,
                    expected_resource_id=expected_resource_id,
                    expected_manifest_hash=expected_manifest_hash,
                )
                if snap_inconsistent and not refs:
                    surviving.append(w)
                    continue
            packing_num, _, packing_truncated = compute_disjoint_justification_packing_bounded(
                refs, assumptions_by_bit, max_expansions=max_packing_expansions
            )
            if packing_truncated or packing_num < required_disjoint_refutations:
                surviving.append(w)

        return surviving

    def compute_belief_set(
        self,
        observed_consistent_propositions: Set[str],
        refuted_world_ids: Set[str],
        budget_exceeded: bool = False,
    ) -> List[FaultWorld]:
        if budget_exceeded:
            return [w for w in self.candidate_worlds if w.world_id not in refuted_world_ids]

        plausible: List[FaultWorld] = []
        for w in self.candidate_worlds:
            if w.world_id in refuted_world_ids:
                continue
            if observed_consistent_propositions.issubset(w.compatible_propositions):
                plausible.append(w)
        return plausible

    @staticmethod
    def pairwise_ambiguity_detected(plausible_worlds: Sequence[FaultWorld]) -> bool:
        for i in range(len(plausible_worlds)):
            for j in range(i + 1, len(plausible_worlds)):
                if plausible_worlds[i].safe_mutations.isdisjoint(plausible_worlds[j].safe_mutations):
                    return True
        return False

    @staticmethod
    def global_safe_mutations(plausible_worlds: Sequence[FaultWorld]) -> FrozenSet[str]:
        if not plausible_worlds:
            return frozenset()
        common = set(plausible_worlds[0].safe_mutations)
        for w in plausible_worlds[1:]:
            common &= set(w.safe_mutations)
        return frozenset(common)

    def advance_snapshot_states(
        self,
        plausible_worlds: Sequence[FaultWorld],
        committed_ops: Sequence[InFlightMutation],
        snapshot_context: Optional[SnapshotContext] = None,
    ) -> Tuple[bool, List[FaultWorld], str]:
        """
        P0-C Repaired Versioned-Snapshot Base-State Advancement:
          - Fail-closed unknown-order commit barrier: whenever `require_versioned_commits`
            is enabled (default True) and ANY committed op has `commit_version is None`,
            refuses to guess commit order and returns
            `(False, [], "BLOCKED_UNVERSIONED_COMMIT_RECONCILIATION_REQUIRED")`.
          - For versioned commits sorted in strictly ascending commit_version order:
              * commit_version <= v_min: ALREADY reflected in snapshot -> skip (no double-counting).
              * v_min < commit_version <= v_max: window overlap -> apply T_opt (skip or apply)
                chronologically BEFORE any strictly post-snapshot commit.
              * commit_version > v_max: strictly post-snapshot -> apply T_det once in commit order.
          - Any transition mapping to an unmodeled world ID fails closed (`UNMODELED_COMMIT_TRANSITION`).
        """
        ctx = snapshot_context
        deduped_commits: Dict[str, InFlightMutation] = {}
        for op in committed_ops:
            deduped_commits[op.op_id] = op

        require_versioned = True if ctx is None else ctx.require_versioned_commits
        if require_versioned and any(op.commit_version is None for op in deduped_commits.values()):
            return False, [], "BLOCKED_UNVERSIONED_COMMIT_RECONCILIATION_REQUIRED"

        ordered_commits = sorted(
            deduped_commits.values(),
            key=lambda c: (c.commit_version if c.commit_version is not None else 10 ** 12, c.sequence_number or 0, c.op_id),
        )

        # Revision 6 (Theorem 5 Part 2 repair): contiguous commit-version barrier
        if ctx is not None and ctx.require_contiguous_versions:
            v_min = ctx.effective_min_version
            v_max = ctx.effective_max_version
            present_versions = {
                c.commit_version for c in ordered_commits if c.commit_version is not None
            }
            v_head = max([v_max] + list(present_versions)) if present_versions else v_max
            for expected_v in range(v_min + 1, v_head + 1):
                if expected_v not in present_versions:
                    return False, [], "BLOCKED_COMMIT_VERSION_GAP_RECONCILIATION_REQUIRED"

        states: Dict[str, FaultWorld] = {w.world_id: w for w in plausible_worlds}
        for c_op in ordered_commits:
            if ctx is None:
                if c_op.commit_version is not None and c_op.commit_version <= 0:
                    continue
                next_states: Dict[str, FaultWorld] = {}
                for w in states.values():
                    post_id = w.transition_map.get(c_op.action)
                    if post_id is not None:
                        if post_id not in self.worlds_by_id:
                            return False, [], "UNMODELED_COMMIT_TRANSITION"
                        next_states[post_id] = self.worlds_by_id[post_id]
                    else:
                        next_states[w.world_id] = w
                states = next_states
                continue

            v_min = ctx.effective_min_version
            v_max = ctx.effective_max_version
            cv = c_op.commit_version

            if cv is not None and cv <= v_min:
                continue
            elif cv is not None and cv > v_max:
                next_states = {}
                for w in states.values():
                    post_id = w.transition_map.get(c_op.action)
                    if post_id is not None:
                        if post_id not in self.worlds_by_id:
                            return False, [], "UNMODELED_COMMIT_TRANSITION"
                        next_states[post_id] = self.worlds_by_id[post_id]
                    else:
                        next_states[w.world_id] = w
                states = next_states
            else:
                # Versioned window overlap (v_min < cv <= v_max) or explicitly permitted unversioned replay
                if cv is None and not ctx.require_versioned_commits:
                    next_states = {}
                    for w in states.values():
                        post_id = w.transition_map.get(c_op.action)
                        if post_id is not None:
                            if post_id not in self.worlds_by_id:
                                return False, [], "UNMODELED_COMMIT_TRANSITION"
                            next_states[post_id] = self.worlds_by_id[post_id]
                        else:
                            next_states[w.world_id] = w
                    states = next_states
                else:
                    next_states = dict(states)  # branch 1: already reflected in snapshot
                    for w in states.values():   # branch 2: not yet reflected in snapshot
                        post_id = w.transition_map.get(c_op.action)
                        if post_id is not None:
                            if post_id not in self.worlds_by_id:
                                return False, [], "UNMODELED_COMMIT_TRANSITION"
                            next_states[post_id] = self.worlds_by_id[post_id]
                    states = next_states

        return True, list(states.values()), "SNAPSHOT_ADVANCED"

    def compute_base_states_from_snapshot(
        self,
        plausible_worlds: Sequence[FaultWorld],
        committed_ops: Sequence[InFlightMutation],
        snapshot_context: Optional[SnapshotContext] = None,
    ) -> List[FaultWorld]:
        ok, states, reason = self.advance_snapshot_states(
            plausible_worlds, committed_ops, snapshot_context=snapshot_context
        )
        if not ok:
            raise ValueError(f"Snapshot advancement blocked: {reason}")
        return states

    def evaluate_concurrent_compound_safety_with_certificate(
        self,
        candidate_action: str,
        resource_group: str,
        exposure_cost: int,
        plausible_worlds: Sequence[FaultWorld],
        inflight_ops: Sequence[InFlightMutation],
        legacy_onesided_check: bool = False,
        snapshot_context: Optional[SnapshotContext] = None,
        direct_domain_costs: Optional[Dict[str, int]] = None,
        candidate_partial_effect_worlds: Tuple[str, ...] = (),
        candidate_predecessor_op_ids: Tuple[str, ...] = (),
        candidate_op_id: str = "__candidate__",
        physical_refinement_mode: str = "SERIALIZABLE_ATOMIC",
        candidate_phases: Sequence[InFlightMutation] = (),
        admission_revisions: Optional[Dict[str, int]] = None,
    ) -> Tuple[bool, str, Optional[CompatibilityCertificate]]:
        """
        Repaired Theorem 5 Gate (Revision 4 + Revision 5 P0.1 / P0.2 / P1.3):
          1. Multi-Resource Constraint Closure Dom*(a) + Cross-Root Exposure Conservation.
          2. Scopes commits and unresolved operations to Dom*(a) under the frame condition.
          3. Versioned Snapshot Base-State Advancement with fail-closed unknown-order commit barrier.
          4. Physical execution refinement check (R1 SERIALIZABLE_ATOMIC, R2 PHASE_DECOMPOSED,
             or fail-closed UNVERIFIED_ASYNC for overlapping operations).
          5. Mode S full predecessor-prefix invariant preservation + candidate invariant check, OR
             Mode C exhaustive CompatibilityCertificate over ALL subsets (including absent-a),
             bound to per-domain admission linearization revisions.
        """
        self.last_compatibility_certificate = None
        if not plausible_worlds:
            return False, "EMPTY_BELIEF_SET", None

        base_costs = dict(direct_domain_costs) if direct_domain_costs is not None else {resource_group: exposure_cost}
        if self.dependency_graph is not None:
            closed_costs = self.dependency_graph.closure_costs(base_costs)
        else:
            closed_costs = base_costs
        domain_closure = tuple(sorted(closed_costs.keys()))
        closure_set = set(domain_closure)

        # Scope to operations touching Dom*(a) (reflects Theorem 5 closed-domain frame condition)
        scoped_ops = [
            op for op in inflight_ops
            if not closure_set.isdisjoint(op.affected_domains or (op.resource_group,))
        ]

        # Check exposure budget across ALL domains in closure Dom*(a)
        for d, req_cost in closed_costs.items():
            if d in self.exposure_caps:
                current_exposure = sum(
                    op.domain_costs.get(d, op.exposure_cost)
                    for op in scoped_ops
                    if d in (op.affected_domains or (op.resource_group,))
                )
                if current_exposure + req_cost > self.exposure_caps[d]:
                    return False, "EXPOSURE_BUDGET_EXCEEDED", None

        # Ensure pairwise disjointness of op_ids between I_C and I_H union I_U
        committed_ids = {op.op_id for op in scoped_ops if op.lifecycle_state == "COMMITTED"}
        unresolved_ids = {op.op_id for op in scoped_ops if op.lifecycle_state != "COMMITTED"}
        if not committed_ids.isdisjoint(unresolved_ids):
            return False, "INVALID_DUPLICATE_COMMITTED_AND_UNKNOWN_OP", None

        committed_ops = [op for op in scoped_ops if op.lifecycle_state == "COMMITTED"]
        unresolved_ops = [op for op in scoped_ops if op.lifecycle_state != "COMMITTED"]

        ok_snap, base_worlds, snap_reason = self.advance_snapshot_states(
            plausible_worlds, committed_ops, snapshot_context=snapshot_context
        )
        if not ok_snap:
            return False, snap_reason, None

        if legacy_onesided_check:
            reachable_by_id: Dict[str, FaultWorld] = {w.world_id: w for w in base_worlds}
            for op in unresolved_ops:
                next_layer: Dict[str, FaultWorld] = dict(reachable_by_id)
                for w in reachable_by_id.values():
                    post_id = w.transition_map.get(op.action)
                    if post_id is not None and post_id in self.worlds_by_id:
                        next_layer[post_id] = self.worlds_by_id[post_id]
                    for part_id in op.partial_effect_worlds:
                        if part_id in self.worlds_by_id:
                            next_layer[part_id] = self.worlds_by_id[part_id]
                reachable_by_id = next_layer
            if any(not w.viable for w in reachable_by_id.values()):
                return False, "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE", None
            if candidate_action not in self.global_safe_mutations(list(reachable_by_id.values())):
                return False, "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE", None
            return True, "ADMIT_LEGACY_ONESIDED_UNSOUND", None

        unserialized_ops = [op for op in unresolved_ops if not op.serialized_before_new]
        if unserialized_ops:
            # Mode C MUST verify physical refinement, U_S prefixes, AND all subsets (including absent-a)
            ok_c, cert, reason_c = ModeCCompatibilityVerifier.verify(
                candidate_action=candidate_action,
                domain_closure=domain_closure,
                base_worlds=base_worlds,
                unresolved_ops=unresolved_ops,
                worlds_by_id=self.worlds_by_id,
                aggregate_exposure_ok=True,
                candidate_partial_effect_worlds=candidate_partial_effect_worlds,
                candidate_predecessor_op_ids=candidate_predecessor_op_ids,
                candidate_op_id=candidate_op_id,
                physical_refinement_mode=physical_refinement_mode,
                candidate_phases=candidate_phases,
                admission_revisions=admission_revisions,
            )
            self.last_compatibility_certificate = cert
            return ok_c, reason_c, cert

        # Mode S physical refinement check: if target is UNVERIFIED_ASYNC, multiple unordered
        # predecessors could physically overlap (write skew) unless totally ordered!
        if physical_refinement_mode not in ModeCCompatibilityVerifier.VALID_PHYSICAL_REFINEMENT_MODES:
            return False, "MODE_S_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED", None
        if physical_refinement_mode == "UNVERIFIED_ASYNC" and len(unresolved_ops) >= 2:
            ok_ord, exts = ModeCCompatibilityVerifier._enumerate_linear_extensions(unresolved_ops)
            if not ok_ord:
                return False, "CYCLIC_PREDECESSOR_ORDER", None
            if len(exts) > 1:
                return False, "MODE_S_REJECTED_PHYSICAL_REFINEMENT_UNVERIFIED", None

        # Mode S: First verify FULL inductive prefix sequential safety across all subsets &
        # linear extensions of unresolved predecessors (catches unsafe intermediate predecessor states!)
        ok_pre, reachable_by_id, pre_reason = ModeCCompatibilityVerifier.verify_prefix_schedule_safety(
            base_worlds, unresolved_ops, self.worlds_by_id
        )
        if not ok_pre:
            return False, pre_reason, None

        safe_across_all = self.global_safe_mutations(list(reachable_by_id.values()))
        if candidate_action not in safe_across_all:
            return False, "UNSAFE_UNDER_COMPOUND_INFLIGHT_STATE", None

        for w in reachable_by_id.values():
            post_a_id = w.transition_map.get(candidate_action)
            if post_a_id is not None:
                if post_a_id not in self.worlds_by_id or not self.worlds_by_id[post_a_id].viable:
                    return False, "UNSAFE_POST_STATE_EXITS_VIABILITY", None
            for part_id in candidate_partial_effect_worlds:
                if part_id not in self.worlds_by_id or not self.worlds_by_id[part_id].viable:
                    return False, "UNSAFE_PARTIAL_EFFECT_STATE", None

        return True, "ADMIT_COMPOUND_SAFE", None

    def evaluate_concurrent_compound_safety(
        self,
        candidate_action: str,
        resource_group: str,
        exposure_cost: int,
        plausible_worlds: Sequence[FaultWorld],
        inflight_ops: Sequence[InFlightMutation],
        legacy_onesided_check: bool = False,
        snapshot_context: Optional[SnapshotContext] = None,
        direct_domain_costs: Optional[Dict[str, int]] = None,
        candidate_partial_effect_worlds: Tuple[str, ...] = (),
        candidate_predecessor_op_ids: Tuple[str, ...] = (),
        candidate_op_id: str = "__candidate__",
        physical_refinement_mode: str = "SERIALIZABLE_ATOMIC",
        candidate_phases: Sequence[InFlightMutation] = (),
        admission_revisions: Optional[Dict[str, int]] = None,
    ) -> Tuple[bool, str]:
        ok, reason, _ = self.evaluate_concurrent_compound_safety_with_certificate(
            candidate_action=candidate_action,
            resource_group=resource_group,
            exposure_cost=exposure_cost,
            plausible_worlds=plausible_worlds,
            inflight_ops=inflight_ops,
            legacy_onesided_check=legacy_onesided_check,
            snapshot_context=snapshot_context,
            direct_domain_costs=direct_domain_costs,
            candidate_partial_effect_worlds=candidate_partial_effect_worlds,
            candidate_predecessor_op_ids=candidate_predecessor_op_ids,
            candidate_op_id=candidate_op_id,
            physical_refinement_mode=physical_refinement_mode,
            candidate_phases=candidate_phases,
            admission_revisions=admission_revisions,
        )
        return ok, reason

    def select_informative_probe(self, plausible_worlds: Sequence[FaultWorld]) -> Optional[str]:
        if len(plausible_worlds) <= 1:
            return None

        best_probe: Optional[str] = None
        best_diversity = 1

        for probe in self.safe_probes:
            outcomes = {w.probe_outcomes.get(probe, "obs:none") for w in plausible_worlds}
            if len(outcomes) > best_diversity:
                best_diversity = len(outcomes)
                best_probe = probe

        return best_probe


# =============================================================================
# 6. Resource Enforcement Tiers (E0, E1, E1+, E2) & SQLite Protected Target
# =============================================================================

ENFORCEMENT_TIER_SPECS: Dict[str, Dict[str, str]] = {
    "E0_PROXY_ONLY": {
        "guaranteed": "Pre-dispatch admission epoch check at kf-proxy only.",
        "not_guaranteed": "Post-admission revocation ordering, target freshness, or non-cancellable exclusion.",
        "linearization_point": "None at target (admission check only at t_adm).",
        "required_target_capabilities": "None.",
        "failure_behavior": "Network delay or partition leaks stale mutations up to TTL.",
        "residual_freshness_risk": "> 0 over [t_adm, t_eff].",
    },
    "E1_ASYNC_FENCE": {
        "guaranteed": "Rejects stale requests arriving AFTER local target receives Fence(r, v).",
        "not_guaranteed": "Exclusion against in-flight requests overtaking delayed Fence(r, v) messages.",
        "linearization_point": "None globally (local v_fence[r] only).",
        "required_target_capabilities": "Local epoch register updated asynchronously.",
        "failure_behavior": "Partition or fence delay leaks stale commits.",
        "residual_freshness_risk": "> 0 over Fence message propagation delay.",
    },
    "E1_PLUS_READ_LOCK": {
        "guaranteed": "Rejects certificates already revoked prior to synchronous ReadIndex t_read.",
        "not_guaranteed": "Zero-race exclusion when revocation commits in (t_read, t_eff).",
        "linearization_point": "Read check at t_read != effect at t_eff (split window).",
        "required_target_capabilities": "Target-local lock + synchronous control-plane ReadIndex.",
        "failure_behavior": "Control-plane revocation during (t_read, t_eff) leaks stale commit.",
        "residual_freshness_risk": "> 0 over post-read commit window (t_read, t_eff).",
    },
    "E1_PLUS_QUIESCENCE_BARRIER": {
        "guaranteed": (
            "Zero post-FenceAck stale commits once synchronous FenceAck(r, v, C_{<=v}) completes "
            "with no orphaned non-cancellable op; blocks conflicting admissions via UNKNOWN I_U(d) "
            "if target stalls/partitions before FenceAck or non-cancellable op cannot be lease-fenced."
        ),
        "not_guaranteed": (
            "Does NOT guarantee control-plane lease expiry alone stops an already-initiated "
            "non-cancellable external operation unless the external resource itself enforces lease expiry; "
            "does NOT abort in-progress local syscalls after t_rev_init."
        ),
        "linearization_point": (
            "t_fenced(v) only upon synchronous FenceAck (or lease expiry + target-enforced lease exclusion); "
            "otherwise remains un-fenced (indefinite safety block)."
        ),
        "required_target_capabilities": (
            "Target lock_r + RegisterPrepared CAS + synchronous FenceBarrier/FenceAck (plus target-side "
            "lease/fence check if post-expiry exclusion on partition is claimed)."
        ),
        "failure_behavior": (
            "Crash/partition/process-suspension during non-cancellable op retains op in UNKNOWN (I_U) "
            "and blocks conflicting admissions until FenceAck or definitive external barrier/reconciliation."
        ),
        "residual_freshness_risk": (
            "> 0 relative to t_rev_init; 0 post-t_fenced ONLY when FenceAck or target-enforced lease "
            "barrier is achieved (never from control-plane lease expiry alone)."
        ),
    },
    "E2_SHARED_ATOMIC": {
        "guaranteed": (
            "Atomic single-transaction epoch validation, sequence-frontier ordering, and exposure cap "
            "enforcement at the physical commit point t_tx."
        ),
        "not_guaranteed": "Protection against unmediated out-of-band external writers bypassing the boundary.",
        "linearization_point": "Single indivisible transaction / target-native CAS at t_tx.",
        "required_target_capabilities": "Shared atomic transaction (etcd/SQLite WAL) or target-native CAS register.",
        "failure_behavior": "Aborts stale certificates deterministically at t_tx with zero side effect.",
        "residual_freshness_risk": "0 (conditional on complete writer mediation).",
    },
}


@dataclass(frozen=True)
class EpistemicLeaseCertificate:
    action: str
    support_mask: int
    admission_epoch: int
    packing_number: int
    plausible_worlds: Tuple[str, ...]
    signature: str
    auth_epoch: int = 0
    auth_valid_until: Optional[int] = None
    resource_id: str = ""
    manifest_hash: str = ""
    snapshot_version: Optional[int] = None
    proof_manifest_digest: str = ""
    admission_revisions: Tuple[Tuple[str, int], ...] = ()

    def canonical_payload_bytes(self) -> bytes:
        payload = {
            "action": self.action,
            "support_mask": self.support_mask,
            "admission_epoch": self.admission_epoch,
            "packing_number": self.packing_number,
            "plausible_worlds": list(self.plausible_worlds),
            "auth_epoch": self.auth_epoch,
            "auth_valid_until": self.auth_valid_until,
            "resource_id": self.resource_id,
            "manifest_hash": self.manifest_hash,
            "snapshot_version": self.snapshot_version,
            "proof_manifest_digest": self.proof_manifest_digest,
            "admission_revisions": [[d, r] for d, r in self.admission_revisions],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def compute_signature(self, signing_key: bytes) -> str:
        return hmac.new(signing_key, self.canonical_payload_bytes(), hashlib.sha256).hexdigest()[:32]


@dataclass
class TargetExecutionReceipt:
    committed: bool
    status: str
    target_epoch_at_check: int
    action: str
    enforcement_tier: str = "E2_SHARED_ATOMIC"
    commit_version: Optional[int] = None


class TargetResourceBoundary:
    """
    Models KedgeFlow resource enforcement tiers (E0, E1, E1+-read, E1+-quiescence, E2).
    Task 2 Repair: Explicitly distinguishes:
      - Control-plane lease expiry (t > t_lease + Delta_skew)
      - Target acknowledgment (FenceAck)
      - Guaranteed absence of future external effects (requires target-enforced lease or FenceAck)
      - Completion of an already-initiated non-cancellable external operation
    """

    def __init__(
        self,
        initial_epoch: int = 1,
        enforce_target_fence: bool = True,
        tier: str = "E2_SHARED_ATOMIC",
        target_enforces_lease_at_commit: bool = False,
        clock_skew_bound: int = 0,
    ) -> None:
        self.authoritative_epoch: int = initial_epoch
        self.local_target_fence: int = initial_epoch
        self.fenced_linearized_epoch: int = initial_epoch
        self.authoritative_auth_epoch: int = 0
        self.local_auth_epoch: int = 0
        self.prepared_slot: Optional[Tuple[str, int]] = None
        self.noncancellable_inflight: Optional[Tuple[str, str, int, int]] = None  # (op_id, action, adm_epoch, lease_expiry)
        self.fence_completion_declared: bool = True
        self.enforce_target_fence: bool = enforce_target_fence
        self.tier: str = tier
        self.target_enforces_lease_at_commit: bool = target_enforces_lease_at_commit
        self.clock_skew_bound: int = clock_skew_bound
        self.execution_log: List[TargetExecutionReceipt] = []
        self.committed_op_ids: Dict[str, TargetExecutionReceipt] = {}
        self.unknown_prepared_ops: List[str] = []
        self.target_commit_seq: int = 0

    def advance_invalidation_fence(self, new_min_epoch: int, fence_message_delayed: bool = False) -> None:
        if new_min_epoch > self.authoritative_epoch:
            self.authoritative_epoch = new_min_epoch
        if not fence_message_delayed and new_min_epoch > self.local_target_fence:
            self.local_target_fence = new_min_epoch
            if self.prepared_slot is None and self.noncancellable_inflight is None:
                self.fenced_linearized_epoch = max(self.fenced_linearized_epoch, new_min_epoch)
                self.fence_completion_declared = True

    def revoke_authorization_epoch(self, new_min_auth_epoch: int, fence_message_delayed: bool = False) -> None:
        """
        P1.1: Advance authorization/policy revocation epoch independently of telemetry evidence epoch.
        An action that remains physically safe and epistemically supported can still be revoked
        when approval, incident freeze, or IAM/RBAC delegation changes post-admission.
        """
        if new_min_auth_epoch > self.authoritative_auth_epoch:
            self.authoritative_auth_epoch = new_min_auth_epoch
        if not fence_message_delayed and new_min_auth_epoch > self.local_auth_epoch:
            self.local_auth_epoch = new_min_auth_epoch

    def deliver_delayed_fence(self) -> List[str]:
        if self.authoritative_epoch > self.local_target_fence:
            self.local_target_fence = self.authoritative_epoch
        if self.authoritative_auth_epoch > self.local_auth_epoch:
            self.local_auth_epoch = self.authoritative_auth_epoch
        if self.prepared_slot is None and self.noncancellable_inflight is None and self.authoritative_epoch > self.fenced_linearized_epoch:
            self.fenced_linearized_epoch = self.authoritative_epoch
            self.fence_completion_declared = True
        return [r.action for r in self.execution_log if r.committed]

    def start_noncancellable_prepared_op(
        self,
        op_id: str,
        action: str,
        admission_epoch: int,
        start_time: int,
        lease_expiry: int,
    ) -> Tuple[bool, str]:
        """
        Task 2 Counterexample Setup:
        Target registers prepared intent (lease_expiry = 10) and initiates a non-cancellable
        external effect at start_time = 9 before stalling or partitioning.
        """
        effective_fence = max(self.local_target_fence, self.authoritative_epoch)
        if admission_epoch < effective_fence:
            return False, "REJECTED_STALE_EPOCH"
        if start_time > lease_expiry - self.clock_skew_bound:
            return False, "REJECTED_LEASE_EXPIRED_BEFORE_START"
        self.prepared_slot = (action, admission_epoch)
        self.noncancellable_inflight = (op_id, action, admission_epoch, lease_expiry)
        self.fence_completion_declared = False
        return True, "NONCANCELLABLE_OP_STARTED"

    def initiate_revocation_at_time(self, new_min_epoch: int, now_time: int) -> Tuple[bool, str]:
        """
        Task 2 Repair:
        Revocation authority advances authoritative_epoch and checks if fence completion t_fenced(v)
        can safely be declared at `now_time` (e.g., t=10 after lease expiry):
          - If an unacknowledged non-cancellable op is active and `not target_enforces_lease_at_commit`,
            control-plane lease expiry does NOT prove post-expiry exclusion!
            Therefore `fence_complete = False`, `op_id` stays in `unknown_prepared_ops` (I_U),
            and conflicting mutations are blocked indefinitely until FenceAck or definitive barrier!
          - Only if `target_enforces_lease_at_commit` and `now_time > lease_expiry + clock_skew_bound`
            can post-expiry exclusion be declared safely (while still retaining UNKNOWN outcome!).
        """
        if new_min_epoch > self.authoritative_epoch:
            self.authoritative_epoch = new_min_epoch

        if self.noncancellable_inflight is None and self.prepared_slot is None:
            self.fenced_linearized_epoch = max(self.fenced_linearized_epoch, new_min_epoch)
            self.fence_completion_declared = True
            return True, "FENCE_COMPLETE_QUIESCENT"

        if self.noncancellable_inflight is not None:
            op_id, _, _, lease_expiry = self.noncancellable_inflight
            if op_id not in self.unknown_prepared_ops:
                self.unknown_prepared_ops.append(op_id)
            if now_time <= lease_expiry + self.clock_skew_bound:
                self.fence_completion_declared = False
                return False, "FENCE_PENDING_ACTIVE_LEASE"
            # Control-plane lease has expired at `now_time > lease_expiry + clock_skew_bound`:
            if self.target_enforces_lease_at_commit:
                self.fenced_linearized_epoch = max(self.fenced_linearized_epoch, new_min_epoch)
                self.fence_completion_declared = True
                return True, "FENCE_COMPLETE_TARGET_LEASE_ENFORCED_OUTCOME_UNKNOWN"
            # Non-lease-fenced target: CANNOT declare fence complete solely on control-plane lease expiry!
            self.fence_completion_declared = False
            return False, "FENCE_BLOCKED_NONCANCELLABLE_NO_TARGET_LEASE"

        self.fence_completion_declared = False
        return False, "FENCE_PENDING_PREPARED"

    def complete_noncancellable_op(self, op_id: str, completion_time: int) -> TargetExecutionReceipt:
        """
        Models late completion of the non-cancellable external operation at e.g. t=11
        (after control-plane lease expired at t=10).
        """
        if self.noncancellable_inflight is None or self.noncancellable_inflight[0] != op_id:
            return TargetExecutionReceipt(False, "NO_SUCH_NONCANCELLABLE_OP", self.authoritative_epoch, "", self.tier)

        _, action, adm_epoch, lease_expiry = self.noncancellable_inflight
        if self.target_enforces_lease_at_commit and completion_time > lease_expiry - self.clock_skew_bound:
            # Target itself enforces lease expiry -> late completion physically rejected!
            self.noncancellable_inflight = None
            self.prepared_slot = None
            if op_id in self.unknown_prepared_ops:
                self.unknown_prepared_ops.remove(op_id)
            receipt = TargetExecutionReceipt(
                False, "REJECTED_EXPIRED_LEASE_AT_TARGET", self.authoritative_epoch, action, self.tier
            )
            self.execution_log.append(receipt)
            return receipt

        # Non-lease-fenced target: external effect completes at t=11 after CP lease expiry!
        self.target_commit_seq += 1
        self.noncancellable_inflight = None
        self.prepared_slot = None
        if op_id in self.unknown_prepared_ops:
            self.unknown_prepared_ops.remove(op_id)
        receipt = TargetExecutionReceipt(
            True,
            "COMMITTED_LATE_AFTER_CP_LEASE_EXPIRY",
            self.authoritative_epoch,
            action,
            self.tier,
            commit_version=self.target_commit_seq,
        )
        self.committed_op_ids[op_id] = receipt
        self.execution_log.append(receipt)
        # Once target completes and sends FenceAck, fence can now advance safely:
        if self.authoritative_epoch > self.local_target_fence:
            self.local_target_fence = self.authoritative_epoch
            self.fenced_linearized_epoch = self.authoritative_epoch
            self.fence_completion_declared = True
        return receipt

    def apply_definitive_external_barrier(self, op_id: str, no_effect_proven: bool) -> Tuple[bool, str]:
        """
        Resolves an indefinite block when an out-of-band definitive barrier (e.g. STONITH,
        IAM credential revocation propagation, or storage fencing) proves outcome.
        """
        if self.noncancellable_inflight is not None and self.noncancellable_inflight[0] == op_id:
            self.noncancellable_inflight = None
            self.prepared_slot = None
        if op_id in self.unknown_prepared_ops:
            self.unknown_prepared_ops.remove(op_id)
        if not self.unknown_prepared_ops:
            self.fenced_linearized_epoch = max(self.fenced_linearized_epoch, self.authoritative_epoch)
            self.fence_completion_declared = True
        return True, "RESOLVED_BY_DEFINITIVE_BARRIER_NO_EFFECT" if no_effect_proven else "RESOLVED_BY_DEFINITIVE_BARRIER_COMMITTED"

    def can_admit_conflicting_mutation(self) -> Tuple[bool, str]:
        if self.unknown_prepared_ops or self.noncancellable_inflight is not None or not self.fence_completion_declared:
            return False, "BLOCKED_BY_UNKNOWN_NONCANCELLABLE_OR_UNFENCED_OP"
        return True, "CLEAR_TO_ADMIT"

    def execute_e1_plus_read_index_with_post_read_race(
        self,
        cert: EpistemicLeaseCertificate,
        revocation_epoch_during_post_read_window: Optional[int] = None,
    ) -> TargetExecutionReceipt:
        epoch_at_read = self.authoritative_epoch
        if cert.admission_epoch < epoch_at_read:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_STALE_AT_READ_INDEX", epoch_at_read, cert.action, "E1_PLUS_READ_LOCK"
            )
            self.execution_log.append(receipt)
            return receipt

        if (
            revocation_epoch_during_post_read_window is not None
            and revocation_epoch_during_post_read_window > self.authoritative_epoch
        ):
            self.authoritative_epoch = revocation_epoch_during_post_read_window

        self.target_commit_seq += 1
        receipt = TargetExecutionReceipt(
            True, "COMMITTED_AFTER_POST_READ_REVOCATION", epoch_at_read, cert.action, "E1_PLUS_READ_LOCK",
            commit_version=self.target_commit_seq,
        )
        self.execution_log.append(receipt)
        return receipt

    def execute_quiescence_handshake(
        self,
        cert: EpistemicLeaseCertificate,
        op_id: str = "op_default",
        crash_while_prepared: bool = False,
        concurrent_revocation_epoch: Optional[int] = None,
        concurrent_auth_revocation_epoch: Optional[int] = None,
        now_time: Optional[int] = None,
    ) -> Tuple[TargetExecutionReceipt, bool]:
        if op_id in self.committed_op_ids:
            return self.committed_op_ids[op_id], False

        effective_fence = max(self.local_target_fence, self.authoritative_epoch)
        if cert.admission_epoch < effective_fence:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_STALE_EPOCH", effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER"
            )
            self.execution_log.append(receipt)
            return receipt, False

        effective_auth = max(self.local_auth_epoch, self.authoritative_auth_epoch)
        if cert.auth_epoch < effective_auth:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_AUTHORIZATION_REVOKED", effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER"
            )
            self.execution_log.append(receipt)
            return receipt, False

        if cert.auth_valid_until is not None:
            if now_time is None:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE", effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER"
                )
                self.execution_log.append(receipt)
                return receipt, False
            if now_time > cert.auth_valid_until:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_EXPIRED", effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER"
                )
                self.execution_log.append(receipt)
                return receipt, False

        self.prepared_slot = (cert.action, cert.admission_epoch)
        revocation_blocked_by_lock = False
        auth_revoked_while_prepared = False

        if concurrent_revocation_epoch is not None and concurrent_revocation_epoch > self.authoritative_epoch:
            self.authoritative_epoch = concurrent_revocation_epoch
            revocation_blocked_by_lock = True

        # Revision 7 P0.1: Under Tier E1+-quiescence, an authorization revocation
        # initiated after the target entered prepared_slot blocks on lock_r until
        # FenceAck; the already-prepared op can still commit in [t_rev_init, t_fenced),
        # whereas post-FenceAck attempts are excluded deterministically.
        if (
            concurrent_auth_revocation_epoch is not None
            and concurrent_auth_revocation_epoch > self.authoritative_auth_epoch
        ):
            self.authoritative_auth_epoch = concurrent_auth_revocation_epoch
            revocation_blocked_by_lock = True
            auth_revoked_while_prepared = True

        if crash_while_prepared:
            if op_id not in self.unknown_prepared_ops:
                self.unknown_prepared_ops.append(op_id)
            self.prepared_slot = None
            # When non-lease-fenced target crashes while prepared, fence cannot be declared safely!
            if not self.target_enforces_lease_at_commit:
                self.fence_completion_declared = False
            receipt = TargetExecutionReceipt(
                False, "UNKNOWN_CRASHED_IN_PREPARED", effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER"
            )
            self.execution_log.append(receipt)
            return receipt, revocation_blocked_by_lock

        self.target_commit_seq += 1
        commit_reason = (
            "COMMITTED_PRE_FENCE_ACK_AUTH_REVOKED"
            if auth_revoked_while_prepared
            else "COMMITTED"
        )
        receipt = TargetExecutionReceipt(
            True, commit_reason, effective_fence, cert.action, "E1_PLUS_QUIESCENCE_BARRIER",
            commit_version=self.target_commit_seq,
        )
        self.execution_log.append(receipt)
        self.committed_op_ids[op_id] = receipt

        self.prepared_slot = None
        if self.authoritative_epoch > self.local_target_fence:
            self.local_target_fence = self.authoritative_epoch
            self.fenced_linearized_epoch = self.authoritative_epoch
            self.fence_completion_declared = True
        if self.authoritative_auth_epoch > self.local_auth_epoch:
            self.local_auth_epoch = self.authoritative_auth_epoch
            self.fence_completion_declared = True

        return receipt, revocation_blocked_by_lock

    def execute_with_certificate(
        self,
        cert: EpistemicLeaseCertificate,
        now_time: Optional[int] = None,
    ) -> TargetExecutionReceipt:
        if not self.enforce_target_fence or self.tier == "E0_PROXY_ONLY":
            self.target_commit_seq += 1
            receipt = TargetExecutionReceipt(
                True, "COMMITTED", self.local_target_fence, cert.action, "E0_PROXY_ONLY",
                commit_version=self.target_commit_seq,
            )
            self.execution_log.append(receipt)
            return receipt

        if self.tier == "E1_ASYNC_FENCE":
            if cert.admission_epoch < self.local_target_fence:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_STALE_EPOCH", self.local_target_fence, cert.action, self.tier
                )
            elif cert.auth_epoch < self.local_auth_epoch:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_REVOKED", self.local_target_fence, cert.action, self.tier
                )
            elif cert.auth_valid_until is not None and now_time is None:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE", self.local_target_fence, cert.action, self.tier
                )
            elif cert.auth_valid_until is not None and now_time > cert.auth_valid_until:
                receipt = TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_EXPIRED", self.local_target_fence, cert.action, self.tier
                )
            else:
                self.target_commit_seq += 1
                receipt = TargetExecutionReceipt(
                    True, "COMMITTED", self.local_target_fence, cert.action, self.tier,
                    commit_version=self.target_commit_seq,
                )
            self.execution_log.append(receipt)
            return receipt

        if self.tier == "E1_PLUS_READ_LOCK":
            return self.execute_e1_plus_read_index_with_post_read_race(cert)

        if cert.admission_epoch < self.authoritative_epoch:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_STALE_EPOCH", self.authoritative_epoch, cert.action, self.tier
            )
        elif cert.auth_epoch < self.authoritative_auth_epoch:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_AUTHORIZATION_REVOKED", self.authoritative_epoch, cert.action, self.tier
            )
        elif cert.auth_valid_until is not None and now_time is None:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE", self.authoritative_epoch, cert.action, self.tier
            )
        elif cert.auth_valid_until is not None and now_time > cert.auth_valid_until:
            receipt = TargetExecutionReceipt(
                False, "REJECTED_AUTHORIZATION_EXPIRED", self.authoritative_epoch, cert.action, self.tier
            )
        else:
            self.target_commit_seq += 1
            receipt = TargetExecutionReceipt(
                True, "COMMITTED", self.authoritative_epoch, cert.action, self.tier,
                commit_version=self.target_commit_seq,
            )
        self.execution_log.append(receipt)
        return receipt


class SQLiteProtectedResourceBoundary:
    """
    Real transactional SQLite WAL protected resource adapter demonstrating:
      - Tier E2 atomic epoch fencing
      - Task 3 Mode S sequence-frontier & multi-predecessor set Pred(a) enforcement
      - Idempotent op_id deduplication and restart commit-version reconciliation
      - Cross-root cluster drain exposure cap enforcement
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        initial_epoch: int = 10,
        max_drained_nodes: int = 1,
        signing_key: Optional[bytes] = None,
        expected_manifest_hash: Optional[str] = None,
    ) -> None:
        if type(max_drained_nodes) is not int or max_drained_nodes < 1:
            raise ValueError("drain cap must be a positive integer")
        self.db_path = db_path
        self.max_drained_nodes = max_drained_nodes
        self.signing_key = signing_key
        self.expected_manifest_hash = expected_manifest_hash
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS control_epochs (resource_id TEXT PRIMARY KEY, min_valid_epoch INTEGER NOT NULL, last_committed_seq INTEGER NOT NULL DEFAULT 0, min_auth_epoch INTEGER NOT NULL DEFAULT 0);"
        )
        if "min_auth_epoch" not in {
            row[1] for row in self.conn.execute("PRAGMA table_info(control_epochs)")
        }:
            try:
                self.conn.execute(
                    "ALTER TABLE control_epochs ADD COLUMN min_auth_epoch INTEGER NOT NULL DEFAULT 0;"
                )
            except sqlite3.OperationalError:
                if "min_auth_epoch" not in {
                    row[1] for row in self.conn.execute("PRAGMA table_info(control_epochs)")
                }:
                    raise
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cluster_nodes (node_id TEXT PRIMARY KEY, status TEXT NOT NULL, committed_epoch INTEGER NOT NULL);"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS committed_ops (op_id TEXT PRIMARY KEY, action TEXT NOT NULL, commit_version INTEGER NOT NULL, status TEXT NOT NULL, identity_json TEXT);"
        )
        if "identity_json" not in {
            row[1] for row in self.conn.execute("PRAGMA table_info(committed_ops)")
        }:
            try:
                self.conn.execute("ALTER TABLE committed_ops ADD COLUMN identity_json TEXT;")
            except sqlite3.OperationalError:
                if "identity_json" not in {
                    row[1] for row in self.conn.execute("PRAGMA table_info(committed_ops)")
                }:
                    raise
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS resource_limits (resource_id TEXT PRIMARY KEY, max_drained_nodes INTEGER NOT NULL CHECK(max_drained_nodes > 0));"
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO resource_limits (resource_id, max_drained_nodes) VALUES ('db_cluster', ?);",
            (max_drained_nodes,),
        )
        stored_cap = self.conn.execute(
            "SELECT max_drained_nodes FROM resource_limits WHERE resource_id = 'db_cluster';"
        ).fetchone()[0]
        if stored_cap != max_drained_nodes:
            self.conn.close()
            raise ValueError("drain cap differs from durable resource configuration")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, epoch INTEGER, outcome TEXT);"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS resource_predicates (predicate_id TEXT PRIMARY KEY, holds INTEGER NOT NULL, updated_seq INTEGER NOT NULL DEFAULT 0);"
        )
        existing = self.conn.execute(
            "SELECT min_valid_epoch FROM control_epochs WHERE resource_id = 'db_cluster';"
        ).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO control_epochs (resource_id, min_valid_epoch, last_committed_seq) VALUES ('db_cluster', ?, 0);",
                (initial_epoch,),
            )
            for nid in ("node_1", "node_2", "node_3"):
                self.conn.execute(
                    "INSERT OR REPLACE INTO cluster_nodes (node_id, status, committed_epoch) VALUES (?, 'ACTIVE', ?);",
                    (nid, initial_epoch),
                )

    def reopen(self) -> None:
        """Simulate target/gateway restart from durable SQLite WAL storage."""
        self.conn.close()
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")

    def durable_committed_versions(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT op_id, commit_version FROM committed_ops WHERE status = 'COMMITTED';"
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def durable_fenced_no_effect_ops(self) -> Set[str]:
        rows = self.conn.execute(
            "SELECT op_id FROM committed_ops WHERE status = 'FENCED_NO_EFFECT';"
        ).fetchall()
        return {str(r[0]) for r in rows}

    def current_seq(self) -> int:
        row = self.conn.execute(
            "SELECT last_committed_seq FROM control_epochs WHERE resource_id = 'db_cluster';"
        ).fetchone()
        return int(row[0])

    def current_min_epoch(self) -> int:
        row = self.conn.execute(
            "SELECT min_valid_epoch FROM control_epochs WHERE resource_id = 'db_cluster';"
        ).fetchone()
        return int(row[0])

    def current_auth_epoch(self) -> int:
        row = self.conn.execute(
            "SELECT min_auth_epoch FROM control_epochs WHERE resource_id = 'db_cluster';"
        ).fetchone()
        return int(row[0])

    def revoke_epoch_atomically(self, new_min_epoch: int) -> None:
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            self.conn.execute(
                "UPDATE control_epochs SET min_valid_epoch = MAX(min_valid_epoch, ?) WHERE resource_id = 'db_cluster';",
                (new_min_epoch,),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def revoke_authorization_atomically(self, new_min_auth_epoch: int) -> None:
        """P1.1/P1.2: Durable atomic authorization/policy revocation surviving crash/restart."""
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            self.conn.execute(
                "UPDATE control_epochs SET min_auth_epoch = MAX(min_auth_epoch, ?) WHERE resource_id = 'db_cluster';",
                (new_min_auth_epoch,),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def record_fenced_no_effect(self, op_id: str, action: str = "") -> bool:
        """
        Durably record an authoritative FENCED_NO_EFFECT tombstone.
        Returns True iff op_id is recorded as FENCED_NO_EFFECT; returns False if op_id
        had ALREADY COMMITTED (so callers never release an already-committed op from I_U).
        """
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            existing = self.conn.execute(
                "SELECT status FROM committed_ops WHERE op_id = ?;", (op_id,)
            ).fetchone()
            if existing is not None:
                self.conn.execute("COMMIT;")
                return str(existing[0]) == "FENCED_NO_EFFECT"
            row = self.conn.execute(
                "SELECT last_committed_seq FROM control_epochs WHERE resource_id = 'db_cluster';"
            ).fetchone()
            cur_seq = int(row[0])
            self.conn.execute(
                "INSERT INTO committed_ops (op_id, action, commit_version, status) VALUES (?, ?, ?, 'FENCED_NO_EFFECT');",
                (op_id, action, cur_seq),
            )
            self.conn.execute("COMMIT;")
            return True
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def set_live_predicate(self, predicate_id: str, holds: bool) -> None:
        """
        Revision 7 P0.2: Durably record live target-side predicate truth for actions
        whose semantics require CommitPreconditionValid(a, t_eff) rather than
        historical SnapshotPreconditionValid(a, v_snap).
        """
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            row = self.conn.execute(
                "SELECT last_committed_seq FROM control_epochs WHERE resource_id = 'db_cluster';"
            ).fetchone()
            cur_seq = int(row[0])
            self.conn.execute(
                "INSERT INTO resource_predicates (predicate_id, holds, updated_seq) VALUES (?, ?, ?) "
                "ON CONFLICT(predicate_id) DO UPDATE SET holds = excluded.holds, updated_seq = excluded.updated_seq;",
                (predicate_id, 1 if holds else 0, cur_seq),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def get_live_predicate(self, predicate_id: str) -> Optional[bool]:
        row = self.conn.execute(
            "SELECT holds FROM resource_predicates WHERE predicate_id = ?;",
            (predicate_id,),
        ).fetchone()
        return None if row is None else bool(row[0])

    def execute_fenced_mutation(
        self,
        cert: EpistemicLeaseCertificate,
        target_node: str = "node_1",
        expected_predecessor_seq: Optional[int] = None,
        op_id: Optional[str] = None,
        required_predecessor_op_ids: Sequence[str] = (),
        min_committed_seq: Optional[int] = None,
        now_time: Optional[int] = None,
        required_commit_predicates: Sequence[str] = (),
        commit_precondition_check: Optional[Callable[[sqlite3.Connection], bool]] = None,
    ) -> TargetExecutionReceipt:
        """
        Executes a Tier E2 atomic transaction checking:
          1. Idempotence deduplication via `op_id` + full operation identity binding
          2. Epistemic evidence fence: `cert.admission_epoch >= control_epochs.min_valid_epoch`
          3. Execution-time authorization freshness (P1.1 / Rev7 P0.1):
             `cert.auth_epoch >= control_epochs.min_auth_epoch` and
             `now_time <= cert.auth_valid_until` (when bounded)
          4. Mode S Execution-Order Enforcement via:
             - `expected_predecessor_seq` (strict chain),
             - `min_committed_seq` (sequence frontier), and/or
             - `required_predecessor_op_ids` (multi-predecessor frontier Pred(a), supporting
               predecessors with different sequence numbers or FENCED_NO_EFFECT settlement!)
          5. Optional Commit-Time Precondition Revalidation (Rev7 P0.2):
             ONLY enforced when `required_commit_predicates` or `commit_precondition_check`
             is supplied (actions with CommitPreconditionValid(a, t_eff) semantics).
             Historical-snapshot actions leave both unset and rely on SnapshotPreconditionValid(a, v_snap).
          6. Cross-Root Exposure Conservation (total DRAINED nodes <= max_drained_nodes)
        """
        cert_identity: Dict[str, object] = {
            "action": cert.action,
            "support_mask": cert.support_mask,
            "admission_epoch": cert.admission_epoch,
            "packing_number": cert.packing_number,
            "plausible_worlds": cert.plausible_worlds,
            "signature": cert.signature,
            "auth_epoch": cert.auth_epoch,
            "auth_valid_until": cert.auth_valid_until,
        }
        if cert.resource_id:
            cert_identity["resource_id"] = cert.resource_id
        if cert.manifest_hash:
            cert_identity["manifest_hash"] = cert.manifest_hash
        if cert.snapshot_version is not None:
            cert_identity["snapshot_version"] = cert.snapshot_version
        if cert.proof_manifest_digest:
            cert_identity["proof_manifest_digest"] = cert.proof_manifest_digest
        if cert.admission_revisions:
            cert_identity["admission_revisions"] = [[d, r] for d, r in cert.admission_revisions]

        op_identity_payload: Dict[str, object] = {
            "certificate": cert_identity,
            "target_node": target_node,
            "expected_predecessor_seq": expected_predecessor_seq,
            "required_predecessor_op_ids": sorted(set(required_predecessor_op_ids)),
            "min_committed_seq": min_committed_seq,
        }
        if required_commit_predicates:
            op_identity_payload["required_commit_predicates"] = sorted(set(required_commit_predicates))
        operation_identity = json.dumps(op_identity_payload, sort_keys=True, separators=(",", ":"))
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            row = self.conn.execute(
                "SELECT min_valid_epoch, last_committed_seq, min_auth_epoch FROM control_epochs WHERE resource_id = 'db_cluster';"
            ).fetchone()
            cur_epoch = int(row[0])
            cur_seq = int(row[1])
            cur_auth_epoch = int(row[2])

            if self.signing_key is not None:
                expected_sig = cert.compute_signature(self.signing_key)
                if not hmac.compare_digest(cert.signature, expected_sig):
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_INVALID_CERTIFICATE_SIGNATURE');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_INVALID_CERTIFICATE_SIGNATURE", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )

            if self.expected_manifest_hash is not None and cert.manifest_hash != self.expected_manifest_hash:
                self.conn.execute(
                    "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_STALE_MANIFEST_HASH');",
                    (cert.action, cert.admission_epoch),
                )
                self.conn.execute("COMMIT;")
                return TargetExecutionReceipt(
                    False, "REJECTED_STALE_MANIFEST_HASH", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                )

            if op_id is not None:
                prev = self.conn.execute(
                    "SELECT commit_version, status, identity_json FROM committed_ops WHERE op_id = ?;",
                    (op_id,),
                ).fetchone()
                if prev is not None:
                    self.conn.execute("COMMIT;")
                    if prev[1] == "COMMITTED" and prev[2] != operation_identity:
                        return TargetExecutionReceipt(
                            False, "REJECTED_OP_ID_CONFLICT", cur_epoch, cert.action,
                            "E2_SHARED_ATOMIC", commit_version=int(prev[0]),
                        )
                    return TargetExecutionReceipt(
                        prev[1] == "COMMITTED",
                        "DUPLICATE_IDEMPOTENT_COMMITTED" if prev[1] == "COMMITTED" else "REJECTED_PREVIOUSLY_FENCED",
                        cur_epoch,
                        cert.action,
                        "E2_SHARED_ATOMIC",
                        commit_version=int(prev[0]),
                    )

            if cert.admission_epoch < cur_epoch:
                self.conn.execute(
                    "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_STALE_EPOCH');",
                    (cert.action, cert.admission_epoch),
                )
                self.conn.execute("COMMIT;")
                return TargetExecutionReceipt(False, "REJECTED_STALE_EPOCH", cur_epoch, cert.action, "E2_SHARED_ATOMIC")

            if cert.auth_epoch < cur_auth_epoch:
                self.conn.execute(
                    "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_AUTHORIZATION_REVOKED');",
                    (cert.action, cert.admission_epoch),
                )
                self.conn.execute("COMMIT;")
                return TargetExecutionReceipt(
                    False, "REJECTED_AUTHORIZATION_REVOKED", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                )

            if cert.auth_valid_until is not None:
                if now_time is None:
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_AUTHORIZATION_CLOCK_UNAVAILABLE", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )
                if now_time > cert.auth_valid_until:
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_AUTHORIZATION_EXPIRED');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_AUTHORIZATION_EXPIRED", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )

            if expected_predecessor_seq is not None and cur_seq != expected_predecessor_seq:
                self.conn.execute(
                    "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_OUT_OF_ORDER_SEQUENCE');",
                    (cert.action, cert.admission_epoch),
                )
                self.conn.execute("COMMIT;")
                return TargetExecutionReceipt(
                    False, "REJECTED_OUT_OF_ORDER_SEQUENCE", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                )

            if min_committed_seq is not None and cur_seq < min_committed_seq:
                self.conn.execute(
                    "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_FRONTIER_NOT_REACHED');",
                    (cert.action, cert.admission_epoch),
                )
                self.conn.execute("COMMIT;")
                return TargetExecutionReceipt(
                    False, "REJECTED_FRONTIER_NOT_REACHED", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                )

            if required_predecessor_op_ids:
                placeholders = ",".join("?" for _ in required_predecessor_op_ids)
                settled = {
                    str(r[0])
                    for r in self.conn.execute(
                        f"SELECT op_id FROM committed_ops WHERE op_id IN ({placeholders}) AND status IN ('COMMITTED', 'FENCED_NO_EFFECT');",
                        tuple(required_predecessor_op_ids),
                    ).fetchall()
                }
                if not set(required_predecessor_op_ids).issubset(settled):
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_MISSING_PREDECESSOR_FRONTIER');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_MISSING_PREDECESSOR_FRONTIER", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )

            if required_commit_predicates:
                for pred_id in sorted(set(required_commit_predicates)):
                    pred_row = self.conn.execute(
                        "SELECT holds FROM resource_predicates WHERE predicate_id = ?;",
                        (pred_id,),
                    ).fetchone()
                    if pred_row is None or int(pred_row[0]) != 1:
                        self.conn.execute(
                            "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_COMMIT_PRECONDITION_FALSE');",
                            (cert.action, cert.admission_epoch),
                        )
                        self.conn.execute("COMMIT;")
                        return TargetExecutionReceipt(
                            False, "REJECTED_COMMIT_PRECONDITION_FALSE", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                        )

            if commit_precondition_check is not None:
                if not bool(commit_precondition_check(self.conn)):
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_COMMIT_PRECONDITION_FALSE');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_COMMIT_PRECONDITION_FALSE", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )

            if cert.action.startswith("drain_"):
                target_status = self.conn.execute(
                    "SELECT status FROM cluster_nodes WHERE node_id = ?;", (target_node,)
                ).fetchone()
                if target_status is None:
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_OUTSIDE_DOMAIN", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )
                if target_status[0] != "ACTIVE":
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_ALREADY_DRAINED", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )
                drained_count = int(
                    self.conn.execute("SELECT COUNT(*) FROM cluster_nodes WHERE status = 'DRAINED';").fetchone()[0]
                )
                shared_cap = int(self.conn.execute(
                    "SELECT max_drained_nodes FROM resource_limits WHERE resource_id = 'db_cluster';"
                ).fetchone()[0])
                if drained_count + 1 > shared_cap:
                    self.conn.execute(
                        "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'REJECTED_EXPOSURE_CAP');",
                        (cert.action, cert.admission_epoch),
                    )
                    self.conn.execute("COMMIT;")
                    return TargetExecutionReceipt(
                        False, "REJECTED_EXPOSURE_CAP", cur_epoch, cert.action, "E2_SHARED_ATOMIC"
                    )
                changed = self.conn.execute(
                    "UPDATE cluster_nodes SET status = 'DRAINED', committed_epoch = ? WHERE node_id = ? AND status = 'ACTIVE';",
                    (cert.admission_epoch, target_node),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("target row changed before receipt commit")

            new_seq = cur_seq + 1
            self.conn.execute(
                "UPDATE control_epochs SET last_committed_seq = ? WHERE resource_id = 'db_cluster';",
                (new_seq,),
            )
            if op_id is not None:
                self.conn.execute(
                    "INSERT INTO committed_ops (op_id, action, commit_version, status, identity_json) VALUES (?, ?, ?, 'COMMITTED', ?);",
                    (op_id, cert.action, new_seq, operation_identity),
                )
            self.conn.execute(
                "INSERT INTO audit_journal (action, epoch, outcome) VALUES (?, ?, 'COMMITTED');",
                (cert.action, cert.admission_epoch),
            )
            self.conn.execute("COMMIT;")
            return TargetExecutionReceipt(
                True, "COMMITTED", cur_epoch, cert.action, "E2_SHARED_ATOMIC", commit_version=new_seq
            )
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise
