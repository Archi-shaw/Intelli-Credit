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

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def model_id(self) -> str:
        return f"{self.name}:{self.model}"


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP]


class HashingEmbedder(EmbeddingProvider):
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

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for i, v in self._features(text).items():
                vec[i] = v
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([round(x / norm, 6) for x in vec])
        return out


class VoyageEmbedder(EmbeddingProvider):
    name = "voyage"
    is_semantic = True

    def __init__(self, api_key: str, model: str = "voyage-3.5"):
        if not api_key:
            raise EmbeddingError("EMBEDDING_API_KEY is required for the Voyage embedding provider.")
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = httpx.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model},
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Voyage request failed: {exc}") from exc
        if resp.status_code != 200:
            raise EmbeddingError(f"Voyage API error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


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

    def embed(self, texts: list[str]) -> list[list[float]]:
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
