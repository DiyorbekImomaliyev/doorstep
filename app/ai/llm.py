"""The ONE adapter for model calls (RULES.md AI provider rule).

- Every call returns a validated Pydantic model (hard rule 3). Invalid output: one retry on the
  same provider, then `InvalidModelOutput`; callers use their own fallback.
- The main provider failing for budget, quota, auth, expiry or outage -> one retry on the
  fallback provider. `last_provider` records which provider answered.
- User text is redacted before it leaves the process (SE-02, hard rule 9).
- Claude: official SDK, `messages.parse(output_format=...)` structured outputs. Claude Sonnet 5.5
  rejects non-default `temperature` and forced tool choice, so determinism there comes
  from structured output plus low effort; Gemini calls use temperature 0.
- Opus models are refused in the app (RULES.md).
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger("doorstep.llm")

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class ProviderDown(LLMError):
    """Budget, quota, credentials, expiry or outage: try the fallback provider."""


class InvalidModelOutput(LLMError):
    """The model answered twice with output that does not match the schema."""


class LLMUnavailable(LLMError):
    """No provider is configured, or every provider is down."""


@dataclass(frozen=True)
class Image:
    media_type: str  # image/jpeg | image/png | image/webp
    data: bytes


# ---------------------------------------------------------------- redaction (SE-02)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)")
_UK_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*\d[A-Z]{2}\b", re.IGNORECASE)
_US_ZIP4 = re.compile(r"\b(\d{5})-\d{4}\b")


def data_block(tag: str, text: str, *, limit: int) -> str:
    """Untrusted text (a buyer's note, a question, a policy) as data inside one tag pair.

    RULES.md hard rule 8: <, > and & are escaped, so the text can never close its
    block or open another, whatever it says. The limit cuts the text before escaping.
    """
    return f"<{tag}>{html.escape(text[:limit], quote=False)}</{tag}>"


_DATE_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
_HELD = re.compile(r"\x00(\d+)\x00")
_PHONE_MAX_DIGITS = 13  # +44 7700 900123 has 12; a longer run is a tracking number, not a phone


def _phone(match: re.Match[str]) -> str:
    digits = sum(c.isdigit() for c in match.group(0))
    return "[phone]" if digits <= _PHONE_MAX_DIGITS else match.group(0)


def redact(text: str) -> str:
    """Emails and phone numbers removed; postcodes cut to the district (minimum data).

    Dates, times and tracking numbers are facts a claim or an explanation cites, never personal
    data: dates are set aside before the phone pattern runs, and a digit run longer than any phone
    number is left as it is.
    """
    text = _EMAIL.sub("[email]", text)
    text = _UK_POSTCODE.sub(lambda m: m.group(1).upper(), text)
    text = _US_ZIP4.sub(r"\1", text)
    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return f"\x00{len(held) - 1}\x00"

    text = _PHONE.sub(_phone, _DATE_TIME.sub(hold, text))
    return _HELD.sub(lambda m: held[int(m.group(1))], text)


# ---------------------------------------------------------------- providers


class Provider(Protocol):
    name: str

    def generate(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        temperature: float,
        images: Sequence[Image],
    ) -> T: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, api_key: str, model: str, *, base_url: str | None = None, timeout_s: float = 60.0
    ) -> None:
        if "opus" in model.lower():
            raise LLMError("Opus models are not used in the app (RULES.md)")
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(
            # EX-05: no hidden SDK retries; the adapter's fallback is the retry.
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,
        )

    def generate(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        temperature: float,
        images: Sequence[Image],
    ) -> T:
        a = self._anthropic
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii"),
                },
            }
            for img in images
        ]
        content.append({"type": "text", "text": user})
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": content}],  # type: ignore[list-item]
                output_format=schema,
                output_config={"effort": "low"},
            )
        except (a.RateLimitError, a.AuthenticationError, a.PermissionDeniedError) as exc:
            raise ProviderDown(f"anthropic {type(exc).__name__}") from exc
        except a.APIStatusError as exc:
            body = str(getattr(exc, "message", ""))
            if exc.status_code >= 500 or "credit" in body.lower() or "billing" in body.lower():
                raise ProviderDown(f"anthropic HTTP {exc.status_code}") from exc
            raise InvalidModelOutput(f"anthropic rejected the request: {exc.status_code}") from exc
        except (a.APIConnectionError, a.APITimeoutError) as exc:
            raise ProviderDown("anthropic unreachable") from exc
        except ValidationError as exc:
            raise InvalidModelOutput(str(exc)[:200]) from exc
        if response.stop_reason == "refusal":
            raise InvalidModelOutput("model refused")
        parsed = response.parsed_output
        if not isinstance(parsed, schema):
            raise InvalidModelOutput("no parsed output")
        return parsed


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)

    def generate(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        temperature: float,
        images: Sequence[Image],
    ) -> T:
        parts: list[dict[str, Any]] = [
            {
                "inline_data": {
                    "mime_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii"),
                }
            }
            for img in images
        ]
        parts.append({"text": user})
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
            },
        }
        try:
            r = self.http.post(
                f"/v1beta/models/{self.model}:generateContent",
                json=body,
                headers={"x-goog-api-key": self.api_key},
            )
        except httpx.HTTPError as exc:
            raise ProviderDown("gemini unreachable") from exc
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            raise ProviderDown(f"gemini HTTP {r.status_code}")
        if r.status_code >= 400:
            raise InvalidModelOutput(f"gemini rejected the request: {r.status_code}")
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return schema.model_validate_json(text)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidModelOutput(f"gemini output invalid: {type(exc).__name__}") from exc


# ---------------------------------------------------------------- adapter


class CallBudget(Protocol):
    """EX-06: a daily cap on the main provider (app.ai.usage.DbCallBudget)."""

    def take(self, provider: str) -> bool: ...


class LLMAdapter:
    def __init__(
        self,
        primary: Provider,
        fallback: Provider | None = None,
        *,
        budget: CallBudget | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.budget = budget
        self.last_provider: str | None = None

    def _generate(self, provider: Provider, schema: type[T], **kw: Any) -> T:
        if provider is self.primary and self.budget is not None:
            if not self.budget.take(provider.name):
                # EX-06: past the daily cap the paid provider counts as down. Each paid attempt
                # is charged, the retry after invalid output included.
                raise ProviderDown(f"{provider.name} daily call limit reached")
        return provider.generate(schema, **kw)

    def _try(self, provider: Provider, schema: type[T], **kw: Any) -> T:
        try:
            return self._generate(provider, schema, **kw)
        except InvalidModelOutput as first:
            log.warning("%s output invalid (%s); retrying once", provider.name, first)
            return self._generate(provider, schema, **kw)

    def complete_structured(
        self,
        *,
        schema: type[T],
        system: str,
        user: str,
        temperature: float = 0.0,
        images: Sequence[Image] = (),
        purpose: str = "",
        **_: Any,
    ) -> T:
        kw: dict[str, Any] = {
            "system": system,
            "user": redact(user),
            "temperature": temperature,
            "images": list(images),
        }
        try:
            result = self._try(self.primary, schema, **kw)
            self.last_provider = self.primary.name
            return result
        except ProviderDown as down:
            if self.fallback is None:
                raise LLMUnavailable(str(down)) from down
            log.warning(
                "%s down for %s (%s); using %s",
                self.primary.name,
                purpose,
                down,
                self.fallback.name,
            )
        try:
            result = self._try(self.fallback, schema, **kw)
        except ProviderDown as down:
            raise LLMUnavailable(str(down)) from down
        self.last_provider = self.fallback.name
        return result


DEFAULT_TIMEOUT_S = 20.0
DEFAULT_DAILY_CALL_LIMIT = 400


def daily_limit_from_env() -> int:
    """EX-06: LLM_DAILY_CALL_LIMIT; 0 turns the paid provider off; a bad value -> the default."""
    try:
        value = int(os.environ.get("LLM_DAILY_CALL_LIMIT", DEFAULT_DAILY_CALL_LIMIT))
    except ValueError:
        return DEFAULT_DAILY_CALL_LIMIT
    return value if value >= 0 else DEFAULT_DAILY_CALL_LIMIT


def timeout_from_env() -> float:
    """EX-05: one bound for every runtime model call; a bad value falls back to the default."""
    try:
        value = float(os.environ.get("LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def _provider(name: str) -> Provider | None:
    name = name.strip().lower()
    timeout_s = timeout_from_env()
    if name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(
            os.environ["ANTHROPIC_API_KEY"],
            os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            timeout_s=timeout_s,
        )
    if name == "gemini" and os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider(
            os.environ["GEMINI_API_KEY"],
            os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
            timeout_s=timeout_s,
        )
    return None


def adapter_from_env(
    *, purpose: str = "runtime", budget: CallBudget | None = None
) -> LLMAdapter | None:
    """runtime: LLM_PROVIDER with LLM_FALLBACK_PROVIDER, the main one capped by `budget` (EX-06).
    eval: EVAL_LLM_PROVIDER only, uncapped (evals/recording.py adds the opt-in, capped paid
    fallback). None when no provider has a key (callers use templates)."""
    if purpose == "eval":
        primary = _provider(os.environ.get("EVAL_LLM_PROVIDER", "gemini"))
        return LLMAdapter(primary) if primary else None
    primary = _provider(os.environ.get("LLM_PROVIDER", "anthropic"))
    fallback = _provider(os.environ.get("LLM_FALLBACK_PROVIDER", "gemini"))
    if primary is None:
        return LLMAdapter(fallback) if fallback else None
    return LLMAdapter(primary, fallback, budget=budget)


def provider_adapter(name: str) -> LLMAdapter | None:
    """One named provider with no fallback and no cap, or None without its key. Evals use it for
    their opt-in paid fallback and apply their own cap."""
    provider = _provider(name)
    return LLMAdapter(provider) if provider else None


_default: LLMAdapter | None = None


def complete_structured(
    *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
) -> BaseModel:
    """Module-level entry point (kept for callers that patch it in tests)."""
    global _default
    if _default is None:
        _default = adapter_from_env()
    if _default is None:
        raise LLMUnavailable("no model provider configured")
    return _default.complete_structured(schema=schema, system=system, user=user, **kwargs)
