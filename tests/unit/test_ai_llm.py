"""The one model adapter: structured output, one retry, provider fallback, redaction (SE-02)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.ai.llm import (
    AnthropicProvider,
    GeminiProvider,
    InvalidModelOutput,
    LLMAdapter,
    LLMError,
    LLMUnavailable,
    ProviderDown,
    redact,
)


class Verdict(BaseModel):
    matches: bool
    reason: str


class Scripted:
    def __init__(self, name: str, *answers):
        self.name = name
        self.answers = list(answers)
        self.calls: list[dict] = []

    def generate(self, schema, **kw):
        self.calls.append(kw)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


OK = Verdict(matches=True, reason="ok")


def test_returns_the_validated_model_and_records_the_provider():
    adapter = LLMAdapter(Scripted("anthropic", OK), Scripted("gemini"))
    assert adapter.complete_structured(schema=Verdict, system="s", user="u") == OK
    assert adapter.last_provider == "anthropic"


def test_invalid_output_is_retried_once_on_the_same_provider():
    primary = Scripted("anthropic", InvalidModelOutput("bad"), OK)
    adapter = LLMAdapter(primary)
    assert adapter.complete_structured(schema=Verdict, system="s", user="u") == OK
    assert len(primary.calls) == 2


def test_invalid_output_twice_raises_so_the_caller_uses_its_fallback():
    adapter = LLMAdapter(Scripted("anthropic", InvalidModelOutput("a"), InvalidModelOutput("b")))
    with pytest.raises(InvalidModelOutput):
        adapter.complete_structured(schema=Verdict, system="s", user="u")


def test_budget_or_outage_on_the_main_provider_falls_back_once():
    fallback = Scripted("gemini", OK)
    adapter = LLMAdapter(Scripted("anthropic", ProviderDown("credit")), fallback)
    assert adapter.complete_structured(schema=Verdict, system="s", user="u") == OK
    assert adapter.last_provider == "gemini"


def test_every_provider_down_is_unavailable():
    adapter = LLMAdapter(
        Scripted("anthropic", ProviderDown("x")), Scripted("gemini", ProviderDown("y"))
    )
    with pytest.raises(LLMUnavailable):
        adapter.complete_structured(schema=Verdict, system="s", user="u")


def test_SE_02_personal_fields_are_redacted_before_the_call():
    primary = Scripted("anthropic", OK)
    LLMAdapter(primary).complete_structured(
        schema=Verdict,
        system="s",
        user="Sam sam@example.com, +44 7700 900123, Flat 4, N1 9GU; US 94107-1234",
    )
    sent = primary.calls[0]["user"]
    assert "sam@example.com" not in sent and "7700" not in sent
    assert "N1 9GU" not in sent and "N1" in sent
    assert "94107-1234" not in sent


def test_SE_02_redact_keeps_order_amounts():
    assert redact("Order ORD12345 for £60.00") == "Order ORD12345 for £60.00"


def test_opus_is_never_used_in_the_app():
    with pytest.raises(LLMError):
        AnthropicProvider("sk-test", "claude-opus-5-5")


def _anthropic_message(text: str, stop: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_anthropic_structured_output_over_the_wire(httpserver):
    httpserver.expect_request("/v1/messages", method="POST").respond_with_json(
        _anthropic_message(json.dumps({"matches": False, "reason": "blue, not black"}))
    )
    provider = AnthropicProvider(
        "sk-test", "claude-sonnet-5-5", base_url=httpserver.url_for("").rstrip("/")
    )
    out = provider.generate(Verdict, system="s", user="u", temperature=0, images=[])
    assert out == Verdict(matches=False, reason="blue, not black")
    [(request, _)] = httpserver.log
    body = request.get_json()
    assert body["model"] == "claude-sonnet-5-5"
    assert "temperature" not in body and "tool_choice" not in body  # 400s on Sonnet 5.5
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "sk-test" in (request.headers.get("x-api-key") or request.headers["Authorization"])


def test_anthropic_rate_limit_is_provider_down(httpserver):
    httpserver.expect_request("/v1/messages").respond_with_json(
        {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}, status=429
    )
    provider = AnthropicProvider(
        "sk-test", "claude-sonnet-5-5", base_url=httpserver.url_for("").rstrip("/")
    )
    provider.client = provider.client.with_options(max_retries=0)
    with pytest.raises(ProviderDown):
        provider.generate(Verdict, system="s", user="u", temperature=0, images=[])


def test_gemini_structured_output_over_the_wire(httpserver):
    httpserver.expect_request(
        "/v1beta/models/gemini-x:generateContent", method="POST"
    ).respond_with_json(
        {"candidates": [{"content": {"parts": [{"text": '{"matches": true, "reason": "ok"}'}]}}]}
    )
    provider = GeminiProvider("g-key", "gemini-x", base_url=httpserver.url_for(""))
    assert provider.generate(Verdict, system="s", user="u", temperature=0, images=[]) == OK
    [(request, _)] = httpserver.log
    config = request.get_json()["generationConfig"]
    assert config["temperature"] == 0 and config["responseMimeType"] == "application/json"
    assert request.headers["x-goog-api-key"] == "g-key"


@pytest.mark.parametrize(("status", "error"), [(429, ProviderDown), (503, ProviderDown)])
def test_gemini_quota_and_outage_are_provider_down(httpserver, status, error):
    httpserver.expect_request("/v1beta/models/gemini-x:generateContent").respond_with_data(
        "no", status=status
    )
    provider = GeminiProvider("g-key", "gemini-x", base_url=httpserver.url_for(""))
    with pytest.raises(error):
        provider.generate(Verdict, system="s", user="u", temperature=0, images=[])


def test_gemini_invalid_json_is_invalid_output(httpserver):
    httpserver.expect_request("/v1beta/models/gemini-x:generateContent").respond_with_json(
        {"candidates": [{"content": {"parts": [{"text": '{"matches": "maybe"}'}]}}]}
    )
    provider = GeminiProvider("g-key", "gemini-x", base_url=httpserver.url_for(""))
    with pytest.raises(InvalidModelOutput):
        provider.generate(Verdict, system="s", user="u", temperature=0, images=[])
