"""AS-22: every Studio agent turn says which provider answered, so Ask Doorstep can show it.

The adapter (`app/ai/llm.py`) records `last_provider`; the turn passes it on as an optional field,
so a client that predates it keeps working. A turn no model answered carries none.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.ai.studio_agent import FALLBACK, StudioAgent, StudioTurn, StudioTurnIn, StudioTurnOut


class ProviderLLM:
    def __init__(self, provider: object) -> None:
        self.last_provider = provider

    def complete_structured(self, *, schema: type[BaseModel], **_: Any) -> BaseModel:
        return StudioTurn(message="Nothing stands out.")


class NoAttributeLLM:
    def complete_structured(self, *, schema: type[BaseModel], **_: Any) -> BaseModel:
        return StudioTurn(message="Nothing stands out.")


def turn() -> StudioTurnIn:
    return StudioTurnIn.model_validate(
        {"agent": "doorstep", "items": [{"kind": "user", "text": "Hi"}]}
    )


def test_AS_22_01_a_model_turn_names_the_provider_that_answered():
    for name in ("anthropic", "gemini"):
        got = StudioAgent(ProviderLLM(name)).turn(turn())
        assert got.mode == "model" and got.provider == name


def test_AS_22_02_a_turn_without_a_provider_name_carries_none():
    assert StudioAgent(NoAttributeLLM()).turn(turn()).provider is None
    assert StudioAgent(ProviderLLM(None)).turn(turn()).provider is None
    assert StudioAgent(ProviderLLM(42)).turn(turn()).provider is None


def test_AS_22_03_no_model_means_no_provider():
    got = StudioAgent(None).turn(turn())
    assert got.mode == "fallback" and got.message == FALLBACK and got.provider is None


def test_AS_22_04_the_field_is_optional_for_older_clients():
    old = StudioTurnOut(message="x", calls=[], mode="model")
    assert old.provider is None
    assert "provider" in StudioTurnOut.model_fields
