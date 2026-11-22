#!/usr/bin/env python3
"""
PIEQ Revision 9 — Live-LLM Evaluation via Locally Authenticated Agent CLIs
(`claude -p`, `codex exec`). Operator-requested run (Nov 21, 2026).

EVIDENCE-TIER DISCLOSURE (read before citing results):
  - This IS genuine live LLM inference: every proposal is produced by a real model call
    at evaluation time. No replay, no deterministic proposer, no fixture completion.
  - This is NOT the P0.1 "pinned multi-provider" evaluation. That protocol requires
    independently verified server-attested model snapshots via direct provider APIs and
    remains BLOCKED (no API credentials). Differences, disclosed:
      1. Model identity: `claude_cli` reports a CLI-surfaced model ID (e.g.
         `claude-sonnet-5-5`) without an immutable snapshot; `codex_cli` (Bedrock
         `openai.gpt-5.6-sol` per user config) reports NO model ID in its event stream,
         so its identity is recorded as `cli-config-declared:*` and marked unattested.
      2. Sampling: temperature/seed/max_output_tokens CANNOT be enforced through these
         CLIs; the CLIs' own defaults apply (codex: reasoning_effort=high per user
         config). Recorded sampling parameters are DECLARED-ONLY.
      3. The CLIs wrap models in an agent harness (large system prompt, ~18-21k input
         tokens per call), so token/cost numbers include harness overhead.
  - The rev8 pinned runner `run_rev8_pinned_live_eval.py` is NOT modified or weakened:
    this script substitutes only the manifest validator (CLI-aware, >= 2 CLI families)
    and reuses `run_pinned_live_evaluation` verbatim, so prompts, schema, typed
    certificate builder, verifier, probe policy, retry budget, and trace fields are
    byte-identical to the pinned protocol. Ground-truth labels remain inaccessible to
    the live pipeline (only `build_heldout_cases()` public fields enter prompts).

Configurations:
  A `orig_freeform`      B `typed_interface`      C `typed_plus_probes`
  D `--config-d`: ONE live model (agent_0, sonnet) granted ALL telemetry handles,
     typed + probes (tests whether multiple live agents matter once evidence access
     is held constant)
  E `full_pieq`

Usage:
  python3 scripts/run_rev9_cli_live_eval.py --protocols typed_interface --limit 2   # smoke
  python3 scripts/run_rev9_cli_live_eval.py --protocols orig_freeform              # full, 1 proto
  python3 scripts/run_rev9_cli_live_eval.py --config-d                              # config D
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_rev8_pinned_live_eval as rev8
from pieq.heldout_cases import build_heldout_cases
from pieq.live_providers import CLIError, CLIProvider, Reply

CLI_FAMILIES = {"claude_cli": "anthropic-claude-cli", "codex_cli": "openai-codex-cli-bedrock"}

CLI_MANIFEST = {
    "run_id": "pieq-rev9-cli-live-unpinned-v1",
    "run_kind": "cli_wrapper_live_unpinned",
    "protocol_version": "pieq-rev8-live-eval-v1",
    "require_server_attested_model_id": False,
    "model_identity_caveat": (
        "claude_cli model IDs are CLI-surfaced without immutable snapshots; codex_cli "
        "reports no model ID (identity recorded from user CLI config, unattested)."
    ),
    "sampling_caveat": "Declared only; CLIs do not accept temperature/seed overrides.",
    "instances": [
        {"agent_id": "agent_0", "provider": "claude_cli", "family": CLI_FAMILIES["claude_cli"],
         "model": "sonnet", "snapshot": "cli-unpinned", "supports_seed": False,
         "input_usd_per_million": 0.0, "output_usd_per_million": 0.0},
        {"agent_id": "agent_1", "provider": "claude_cli", "family": CLI_FAMILIES["claude_cli"],
         "model": "haiku", "snapshot": "cli-unpinned", "supports_seed": False,
         "input_usd_per_million": 0.0, "output_usd_per_million": 0.0},
        {"agent_id": "agent_2", "provider": "claude_cli", "family": CLI_FAMILIES["claude_cli"],
         "model": "opus", "snapshot": "cli-unpinned", "supports_seed": False,
         "input_usd_per_million": 0.0, "output_usd_per_million": 0.0},
        {"agent_id": "agent_3", "provider": "codex_cli", "family": CLI_FAMILIES["codex_cli"],
         "model": "openai.gpt-5.6-sol", "snapshot": "cli-unpinned", "supports_seed": False,
         "input_usd_per_million": 0.0, "output_usd_per_million": 0.0},
        {"agent_id": "agent_4", "provider": "codex_cli", "family": CLI_FAMILIES["codex_cli"],
         "model": "openai.gpt-5.6-sol", "snapshot": "cli-unpinned", "supports_seed": False,
         "input_usd_per_million": 0.0, "output_usd_per_million": 0.0},
    ],
    "sampling": {"temperature": None, "top_p": None, "seed": None, "max_output_tokens": 4096,
                 "enforced": False},
    "timeout_seconds": 300,
    "max_retries_typed": 2,
    "max_calls": 4000,
    "spending_cap_usd": 40.0,
    "packing_expansions": 10000,
}

DEFER_FALLBACK = json.dumps({
    "proposed_action": "defer", "hypothesized_worlds": [], "requested_probes": [],
    "derivations": [],
})


class ResilientCLI:
    """Wraps CLIProvider: one retry on CLI/timeout failure, then a recorded
    provider-failure defer reply (failures are P0.1 measurements, not run aborts).
    Also supplies declared identity when the CLI reports no model ID (codex)."""

    def __init__(self, name: str, max_attempts: int = 2) -> None:
        self.inner = CLIProvider(name)
        self.name = name
        self.max_attempts = max_attempts
        self.failures: list[dict] = []

    def preflight(self):
        return self.inner.preflight()

    def complete(self, instance, prompt, manifest):
        last_err: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                reply = self.inner.complete(instance, prompt, manifest)
                if not isinstance(reply.model, str) or not reply.model.strip():
                    usage = dict(reply.usage or {})
                    usage["model_identity_unattested"] = True
                    reply = Reply(reply.text, usage, f"cli-config-declared:{instance['model']}")
                return reply
            except (CLIError, TimeoutError) as exc:
                last_err = exc
        failure = {"provider": self.name, "model": instance.get("model"),
                   "error": f"{type(last_err).__name__}: {str(last_err)[:300]}"}
        self.failures.append(failure)
        return Reply(DEFER_FALLBACK,
                     {"provider_failure": failure["error"], "input_tokens": 0, "output_tokens": 0},
                     f"PROVIDER_FAILURE:{self.name}")


def validate_cli_manifest(manifest):
    """CLI-aware replacement for rev8.validate_pinned_live_manifest (same return shape).
    Requires >= 2 distinct CLI model families; forbids mock/REST mixing so the run is
    uniformly labeled as CLI-wrapper evidence."""
    if manifest.get("protocol_version") != "pieq-rev8-live-eval-v1":
        raise ValueError("Unsupported protocol_version")
    if manifest.get("run_kind") != "cli_wrapper_live_unpinned":
        raise ValueError("This validator only accepts cli_wrapper_live_unpinned manifests")
    instances = manifest.get("instances", [])
    if not instances:
        raise ValueError("No instances")
    families, providers = set(), set()
    for inst in instances:
        if inst.get("provider") not in CLI_FAMILIES:
            raise ValueError(f"Provider {inst.get('provider')!r} is not a known CLI provider")
        if not inst.get("model") or not inst.get("family"):
            raise ValueError("Each instance must declare model and family")
        families.add(inst["family"])
        providers.add(inst["provider"])
    if len(instances) > 1 and len(families) < 2:
        raise ValueError("Multi-agent CLI runs require >= 2 distinct CLI model families")
    return {
        "manifest_sha256": rev8._canonical_sha256(manifest),
        "provider_families": sorted(families),
        "providers": sorted(providers),
        "pinned_models": {
            i["agent_id"]: f"{i['provider']}:{i['model']}@{i['snapshot']}" for i in instances
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocols", default="orig_freeform,typed_interface,typed_plus_probes,full_pieq")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--trace", type=Path, default=None)
    parser.add_argument("--config-d", action="store_true",
                        help="Config D: single live model (agent_0 sonnet) with ALL telemetry handles, typed_plus_probes")
    args = parser.parse_args()

    manifest = json.loads(json.dumps(CLI_MANIFEST))
    cases = build_heldout_cases()
    if args.limit > 0:
        cases = cases[: args.limit]

    if args.config_d:
        protocols = ["typed_plus_probes"]
        manifest["run_id"] = "pieq-rev9-cli-live-unpinned-configD-v1"
        manifest["instances"] = [manifest["instances"][0]]  # agent_0 sonnet only
        cases = [
            dataclasses.replace(
                c,
                agent_allocation={
                    "agent_0": tuple(sorted({h for hs in c.agent_allocation.values() for h in hs}))
                },
            )
            for c in cases
        ]
        default_trace = REPO_ROOT / "results" / "rev9_cli_live_traces_configD.jsonl"
    else:
        protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
        tag = "_".join(protocols) if len(protocols) == 1 else "all"
        default_trace = REPO_ROOT / "results" / f"rev9_cli_live_traces_{tag}.jsonl"
    trace_path = args.trace or default_trace

    # Reuse the rev8 evaluation core verbatim; only the manifest validator differs.
    rev8.validate_pinned_live_manifest = validate_cli_manifest

    providers = {name: ResilientCLI(name) for name in {i["provider"] for i in manifest["instances"]}}
    for p in providers.values():
        p.preflight()

    started = datetime.now(timezone.utc).isoformat()
    summary = rev8.run_pinned_live_evaluation(
        cases=cases, manifest=manifest, trace_path=trace_path, protocols=protocols,
        providers_override=providers,
    )
    summary["schema_version"] = "pieq-rev9-cli-live-summary-v1"
    summary["run_kind"] = "cli_wrapper_live_unpinned"
    summary["evidence_category"] = (
        "genuine live LLM inference via locally authenticated CLI wrappers; "
        "model identities unpinned/partially unattested; sampling not enforced"
    )
    summary["started_utc"] = started
    summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
    summary["provider_failures"] = {n: p.failures for n, p in providers.items()}
    summary["config_d"] = bool(args.config_d)
    out = trace_path.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
