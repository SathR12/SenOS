"""Native Gemini generateContent function-calling adapter."""

from copy import deepcopy
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

from .provider import AgentError, send_request


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, model: str | None = None):
        self.model = (model or os.environ.get("SENOS_GEMINI_MODEL", "gemini-3.1-flash-lite")).strip()
        self._key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        if not self._key:
            raise AgentError("Set GEMINI_API_KEY in your local .env or environment")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.model):
            raise AgentError("Use a Gemini model name without URL paths or query parameters")

    def _contents(self, history: list[dict]) -> list[dict]:
        contents = []
        calls = {}
        for item in history:
            if item.get("role") == "user":
                contents.append({"role": "user", "parts": [{"text": item["content"]}]})
            elif item.get("type") == "function_call":
                native = item.get("gemini_content")
                if not isinstance(native, dict):
                    raise AgentError("Missing Gemini tool history; cannot continue this run")
                # Preserve complete native content, including thought signatures, verbatim.
                contents.append(deepcopy(native))
                native_call = next(part["functionCall"] for part in native["parts"] if "functionCall" in part)
                calls[item["call_id"]] = native_call
            elif item.get("type") == "function_call_output":
                native_call = calls.get(item["call_id"])
                if native_call is None:
                    raise AgentError("Gemini tool result has no matching call")
                response = {"name": native_call["name"], "response": json.loads(item["output"])}
                if native_call.get("id"):
                    response["id"] = native_call["id"]
                contents.append({"role": "user", "parts": [{"functionResponse": response}]})
            else:
                raise AgentError("Unsupported Gemini conversation item")
        return contents

    def respond(self, history: list[dict], tools: list[dict], instructions: str) -> dict:
        declarations = []
        for tool in tools:
            declaration = {"name": tool["name"], "description": tool["description"]}
            if tool["parameters"].get("properties"):
                declaration["parametersJsonSchema"] = tool["parameters"]
            declarations.append(declaration)
        payload = {"systemInstruction": {"parts": [{"text": instructions}]},
            "contents": self._contents(history), "tools": [{"functionDeclarations": declarations}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": {"maxOutputTokens": 4096, "candidateCount": 1}}
        request = Request(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": self._key}, method="POST")
        try:
            # Model calls have no resource side effects. Retry a transient transport
            # failure once; the executor still checks the original deadline.
            for attempt in range(2):
                try:
                    with send_request(request) as response:
                        raw = response.read(1_000_001)
                    break
                except HTTPError:
                    raise
                except (URLError, OSError, TimeoutError):
                    if attempt:
                        raise
            if len(raw) > 1_000_000:
                raise AgentError("Gemini response exceeded the supported size")
            result = json.loads(raw)
        except HTTPError as error:
            error.close()
            messages = {400: "Gemini rejected the request; check the model name, API key, and supported tool settings",
                401: "Gemini authentication failed; check GEMINI_API_KEY",
                403: "Gemini refused access; check API-key restrictions and project permissions",
                404: "Gemini model is unavailable; choose an accessible model with --model",
                429: "Gemini quota or rate limit reached; check your project quota and retry later",
                503: "Gemini is temporarily unavailable or overloaded; retry later or choose another accessible model"}
            raise AgentError(messages.get(error.code, f"Gemini request failed (HTTP {error.code})")) from None
        except (URLError, OSError, TimeoutError):
            raise AgentError("Cannot reach Gemini within the timeout; check connectivity and retry") from None
        except (ValueError, UnicodeError):
            raise AgentError("Gemini returned invalid JSON") from None
        if not isinstance(result, dict):
            raise AgentError("Gemini returned an invalid response")
        candidates = result.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
            raise AgentError("Gemini returned no usable candidate; request may have been blocked")
        candidate = candidates[0]
        if candidate.get("finishReason") != "STOP":
            raise AgentError("Gemini response was blocked or incomplete; no policy was applied")
        content = candidate.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise AgentError("Gemini returned invalid tool content")
        output = []
        for part in content["parts"]:
            if not isinstance(part, dict):
                raise AgentError("Gemini returned invalid tool parts")
            if "functionCall" not in part:
                continue
            call = part["functionCall"]
            if not isinstance(call, dict) or not isinstance(call.get("args", {}), dict):
                raise AgentError("Gemini returned invalid function arguments")
            output.append({"type": "function_call", "name": call.get("name"),
                "arguments": json.dumps(call.get("args", {})), "call_id": uuid4().hex,
                "gemini_content": deepcopy(content)})
        return {"status": "completed", "output": output}
