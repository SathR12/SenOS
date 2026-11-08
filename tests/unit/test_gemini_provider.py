import io
import json
import os
from urllib.error import HTTPError

import pytest

from senos.agents import gemini, provider
from senos.agents.environment import load_agent_env
from senos.agents.llm_tools import tool_definitions


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-test-key")
    return gemini.GeminiProvider()


def native(parts=None, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"role": "model", "parts": parts or [
        {"functionCall": {"name": "list_workloads", "args": {}, "id": "native-id"}, "thoughtSignature": "opaque-signature"}]}}]}


def test_native_calls_and_signatures_are_preserved(client, monkeypatch):
    requests = []
    def send(request):
        requests.append(request)
        return io.BytesIO(json.dumps(native()).encode())
    monkeypatch.setattr(gemini, "send_request", send)
    history = [{"role": "user", "content": "Prioritize web for ten minutes"}]
    response = client.respond(history, tool_definitions(), "system rules")
    item = response["output"][0]
    assert item["name"] == "list_workloads" and json.loads(item["arguments"]) == {}
    assert "private-gemini-test-key" not in requests[0].full_url
    assert requests[0].get_header("X-goog-api-key") == "private-gemini-test-key"
    assert requests[0].full_url.endswith("gemini-3.1-flash-lite:generateContent")
    body = json.loads(requests[0].data)
    assert "functionDeclarations" in body["tools"][0]
    assert "instructions" not in body and "input" not in body
    history.extend(response["output"])
    history.append({"type": "function_call_output", "call_id": item["call_id"], "output": '{"workloads": []}'})
    client.respond(history, tool_definitions(), "system rules")
    contents = json.loads(requests[1].data)["contents"]
    assert contents[1] == native()["candidates"][0]["content"]
    function_response = contents[2]["parts"][0]["functionResponse"]
    assert function_response == {"name": "list_workloads", "id": "native-id", "response": {"workloads": []}}


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503])
def test_gemini_errors_are_sanitized(client, monkeypatch, code):
    def fail(request):
        raise HTTPError(request.full_url, code, "private-gemini-test-key", {}, io.BytesIO(b"private request contents"))
    monkeypatch.setattr(gemini, "send_request", fail)
    with pytest.raises(provider.AgentError) as error:
        client.respond([], [], "rules")
    assert "Gemini" in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", json.dumps(native(finish="MAX_TOKENS")).encode(), b"x" * 1000001])
def test_invalid_or_incomplete_responses_are_rejected(client, monkeypatch, body):
    monkeypatch.setattr(gemini, "send_request", lambda request: io.BytesIO(body))
    with pytest.raises(provider.AgentError):
        client.respond([], [], "rules")


def test_env_loading_is_literal_allowlisted_and_environment_wins(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('export GEMINI_API_KEY="file-key" # comment\nSENOS_GEMINI_MODEL=gemini-test\nPATH=bad\nGOOGLE_API_KEY=\'$(never-execute)\'\n')
    monkeypatch.setenv("GEMINI_API_KEY", "environment-key")
    monkeypatch.delenv("SENOS_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    original_path = os.environ.get("PATH")
    load_agent_env(path)
    assert os.environ["GEMINI_API_KEY"] == "environment-key"
    assert os.environ["SENOS_GEMINI_MODEL"] == "gemini-test"
    assert os.environ["GOOGLE_API_KEY"] == "$(never-execute)"
    assert os.environ.get("PATH") == original_path


def test_malformed_env_never_echoes_secret_and_loads_nothing(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('GEMINI_API_KEY=first-value\nSENOS_GEMINI_MODEL="private-unclosed-secret\n')
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(provider.AgentError) as error:
        load_agent_env(path)
    assert "private" not in str(error.value)
    assert "GEMINI_API_KEY" not in os.environ


def test_provider_factory_prefers_gemini_and_explicit_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('GEMINI_API_KEY=local-key\n')
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "SENOS_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert provider.create_provider().name == "gemini"
    assert provider.create_provider(name="openai").name == "openai"
    with pytest.raises(provider.AgentError, match="Choose gemini or openai"):
        provider.create_provider(name="unknown")


def test_model_names_cannot_change_api_endpoint(client, monkeypatch):
    with pytest.raises(provider.AgentError, match="without URL paths"):
        gemini.GeminiProvider(model="../evil?key=anything")
