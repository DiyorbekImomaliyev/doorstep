"""The ONE adapter for model calls (CLAUDE.md AI provider rule).

- Every call returns a validated Pydantic model (hard rule 3). Invalid output: one retry on the
  same provider, then `InvalidModelOutput`; callers use their SPEC section 10 fallback.
- The main provider failing for budget, quota, auth, expiry or outage -> one retry on the
  fallback provider. `last_provider` records which provider answered.
- User text is redacted before it leaves the process (SE-02, hard rule 9).
- Claude: official SDK, `messages.parse(output_format=...)` structured outputs. Claude Sonnet 5.5
  rejects non-default `temperature` and forced tool choice (ADR-025), so determinism there comes
  from structured output plus low effort; Gemini calls use temperature 0.
- Opus models are refused in the app (CLAUDE.md).
"""

from __future__ import annotations

import base64
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


def redact(text: str) -> str:
    """Emails and phone numbers removed; postcodes cut to the district (minimum data)."""
    text = _EMAIL.sub("[email]", text)
    text = _UK_POSTCODE.sub(lambda m: m.group(1).upper(), text)
    text = _US_ZIP4.sub(r"\1", text)
    return _PHONE.sub("[phone]", text)


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
            raise LLMError("Opus models are not used in the app (CLAUDE.md)")
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


class LLMAdapter:
    def __init__(self, primary: Provider, fallback: Provider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_provider: str | None = None

    def _try(self, provider: Provider, schema: type[T], **kw: Any) -> T:
        try:
            return provider.generate(schema, **kw)
        except InvalidModelOutput as first:
            log.warning("%s output invalid (%s); retrying once", provider.name, first)
            return provider.generate(schema, **kw)

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


def adapter_from_env(*, purpose: str = "runtime") -> LLMAdapter | None:
    """runtime: LLM_PROVIDER with LLM_FALLBACK_PROVIDER. eval: EVAL_LLM_PROVIDER only (never the
    paid key for bulk work). None when no provider has a key (callers use templates)."""
    if purpose == "eval":
        primary = _provider(os.environ.get("EVAL_LLM_PROVIDER", "gemini"))
        return LLMAdapter(primary) if primary else None
    primary = _provider(os.environ.get("LLM_PROVIDER", "anthropic"))
    fallback = _provider(os.environ.get("LLM_FALLBACK_PROVIDER", "gemini"))
    if primary is None:
        return LLMAdapter(fallback) if fallback else None
    return LLMAdapter(primary, fallback)


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
