"""EX-05: every runtime model call is bounded, with no hidden SDK retries.

The critique (Nov 5) found a buyer's "Not here" tap could wait about three minutes in the worst
case: 60 seconds per Anthropic attempt, one hidden SDK retry, then 60 seconds on Gemini.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai import llm
from app.ai.llm import (
    DEFAULT_TIMEOUT_S,
    AnthropicProvider,
    GeminiProvider,
    LLMAdapter,
    LLMUnavailable,
    ProviderDown,
    adapter_from_env,
    timeout_from_env,
)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "gemini")


def test_EX_05_runtime_providers_use_the_configured_bound(keys, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "12")
    adapter = adapter_from_env()
    assert adapter is not None
    primary, fallback = adapter.primary, adapter.fallback
    assert isinstance(primary, AnthropicProvider) and isinstance(fallback, GeminiProvider)
    assert primary.client.timeout == 12.0
    assert primary.client.max_retries == 0  # the adapter's fallback is the only retry
    assert fallback.http.timeout == httpx.Timeout(12.0)


def test_EX_05_the_default_bound_is_twenty_seconds(keys, monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)
    assert timeout_from_env() == DEFAULT_TIMEOUT_S == 20.0


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
def test_EX_05_a_bad_value_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("LLM_TIMEOUT_S", raw)
    assert timeout_from_env() == DEFAULT_TIMEOUT_S


class _Slow:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def generate(self, schema, **_):
        self.calls += 1
        raise ProviderDown(f"{self.name} timed out")


def test_EX_05_a_timeout_tries_the_fallback_once_then_gives_up(monkeypatch):
    primary, fallback = _Slow("anthropic"), _Slow("gemini")
    with pytest.raises(LLMUnavailable):
        LLMAdapter(primary, fallback).complete_structured(schema=dict, system="s", user="u")
    assert (primary.calls, fallback.calls) == (1, 1)  # two bounded calls, no more


def test_EX_05_an_anthropic_timeout_counts_as_the_provider_being_down(monkeypatch):
    provider = AnthropicProvider("test-not-a-key", "claude-sonnet-5-5", timeout_s=0.01)
    a = provider._anthropic

    def boom(**_):
        raise a.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.test"))

    monkeypatch.setattr(provider.client.messages, "parse", boom)
    with pytest.raises(ProviderDown):
        provider.generate(dict, system="s", user="u", temperature=0.0, images=[])
    assert llm.DEFAULT_TIMEOUT_S == 20.0
