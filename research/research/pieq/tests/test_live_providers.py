import json

import pytest

from pieq.live_protocol import mock_manifest
from pieq.live_providers import RESTProvider, _NoCredentialRedirect


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, *args):
        return json.dumps(self.payload).encode()


def test_provider_does_not_substitute_requested_id_for_missing_returned_version(monkeypatch):
    payload = {"message": {"content": "{}"}}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    instance = {"model": "requested-id"}
    provider = RESTProvider("ollama")
    reply = provider.complete(instance, "prompt", mock_manifest())
    assert reply.model is None

    payload["modelVersion"] = "returned-version"
    reply = provider.complete(instance, "prompt", mock_manifest())
    assert reply.model == "returned-version"


@pytest.mark.parametrize("provider,key,override", [
    ("openai", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    ("gemini", "GEMINI_API_KEY", "GEMINI_BASE_URL"),
])
def test_credentialed_provider_rejects_plain_http_endpoint(monkeypatch, provider, key, override):
    monkeypatch.setenv(key, "test-secret")
    monkeypatch.setenv(override, "http://example.test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("insecure request was dispatched"),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        RESTProvider(provider).complete({"model": "test-model"}, "prompt", mock_manifest())


def test_credentialed_redirect_does_not_forward_authorization(monkeypatch):
    original = __import__("urllib.request", fromlist=["Request"]).Request(
        "https://api.example.test/v1/chat/completions",
        data=b"{}",
        headers={"Authorization": "Bearer sentinel-secret"},
    )
    handler = _NoCredentialRedirect()
    assert handler.redirect_request(
        original, None, 302, "Found", {}, "http://untrusted.example.test/collect",
    ) is None


def test_credentialed_provider_installs_no_redirect_handler(monkeypatch):
    handlers = []

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.openai.com/v1/chat/completions"
            assert request.get_header("Authorization") == "Bearer sentinel-secret"
            return FakeResponse({"choices": [{"message": {"content": "{}"}}], "model": "pinned"})

    def capture_opener(*args):
        handlers.extend(args)
        return FakeOpener()

    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-secret")
    monkeypatch.setattr("urllib.request.build_opener", capture_opener)
    RESTProvider("openai").complete(
        {"model": "pinned"}, "prompt", mock_manifest(),
    )
    assert any(isinstance(handler, _NoCredentialRedirect) for handler in handlers)


def test_local_openai_allows_http_sends_thinking_off_and_maps_usage(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["headers"] = dict(request.header_items())
        return FakeResponse({"choices": [{"message": {"content": "{}"}}],
                             "model": "Qwen/Qwen3-32B",
                             "usage": {"prompt_tokens": 7, "completion_tokens": 3}})

    monkeypatch.setenv("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:30000")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    reply = RESTProvider("local_openai").complete(
        {"model": "Qwen/Qwen3-32B", "supports_seed": True}, "prompt", mock_manifest())
    assert captured["url"] == "http://127.0.0.1:30000/v1/chat/completions"
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["body"]["seed"] == mock_manifest()["seed"]
    assert not any(key.lower() == "authorization" for key in captured["headers"])
    assert reply.usage["prompt_tokens"] == 7 and reply.model == "Qwen/Qwen3-32B"


def test_cli_provider_claude_parses_result_usage_and_canonical_model(monkeypatch):
    from pieq.live_providers import CLIProvider

    captured = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "is_error": False, "result": "{\"ok\":true}", "total_cost_usd": 0.0026,
            "usage": {"input_tokens": 2, "cache_creation_input_tokens": 20650,
                      "cache_read_input_tokens": 0, "output_tokens": 9},
            "modelUsage": {"claude-haiku-5-5": {}},
        })

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    reply = CLIProvider("claude_cli").complete(
        {"model": "claude-haiku-5-5", "supports_seed": False}, "prompt-body", mock_manifest())
    assert captured["argv"][0] == "claude" and "--max-turns" in captured["argv"]
    assert captured["kwargs"]["input"] == "prompt-body"
    assert "shell" not in captured["kwargs"]
    assert reply.text == "{\"ok\":true}"
    assert reply.usage["input_tokens"] == 20652 and reply.usage["output_tokens"] == 9
    assert reply.model == "claude-haiku-5-5"


def test_cli_provider_codex_reads_last_message_and_turn_usage(monkeypatch, tmp_path):
    from pieq.live_providers import CLIProvider

    provider = CLIProvider("codex_cli")

    def fake_run(argv, **kwargs):
        output_path = argv[argv.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("{\"ok\":true}")

        class FakeCompleted:
            returncode = 0
            stderr = ""
            stdout = "\n".join([
                json.dumps({"type": "thread.started", "thread_id": "t1"}),
                json.dumps({"type": "turn.completed",
                            "usage": {"input_tokens": 18055, "output_tokens": 9}}),
            ])
        return FakeCompleted()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    reply = provider.complete({"model": "openai.gpt-5.6-sol", "supports_seed": False},
                              "prompt-body", mock_manifest())
    assert reply.text == "{\"ok\":true}"
    assert reply.usage["input_tokens"] == 18055 and reply.usage["output_tokens"] == 9


def test_cli_provider_nonzero_exit_and_timeout_are_distinct(monkeypatch):
    import subprocess as sp
    from pieq.live_providers import CLIError, CLIProvider

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)

    class FakeFailure:
        returncode = 2
        stderr = "boom"
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: FakeFailure())
    with pytest.raises(CLIError):
        CLIProvider("claude_cli").complete({"model": "m", "supports_seed": False},
                                           "p", mock_manifest())

    def fake_timeout(argv, **kwargs):
        raise sp.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", fake_timeout)
    with pytest.raises(TimeoutError):
        CLIProvider("claude_cli").complete({"model": "m", "supports_seed": False},
                                           "p", mock_manifest())
