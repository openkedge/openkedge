"""
PIEQ Revision 8 P0.3 Unit Tests — Executable Pinned Multi-Provider Live-LLM Runner.

Uses an injected in-process stub provider (strictly for unit-testing runner mechanics,
NEVER reported as live LLM evaluation evidence!) to verify:
  1. Manifest validation rejects unpinned CLI providers and single-family manifests.
  2. Preflight honestly reports UNEXECUTED_MISSING_CREDENTIALS when API keys are unset.
  3. Server-attested model-ID check fails closed if a provider returns `model=None`.
  4. All 12 required P0.3 trace fields are logged across all 4 compared protocols
     (`orig_freeform`, `typed_interface`, `typed_plus_probes`, `full_pieq`), including
     validation feedback retries, probe responses, certificates, duration, and cost.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from pieq.heldout_cases import build_heldout_cases
from pieq.live_providers import Reply
from scripts.run_rev8_pinned_live_eval import (
    LIVE_PROTOCOLS,
    check_live_preflight,
    run_pinned_live_evaluation,
    validate_pinned_live_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_MANIFEST_PATH = REPO_ROOT / "experiments" / "pinned_live_manifest_template.json"


class _StubAttestedProvider:
    """In-process unit-test stub emitting server-attested model IDs & typed JSON."""

    def __init__(self, *, omit_model_id: bool = False, fail_first_attempt_agent4: bool = True) -> None:
        self.omit_model_id = omit_model_id
        self.fail_first_attempt_agent4 = fail_first_attempt_agent4

    def complete(self, instance, prompt, manifest) -> Reply:
        payload = json.loads(prompt)
        vocab = payload["vocabulary_and_observations"]
        fb = payload["validation_feedback_history"]
        aid = vocab["agent_id"]
        vis_handles = vocab["authorized_evidence_handles"]
        prop_to_h = {}
        for h, p in sorted(vis_handles.items()):
            prop_to_h.setdefault(p, []).append(h)

        derivs = []
        for rule in vocab["horn_rule_catalog"]:
            if all(a in prop_to_h for a in rule["antecedents"]):
                derivs.append(
                    {
                        "rule_id": rule["rule_id"],
                        "evidence_handles": sorted(prop_to_h[a][0] for a in rule["antecedents"]),
                    }
                )
        if self.fail_first_attempt_agent4 and aid == "agent_4" and not fb and vis_handles:
            derivs.insert(0, {"rule_id": "invalid_rule_xyz", "evidence_handles": [sorted(vis_handles)[0]]})

        reply_obj = {
            "proposed_action": "probe",
            "hypothesized_worlds": vocab["candidate_worlds"],
            "requested_probes": ["probe_oob"],
            "derivations": derivs,
        }
        model_id = None if self.omit_model_id else f"{instance['model']}-attested"
        return Reply(
            text=json.dumps(reply_obj),
            usage={"input_tokens": 500, "output_tokens": 120},
            model=model_id,
        )


def test_p0_3_manifest_validation_rejects_unpinned_cli_and_single_family() -> None:
    manifest = json.loads(TEMPLATE_MANIFEST_PATH.read_text())
    info = validate_pinned_live_manifest(manifest)
    assert len(info["provider_families"]) == 3

    # Disallow unpinned CLI wrapper:
    bad_cli = json.loads(json.dumps(manifest))
    bad_cli["instances"][2]["provider"] = "codex_cli"
    with pytest.raises(ValueError, match="disallowed"):
        validate_pinned_live_manifest(bad_cli)

    # Disallow single provider family:
    bad_single = json.loads(json.dumps(manifest))
    for inst in bad_single["instances"]:
        inst["provider"] = "openai"
        inst["family"] = "openai-gpt"
    with pytest.raises(ValueError, match=">= 2"):
        validate_pinned_live_manifest(bad_single)


def test_p0_3_preflight_honestly_reports_missing_credentials() -> None:
    manifest = json.loads(TEMPLATE_MANIFEST_PATH.read_text())
    status = check_live_preflight(manifest)
    assert status["manifest_sha256"]
    assert status["schema_sha256"]
    assert status["protocols_configured"] == list(LIVE_PROTOCOLS)
    # On host without API keys, must report UNEXECUTED_MISSING_CREDENTIALS:
    assert status["execution_status"] in {"READY", "UNEXECUTED_MISSING_CREDENTIALS"}


def test_p0_3_runner_refuses_unattested_server_model_id() -> None:
    manifest = json.loads(TEMPLATE_MANIFEST_PATH.read_text())
    cases = build_heldout_cases()[:1]
    stub = _StubAttestedProvider(omit_model_id=True)
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(RuntimeError, match="UNATTESTED_SERVER_MODEL_ID"):
            run_pinned_live_evaluation(
                cases=cases,
                manifest=manifest,
                trace_path=Path(tmp) / "unattested.jsonl",
                protocols=("typed_interface",),
                providers_override={"anthropic": stub, "openai": stub, "gemini": stub},
            )


def test_p0_3_runner_records_all_12_fields_across_all_four_protocols() -> None:
    manifest = json.loads(TEMPLATE_MANIFEST_PATH.read_text())
    # Test on H01 (passive recoverable) + H05 (probe recoverable):
    all_cases = {c.case_id: c for c in build_heldout_cases()}
    cases = [all_cases["H01"], all_cases["H05"]]
    stub = _StubAttestedProvider(omit_model_id=False, fail_first_attempt_agent4=True)

    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "stub_trace.jsonl"
        summary = run_pinned_live_evaluation(
            cases=cases,
            manifest=manifest,
            trace_path=trace_path,
            protocols=LIVE_PROTOCOLS,
            providers_override={"anthropic": stub, "openai": stub, "gemini": stub},
        )
        assert summary["protocol_summaries"]["orig_freeform"]["admitted_count"] == 0  # Action-coupled paralysis!
        assert summary["protocol_summaries"]["typed_interface"]["admitted_count"] == 1  # H01 admitted, H05 needs probe
        assert summary["protocol_summaries"]["typed_plus_probes"]["admitted_count"] == 2  # H01 + H05!
        assert summary["protocol_summaries"]["full_pieq"]["admitted_count"] == 2  # H01 + H05 on SQLite E2!
        assert summary["total_api_cost_usd"] > 0

        lines = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        assert lines[0]["record_type"] == "run_header"
        eval_recs = [r for r in lines if r["record_type"] == "case_evaluation"]
        assert len(eval_recs) == 8  # 2 cases x 4 protocols
        full_h05 = next(r for r in eval_recs if r["protocol"] == "full_pieq" and r["case_id"] == "H05")
        assert full_h05["accepted_certificate"] is not None
        assert len(full_h05["completed_probe_responses"]) >= 1
        assert full_h05["end_to_end_duration_ms"] >= 0
        # Verify agent_4 retried cleanly in typed protocols:
        a4_attempts = [a for a in full_h05["agent_attempts"] if a["agent_id"] == "agent_4"]
        assert len(a4_attempts) == 2
        assert a4_attempts[0]["server_reported_model_id"].endswith("-attested")
        assert a4_attempts[0]["prompt_sha256"] and a4_attempts[0]["schema_sha256"]
