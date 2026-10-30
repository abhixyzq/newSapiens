# NEW SAPIENS — AI Work Intelligence Platform

*Your Workday. Understood. Documented. Optimized.*

Capture → Transcribe → Understand → Remember → Analyze → Report.
Every extracted item points at real transcript lines; Ask Sapiens answers only from records the asking user may read, with `[S#]` citations, and says so when nothing is found.

```
backend/    FastAPI + SQLAlchemy API, processing pipeline, RAG, PDF reports, 26 tests
frontend/   Next.js 15 (App Router) + Tailwind UI (same-origin proxy to the API)
docs/       ARCHITECTURE.md (design, security model, decisions, known gaps)
```

## Run locally (no Docker, no keys needed)

```bash
# 1. backend  (Python 3.11+)
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env                              # defaults: SQLite, no STT, rule-based extraction
uvicorn app.main:app --port 8000                     # API docs: http://localhost:8000/docs

# 2. frontend (Node 20+)
cd ../frontend
npm install && npm run build && npm start            # http://localhost:3000   (or `npm run dev`)
```

Register, then **Conversations → New session → Paste transcript** (or upload audio / record in the browser).
With no STT provider configured, uploaded audio waits in *Awaiting transcript* instead of faking a result.

## Make it do real transcription / AI

Set in `.env` (see `.env.example`): `STT_PROVIDER=openai|faster_whisper`, `LLM_PROVIDER=openai|anthropic` + `LLM_MODEL`,
`EMBEDDING_PROVIDER=openai`. Without them the app runs a conservative rule-based extractor and a lexical hash embedding (clearly labelled in the UI and in reports).

## Docker / production

```bash
cp .env.example .env     # set SECRET_KEY, POSTGRES_PASSWORD, COOKIE_SECURE=true behind HTTPS
docker compose up --build
```
Compose uses PostgreSQL with pgvector. **That path has not been exercised in this build** — see "Status" in `docs/ARCHITECTURE.md`.

## Tests

```bash
cd backend && pytest -q          # 26 tests: auth, consent, pipeline, RAG + citation grounding, isolation, audio, reports, deletion
cd frontend && npm run lint && npm test && npm run build
```

## Status

See `docs/ARCHITECTURE.md` §10 for exactly what is built, verified, and not built.
