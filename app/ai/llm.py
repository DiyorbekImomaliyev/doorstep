"""The ONE adapter for model calls (CLAUDE.md AI provider rule). Built in a later phase."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def complete_structured(
    *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
) -> BaseModel:
    raise NotImplementedError
