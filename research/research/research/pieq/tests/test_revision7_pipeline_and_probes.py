"""
Revision 7 P1 Tests — Typed Proof-Submission Pipeline and Active Diagnostic Probe Loop.
"""
from __future__ import annotations

from pieq.core import (
    BoundPrimitiveAssumption,
    BoundedATMSEngine,
    DeterministicRuleVerifier,
    FaultWorld,
    InferenceRule,
)
from pieq.probe_loop import (
    DiagnosticProbeSpec,
    ProbeBudget,
    run_active_diagnostic_probe_loop,
)
from pieq.typed_pipeline import (
    TypedCertificateBuilder,
    TypedVocabulary,
    run_typed_proposal_with_feedback,
)


def _setup_failover_env(secret_key: bytes = b"rev7-pipe-key"):
    rules = [
        InferenceRule("r_act0", frozenset({"obs:hb_absent", "obs:wal_caught_up"}), "act:promote_replica"),
        InferenceRule("r_act1", frozenset({"obs:bmc_power_off"}), "act:promote_replica"),
        InferenceRule("r_act_shared", frozenset({"obs:shared_api_timeout"}), "act:promote_replica"),
        InferenceRule("r_ref0", frozenset({"obs:hb_absent", "obs:wal_caught_up"}), "refute:s_partition"),
        InferenceRule("r_ref1", frozenset({"obs:bmc_power_off"}), "refute:s_partition"),
        InferenceRule("r_ref_shared", frozenset({"obs:shared_api_timeout"}), "refute:s_partition"),
    ]
    verifier = DeterministicRuleVerifier(rules, [], secret_key=secret_key)
    atms = BoundedATMSEngine(
        verifier,
        expected_epoch=10,
        expected_resource_id="pg_cluster",
        expected_manifest_hash="mh_v7",
    )

    def make_obs(bit: int, aid: str, ch: str, dom: str, prop: str, epoch: int = 10, ver: int = 5, mh: str = "mh_v7"):
        sig = verifier.sign_telemetry(
            assumption_id=aid,
            channel_id=ch,
            failure_domain=dom,
            epoch_revision=epoch,
            proposition=prop,
            observation_version=ver,
            bit_index=bit,
            resource_id="pg_cluster",
            manifest_hash=mh,
        )
        return BoundPrimitiveAssumption(
            bit_index=bit,
            assumption_id=aid,
            channel_id=ch,
            failure_domain=dom,
            epoch_revision=epoch,
            proposition=prop,
            signature=sig,
            observation_version=ver,
            resource_id="pg_cluster",
            manifest_hash=mh,
        )

    e0 = make_obs(0, "e0", "ch_0", "fd_0", "obs:hb_absent")
    e1 = make_obs(1, "e1", "ch_1", "fd_0", "obs:wal_caught_up")  # Same fd_0 as e0
    e_stale = make_obs(2, "e_stale", "ch_2", "fd_2", "obs:bmc_power_off", epoch=9)
    assert atms.register_assumption(e0) is True
    assert atms.register_assumption(e1) is True
    # e_stale intentionally fails ATMS registration due to stale epoch=9 != 10
    assert atms.register_assumption(e_stale) is False

    worlds = [
        FaultWorld("s_primary_dead", frozenset(), frozenset({"promote_replica", "defer", "probe"})),
        FaultWorld("s_partition", frozenset(), frozenset({"reroute_link", "defer", "probe"})),
    ]
    vocab = TypedVocabulary(
        permissible_actions=("defer", "probe", "promote_replica", "reroute_link"),
        world_ids=("s_primary_dead", "s_partition"),
        rules={r.rule_id: r for r in rules},
        agent_authorized_handles={
            "agent_0": frozenset({"e0", "e1", "e_stale"}),
            "agent_1": frozenset({"e0"}),
        },
        assumptions_by_id={"e0": e0, "e1": e1, "e_stale": e_stale},
        expected_resource_id="pg_cluster",
        expected_epoch=10,
        expected_observation_version=5,
        expected_manifest_hash="mh_v7",
    )
    return verifier, atms, worlds, vocab, make_obs


def test_typed_pipeline_decouples_cautious_action_and_blocks_forged_fields() -> None:
    _, atms, _, vocab, _ = _setup_failover_env()
    builder = TypedCertificateBuilder(vocab, atms)

    # 1. Cautious agent recommends "probe", yet submits valid act:promote_replica & refute:s_partition derivations:
    cautious_proposal = {
        "proposed_action": "probe",
        "hypothesized_worlds": ["s_primary_dead", "s_partition"],
        "requested_probes": ["probe_bmc"],
        "derivations": [
            {"rule_id": "r_act0", "evidence_handles": ["e0", "e1"]},
            {"rule_id": "r_ref0", "evidence_handles": ["e0", "e1"]},
        ],
    }
    # Under decoupled typed interface, BOTH derivations pass TCB verification!
    rep = builder.validate_and_register_proposal("agent_0", cautious_proposal)
    assert rep.schema_rejected_proposal is False
    assert len(rep.outcomes) == 2
    assert all(o.accepted and o.schema_valid and o.horn_rule_valid and o.provenance_valid for o in rep.outcomes)
    assert len(atms.active_witness_justifications("act:promote_replica")) == 1

    # Under legacy action coupling, act:promote_replica is falsely rejected with incorrect_action_or_goal_binding:
    rep_legacy = builder.validate_and_register_proposal(
        "agent_0", cautious_proposal, enforce_action_coupling=True
    )
    assert rep_legacy.outcomes[0].accepted is False
    assert rep_legacy.outcomes[0].primary_category == "incorrect_action_or_goal_binding"

    # 2. Untrusted LLM trying to invent collector signature / failure_domain / env_bitmask is rejected fail-closed:
    forged = {
        "proposed_action": "promote_replica",
        "hypothesized_worlds": ["s_primary_dead"],
        "requested_probes": [],
        "derivations": [
            {"rule_id": "r_act0", "evidence_handles": ["e0", "e1"], "failure_domain": "fd_forged"}
        ],
    }
    rep_forged = builder.validate_and_register_proposal("agent_0", forged)
    assert rep_forged.outcomes[0].accepted is False
    assert rep_forged.outcomes[0].primary_category == "incorrect_failure_domain_or_provenance_references"

    # 3. Stale epoch handle rejected cleanly with stale_resource_epoch_version_or_manifest_binding:
    stale_prop = {
        "proposed_action": "promote_replica",
        "hypothesized_worlds": ["s_primary_dead"],
        "requested_probes": [],
        "derivations": [{"rule_id": "r_act1", "evidence_handles": ["e_stale"]}],
    }
    rep_stale = builder.validate_and_register_proposal("agent_0", stale_prop)
    assert rep_stale.outcomes[0].accepted is False
    assert rep_stale.outcomes[0].primary_category == "stale_resource_epoch_version_or_manifest_binding"

    # 4. Unauthorized handle rejected cleanly with incorrect_primitive_evidence_identifiers:
    unauth_prop = {
        "proposed_action": "promote_replica",
        "hypothesized_worlds": ["s_primary_dead"],
        "requested_probes": [],
        "derivations": [{"rule_id": "r_act0", "evidence_handles": ["e0", "e1"]}],
    }
    rep_unauth = builder.validate_and_register_proposal("agent_1", unauth_prop)
    assert rep_unauth.outcomes[0].accepted is False
    assert rep_unauth.outcomes[0].primary_category == "incorrect_primitive_evidence_identifiers"


def test_typed_pipeline_bounded_feedback_repairs_structural_error() -> None:
    _, atms, _, vocab, _ = _setup_failover_env()
    builder = TypedCertificateBuilder(vocab, atms)

    def flaky_proposer(prompt_payload, feedback_history):
        # Attempt 1: makes a realistic r4-style error (wrong antecedent + unknown rule)
        if not feedback_history:
            return {
                "proposed_action": "probe",
                "hypothesized_worlds": ["s_primary_dead"],
                "requested_probes": [],
                "derivations": [
                    {"rule_id": "conflict", "evidence_handles": ["e0"]},
                    {"rule_id": "r_act0", "evidence_handles": ["e0"]},  # missing e1
                ],
            }
        # Attempt 2: uses structured error feedback to emit valid typed derivations
        assert len(feedback_history) == 1
        cats = {d["category"] for d in feedback_history[0]["rejected_derivations"]}
        assert "invalid_proof_structure_or_schema" in cats
        assert "invalid_horn_antecedent_mapping" in cats
        return {
            "proposed_action": "probe",
            "hypothesized_worlds": ["s_primary_dead"],
            "requested_probes": [],
            "derivations": [
                {"rule_id": "r_act0", "evidence_handles": ["e0", "e1"]},
                {"rule_id": "r_ref0", "evidence_handles": ["e0", "e1"]},
            ],
        }

    rep = run_typed_proposal_with_feedback(builder, "agent_0", flaky_proposer, max_retries=2)
    assert rep.attempts_used == 2
    assert all(o.accepted for o in rep.outcomes)
    assert len(atms.active_witness_justifications("act:promote_replica")) == 1
    assert len(atms.active_witness_justifications("refute:s_partition")) == 1


def test_active_probe_loop_preserves_provenance_and_blocks_unsafe_probes() -> None:
    """
    Verifies all critical active diagnostic probe loop invariants:
      1. Non-read-only probe is rejected fail-closed.
      2. A probe sharing failure_domain='fd_0' with passive sensors MUST NOT inflate nu_pack!
      3. A genuinely independent OOB BMC probe ('fd_oob_bmc') increases nu_pack from 1 -> 2,
         refutes s_partition with f+1=2 disjoint domains, and unlocks safe promote_replica!
    """
    _, atms, worlds, vocab, make_obs = _setup_failover_env()
    builder = TypedCertificateBuilder(vocab, atms)

    # Register initial passive evidence from fd_0 only (nu_pack = 1 < k_a = 2, f = 1):
    builder.validate_and_register_proposal(
        "agent_0",
        {
            "proposed_action": "probe",
            "hypothesized_worlds": ["s_primary_dead", "s_partition"],
            "requested_probes": [],
            "derivations": [
                {"rule_id": "r_act0", "evidence_handles": ["e0", "e1"]},
                {"rule_id": "r_ref0", "evidence_handles": ["e0", "e1"]},
            ],
        },
    )

    # Case A: ONLY a mutative (unsafe) probe + a SAME-DOMAIN (fd_0) read-only probe available:
    unsafe_and_shared_probes = [
        DiagnosticProbeSpec(
            probe_id="probe_reboot_unsafe",
            collector_id="col_mut",
            failure_domain="fd_oob_bmc",
            read_only=False,  # MUST BE REJECTED FAIL-CLOSED!
            safety_contract_verified=True,
            world_outcome_map={"s_primary_dead": "obs:bmc_power_off", "s_partition": "obs:bmc_power_on"},
            target_actions=("promote_replica",),
        ),
        DiagnosticProbeSpec(
            probe_id="probe_shared_fd0",
            collector_id="col_api_2",
            failure_domain="fd_0",  # Shares failure domain with e0/e1!
            read_only=True,
            safety_contract_verified=True,
            world_outcome_map={"s_primary_dead": "obs:shared_api_timeout", "s_partition": "obs:shared_api_ok"},
            target_actions=("promote_replica",),
        ),
    ]

    def exec_shared(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        return make_obs(next_bit, f"obs_{spec.probe_id}", spec.collector_id, spec.failure_domain, "obs:shared_api_timeout")

    res_shared = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote_replica", "reroute_link"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=unsafe_and_shared_probes,
        probe_executor=exec_shared,
        budget=ProbeBudget(max_invocations=2, max_cost_units=5.0),
    )
    assert res_shared.outcome == "DEFER_ESCALATE"
    assert ("probe_reboot_unsafe", "REJECTED_NON_READ_ONLY_PROBE") in res_shared.rejected_unsafe_probes
    # Even though probe_shared_fd0 executed as a separate API call, recursive failure-domain
    # provenance keeps packing at 1 (NEVER inflated to 2)!
    assert res_shared.action_packing["promote_replica"] == 1
    assert res_shared.refutation_packing["s_partition"] == 1
    assert set(res_shared.surviving_worlds) == {"s_primary_dead", "s_partition"}

    # Case B: Now supply a genuinely disjoint OOB BMC read-only probe ('fd_oob_bmc'):
    oob_probe = DiagnosticProbeSpec(
        probe_id="probe_oob_bmc",
        collector_id="col_bmc_ipmi",
        failure_domain="fd_oob_bmc",
        read_only=True,
        safety_contract_verified=True,
        world_outcome_map={"s_primary_dead": "obs:bmc_power_off", "s_partition": "obs:bmc_power_on"},
        target_actions=("promote_replica",),
    )

    def exec_oob(spec: DiagnosticProbeSpec, next_bit: int) -> BoundPrimitiveAssumption:
        return make_obs(next_bit, f"obs_{spec.probe_id}", spec.collector_id, spec.failure_domain, "obs:bmc_power_off")

    res_oob = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote_replica", "reroute_link"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=[oob_probe],
        probe_executor=exec_oob,
        budget=ProbeBudget(max_invocations=2, max_cost_units=5.0),
        ranking_mode="calibrated_evsi",
        calibrated_priors={"s_primary_dead": 0.5, "s_partition": 0.5},
        action_utilities={
            ("promote_replica", "s_primary_dead"): 10.0,
            ("promote_replica", "s_partition"): -50.0,
            ("reroute_link", "s_primary_dead"): -50.0,
            ("reroute_link", "s_partition"): 10.0,
            ("defer", "s_primary_dead"): 0.0,
            ("defer", "s_partition"): 0.0,
            ("probe", "s_primary_dead"): -0.5,
            ("probe", "s_partition"): -0.5,
        },
    )
    assert res_oob.outcome == "ADMIT_MUTATION"
    assert res_oob.admitted_action == "promote_replica"
    assert res_oob.probes_executed == 1
    assert res_oob.probes_effective == 1
    assert res_oob.action_packing["promote_replica"] == 2
    assert res_oob.surviving_worlds == ("s_primary_dead",)


def test_active_probe_loop_enforces_strict_budget_termination() -> None:
    _, atms, worlds, vocab, make_obs = _setup_failover_env()
    probes = [
        DiagnosticProbeSpec(
            probe_id="p_expensive",
            collector_id="col_bmc",
            failure_domain="fd_oob",
            read_only=True,
            safety_contract_verified=True,
            cost_units=10.0,  # exceeds max_cost_units=2.0
            world_outcome_map={"s_primary_dead": "obs:bmc_power_off", "s_partition": "obs:bmc_power_on"},
            target_actions=("promote_replica",),
        )
    ]
    res = run_active_diagnostic_probe_loop(
        atms=atms,
        worlds=worlds,
        candidate_actions=("defer", "probe", "promote_replica", "reroute_link"),
        incident_class="db_failover",
        f_fault_budget=1,
        k_a=2,
        available_probes=probes,
        probe_executor=lambda spec, b: make_obs(b, "x", spec.collector_id, spec.failure_domain, "obs:bmc_power_off"),
        budget=ProbeBudget(max_invocations=2, max_cost_units=2.0),
    )
    assert res.outcome == "DEFER_ESCALATE"
    assert res.probes_executed == 0
    assert res.budget_exhausted_reason == "MAX_COST_OR_LOAD_REACHED"
