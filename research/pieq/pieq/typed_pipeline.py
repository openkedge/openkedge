"""
Revision 7 P1 — Typed Proof-Submission Interface and Deterministic Certificate Builder.

Architectural separation:
  - Untrusted LLM proposers select hypotheses, candidate action preferences, probe requests,
    and candidate derivations as typed `(rule_id, evidence_handles)` references drawn from
    a finite, verifier-known vocabulary (`TypedVocabulary`).
  - Deterministic trusted code (`TypedCertificateBuilder`) resolves registered evidence handles
    to server-side collector-signed `BoundPrimitiveAssumption` objects, computes exact bitmasks
    and consequents from trusted `M_sys`, validates authorization and binding constraints, and
    submits the resulting proof into the UNCHANGED `DeterministicRuleVerifier` and
    `BoundedATMSEngine`.
  - Proposers NEVER invent or supply collector signatures, failure-domain identities, bitmasks,
    or authorization metadata.
  - Evidence submission is decoupled from local action preference (`probe`/`defer`), eliminating
    the `r4` protocol defect where cautious agents had 95/140 valid proofs discarded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    InferenceRule,
    PlausibleWorldGate,
    compute_disjoint_justification_packing_bounded,
)

# Mutually exclusive 8-category primary proof-failure taxonomy (Revision 7 P1):
FAILURE_CATEGORIES: Tuple[str, ...] = (
    "invalid_proof_structure_or_schema",
    "incorrect_action_or_goal_binding",
    "invalid_horn_antecedent_mapping",
    "incorrect_primitive_evidence_identifiers",
    "incorrect_failure_domain_or_provenance_references",
    "stale_resource_epoch_version_or_manifest_binding",
    "semantically_invalid_reasoning_despite_schema_valid",
    "insufficient_independent_evidence_despite_valid_proof",
)

# Server-owned fields that untrusted LLM proposals are strictly forbidden to inject:
FORBIDDEN_UNTRUSTED_FIELDS: FrozenSet[str] = frozenset(
    {
        "signature",
        "failure_domain",
        "failure_domains",
        "collector_signature",
        "auth_epoch",
        "auth_valid_until",
        "env_bitmask",
        "bit_index",
        "manifest_hash",
        "proof_manifest_digest",
    }
)


def map_r4_rejection_to_primary_category(reason: str) -> str:
    """Map an archived `r4` per-proof verifier reason into the 8-category taxonomy."""
    mapping = {
        "malformed_or_out_of_range_mask": "invalid_proof_structure_or_schema",
        "unknown_rule_id": "invalid_proof_structure_or_schema",
        "action_not_requested": "incorrect_action_or_goal_binding",
        "refutation_not_requested": "incorrect_action_or_goal_binding",
        "wrong_consequent": "incorrect_action_or_goal_binding",
        "invalid_world_refutation_rule": "incorrect_action_or_goal_binding",
        "wrong_or_extra_antecedents": "invalid_horn_antecedent_mapping",
        "unauthorized_assumption": "incorrect_primitive_evidence_identifiers",
        "unsigned_or_badly_signed_assumption": "incorrect_failure_domain_or_provenance_references",
        "stale_binding": "stale_resource_epoch_version_or_manifest_binding",
        "verification_budget_exhausted": "insufficient_independent_evidence_despite_valid_proof",
    }
    return mapping.get(reason, "invalid_proof_structure_or_schema")


@dataclass(frozen=True)
class TypedVocabulary:
    """Finite, verifier-known vocabulary exposed to untrusted proposers."""

    permissible_actions: Tuple[str, ...]
    world_ids: Tuple[str, ...]
    rules: Dict[str, InferenceRule]
    # Server-side authoritative handles per agent; LLMs only see {handle_id: proposition}
    agent_authorized_handles: Dict[str, FrozenSet[str]]
    assumptions_by_id: Dict[str, BoundPrimitiveAssumption]
    expected_resource_id: Optional[str] = None
    expected_epoch: Optional[int] = None
    expected_observation_version: Optional[int] = None
    expected_manifest_hash: Optional[str] = None

    def agent_prompt_payload(self, agent_id: str) -> Dict[str, Any]:
        """Return only safe, verifier-known vocabulary visible to `agent_id` (no secrets/domains)."""
        allowed = sorted(self.agent_authorized_handles.get(agent_id, frozenset()))
        visible_handles = {
            h: self.assumptions_by_id[h].proposition
            for h in allowed
            if h in self.assumptions_by_id
        }
        rule_catalog = [
            {
                "rule_id": r.rule_id,
                "antecedents": sorted(r.antecedents),
                "consequent": r.consequent,
            }
            for r in sorted(self.rules.values(), key=lambda x: x.rule_id)
        ]
        return {
            "agent_id": agent_id,
            "permissible_actions": list(self.permissible_actions),
            "candidate_worlds": list(self.world_ids),
            "authorized_evidence_handles": visible_handles,
            "horn_rule_catalog": rule_catalog,
            "schema_contract": {
                "required_keys": [
                    "proposed_action",
                    "hypothesized_worlds",
                    "requested_probes",
                    "derivations",
                ],
                "derivation_keys": ["rule_id", "evidence_handles"],
                "forbidden_fields": sorted(FORBIDDEN_UNTRUSTED_FIELDS),
            },
        }


@dataclass(frozen=True)
class ProofValidationOutcome:
    """Exact per-derivation audit record tracking every validation stage independently."""

    agent_id: str
    rule_id: str
    evidence_handles: Tuple[str, ...]
    schema_valid: bool
    horn_rule_valid: bool
    provenance_valid: bool
    accepted: bool
    primary_category: Optional[str] = None
    detail: str = ""
    consequent: Optional[str] = None
    env_bitmask: Optional[int] = None


@dataclass
class AgentSubmissionReport:
    agent_id: str
    attempts_used: int
    proposed_action: str
    hypothesized_worlds: Tuple[str, ...]
    requested_probes: Tuple[str, ...]
    outcomes: List[ProofValidationOutcome] = field(default_factory=list)
    initial_attempt_rejections: List[ProofValidationOutcome] = field(default_factory=list)
    schema_rejected_proposal: bool = False
    proposal_error_category: Optional[str] = None
    proposal_error_detail: str = ""


class TypedCertificateBuilder:
    """
    Deterministic trusted builder translating typed LLM derivations into verified
    ATMS justifications without ever allowing the LLM to forge signatures, bitmasks,
    failure domains, or authorization metadata.
    """

    def __init__(self, vocabulary: TypedVocabulary, atms: BoundedATMSEngine) -> None:
        self.vocab = vocabulary
        self.atms = atms

    def validate_and_register_proposal(
        self,
        agent_id: str,
        raw_proposal: Any,
        *,
        round_tag: str = "round_0",
        enforce_action_coupling: bool = False,
    ) -> AgentSubmissionReport:
        """
        Validate a raw typed proposal dictionary and register all valid derivations
        into `self.atms` via the unchanged `BoundedATMSEngine.add_verified_justification`.

        Note: `enforce_action_coupling` defaults to False (decoupled evidence submission).
        Setting it True reproduces the legacy `r4` coupling failure mode for A/B testing.
        """
        if not isinstance(raw_proposal, dict):
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action="defer",
                hypothesized_worlds=(),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="invalid_proof_structure_or_schema",
                proposal_error_detail="Proposal must be a JSON object.",
            )

        # Reject any attempt by untrusted proposer to inject server-owned provenance/auth fields:
        leaked = FORBIDDEN_UNTRUSTED_FIELDS & set(raw_proposal.keys())
        if leaked:
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action="defer",
                hypothesized_worlds=(),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="incorrect_failure_domain_or_provenance_references",
                proposal_error_detail=f"Untrusted proposer attempted to supply server-owned fields: {sorted(leaked)}",
            )

        required_keys = {"proposed_action", "hypothesized_worlds", "requested_probes", "derivations"}
        if not required_keys.issubset(raw_proposal.keys()):
            missing = sorted(required_keys - set(raw_proposal.keys()))
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action="defer",
                hypothesized_worlds=(),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="invalid_proof_structure_or_schema",
                proposal_error_detail=f"Missing required keys: {missing}",
            )

        proposed_action = raw_proposal["proposed_action"]
        if type(proposed_action) is not str or proposed_action not in self.vocab.permissible_actions:
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action="defer",
                hypothesized_worlds=(),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="incorrect_action_or_goal_binding",
                proposal_error_detail=f"Unknown proposed_action: {proposed_action!r}",
            )

        hyp_worlds = raw_proposal["hypothesized_worlds"]
        if not isinstance(hyp_worlds, list) or any(
            type(w) is not str or w not in self.vocab.world_ids for w in hyp_worlds
        ):
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action=proposed_action,
                hypothesized_worlds=(),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="invalid_proof_structure_or_schema",
                proposal_error_detail="hypothesized_worlds must be a list of known candidate world IDs.",
            )

        req_probes = raw_proposal["requested_probes"]
        if not isinstance(req_probes, list) or any(type(p) is not str for p in req_probes):
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action=proposed_action,
                hypothesized_worlds=tuple(hyp_worlds),
                requested_probes=(),
                schema_rejected_proposal=True,
                proposal_error_category="invalid_proof_structure_or_schema",
                proposal_error_detail="requested_probes must be a list of strings.",
            )

        derivations = raw_proposal["derivations"]
        if not isinstance(derivations, list) or len(derivations) > 256:
            return AgentSubmissionReport(
                agent_id=agent_id,
                attempts_used=1,
                proposed_action=proposed_action,
                hypothesized_worlds=tuple(hyp_worlds),
                requested_probes=tuple(req_probes),
                schema_rejected_proposal=True,
                proposal_error_category="invalid_proof_structure_or_schema",
                proposal_error_detail="derivations must be a list of <= 256 objects.",
            )

        report = AgentSubmissionReport(
            agent_id=agent_id,
            attempts_used=1,
            proposed_action=proposed_action,
            hypothesized_worlds=tuple(hyp_worlds),
            requested_probes=tuple(req_probes),
        )

        authorized_handles = self.vocab.agent_authorized_handles.get(agent_id, frozenset())
        seen_keys: Set[Tuple[str, Tuple[str, ...]]] = set()

        for item in derivations:
            outcome = self._validate_single_derivation(
                agent_id=agent_id,
                item=item,
                authorized_handles=authorized_handles,
                proposed_action=proposed_action,
                round_tag=round_tag,
                enforce_action_coupling=enforce_action_coupling,
                seen_keys=seen_keys,
            )
            report.outcomes.append(outcome)

        return report

    def _validate_single_derivation(
        self,
        *,
        agent_id: str,
        item: Any,
        authorized_handles: FrozenSet[str],
        proposed_action: str,
        round_tag: str,
        enforce_action_coupling: bool,
        seen_keys: Set[Tuple[str, Tuple[str, ...]]],
    ) -> ProofValidationOutcome:
        # Stage 1: Schema & forbidden-field checks
        if not isinstance(item, dict):
            return ProofValidationOutcome(
                agent_id, "", (), False, False, False, False,
                "invalid_proof_structure_or_schema", "Derivation entry must be an object.",
            )
        leaked = FORBIDDEN_UNTRUSTED_FIELDS & set(item.keys())
        if leaked:
            return ProofValidationOutcome(
                agent_id, str(item.get("rule_id", "")), (), False, False, False, False,
                "incorrect_failure_domain_or_provenance_references",
                f"Derivation attempted to supply server-owned fields: {sorted(leaked)}",
            )
        allowed_keys = {"rule_id", "evidence_handles", "claimed_consequent"}
        if not {"rule_id", "evidence_handles"}.issubset(item.keys()) or not set(item.keys()).issubset(allowed_keys):
            return ProofValidationOutcome(
                agent_id, str(item.get("rule_id", "")), (), False, False, False, False,
                "invalid_proof_structure_or_schema",
                "Derivation keys must be {'rule_id', 'evidence_handles'} (optional 'claimed_consequent').",
            )
        rule_id = item["rule_id"]
        handles = item["evidence_handles"]
        if type(rule_id) is not str or not isinstance(handles, list) or any(type(h) is not str for h in handles):
            return ProofValidationOutcome(
                agent_id, str(rule_id), (), False, False, False, False,
                "invalid_proof_structure_or_schema",
                "rule_id must be str and evidence_handles must be list[str].",
            )
        handles_tuple = tuple(sorted(set(handles)))
        if len(handles_tuple) != len(handles):
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, False, False, False, False,
                "invalid_proof_structure_or_schema",
                "Duplicate evidence_handles in derivation.",
            )

        # Rule lookup in finite trusted vocabulary:
        rule = self.vocab.rules.get(rule_id)
        if rule is None:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, False, False, False, False,
                "invalid_proof_structure_or_schema",
                f"Unknown rule_id {rule_id!r} (not in trusted M_sys).",
            )

        # Optional goal/consequent binding check if proposer supplied claimed_consequent:
        consequent = rule.consequent
        if "claimed_consequent" in item and item["claimed_consequent"] != consequent:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "incorrect_action_or_goal_binding",
                f"claimed_consequent {item['claimed_consequent']!r} != rule consequent {consequent!r}.",
                consequent=consequent,
            )
        if consequent.startswith("refute:") and consequent[7:] not in self.vocab.world_ids:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "incorrect_action_or_goal_binding",
                f"Rule refutes unknown world {consequent[7:]!r}.",
                consequent=consequent,
            )
        if consequent.startswith("act:") and consequent[4:] not in self.vocab.permissible_actions:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "incorrect_action_or_goal_binding",
                f"Rule targets non-permissible action {consequent[4:]!r}.",
                consequent=consequent,
            )
        if not consequent.startswith(("act:", "refute:")):
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "incorrect_action_or_goal_binding",
                f"Unsupported consequent prefix: {consequent!r}.",
                consequent=consequent,
            )
        if enforce_action_coupling and consequent.startswith("act:") and proposed_action != consequent[4:]:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "incorrect_action_or_goal_binding",
                f"Legacy action coupling rejected act:{consequent[4:]} because proposed_action={proposed_action!r}.",
                consequent=consequent,
            )

        # Stage 2: Evidence handle authorization & server-side lookup
        resolved: List[BoundPrimitiveAssumption] = []
        for h in handles_tuple:
            if h not in self.vocab.assumptions_by_id:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "incorrect_primitive_evidence_identifiers",
                    f"Unknown evidence handle {h!r}.",
                    consequent=consequent,
                )
            if h not in authorized_handles:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "incorrect_primitive_evidence_identifiers",
                    f"Evidence handle {h!r} is not authorized for {agent_id}.",
                    consequent=consequent,
                )
            resolved.append(self.vocab.assumptions_by_id[h])

        # Stage 3: Resource/epoch/version/manifest binding verification (before Horn matching)
        for a in resolved:
            if self.vocab.expected_resource_id is not None and a.resource_id != self.vocab.expected_resource_id:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "stale_resource_epoch_version_or_manifest_binding",
                    f"Handle {a.assumption_id} resource_id mismatch ({a.resource_id!r}).",
                    consequent=consequent,
                )
            if self.vocab.expected_epoch is not None and a.epoch_revision != self.vocab.expected_epoch:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "stale_resource_epoch_version_or_manifest_binding",
                    f"Handle {a.assumption_id} stale epoch ({a.epoch_revision} != {self.vocab.expected_epoch}).",
                    consequent=consequent,
                )
            if (
                self.vocab.expected_observation_version is not None
                and a.observation_version != self.vocab.expected_observation_version
            ):
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "stale_resource_epoch_version_or_manifest_binding",
                    f"Handle {a.assumption_id} torn observation_version ({a.observation_version}).",
                    consequent=consequent,
                )
            if self.vocab.expected_manifest_hash is not None and a.manifest_hash != self.vocab.expected_manifest_hash:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, False, False, False,
                    "stale_resource_epoch_version_or_manifest_binding",
                    f"Handle {a.assumption_id} stale manifest_hash.",
                    consequent=consequent,
                )

        # Stage 4: Horn-rule antecedent bijection
        props = [a.proposition for a in resolved]
        if len(props) != len(rule.antecedents) or set(props) != set(rule.antecedents):
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, False, False, False,
                "invalid_horn_antecedent_mapping",
                f"Handles yield propositions {sorted(props)}, expected {sorted(rule.antecedents)}.",
                consequent=consequent,
            )

        # Stage 5: Deterministic bitmask construction & collector signature / provenance check
        env_mask = 0
        for a in resolved:
            if a.bit_index not in self.atms.assumptions_by_bit:
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, True, False, False,
                    "incorrect_failure_domain_or_provenance_references",
                    f"Handle {a.assumption_id} bit {a.bit_index} is not registered in ATMS.",
                    consequent=consequent,
                )
            registered = self.atms.assumptions_by_bit[a.bit_index]
            if registered.assumption_id != a.assumption_id or not self.atms.verifier.verify_assumption_signature(registered):
                return ProofValidationOutcome(
                    agent_id, rule_id, handles_tuple, True, True, False, False,
                    "incorrect_failure_domain_or_provenance_references",
                    f"Collector signature verification failed for {a.assumption_id}.",
                    consequent=consequent,
                )
            env_mask |= 1 << a.bit_index

        # Deduplicate repeated identical derivations within a single agent proposal cleanly:
        dedup_key = (rule_id, handles_tuple)
        if dedup_key in seen_keys:
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, True, True, True,
                None, "duplicate_derivation_already_registered", consequent=consequent, env_bitmask=env_mask,
            )
        seen_keys.add(dedup_key)

        # Stage 6: Hand off to UNCHANGED core TCB BoundedATMSEngine!
        cert = self.atms.add_verified_justification(
            agent_id,
            f"{agent_id}:{round_tag}",
            consequent,
            env_mask,
            rule_id,
        )
        if cert is None:
            cat = (
                "insufficient_independent_evidence_despite_valid_proof"
                if self.atms.budget_exceeded
                else "semantically_invalid_reasoning_despite_schema_valid"
            )
            return ProofValidationOutcome(
                agent_id, rule_id, handles_tuple, True, True, False, False,
                cat,
                "ATMS rejected justification (budget exhausted or verification failure).",
                consequent=consequent,
                env_bitmask=env_mask,
            )

        return ProofValidationOutcome(
            agent_id, rule_id, handles_tuple, True, True, True, True,
            None, "verified", consequent=consequent, env_bitmask=env_mask,
        )


def run_typed_proposal_with_feedback(
    builder: TypedCertificateBuilder,
    agent_id: str,
    proposer_fn: Callable[[Dict[str, Any], List[Dict[str, Any]]], Any],
    *,
    max_retries: int = 2,
    round_tag: str = "round_0",
    enforce_action_coupling: bool = False,
) -> AgentSubmissionReport:
    """
    Bounded structured feedback and retry loop for structurally invalid proposals.
    Never weakens TCB verifier acceptance criteria and never leaks other agents' evidence.
    """
    prompt_payload = builder.vocab.agent_prompt_payload(agent_id)
    feedback_history: List[Dict[str, Any]] = []
    last_report: Optional[AgentSubmissionReport] = None
    first_rejections: List[ProofValidationOutcome] = []

    for attempt in range(max_retries + 1):
        raw = proposer_fn(prompt_payload, feedback_history)
        report = builder.validate_and_register_proposal(
            agent_id,
            raw,
            round_tag=f"{round_tag}:try_{attempt}",
            enforce_action_coupling=enforce_action_coupling,
        )
        report.attempts_used = attempt + 1
        rejected = [o for o in report.outcomes if not o.accepted]
        if attempt == 0:
            first_rejections = list(rejected)
        report.initial_attempt_rejections = first_rejections
        last_report = report

        # Stop immediately if proposal had no structural/rule/handle errors:
        if not report.schema_rejected_proposal and not rejected:
            return report

        # Build bounded structured feedback without leaking secrets or external evidence:
        fb: Dict[str, Any] = {
            "attempt": attempt + 1,
            "proposal_error": (
                {
                    "category": report.proposal_error_category,
                    "detail": report.proposal_error_detail,
                }
                if report.schema_rejected_proposal
                else None
            ),
            "rejected_derivations": [
                {
                    "rule_id": o.rule_id,
                    "evidence_handles": list(o.evidence_handles),
                    "category": o.primary_category,
                    "detail": o.detail,
                }
                for o in rejected
            ],
        }
        feedback_history.append(fb)

    assert last_report is not None
    return last_report
