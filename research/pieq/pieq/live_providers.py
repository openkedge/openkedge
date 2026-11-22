"""Small REST and local-CLI adapters; credentials remain in the process environment, never traces."""
from dataclasses import dataclass
import json
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import urllib.request

_BASE_URLS = {
    "openai": ("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "anthropic": ("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "gemini": ("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
    "ollama": ("OLLAMA_BASE_URL", "http://localhost:11434"),
    # Any local OpenAI-compatible chat server (SGLang, vLLM, llama.cpp, LM Studio).
    "local_openai": ("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:30000"),
}
_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

CLI_PROVIDERS = {"claude_cli": "claude", "codex_cli": "codex"}


@dataclass
class Reply:
    text: str
    usage: dict
    model: str | None


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a provider credential to a redirected destination."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class RESTProvider:
    def __init__(self, provider):
        self.provider = provider

    def endpoint_base(self):
        if self.provider not in _BASE_URLS:
            raise ValueError("Unknown provider")
        key_name = _API_KEYS.get(self.provider)
        variable, default = _BASE_URLS[self.provider]
        base = os.getenv(variable, default).rstrip("/")
        endpoint = urlsplit(base)
        permitted = {"https"} if key_name else {"http", "https"}
        if (endpoint.scheme not in permitted or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query
                or endpoint.fragment):
            raise ValueError("Credentialed provider endpoints require HTTPS without userinfo, query, or fragment"
                             if key_name else "Invalid Ollama endpoint")
        return base

    def preflight(self):
        key_name = _API_KEYS.get(self.provider)
        if key_name and not os.environ.get(key_name):
            raise ValueError(f"Missing {key_name}")
        return self.endpoint_base()

    def complete(self, instance, prompt, manifest):
        provider = self.provider
        base = self.preflight()
        model = instance["model"]
        limit = manifest["max_output_tokens"]
        temp = manifest["temperature"]
        headers = {"Content-Type": "application/json"}
        if provider == "openai":
            headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
            url = base + "/chat/completions"
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": temp, "max_completion_tokens": limit}
            if instance.get("supports_seed"):
                body["seed"] = manifest["seed"]
        elif provider == "anthropic":
            headers.update({"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                            "anthropic-version": "2023-06-01"})
            url = base + "/v1/messages"
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": temp, "max_tokens": limit}
        elif provider == "gemini":
            headers["x-goog-api-key"] = os.environ["GEMINI_API_KEY"]
            url = base + f"/models/{model}:generateContent"
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": temp, "maxOutputTokens": limit}}
            if instance.get("supports_seed"):
                body["generationConfig"]["seed"] = manifest["seed"]
        elif provider == "ollama":
            url = base + "/api/chat"
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "stream": False, "options": {"temperature": temp, "num_predict": limit,
                                                "seed": manifest["seed"]}}
        elif provider == "local_openai":
            url = base + "/v1/chat/completions"
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": temp, "max_tokens": limit,
                    # Hybrid-reasoning chat templates (e.g. Qwen3) emit an unbounded
                    # <think> preamble otherwise; unknown keys are rejected loudly by
                    # strict servers rather than silently changing behavior.
                    "chat_template_kwargs": {"enable_thinking": False}}
            if instance.get("supports_seed"):
                body["seed"] = manifest["seed"]
        else:
            raise ValueError("Unknown provider")
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        if provider in _API_KEYS:
            # urllib's default redirect handler copies authorization headers even
            # across origins or down to HTTP; fail the call instead.
            response_context = urllib.request.build_opener(_NoCredentialRedirect()).open(
                request, timeout=manifest["timeout_seconds"],
            )
        else:
            response_context = urllib.request.urlopen(
                request, timeout=manifest["timeout_seconds"],
            )
        with response_context as response:
            result = json.load(response)
        if provider in {"openai", "local_openai"}:
            text = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})
        elif provider == "anthropic":
            text = "".join(block["text"] for block in result["content"] if block["type"] == "text")
            usage = result.get("usage", {})
        elif provider == "gemini":
            text = "".join(part.get("text", "") for part in result["candidates"][0]["content"]["parts"])
            usage = result.get("usageMetadata", {})
        else:
            text = result["message"]["content"]
            usage = {key: result[key] for key in ("prompt_eval_count", "eval_count") if key in result}
        returned_model = result.get("model") or result.get("modelVersion")
        return Reply(text, usage, returned_model if isinstance(returned_model, str) else None)


class CLIError(Exception):
    """A local agent CLI exited abnormally or returned an unusable payload."""


class CLIProvider:
    """Adapter over locally authenticated agent CLIs (claude -p, codex exec).

    Both run as argv subprocesses (never a shell) in an empty scratch directory
    with MCP servers disabled and tools disallowed/sandboxed read-only, so each
    logical request is a single prompt->reply completion. The CLI manages its
    own provider credentials; none enter this process or the trace.
    """

    _CLAUDE_DISALLOWED_TOOLS = ("Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,"
                                "Agent,Task,Skill,TodoWrite,NotebookEdit")

    def __init__(self, provider):
        if provider not in CLI_PROVIDERS:
            raise ValueError("Unknown CLI provider")
        self.provider = provider
        self.binary = CLI_PROVIDERS[provider]
        self._workdir = None

    def endpoint_base(self):
        return f"cli://{self.binary}"

    def preflight(self):
        if shutil.which(self.binary) is None:
            raise ValueError(f"Missing {self.binary} executable on PATH")
        return self.endpoint_base()

    def _scratch_dir(self):
        if self._workdir is None or not os.path.isdir(self._workdir):
            self._workdir = tempfile.mkdtemp(prefix=f"pieq-{self.binary}-")
        return self._workdir

    def _run(self, argv, prompt, timeout):
        try:
            return subprocess.run(argv, input=prompt, text=True, capture_output=True,
                                  timeout=timeout, cwd=self._scratch_dir(), check=False)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"{self.binary} exceeded {timeout}s") from error

    def complete(self, instance, prompt, manifest):
        self.preflight()
        timeout = manifest["timeout_seconds"]
        if self.provider == "claude_cli":
            argv = [self.binary, "-p", "--model", instance["model"],
                    "--output-format", "json", "--max-turns", "1",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--disallowedTools", self._CLAUDE_DISALLOWED_TOOLS]
            completed = self._run(argv, prompt, timeout)
            if completed.returncode != 0:
                raise CLIError(f"claude exit {completed.returncode}: {completed.stderr[-500:]}")
            try:
                payload = json.loads(completed.stdout)
            except ValueError as error:
                raise CLIError("claude CLI emitted non-JSON output") from error
            if not isinstance(payload, dict) or payload.get("is_error") or not isinstance(payload.get("result"), str):
                raise CLIError(f"claude CLI error payload: {str(payload)[:300]}")
            raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            counts = {key: raw.get(key) for key in ("input_tokens", "cache_creation_input_tokens",
                                                    "cache_read_input_tokens", "output_tokens")}
            usage = {"cli_usage": raw, "reported_cost_usd": payload.get("total_cost_usd")}
            if all(type(counts[key]) is int and counts[key] >= 0 for key in counts):
                usage["input_tokens"] = (counts["input_tokens"] + counts["cache_creation_input_tokens"]
                                         + counts["cache_read_input_tokens"])
                usage["output_tokens"] = counts["output_tokens"]
            model_usage = payload.get("modelUsage")
            model = None
            if isinstance(model_usage, dict) and len(model_usage) == 1:
                model = next(iter(model_usage))
            return Reply(payload["result"], usage, model if isinstance(model, str) else None)
        # codex_cli
        with tempfile.NamedTemporaryFile("r", dir=self._scratch_dir(), prefix="last-",
                                         suffix=".txt", delete=False) as handle:
            last_message_path = handle.name
        try:
            argv = [self.binary, "exec", "--ephemeral", "--skip-git-repo-check",
                    "-s", "read-only", "--color", "never", "--json",
                    "-c", "mcp_servers={}", "-m", instance["model"],
                    "-o", last_message_path, "-"]
            completed = self._run(argv, prompt, timeout)
            if completed.returncode != 0:
                raise CLIError(f"codex exit {completed.returncode}: {completed.stderr[-500:]}")
            usage = {}
            model = None
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_usage = event.get("usage")
                if isinstance(event_usage, dict):
                    usage = {"cli_usage": event_usage}
                    counts = {key: event_usage.get(key) for key in ("input_tokens", "output_tokens")}
                    if all(type(value) is int and value >= 0 for value in counts.values()):
                        usage.update(counts)
                if isinstance(event.get("model"), str):
                    model = event["model"]
            with open(last_message_path, encoding="utf-8") as handle:
                text = handle.read()
            if not text.strip():
                raise CLIError("codex produced no final message")
            return Reply(text, usage, model)
        finally:
            try:
                os.unlink(last_message_path)
            except OSError:
                pass


def make_provider(name):
    if name == "mock":
        return MockProvider()
    if name in CLI_PROVIDERS:
        return CLIProvider(name)
    return RESTProvider(name)


class MockProvider:
    """Deterministic scripted proposer using only the exact public prompt.

    This is a software pilot, not simulated evidence of LLM competence.
    """
    def complete(self, instance, prompt, manifest):
        public = json.loads(prompt)["evidence"]
        observations = public["observations"]
        proofs = []
        for rule in public["M_sys"]:
            supporting = [obs for obs in observations if obs["proposition"] in rule["antecedents"]]
            if {obs["proposition"] for obs in supporting} == set(rule["antecedents"]):
                proofs.append({"consequent": rule["consequent"], "rule_id": rule["rule_id"],
                               "env_bitmask": sum(1 << obs["bit_index"] for obs in supporting)})
        actions = sorted({proof["consequent"][4:] for proof in proofs
                          if proof["consequent"].startswith("act:")})
        refuted_worlds = sorted({proof["consequent"][7:] for proof in proofs
                                 if proof["consequent"].startswith("refute:")})
        return Reply(json.dumps({"action": actions[0] if actions else "defer",
                                 "confidence": 0.5, "rationale": "Deterministic software pilot.",
                                 "citations": [obs["assumption_id"] for obs in observations],
                                 "proofs": proofs, "world_refutations": refuted_worlds}, sort_keys=True),
                     {"input_tokens": 0, "output_tokens": 0}, instance["model"])
