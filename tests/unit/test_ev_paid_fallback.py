"""Evals: the paid key is an opt-in, capped fallback for a free-tier stop, never the main model."""

from __future__ import annotations

import pytest

from app.ai.llm import (
    AnthropicProvider,
    LLMAdapter,
    LLMError,
    LLMUnavailable,
    ProviderDown,
    provider_adapter,
)
from app.ai.schemas import PolicyAnswer
from evals import recording
from evals.recording import (
    DEFAULT_PAID_PROMPT_LIMIT,
    PaidBudget,
    PaidFallback,
    PaidKeyRefused,
    Recorder,
    eval_model,
    paid_prompt_limit,
)


class Provider:
    def __init__(self, name: str, model: str, *, down: bool = False) -> None:
        self.name, self.model, self.down, self.calls = name, model, down, 0

    def generate(self, schema, **_):
        self.calls += 1
        if self.down:
            raise ProviderDown(f"{self.name} HTTP 429")
        return PolicyAnswer(answer=f"from {self.name}")


def ask(model) -> str:
    return model.complete_structured(schema=PolicyAnswer, system="s", user="u").answer


@pytest.fixture(autouse=True)
def fresh_run_budget(monkeypatch):
    monkeypatch.setattr(recording, "_run_budget", None)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("EVAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("EVAL_LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.delenv("EVAL_PAID_PROMPT_LIMIT", raising=False)


def test_EV_no_opt_in_means_the_free_provider_only(keys):
    model = eval_model()
    assert isinstance(model, LLMAdapter) and model.fallback is None
    assert model.primary.name == "gemini"


def test_EV_opt_in_wraps_the_free_provider_with_the_paid_one(keys, monkeypatch):
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "anthropic")
    model = eval_model()
    assert isinstance(model, PaidFallback)
    assert model.free.primary.name == "gemini" and model.paid.primary.name == "anthropic"
    assert model.budget.limit == DEFAULT_PAID_PROMPT_LIMIT


def test_EV_every_eval_model_in_a_run_shares_one_paid_budget(keys, monkeypatch):
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "anthropic")
    reader, judge = eval_model(), eval_model()  # a Ragas run builds both
    assert isinstance(reader, PaidFallback) and isinstance(judge, PaidFallback)
    assert reader.budget is judge.budget


@pytest.mark.parametrize(
    ("setting", "limit"),
    [("anthropic", "0"), ("gemini", "5"), ("", "5")],
    ids=["limit-zero", "same-as-free", "unset"],
)
def test_EV_no_paid_fallback_when_off_or_pointless(keys, monkeypatch, setting, limit):
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", setting)
    monkeypatch.setenv("EVAL_PAID_PROMPT_LIMIT", limit)
    assert isinstance(eval_model(), LLMAdapter)


def test_EV_no_free_key_means_replay_only_even_with_the_opt_in(keys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "anthropic")
    assert eval_model() is None


def test_EV_opt_in_without_the_paid_key_keeps_the_free_provider(keys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "anthropic")
    assert isinstance(eval_model(), LLMAdapter)


def test_EV_the_paid_key_is_never_the_main_eval_provider(keys, monkeypatch):
    monkeypatch.setenv("EVAL_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "gemini")
    with pytest.raises(PaidKeyRefused):
        eval_model()


@pytest.mark.parametrize(("raw", "limit"), [("12", 12), ("0", 0), ("-1", 100), ("x", 100)])
def test_EV_paid_prompt_limit_from_env(monkeypatch, raw, limit):
    monkeypatch.setenv("EVAL_PAID_PROMPT_LIMIT", raw)
    assert paid_prompt_limit() == limit


def test_EV_free_answers_first_and_the_paid_one_is_not_called():
    free, paid = Provider("gemini", "flash"), Provider("anthropic", "sonnet")
    model = PaidFallback(LLMAdapter(free), LLMAdapter(paid), PaidBudget(2))
    assert ask(model) == "from gemini"
    assert (model.last_provider, model.model) == ("gemini", "flash")
    assert paid.calls == 0 and model.budget.used == 0


def test_EV_paid_answers_only_while_free_is_down_and_stops_at_the_limit():
    free, paid = Provider("gemini", "flash", down=True), Provider("anthropic", "sonnet")
    model = PaidFallback(LLMAdapter(free), LLMAdapter(paid), PaidBudget(2))
    assert ask(model) == "from anthropic"
    assert (model.last_provider, model.model) == ("anthropic", "sonnet")
    assert ask(model) == "from anthropic"
    with pytest.raises(LLMUnavailable, match="2 prompts"):
        ask(model)
    assert paid.calls == 2 and model.budget.used == 2
    free.down = False  # the free tier is back: it answers again, at no cost
    assert ask(model) == "from gemini" and paid.calls == 2


def test_EV_the_recording_names_the_model_that_answered(tmp_path):
    free, paid = Provider("gemini", "flash", down=True), Provider("anthropic", "sonnet")
    model = PaidFallback(LLMAdapter(free), LLMAdapter(paid), PaidBudget(1))
    recorder = Recorder(model, tmp_path / "r")
    ask(recorder)
    assert recorder.last_provider == "anthropic/sonnet"


def test_EV_a_plain_adapter_recording_still_names_its_model(tmp_path):
    recorder = Recorder(LLMAdapter(Provider("gemini", "flash")), tmp_path / "r")
    ask(recorder)
    assert recorder.last_provider == "gemini/flash"


def test_EV_provider_adapter_builds_one_named_provider(keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    adapter = provider_adapter("anthropic")
    assert adapter is not None and adapter.fallback is None and adapter.budget is None
    assert isinstance(adapter.primary, AnthropicProvider)
    assert provider_adapter("nobody") is None


def test_EV_opus_is_refused_for_the_paid_fallback_too(keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("EVAL_LLM_FALLBACK_PROVIDER", "anthropic")
    with pytest.raises(LLMError, match="Opus"):
        eval_model()
