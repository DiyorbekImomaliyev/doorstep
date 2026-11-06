"""EX-06 — the spending cap on the paid main provider: a daily call count kept in the database (so
the web service and the worker share it); past the cap a call behaves exactly like the provider
being down, so every feature's existing fallback takes over."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import OperationalError

from app.ai.llm import (
    DEFAULT_DAILY_CALL_LIMIT,
    LLMAdapter,
    LLMUnavailable,
    ProviderDown,
    adapter_from_env,
    daily_limit_from_env,
)
from app.ai.schemas import PolicyAnswer
from app.ai.usage import DbCallBudget, ModelUsage


class Provider:
    def __init__(self, name: str, *, down: bool = False):
        self.name = name
        self.calls = 0
        self.down = down

    def generate(self, schema, **_):
        self.calls += 1
        if self.down:
            raise ProviderDown(f"{self.name} down")
        return PolicyAnswer(answer=f"from {self.name}")


def ask(adapter: LLMAdapter) -> str:
    out = adapter.complete_structured(schema=PolicyAnswer, system="s", user="u", purpose="test")
    assert isinstance(out, PolicyAnswer)
    return out.answer


class Today:
    def __init__(self, day: date):
        self.day = day

    def __call__(self) -> date:
        return self.day


def test_EX_06_past_the_cap_the_fallback_answers(session_factory):
    paid, free = Provider("anthropic"), Provider("gemini")
    adapter = LLMAdapter(paid, free, budget=DbCallBudget(session_factory, limit=2))
    assert [ask(adapter) for _ in range(3)] == ["from anthropic", "from anthropic", "from gemini"]
    assert paid.calls == 2 and free.calls == 1
    assert adapter.last_provider == "gemini"


def test_EX_06_past_the_cap_with_no_fallback_the_caller_falls_back(session_factory):
    adapter = LLMAdapter(Provider("anthropic"), budget=DbCallBudget(session_factory, limit=1))
    ask(adapter)
    with pytest.raises(LLMUnavailable):
        ask(adapter)


def test_EX_06_web_and_worker_share_one_count(session_factory):
    web = LLMAdapter(Provider("anthropic"), budget=DbCallBudget(session_factory, limit=2))
    worker = LLMAdapter(Provider("anthropic"), budget=DbCallBudget(session_factory, limit=2))
    ask(web)
    ask(worker)
    with pytest.raises(LLMUnavailable):
        ask(web)
    with session_factory() as s:
        [row] = s.query(ModelUsage).all()
        assert (row.provider, row.calls) == ("anthropic", 2)


def test_EX_06_a_new_utc_day_starts_a_new_count(session_factory):
    today = Today(date(2026, 11, 6))
    adapter = LLMAdapter(
        Provider("anthropic"), budget=DbCallBudget(session_factory, limit=1, today=today)
    )
    ask(adapter)
    with pytest.raises(LLMUnavailable):
        ask(adapter)
    today.day = date(2026, 11, 7)
    assert ask(adapter) == "from anthropic"


def test_EX_06_only_the_main_provider_is_counted(session_factory):
    adapter = LLMAdapter(
        Provider("anthropic", down=True),
        Provider("gemini"),
        budget=DbCallBudget(session_factory, limit=5),
    )
    for _ in range(3):
        assert ask(adapter) == "from gemini"
    with session_factory() as s:
        assert [(r.provider, r.calls) for r in s.query(ModelUsage).all()] == [("anthropic", 3)]


def test_EX_06_zero_turns_the_paid_provider_off(session_factory):
    paid, free = Provider("anthropic"), Provider("gemini")
    adapter = LLMAdapter(paid, free, budget=DbCallBudget(session_factory, limit=0))
    assert ask(adapter) == "from gemini" and paid.calls == 0


def test_EX_06_an_unreadable_count_makes_no_paid_call(session_factory):
    def broken():
        raise OperationalError("select", {}, Exception("database unavailable"))

    paid, free = Provider("anthropic"), Provider("gemini")
    adapter = LLMAdapter(paid, free, budget=DbCallBudget(broken, limit=100))  # type: ignore[arg-type]
    assert ask(adapter) == "from gemini" and paid.calls == 0


@pytest.mark.parametrize(
    ("raw", "limit"),
    [(None, DEFAULT_DAILY_CALL_LIMIT), ("50", 50), ("0", 0), ("-3", 400), ("many", 400)],
)
def test_EX_06_the_limit_comes_from_config(monkeypatch, raw, limit):
    assert DEFAULT_DAILY_CALL_LIMIT == 400
    if raw is None:
        monkeypatch.delenv("LLM_DAILY_CALL_LIMIT", raising=False)
    else:
        monkeypatch.setenv("LLM_DAILY_CALL_LIMIT", raw)
    assert daily_limit_from_env() == limit


def test_EX_06_runtime_is_capped_and_evals_are_not(monkeypatch, session_factory):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("EVAL_LLM_PROVIDER", "gemini")
    budget = DbCallBudget(session_factory, limit=3)
    runtime = adapter_from_env(purpose="runtime", budget=budget)
    evals = adapter_from_env(purpose="eval", budget=budget)
    assert runtime is not None and runtime.budget is budget
    assert evals is not None and evals.budget is None


def test_EX_06_the_retry_after_invalid_output_is_charged(session_factory):
    from app.ai.llm import InvalidModelOutput

    class Garbled(Provider):
        def generate(self, schema, **kw):
            answer = super().generate(schema, **kw)  # counts the call
            if self.calls == 1:
                raise InvalidModelOutput("not JSON")
            return answer

    paid = Garbled("anthropic")
    adapter = LLMAdapter(paid, budget=DbCallBudget(session_factory, limit=5))
    assert ask(adapter) == "from anthropic" and paid.calls == 2
    with session_factory() as s:
        assert s.query(ModelUsage).one().calls == 2
