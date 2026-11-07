"""Record once, replay (CLAUDE.md): model answers for evals, keyed by the exact prompt.

Every eval that calls a model goes through a Recorder. A prompt with a recording is answered
from it. A new prompt is asked live, on EVAL_LLM_PROVIDER only (never the paid key), and its
answer is appended. A changed prompt has a new key, so it is asked again, never answered from an
old recording. Each recording names the model that gave it and the output schema it was given
for, and is not replayed for another schema. Failures are not recorded, so a rerun asks again.
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
from app.ai.llm import Image, LLMUnavailable, adapter_from_env


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
    """Bulk work never runs on the paid provider (CLAUDE.md)."""


def eval_model() -> StructuredLLM | None:
    """The eval provider (EVAL_LLM_PROVIDER, default gemini), or None without a key."""
    if os.environ.get("EVAL_LLM_PROVIDER", "gemini").strip().lower() == "anthropic":
        raise PaidKeyRefused("evals run on the eval provider only, never the paid key")
    return adapter_from_env(purpose="eval")


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
            "model": getattr(getattr(self.live, "primary", None), "model", None),
            "schema": schema_id(schema),
            "answer": answer.model_dump(),
        }
        self.last_provider = _label(row)
        self.saved[key] = row
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return answer
