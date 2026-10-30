"""Permission-aware hybrid retrieval: metadata filters -> (vector + BM25-style keyword) -> reciprocal-rank fusion
-> light phrase-boost rerank. The permission predicate is applied in SQL, before any scoring, so content the caller
cannot read is never even loaded.

SQLite/dev path scores candidates in Python. On PostgreSQL the vector leg is pushed down to pgvector
(`<=>` cosine distance) — that branch is written but was NOT exercised in this build (no Postgres available here)."""
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import Ctx, visible_session_filter
from ..models import KnowledgeChunk, RecordingSession
from . import embeddings

MIN_COSINE = 0.30
MAX_CANDIDATES = 4000


@dataclass
class Hit:
    chunk: KnowledgeChunk
    score: float
    cosine: float
    keyword: float


def candidates(db: Session, ctx: Ctx, *, start: Optional[datetime] = None, end: Optional[datetime] = None,
               project_id=None, session_ids=None, qvec=None):
    q = (select(KnowledgeChunk).join(RecordingSession, RecordingSession.id == KnowledgeChunk.session_id)
         .where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False),
                KnowledgeChunk.workspace_id == ctx.workspace.id))
    if start:
        q = q.where(KnowledgeChunk.occurred_at >= start)
    if end:
        q = q.where(KnowledgeChunk.occurred_at < end)
    if project_id:
        q = q.where(KnowledgeChunk.project_id == project_id)
    if session_ids is not None:
        q = q.where(KnowledgeChunk.session_id.in_(session_ids))
    if db.bind.dialect.name == "postgresql" and qvec is not None:
        q = q.order_by(KnowledgeChunk.embedding.cosine_distance(qvec))
    else:
        q = q.order_by(KnowledgeChunk.occurred_at.desc())
    return db.scalars(q.limit(MAX_CANDIDATES)).all()


def hybrid_search(db: Session, ctx: Ctx, query: str, *, k=8, person: Optional[str] = None, **filters) -> list[Hit]:
    qterms = embeddings.tokens(query)
    qvec = embeddings.embed([query])[0] if qterms else None
    pool = candidates(db, ctx, qvec=qvec, **filters)
    if person:
        p = person.lower()
        pool = [c for c in pool if p in c.text.lower() or any(p == s.lower() for s in c.speakers or [])]
    if not pool or not qterms:
        return []
    docs = [Counter(embeddings.tokens(c.text)) for c in pool]
    n = len(pool)
    avgdl = sum(sum(d.values()) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d) if t in set(qterms))
    kw = []
    for d in docs:
        dl = sum(d.values()) or 1
        s = 0.0
        for t in set(qterms):
            if d[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * d[t] * 2.2 / (d[t] + 1.2 * (0.25 + 0.75 * dl / avgdl))
        kw.append(s)
    cos = [embeddings.cosine(qvec, c.embedding) if c.embedding and c.embedding_model == embeddings.model_name() else 0.0
           for c in pool]
    elig = [i for i in range(n) if kw[i] > 0 or cos[i] >= MIN_COSINE]
    if not elig:
        return []
    rk_v = {i: r for r, i in enumerate(sorted(elig, key=lambda i: -cos[i]))}
    rk_k = {i: r for r, i in enumerate(sorted(elig, key=lambda i: -kw[i]))}
    phrase = " ".join(qterms[:3])
    fused = []
    for i in elig:
        s = 1 / (60 + rk_v[i]) + 1 / (60 + rk_k[i])
        if len(qterms) > 1 and phrase in " ".join(embeddings.tokens(pool[i].text)):
            s += 0.01  # rerank: contiguous phrase match
        fused.append(Hit(pool[i], s, cos[i], kw[i]))
    fused.sort(key=lambda h: -h.score)
    return fused[:k]
