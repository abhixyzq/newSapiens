"""Embedding provider seam. `hash` = local feature-hashing bag-of-words: deterministic, no key, lexical-ish only
(no real semantics across synonyms/languages). Use `openai` (or any compatible model) for true semantic retrieval,
then reindex (POST /api/intelligence/reindex)."""
import hashlib
import math
import re

import httpx

from ..config import get_settings
from .stt import ProviderError, ProviderNotConfigured

STOP = set("""a an the and or but if of to in on at for with from by is are was were be been it this that these those
i you he she we they me my our your their as so do does did not no yes can could should would will just about
have has had there here what when who how which then than also into out up over very more some any""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if t not in STOP and len(t) > 1]


def _hash_embed(text: str, dim: int) -> list[float]:
    v = [0.0] * dim
    for t in tokens(text):
        h = int.from_bytes(hashlib.blake2b(t.encode(), digest_size=8).digest(), "big")
        v[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def model_name() -> str:
    s = get_settings()
    return f"hash-{s.embedding_dim}" if s.embedding_provider == "hash" else s.embedding_model


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if s.embedding_provider == "hash":
        return [_hash_embed(t, s.embedding_dim) for t in texts]
    if s.embedding_provider == "openai":
        if not s.openai_api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY required for EMBEDDING_PROVIDER=openai")
        out = []
        for i in range(0, len(texts), 64):
            try:
                r = httpx.post(f"{s.openai_base_url}/embeddings", timeout=120,
                               headers={"Authorization": f"Bearer {s.openai_api_key}"},
                               json={"model": s.embedding_model, "input": texts[i:i + 64]})
            except httpx.HTTPError as e:
                raise ProviderError(str(e))
            if r.status_code >= 500 or r.status_code == 429:
                raise ProviderError(f"embeddings HTTP {r.status_code}")
            r.raise_for_status()
            out += [d["embedding"] for d in r.json()["data"]]
        return out
    raise ProviderNotConfigured(f"unknown EMBEDDING_PROVIDER {s.embedding_provider}")


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalised (hash) / unit (openai)
