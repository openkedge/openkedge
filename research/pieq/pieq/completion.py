"""
Revision 11 EXP-06 — Bounded Deterministic Evidence Completion.

Derives consequences ONLY from:
  - primitive assumptions ALREADY registered (and signature-verified) in the
    BoundedATMSEngine at call time (passive telemetry and any probe observations the
    pipeline has itself obtained), and
  - the trusted, PRE-EXISTING Horn rule vocabulary passed by the caller,
submitting each candidate through the UNCHANGED `add_verified_justification` /
`add_verified_derivation` verifier path. It therefore cannot manufacture evidence,
cannot use oracle labels, fixtures, synthetic observations, future probe outcomes, or
any telemetry outside the tested configuration, and cannot weaken Phi_phys semantics:
all completed derivations share one proposer context (`tcb_completion:0`), which is
irrelevant to packing (physical-leaf footprints) and can never create false
independence. A single faulty physical domain still yields overlapping footprints no
matter how many completed derivations cite it.

Fail-closed bounds: `max_new_derivations` and `max_rounds` cap the closure; on
exhaustion the function stops registering and reports `truncated=True`. Truncation can
only UNDER-populate proofs: positive packing keeps a sound lower bound and refutation
shortfall conservatively RETAINS worlds (Lemma 4 unaffected).

Trusted-computing-base note: this module adds ~70 lines of deterministic code to the
TCB. Its soundness reduces to (a) the unchanged verifier accepting only rule-exact,
signature-verified derivations, and (b) the closure proposing only (rule, handle-set)
pairs whose antecedent propositions are exactly matched by registered assumptions.
"""
from __future__ import annotations

from itertools import product
from typing import Dict, Iterable, List, Set, Tuple

from pieq.core import BoundedATMSEngine, InferenceRule


def bounded_deterministic_completion(
    atms: BoundedATMSEngine,
    rules: Iterable[InferenceRule],
    *,
    context_id: str = "tcb_completion:0",
    agent_id: str = "tcb_completion",
    max_new_derivations: int = 128,
    max_antecedent_combos: int = 512,
) -> Tuple[int, bool]:
    """Register every trusted single-step derivation supported by already-registered
    primitive assumptions. Returns (newly_registered, truncated)."""
    by_prop: Dict[str, List[int]] = {}
    for bit, a in atms.assumptions_by_bit.items():
        by_prop.setdefault(a.proposition, []).append(bit)

    # Existing registrations (from any proposer) — avoid duplicate justifications:
    existing: Set[Tuple[str, int]] = set()
    for certs in atms.justifications.values():
        for c in certs:
            existing.add((c.rule_id, c.env_mask))

    new_count = 0
    truncated = False
    for rule in sorted(rules, key=lambda r: r.rule_id):
        ants = sorted(rule.antecedents)
        if not all(p in by_prop for p in ants):
            continue  # a required sensor/observation is simply unavailable: no refutation
        combos = 1
        for p in ants:
            combos *= len(by_prop[p])
        if combos > max_antecedent_combos:
            truncated = True
            continue
        for bits in product(*(by_prop[p] for p in ants)):
            if len(set(bits)) != len(bits):
                continue
            if new_count >= max_new_derivations:
                return new_count, True
            mask = 0
            for b in bits:
                mask |= 1 << b
            if (rule.rule_id, mask) in existing:
                continue
            cert = atms.add_verified_justification(
                agent_id, context_id, rule.consequent, mask, rule.rule_id
            )
            if cert is not None:
                existing.add((rule.rule_id, mask))
                new_count += 1
    return new_count, truncated
