import io
import json
from urllib.error import HTTPError, URLError

import pytest

from senos.agents import provider


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-print")
    return provider.OpenAIProvider(model="test-model")


def test_request_uses_documented_responses_interface_and_no_storage(client, monkeypatch):
    captured = []
    def send(request):
        captured.append(request)
        return io.BytesIO(json.dumps({"status": "completed", "output": []}).encode())
    monkeypatch.setattr(provider, "send_request", send)
    assert client.respond([{"role": "user", "content": "test"}], [], "instructions")["status"] == "completed"
    request = captured[0]
    assert request.full_url == "https://api.openai.com/v1/responses"
    payload = json.loads(request.data)
    assert payload["store"] is False and payload["parallel_tool_calls"] is False
    assert payload["tool_choice"] == "required" and payload["max_output_tokens"] == 2048
    assert payload["model"] == "test-model"


@pytest.mark.parametrize("code,message", [(401, "authentication"), (403, "permissions"), (429, "quota"), (500, "HTTP 500"), (302, "HTTP 302")])
def test_api_failures_do_not_echo_secrets_or_provider_bodies(client, monkeypatch, code, message):
    def fail(request):
        raise HTTPError(request.full_url, code, "test-secret-never-print", {}, io.BytesIO(b"private user prompt"))
    monkeypatch.setattr(provider, "send_request", fail)
    with pytest.raises(provider.AgentError) as raised:
        client.respond([], [], "test")
    assert message in str(raised.value)
    assert "test-secret" not in str(raised.value) and "private user prompt" not in str(raised.value)


@pytest.mark.parametrize("failure", [URLError("secret"), TimeoutError("secret"), OSError("secret")])
def test_connection_failures_are_readable(client, monkeypatch, failure):
    def fail(request):
        raise failure
    monkeypatch.setattr(provider, "send_request", fail)
    with pytest.raises(provider.AgentError, match="Cannot reach OpenAI"):
        client.respond([], [], "test")


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"status":"incomplete"}', b"x" * 1000001])
def test_bad_responses_are_rejected(client, monkeypatch, body):
    monkeypatch.setattr(provider, "send_request", lambda request: io.BytesIO(body))
    with pytest.raises(provider.AgentError):
        client.respond([], [], "test")


def test_credentials_and_model_are_environment_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(provider.AgentError, match="OPENAI_API_KEY"):
        provider.OpenAIProvider()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SENOS_OPENAI_MODEL", "configured-model")
    assert provider.OpenAIProvider().model == "configured-model"
    assert provider.OpenAIProvider(model="override").model == "override"


def test_redirects_are_disabled():
    assert provider.NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://example.com") is None
