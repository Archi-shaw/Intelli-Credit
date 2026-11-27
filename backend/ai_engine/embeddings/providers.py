"""Embedding providers.

* ``local-hash``  (default) – deterministic signed feature-hashing of word unigrams/bigrams and
  character trigrams. Runs offline with no model download. It captures lexical similarity
  only (not deep semantics) and is labelled as such in the UI.
* ``voyage``      – Voyage AI embeddings API (requires EMBEDDING_API_KEY).
* ``sentence-transformers`` – local transformer model if the optional package is installed.
"""
from __future__ import annotations

import hashlib
import math
import time
import re
from functools import lru_cache

import httpx

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "was", "were", "be", "by", "with", "as",
    "at", "this", "that", "it", "from", "its", "has", "have", "had", "what", "which", "who", "how", "does", "do", "s",
}


class EmbeddingError(Exception):
    pass


class EmbeddingProvider:
    name = "base"
    model = "base"
    is_semantic = False

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def model_id(self) -> str:
        return f"{self.name}:{self.model}"


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP]


class HashingEmbedder(EmbeddingProvider):  # noqa: D101
    name = "local-hash"

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.model = f"hash-{dim}"

    def _features(self, text: str) -> dict[int, float]:
        toks = tokenize(text)
        feats: dict[int, float] = {}

        def add(key: str, w: float):
            h = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            feats[idx] = feats.get(idx, 0.0) + sign * w

        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for t, c in counts.items():
            add("w:" + t, 1.0 + math.log(c))
            if len(t) > 4:
                for i in range(len(t) - 2):
                    add("c:" + t[i:i + 3], 0.25)
        for a, b in zip(toks, toks[1:]):
            add("b:" + a + "_" + b, 0.7)
        return feats

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for i, v in self._features(text).items():
                vec[i] = v
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([round(x / norm, 6) for x in vec])
        return out


class VoyageEmbedder(EmbeddingProvider):
    """Voyage AI embeddings: batched, retried with backoff, dimension-checked.

    Vectors are stored with ``model_id`` and retrieval only compares vectors from the same model, so switching
    model or dimension never mixes incompatible vectors (re-index with ``python -m app.cli reembed``).
    """

    name = "voyage"
    is_semantic = True
    batch_size = 128
    max_attempts = 3
    url = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "voyage-3.5", client: httpx.Client | None = None):
        if not api_key:
            raise EmbeddingError("EMBEDDING_API_KEY is required for the Voyage embedding provider.")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    def _post(self, batch: list[str], input_type: str | None) -> list[list[float]]:
        body = {"input": batch, "model": self.model}
        if input_type in ("document", "query"):
            body["input_type"] = input_type
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
            except httpx.TimeoutException:
                err = EmbeddingError("Voyage request timed out.")
            except httpx.HTTPError as exc:
                err = EmbeddingError(f"Voyage request failed: {type(exc).__name__}.")
            else:
                if resp.status_code == 200:
                    try:
                        data = sorted(resp.json()["data"], key=lambda d: d["index"])
                        vecs = [[float(x) for x in d["embedding"]] for d in data]
                    except (ValueError, KeyError, TypeError) as exc:
                        raise EmbeddingError("Voyage returned an unexpected response.") from exc
                    if len(vecs) != len(batch):
                        raise EmbeddingError(f"Voyage returned {len(vecs)} vectors for {len(batch)} inputs.")
                    return vecs
                if resp.status_code in (401, 403):
                    raise EmbeddingError(f"Voyage rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code != 429 and resp.status_code < 500:
                    raise EmbeddingError(f"Voyage API error {resp.status_code}.")
                err = EmbeddingError(f"Voyage API error {resp.status_code}" + (" (rate limited)" if resp.status_code == 429 else ""))
                try:
                    delay = max(delay, float(resp.headers.get("retry-after", 0)))
                except ValueError:
                    pass
            if attempt < self.max_attempts:
                time.sleep(min(delay, 20.0))
                delay *= 2
        raise err

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._post(texts[i:i + self.batch_size], input_type))
        dims = {len(v) for v in vectors}
        if len(dims) > 1:
            raise EmbeddingError(f"Voyage returned vectors of different dimensions: {sorted(dims)}.")
        return vectors


class SentenceTransformerEmbedder(EmbeddingProvider):
    name = "sentence-transformers"
    is_semantic = True

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise EmbeddingError("sentence-transformers is not installed (pip install sentence-transformers).") from exc
        self.model = model
        self._m = SentenceTransformer(model)

    def embed(self, texts: list[str], input_type: str | None = None) -> list[list[float]]:
        return [list(map(float, v)) for v in self._m.encode(texts, normalize_embeddings=True)]


@lru_cache(maxsize=4)
def get_embedder(provider: str, model: str, api_key: str = "") -> EmbeddingProvider:
    if provider == "voyage":
        return VoyageEmbedder(api_key, model if model and not model.startswith("hash") else "voyage-3.5")
    if provider == "sentence-transformers":
        return SentenceTransformerEmbedder(model if model and not model.startswith("hash") else "sentence-transformers/all-MiniLM-L6-v2")
    dim = 384
    m = re.match(r"hash-(\d+)", model or "")
    if m:
        dim = int(m.group(1))
    return HashingEmbedder(dim)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
