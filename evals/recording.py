"""Record once, replay (RULES.md): model answers for evals, keyed by the exact prompt.

Every eval that calls a model goes through a Recorder. A prompt with a recording is answered
from it. A new prompt is asked live on EVAL_LLM_PROVIDER (the free tier), and its answer is
appended. The paid key never runs evals by default: EVAL_LLM_FALLBACK_PROVIDER opts in to it as a
fallback that answers only while the free provider is down (quota, outage), for at most
EVAL_PAID_PROMPT_LIMIT prompts per run. A changed prompt has a new key, so it is asked again,
never answered from an old recording. Each recording names the model that gave it and the output
schema it was given for, and is not replayed for another schema. Failures are not recorded, so a
rerun asks again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.ai.explainer import StructuredLLM
from app.ai.llm import Image, LLMAdapter, LLMUnavailable, adapter_from_env, provider_adapter

DEFAULT_PAID_PROMPT_LIMIT = 100


def schema_id(schema: type[BaseModel]) -> str:
    """A short hash of the output schema; a recording made for another schema is not replayed."""
    text = json.dumps(schema.model_json_schema(), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _label(row: dict[str, Any]) -> str | None:
    """ "gemini/gemini-2.5-flash": which provider and model gave a recorded answer."""
    if row.get("model"):
        return f"{row['provider']}/{row['model']}"
    return row.get("provider")


class PaidKeyRefused(RuntimeError):
    """The paid provider is never the main eval provider (RULES.md)."""


def paid_prompt_limit() -> int:
    """EVAL_PAID_PROMPT_LIMIT: paid fallback prompts per run; 0 turns the fallback off. A bad
    value gives the default."""
    try:
        value = int(os.environ.get("EVAL_PAID_PROMPT_LIMIT", DEFAULT_PAID_PROMPT_LIMIT))
    except ValueError:
        return DEFAULT_PAID_PROMPT_LIMIT
    return value if value >= 0 else DEFAULT_PAID_PROMPT_LIMIT


class PaidBudget:
    """Paid fallback prompts for one run, shared by every eval model in the process (a Ragas run
    has a reader and a judge; together they get one limit)."""

    def __init__(self, limit: int) -> None:
        self.limit, self.used = limit, 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


_run_budget: PaidBudget | None = None


def run_budget() -> PaidBudget:
    """This process's paid budget, created once from EVAL_PAID_PROMPT_LIMIT."""
    global _run_budget
    if _run_budget is None:
        _run_budget = PaidBudget(paid_prompt_limit())
    return _run_budget


class PaidFallback:
    """The free eval model first; the paid one only when the free one is down, while the run's
    PaidBudget lasts. Past it a prompt is not run, so it is never charged. `last_provider` and
    `model` name whichever model answered, for the recording."""

    def __init__(self, free: LLMAdapter, paid: LLMAdapter, budget: PaidBudget) -> None:
        self.free, self.paid, self.budget = free, paid, budget
        self.last_provider: str | None = None
        self.model: str | None = None

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        kw = {"schema": schema, "system": system, "user": user, **kwargs}
        try:
            answer = self.free.complete_structured(**kw)
            self.last_provider, self.model = self.free.last_provider, self.free.primary.model
            return answer
        except LLMUnavailable:
            if not self.budget.take():
                limit = self.budget.limit
                raise LLMUnavailable(f"paid eval fallback used its {limit} prompts") from None
        answer = self.paid.complete_structured(**kw)
        self.last_provider, self.model = self.paid.last_provider, self.paid.primary.model
        return answer


def eval_model() -> StructuredLLM | None:
    """The eval provider (EVAL_LLM_PROVIDER, default gemini), or None without a key. With
    EVAL_LLM_FALLBACK_PROVIDER set to another provider, a PaidFallback around it."""
    name = os.environ.get("EVAL_LLM_PROVIDER", "gemini").strip().lower()
    if name == "anthropic":
        raise PaidKeyRefused("the paid key is only an opt-in eval fallback, never the main one")
    free = adapter_from_env(purpose="eval")
    fallback = os.environ.get("EVAL_LLM_FALLBACK_PROVIDER", "").strip().lower()
    limit = paid_prompt_limit()
    if free is None or not fallback or fallback == name or limit == 0:
        return free
    paid = provider_adapter(fallback)
    return PaidFallback(free, paid, run_budget()) if paid else free


class Recorder:
    """A StructuredLLM that answers from recordings and asks the live model only for new
    prompts. `asked` and `replayed` count both kinds."""

    def __init__(
        self,
        live: StructuredLLM | None,
        path: Path,
        delay_s: float = 0.0,
    ) -> None:
        self.live, self.path, self.delay_s = live, path, delay_s
        self.saved: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.saved[row["key"]] = row
        self.last_provider: str | None = None
        self.last_user = ""
        self.asked = self.replayed = 0

    @staticmethod
    def key(schema: type[BaseModel], system: str, user: str, images: Sequence[Image] = ()) -> str:
        """The prompt's hash. Images count by their bytes, so two photos sent with the same text
        never share an answer. A text-only prompt hashes as before."""
        digest = hashlib.sha256(f"{schema.__name__}\n{system}\n{user}".encode())
        for image in images:
            digest.update(b"\nimage:" + image.media_type.encode() + b":" + image.data)
        return digest.hexdigest()[:24]

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        self.last_user, self.last_provider = user, None
        key = self.key(schema, system, user, kwargs.get("images") or ())
        row = self.saved.get(key)
        if row is not None and row.get("schema", schema_id(schema)) != schema_id(schema):
            row = None  # the output schema changed since this answer was recorded
        if row is not None:
            self.replayed += 1
            self.last_provider = _label(row)
            return schema.model_validate(row["answer"])
        if self.live is None:
            raise LLMUnavailable("no recording for this prompt and no eval model configured")
        if self.asked and self.delay_s:
            time.sleep(self.delay_s)  # free-tier rate limit
        self.asked += 1
        answer = self.live.complete_structured(schema=schema, system=system, user=user, **kwargs)
        row = {
            "key": key,
            "provider": getattr(self.live, "last_provider", None),
            "model": getattr(self.live, "model", None)
            or getattr(getattr(self.live, "primary", None), "model", None),
            "schema": schema_id(schema),
            "answer": answer.model_dump(),
        }
        self.last_provider = _label(row)
        self.saved[key] = row
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return answer
