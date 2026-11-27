# External providers

All providers are optional. Without credentials the application stays fully usable and reports the gap honestly.
Keys are read only from the server environment (`.env` locally, Secret Manager in the cloud), are never sent to the
browser, and are redacted from any stored or returned error text.

## Configuration

| Provider | Settings | Without it |
|---|---|---|
| Anthropic Claude | `LLM_PROVIDER=anthropic`, `LLM_API_KEY` (or `ANTHROPIC_API_KEY`), `LLM_MODEL=claude-opus-5-5`, `LLM_EFFORT` | Assistant answers are extractive: the matching recorded figures, findings and passages, each cited |
| Voyage embeddings | `EMBEDDING_PROVIDER=voyage`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL=voyage-3.5` | Local hashing embedder (lexical, no network) |
| Tavily / Brave / SerpAPI | `SEARCH_PROVIDER`, plus `TAVILY_API_KEY` / `BRAVE_API_KEY` / `SERPAPI_API_KEY` (or `SEARCH_API_KEY` for the active one) | Research is recorded as *No verified external research available: provider not configured.* Nothing is presented as verified |

## Behaviour

- **Claude** (`ai_engine/llm/provider.py`, SDK `anthropic` 1.17.0): `client.beta.messages.create` with
  `output_config.effort`, the server-side refusal fallback (`server-side-fallback-2026-07-01`, `fallbacks="default"`),
  and **structured outputs** (`output_config.format` JSON schema) for assistant answers: the model returns
  `{answer, cited_sources, verifiable}`; cited references are checked against the sources actually supplied, and
  invented ones are dropped. Errors are mapped to clear messages: invalid key, missing permission, unknown model, rate
  limit, timeout (retryable), server error, connection failure, refusal, empty or truncated response. Any failure
  falls back to the extractive answer. The SDK retries 408/409/429/5xx twice. A newer major SDK (2.x) exists;
  upgrading needs a changelog review and is not done here.
- **Voyage**: batches of 128 texts, `input_type` document/query, up to 3 attempts with exponential backoff honouring
  `Retry-After` on 429/5xx/timeouts, consistent-dimension check, explicit errors. Each stored chunk records the
  embedding model; retrieval compares only vectors from the current model, so changing model or dimension never mixes
  vectors. Re-index after switching models by re-processing documents.
- **Search**: 20 s timeouts (5 s connect), one retry on timeouts/5xx, no retry on 429 (the research run stops issuing
  queries), strict JSON parsing, only `http(s)` source URLs kept, publication dates parsed when given and retrieval
  time always recorded, source tier and relevance scoring in the research agent. Brave's invalid-key response (HTTP 422,
  `SUBSCRIPTION_TOKEN_INVALID`) is recognised as an authentication error.

## Status in the UI and API

Settings → API configuration lists every provider with *configured / not configured*, the last connection test
(status and time), the last success and the last failure (redacted). Administrators can run a live test; results are
stored in the append-only audit log (`integration.tested`). `GET /api/v1/system/integrations` returns the same data.
Status values: `not_configured` (no key; nothing called), `configured` (key present, not yet tested), `verified` (a
live request succeeded), `error` (a live request failed), `local` (local implementation, no external service).

## Verification status (2026-11-27)

| Provider | Contract tests (mocked HTTP / stubbed SDK) | Live: invalid credentials rejected and mapped | Live: successful request |
|---|---|---|---|
| Anthropic Claude | passed (request shape, structured outputs, error mapping, timeout, refusal) | **passed** against the real API | **not tested — credentials missing** |
| Voyage | passed (batching, input_type, retry/backoff, dimension checks, errors) | **passed** | **not tested — credentials missing** |
| Tavily | passed (parsing, auth header, 401/429/5xx/timeout/invalid JSON, retry) | **passed** | **not tested — credentials missing** |
| Brave | passed (markup stripping, dates, recorded 422 invalid-token response) | **passed** (found and fixed the 422 mapping) | **not tested — credentials missing** |
| SerpAPI | passed (parsing, dates, API error field) | **passed** | **not tested — credentials missing** |

No provider is labelled as live-integrated: the success path needs real keys.

## Running the live tests

```bash
cd backend && .venv/bin/python scripts/check_providers.py --live
```

```bash
cd backend && IC_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests/test_live_providers.py -v -rs
```

Export the keys first (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, `SERPAPI_API_KEY`).
Each test skips with "credentials missing" when its key is absent. Requests are minimal but billed by the provider.
