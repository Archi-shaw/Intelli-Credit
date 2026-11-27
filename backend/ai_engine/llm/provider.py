"""LLM provider abstraction.

The LLM is used only for evidence-grounded interpretation, summarisation and narrative. Credit
scores, amounts and rates are never produced by the model. When no provider is configured the
callers fall back to deterministic/extractive output and label it accordingly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("intellicredit.llm")


class LLMUnavailable(Exception):
    """No provider configured (expected in local/demo setups)."""


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    model: str
    stop_reason: str | None
    provider: str


class LLMProvider:
    name = "none"
    model = ""

    @property
    def available(self) -> bool:
        return False

    supports_structured = False

    def generate(self, *, system: str, messages: list[dict], max_tokens: int = 4000) -> LLMResult:
        raise LLMUnavailable("No LLM provider is configured. Set LLM_PROVIDER and LLM_API_KEY.")

    def generate_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 4000) -> tuple[dict, LLMResult]:
        raise LLMUnavailable("No LLM provider is configured. Set LLM_PROVIDER and LLM_API_KEY.")


class NullProvider(LLMProvider):
    pass


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic Python SDK."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium", timeout: float = 90.0):
        import anthropic

        self._anthropic = anthropic
        # An empty key lets the SDK resolve credentials from the environment (ANTHROPIC_API_KEY / profiles).
        kwargs = {"timeout": timeout, "max_retries": 2}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)
        self._timeout = timeout
        self.model = model
        self.effort = effort

    @property
    def available(self) -> bool:
        return True

    supports_structured = True

    def _create(self, *, system: str, messages: list[dict], max_tokens: int, output_config: dict):
        a = self._anthropic
        try:
            # Server-side refusal fallback ("default" routing) keeps an answer flowing if a request is declined.
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.BadRequestError as exc:
            raise LLMError(f"LLM request rejected: {exc.message}") from exc
        except a.AuthenticationError as exc:
            raise LLMError("LLM authentication failed — check LLM_API_KEY.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError("LLM API key lacks permission for this model.") from exc
        except a.NotFoundError as exc:
            raise LLMError(f"LLM model '{self.model}' not found.") from exc
        except a.RateLimitError as exc:
            raise LLMError("LLM rate limit reached — please retry shortly.", retryable=True) from exc
        except a.APIStatusError as exc:
            raise LLMError(f"LLM service error ({exc.status_code}).", retryable=exc.status_code >= 500) from exc
        except a.APITimeoutError as exc:
            raise LLMError(f"LLM request timed out after {self._timeout:.0f}s.", retryable=True) from exc
        except a.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM service.", retryable=True) from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
        if not text:
            raise LLMError("The model returned an empty response.", retryable=True)
        return LLMResult(text=text, model=getattr(response, "model", self.model), stop_reason=response.stop_reason,
                         provider=self.name)

    def generate(self, *, system: str, messages: list[dict], max_tokens: int = 4000) -> LLMResult:
        return self._create(system=system, messages=messages, max_tokens=max_tokens,
                            output_config={"effort": self.effort})

    def generate_json(self, *, system: str, messages: list[dict], schema: dict, max_tokens: int = 4000) -> tuple[dict, LLMResult]:
        """Structured output: the response's text is guaranteed to be JSON matching ``schema``."""
        import json

        res = self._create(system=system, messages=messages, max_tokens=max_tokens,
                           output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}})
        if res.stop_reason == "max_tokens":
            raise LLMError("The structured response was truncated (max_tokens).", retryable=True)
        try:
            return json.loads(res.text), res
        except ValueError as exc:
            raise LLMError("The model returned invalid JSON for a structured request.") from exc


_cache: dict[tuple, LLMProvider] = {}


def get_llm(provider: str, api_key: str, model: str, effort: str = "medium", timeout: float = 90.0) -> LLMProvider:
    key = (provider, api_key, model, effort, timeout)
    if key in _cache:
        return _cache[key]
    if provider == "anthropic":
        try:
            inst: LLMProvider = AnthropicProvider(api_key, model, effort, timeout)
        except Exception as exc:  # pragma: no cover - SDK import/config errors
            log.error("Anthropic provider initialisation failed: %s", exc)
            inst = NullProvider()
    else:
        inst = NullProvider()
    _cache[key] = inst
    return inst
