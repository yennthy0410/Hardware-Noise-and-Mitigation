"""Optional, event-driven AI diagnostics with no research or IBM write tools.

Provider failures are deliberately converted to an unavailable advisory result
so monitoring and collection can continue without an AI service.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import json
import math
import os
import re
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MAX_COMPLETION_TOKENS = 256
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 16384
MAX_SUMMARY_CHARS = 320
MAX_DIAGNOSIS_CHARS = 800
MAX_SUGGESTION_CHARS = 240
MAX_PROVIDER_TIMEOUT_SECONDS = 20.0


def _bounded_text(value, limit: int) -> str:
    text = str(value)
    text = re.sub(r"(?i)crn:v1:[A-Za-z0-9:._/-]+", "[REDACTED_CRN]", text)
    text = re.sub(r"(?i)(api[_ -]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    return text[:limit]


def _safe_event(event: dict) -> dict:
    """Allowlist tiny event metadata; summarize arrays without sending values."""
    clean = {}
    for key in ("campaign_id", "sequence_no", "event_class", "treatment_id", "local_state", "pub_index"):
        if key not in event:
            continue
        value = event[key]
        if key == "sequence_no" or key == "pub_index":
            if isinstance(value, int) and not isinstance(value, bool):
                clean[key] = value
        elif value is not None:
            clean[key] = _bounded_text(value, 80)
    summary = event.get("safe_summary")
    if summary is not None:
        clean["safe_summary"] = _bounded_text(summary, MAX_SUMMARY_CHARS)

    expectations = event.get("expectation_values")
    if isinstance(expectations, (list, tuple)):
        invalid = []
        for index, value in enumerate(expectations[:64]):
            try:
                if isinstance(value, bool) or not math.isfinite(float(value)):
                    invalid.append(index)
            except (TypeError, ValueError, OverflowError):
                invalid.append(index)
        clean["expectation_count"] = len(expectations)
        clean["invalid_expectation_indexes"] = invalid
    deviations = event.get("standard_deviations")
    if isinstance(deviations, (list, tuple)):
        invalid = []
        for index, value in enumerate(deviations[:64]):
            try:
                if isinstance(value, bool) or not math.isfinite(float(value)):
                    invalid.append(index)
            except (TypeError, ValueError, OverflowError):
                invalid.append(index)
        clean["uncertainty_count"] = len(deviations)
        clean["invalid_uncertainty_indexes"] = invalid
    # request_diagnosis sanitizes before invoking the configured provider, and
    # OpenAICompatibleProvider sanitizes again at its transport boundary. Carry
    # only these already-reduced diagnostics through the second pass.
    for count_key in ("expectation_count", "uncertainty_count"):
        value = event.get(count_key)
        if count_key not in clean and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 64:
            clean[count_key] = value
    for index_key in ("invalid_expectation_indexes", "invalid_uncertainty_indexes"):
        values = event.get(index_key)
        if index_key not in clean and isinstance(values, list):
            indexes = [value for value in values[:64] if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 64]
            clean[index_key] = indexes
    return clean


def _safe_text(value) -> str:
    return _bounded_text(value, MAX_DIAGNOSIS_CHARS)


def _safe_provider_identifier(value) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", text):
        return None
    return text


def _safe_provider_message(value) -> str:
    """Keep useful provider wording while stripping IDs, secrets, and paths."""
    text = _bounded_text(value, 1200)
    patterns = (
        (r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_KEY]"),
        (r"(?i)https?://[^\s\]\[()<>]+", "[REDACTED_URL]"),
        (r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED_EMAIL]"),
        (r"(?i)\b(?:account|organization|project|tenant|subscription|user)[ _-]?(?:id|name|key)?\s*[:=]\s*[^\s,;]+", "[REDACTED_ACCOUNT_FIELD]"),
        (r"(?i)\b(?:org|proj|req|user|acct|team|sess|job)[_-][A-Za-z0-9_-]{6,}\b", "[REDACTED_ID]"),
        (r"(?i)(?:[A-Z]:\\|\\\\)[^\s,;]+", "[REDACTED_PATH]"),
        (r"(?<!\w)/(?:[^\s,;]+/)*[^\s,;]+", "[REDACTED_PATH]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text[:500]


def _http_category(status: int) -> str:
    return {
        400: "bad_request",
        401: "authentication",
        403: "permission",
        404: "not_found",
        429: "rate_limit_or_quota",
    }.get(status, "provider_server_error" if 500 <= status <= 599 else "http_error")


class AIProviderHTTPError(RuntimeError):
    """Sanitized provider HTTP failure; contains no URL, headers, or raw body."""
    def __init__(self, status_code: int, provider_type: str | None,
                 provider_code: str | None, message: str):
        self.status_code = int(status_code)
        self.category = _http_category(self.status_code)
        self.provider_type = provider_type
        self.provider_code = provider_code
        self.safe_message = _safe_provider_message(message)
        super().__init__(f"AI provider HTTP {self.status_code} ({self.category})")

    def public_details(self) -> dict:
        return {"status_code": self.status_code, "category": self.category,
                "provider_error_type": self.provider_type,
                "provider_error_code": self.provider_code,
                "message": self.safe_message}


def _parse_http_error(error: HTTPError) -> AIProviderHTTPError:
    provider_type = provider_code = None
    message = "Provider returned an HTTP error without a readable message."
    try:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) <= MAX_RESPONSE_BYTES:
            payload = json.loads(raw.decode("utf-8"))
            detail = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(detail, dict):
                provider_type = _safe_provider_identifier(detail.get("type"))
                provider_code = _safe_provider_identifier(detail.get("code"))
                if isinstance(detail.get("message"), str):
                    message = detail["message"]
    except Exception:
        # Do not include parser/HTTP exception text because it can contain URLs
        # or transport details. Status remains useful on its own.
        pass
    finally:
        try:
            error.close()
        except Exception:
            pass
    return AIProviderHTTPError(error.code, provider_type, provider_code, message)


def _response_schema() -> dict:
    return {
        "name": "vqe_diagnostic_advice",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "diagnosis": {"type": "string"},
                "safe_recovery_suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["diagnosis", "safe_recovery_suggestions"],
            "additionalProperties": False,
        },
    }


class OpenAICompatibleProvider:
    """JSON HTTPS diagnostics client; no tool execution, file access, or IBM client."""
    def __init__(self, environ=None, timeout: float = 12.0, opener=None):
        env = os.environ if environ is None else environ
        self.api_key = env.get("OPENAI_API_KEY", "")
        self.base_url = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = env.get("OPENAI_MODEL", "gpt-4.1-mini")
        self.timeout = min(max(float(timeout),0.1),MAX_PROVIDER_TIMEOUT_SECONDS)
        self._urlopen = opener or urlopen

    @property
    def local_ollama(self) -> bool:
        try:
            parsed=urlparse(self.base_url)
            return parsed.scheme=="http" and parsed.hostname in ("127.0.0.1","localhost","::1") and parsed.port in (None,11434)
        except ValueError:
            return False

    @property
    def configured(self) -> bool:
        # Ollama's local OpenAI-compatible endpoint does not need a user secret.
        # A fixed ignored compatibility token is sent instead; any configured
        # OPENAI_API_KEY is deliberately never forwarded to localhost.
        return bool(self.model.strip()) and (self.local_ollama or bool(self.api_key.strip()))

    def _payload(self, event: dict) -> dict:
        payload={
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": (
                    "Diagnose this sanitized monitoring event. Return JSON matching the response schema. "
                    "Suggestions are advisory only. Never request credentials, change scientific inputs, "
                    "modify registries, or submit/cancel jobs.")},
                {"role": "user", "content": json.dumps(_safe_event(event), ensure_ascii=False)},
            ],
            "response_format": {"type": "json_schema", "json_schema": _response_schema()},
        }
        if self.local_ollama:
            # Ollama documents max_tokens (not max_completion_tokens) for
            # /v1/chat/completions and does not implement OpenAI's n field.
            payload["max_tokens"]=MAX_COMPLETION_TOKENS
        else:
            payload["n"]=1
            payload["max_completion_tokens"]=MAX_COMPLETION_TOKENS
        return payload

    def diagnose(self, event: dict) -> dict:
        payload = self._payload(event)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("Sanitized AI diagnostic request exceeded the 4 KB limit")
        auth_value="ollama" if self.local_ollama else self.api_key
        request = Request(self.base_url + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + auth_value,
                     "Content-Type": "application/json"}, method="POST")
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(MAX_RESPONSE_BYTES + 1).decode("utf-8"))
        except HTTPError as exc:
            raise _parse_http_error(exc) from None
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("AI diagnostic response exceeded the 16 KB limit")
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        if not isinstance(result, dict) or not isinstance(result.get("diagnosis"), str):
            raise ValueError("AI provider response did not match the diagnostics schema")
        suggestions = result.get("safe_recovery_suggestions")
        if not isinstance(suggestions, list) or any(not isinstance(item, str) for item in suggestions):
            raise ValueError("AI provider suggestions did not match the diagnostics schema")
        return {"diagnosis": _safe_text(result["diagnosis"]),
                "safe_recovery_suggestions": [_bounded_text(item, MAX_SUGGESTION_CHARS) for item in suggestions[:8]],
                "ai_actions_executed": False}


def request_diagnosis(event: dict, provider=None) -> dict:
    """Return safe status or advisory text; errors never escape into monitoring."""
    client = provider or OpenAICompatibleProvider()
    if not getattr(client, "configured", False):
        return {"available": False, "reason": "AI provider is not configured", "ai_actions_executed": False}
    try:
        return {"available": True, **client.diagnose(_safe_event(event))}
    except AIProviderHTTPError as exc:
        return {"available": False, "reason": f"AI provider HTTP {exc.status_code} ({exc.category})",
                "http_error": exc.public_details(), "ai_actions_executed": False}
    except Exception as exc:
        # Do not include provider exception text: some SDK/network exceptions may
        # contain request URLs, headers, or provider/account identifiers.
        return {"available": False, "reason": "AI provider unavailable: " + type(exc).__name__,
                "ai_actions_executed": False}


def mock_connectivity_probe() -> dict:
    """Exercise the real request builder with a mocked HTTP response only."""
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit=-1):
            return json.dumps({"choices": [{"message": {"content": json.dumps({
                "diagnosis": "Synthetic NaN detected at expectation index 3.",
                "safe_recovery_suggestions": ["Revalidate the archived PUB; do not resubmit."]})}}]}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    provider = OpenAICompatibleProvider(
        environ={"OPENAI_API_KEY": "MOCK_ONLY_DO_NOT_USE", "OPENAI_MODEL": "gpt-4.1-mini"},
        opener=fake_urlopen)
    result = request_diagnosis({
        "campaign_id": "synthetic-ai-probe", "sequence_no": 1,
        "event_class": "SYNTHETIC_NONFINITE_PUB", "treatment_id": "M5_DD_ZNE",
        "local_state": "MOCK", "pub_index": 1,
        "expectation_values": [0.1, 0.2, 0.3, float("nan"), 0.5, 0.6, 0.7],
        "standard_deviations": [0.01, 0.02, 0.03, float("nan"), 0.05, 0.06, 0.07],
        "safe_summary": "Synthetic event only; not derived from a real IBM archive."}, provider=provider)
    request_body = json.loads(captured["body"])
    user_content = request_body["messages"][1]["content"]
    sent = json.loads(user_content)
    if sent.get("invalid_expectation_indexes") != [3] or "expectation_values" in sent:
        raise AssertionError("Mock probe did not sanitize the synthetic expectation vector")
    if b"MOCK_ONLY_DO_NOT_USE" in captured["body"] or b"nan" in captured["body"].lower():
        raise AssertionError("Mock probe request leaked credential marker or raw NaN vector")
    return {"mock_only": True, "endpoint_path": "/chat/completions",
        "model": request_body["model"], "request_bytes": len(captured["body"]),
        "max_completion_tokens": request_body["max_completion_tokens"],
        "response_format": request_body["response_format"]["type"],
        "event_sent": sent, "authorization_header_present": "Authorization" in captured["headers"],
        "credential_value_printed": False, "diagnosis": result,
        "ai_actions_executed": False, "network_used": False}
