# NEW SAPIENS — Architecture, decisions and status

## 1. Shape

```
Browser (Next.js) ──same-origin /api proxy──▶ FastAPI
                                               ├─ routers/      auth, orgs, sessions(capture), intelligence, tasks, chat, reports, workspace
                                               ├─ access.py     ONE authorization policy (all reads go through it)
                                               ├─ services/
                                               │    storage     local-disk object store (private; signed, user-bound, expiring URLs)
                                               │    stt         provider interface: none | openai(whisper API) | faster_whisper
                                               │    extraction  LLM engine (JSON, evidence-required) | rule-based engine (EN + basic Hinglish)
                                               │    pipeline    assemble audio → STT → understand → entities → meeting → index   (idempotent)
                                               │    retrieval   permission SQL filter → vector + BM25-style keyword → RRF → phrase rerank
                                               │    rag         query parsing (tz-aware dates/people/projects) → scope → compose → verify citations
                                               │    reports     compile (JSON, labelled) → ReportLab PDF
                                               │    jobs / scheduler   thread pool; daily-report, retention-purge and stuck-job recovery loop
                                               └─ SQLAlchemy models (31 tables)  →  SQLite (dev/test)  |  PostgreSQL + pgvector (prod)
```

Pipeline: `created → recording ⇄ paused → uploaded → transcribing → analyzing → ready`
(`awaiting_transcription` when no STT/transcript; `failed` + retry; `excluded` for sensitive sessions).
Capture is independent of AI: audio is stored and can be processed later, manually uploaded, or replaced by an imported transcript.

## 2. Authorization & privacy model (the core invariant)

* Tenant boundary = **workspace** (`individual`, `organization`, `team`). Users see only workspaces they belong to; others get 403 and org existence is not revealed (404).
* Content visibility is set once, on `recording_sessions`: `private` (owner only) or `shared` (all members of that workspace). Team-shared vs org-shared = which workspace the session lives in.
  **Org role never grants access to someone else's private recording** (tested, including for admins).
* Every derived row (segments, decisions, tasks, commitments, chunks, summaries, citations) inherits visibility through `session_id`; list/search/RAG/report queries all use the same predicate (`access.visible_session_filter`, `services/queries.py`), applied **in SQL before ranking**.
* Citations are re-authorised on every read; a deleted/unshared source returns "Source no longer available".
* Signed audio URLs are HMAC'd, expiring (15 min), bound to a user id, and re-check live membership on each request.
* Consent is a hard precondition: live capture needs `self_recording`; meetings/with-others need `participants_notified`; uploads need `upload_rights`. Stored in `consent_records`. No hidden capture: the browser's own mic permission is used and a persistent red indicator is shown.
* Sensitive sessions (`exclude_from_processing`): stored only; no transcript, extraction, indexing, search or reporting; toggling on purges derived data.
* Deletion: `audio` (files only) or `everything` (audio + transcript + derived data; tombstone row kept for audit). Retention purges audio on `retention_until`. Account deletion cascades through FKs and removes files. Data export: `GET /api/auth/export`.
* Auth: scrypt password hashes, JWT in HttpOnly SameSite=Lax cookie (or Bearer), Origin check on state-changing cookie requests, per-IP rate limits (in-process), audit log for sensitive actions.

## 3. Anti-hallucination design

* An extracted item exists only if it cites real segment ids (LLM output without valid evidence indexes is dropped; rules operate per segment).
* Task owner is set only with evidence (a name beside the verb, or first-person with a known speaker). "I will…" becomes a **commitment** of the speaker, not an invented task assignment. Vague deadlines ("next week") keep the phrase and no date.
* Everything AI-made is `needs review` until a person confirms; reports/answers label it. Re-processing never overwrites human-edited rows.
* Ask Sapiens answers are composed from retrieved records; with an LLM configured the model rewrites but must cite, and the answer is rejected (falling back to the deterministic composer) if it cites nothing or cites an unknown source. "Not found" is an explicit, citation-free answer. Multiple meetings on a date → it asks which one.
* Entity resolution merges only on exact name/alias; near-names stay separate and flagged; merges are explicit user actions.

## 4. Assumptions / decisions (documented rather than asked)

| Decision | Why |
|---|---|
| SQLite for dev/test, Postgres+pgvector for prod via one SQLAlchemy model set; `Embedding` type = `vector(dim)` on PG, JSON elsewhere | No Postgres/Docker in the build environment; keeps the suite runnable anywhere |
| Thread pool + scheduler thread instead of Redis/Celery | Job function is idempotent and retried; swapping to Celery is a wrapper around `jobs.run_with_retry` |
| `kind` (meeting/conversation/voice_note) is chosen by the user | "the meeting on 25 Jan" must not be guessed from audio |
| "I/my" in commitment queries is read as the logged-in user's first name (stated in the answer) | Transparency over silent assumption |
| Year-less dates in questions resolve to the most recent past; in deadlines to the next future occurrence | Matches how people say them |
| Streaming = answer is built and citation-verified first, then streamed | Never display an unverified answer |
| Organization invitations return a one-time token (no email transport) | No mail provider configured |

## 5. API

Interactive OpenAPI at `/docs` (all routes under `/api`). Groups: `auth`, `organizations`/`invitations`/`workspaces`, `sessions` (create/start/pause/resume/stop, resumable chunk `upload`, `transcript` import, `audio-url`, delete modes), `transcripts`/`speakers`, `intelligence` (process, reindex, decisions, tasks, commitments, insights), `tasks`, `chat` (+`/stream`, history, regenerate, PDF export), `reports` (generate, edit, approve, share, download), `projects`, `memory`, `search`, `analytics`, `dashboard`, `notifications`, `integrations`.
Differences from the brief's route list: report period via `anchor_date`; extras above.

## 6. Data model

31 tables: the 29 requested (+ `organization_invitations`, `extracted_insights` for risks/ideas/questions). UUID PKs, created/updated stamps, soft-delete where needed, FK cascades, workspace_id on all content, indexes on workspace/date/status. See `backend/app/models.py`.

## 7. Reports

Sections only appear when they have content (no empty pages): executive summary, timeline, meetings, decisions, tasks, commitments, risks, ideas, open questions, insights (computed counts only), tomorrow's priorities (due/overdue/high-priority only), appendix (source refs with transcript excerpts, confidence notes). Labels: Observed / Confirmed / AI-detected / Needs review. Lifecycle: draft → edit (note, drop items) → approve → share (workspace members only; never grants recording access). Regenerate = new revision. Daily auto-draft at the workspace's local time (individual workspaces).

## 8. Deployment

`docker-compose.yml` (Postgres+pgvector, API, web), `backend/Dockerfile`, `frontend/Dockerfile`, `.github/workflows/ci.yml`. Config is environment-only (`.env.example`); the API refuses to start in production with the default secret.

## 9. Testing

Backend (26): auth, consent gates, extraction grounding, idempotent re-processing keeping human edits, meeting exists / doesn't / ambiguous (+ clarification follow-up), project/person/historical/old-task queries, unknown topic → not found, follow-up inheritance, chat history/regenerate/PDF/SSE, private-data and org/team isolation incl. admin, viewer read-only, audio chunks/resume/idempotent retry/signed playback/edit-preserves-original/both deletion modes, no-STT behaviour, sensitive exclusion, manual-task privacy, reports (edit/approve/share/PDF/empty day/scheduler), dashboard/search/analytics, entity non-merging, timezone correctness, export + account deletion.
Frontend: type-check, production build, 3 unit tests. UI was exercised manually in a browser (register → import → processing → dashboard → Ask Sapiens with citations → report) — that run found and fixed 3 bugs (raw-JSON error text, effect returning a Promise crashing `/ask`, silent UTC fallback without `tzdata`).

## 10. STATUS — built vs. not built

**Built and verified here (SQLite, rule-based extraction, hash embeddings):** auth; workspaces/orgs/teams/roles/invites/audit; consent; session lifecycle; chunked/resumable audio upload with signed playback; transcript import & editing (original preserved); idempotent pipeline; extraction (decisions, tasks, commitments, risks, ideas, questions); entities; projects; hybrid retrieval; Ask Sapiens (dates in user tz, people, projects, follow-ups, clarification, citations, PDF export, SSE); tasks with history; reports + PDF; dashboard, search, analytics; retention/deletion/export; the Next.js UI for all 12 nav items.

**Written but NOT exercised (no key / no infrastructure available):**
* PostgreSQL + pgvector (extension creation, `vector` column, cosine pushdown in `retrieval.candidates`). No Alembic migrations — `create_all` only.
* Real STT (`openai`, `faster_whisper`), real LLM extraction/answer rewriting (`openai`-compatible, `anthropic`), OpenAI embeddings. Provider code follows each API's documented shape but has never run against the live service.

**Not built:**
* Row-level security policies in Postgres — authorization is enforced server-side in the application (single policy module) only. Add RLS as defence in depth before multi-tenant production use.
* Redis/Celery (see §4), S3 storage backend, encryption at rest (rely on disk/volume or bucket encryption), email delivery, task-reminder delivery (only `remind_at` is stored), shared Redis-backed rate limiting.
* Speaker diarization (providers return no speakers; speakers come from transcript labels), Meet/Zoom/Teams connectors (UI marks them "Not built"), Android foreground-service and iOS capture apps (web capture only).
* Automatic weekly/monthly/org report scheduling (generation works on demand), UI for adding team members and picking share recipients (API supports it; UI takes IDs), Playwright E2E suite, cross-browser/mobile QA, accessibility audit.

**Known quality limits:** rule-based extraction is heuristic (English + a little Hinglish) and conservative; its "summary" is extractive and labelled so. Hash embeddings are lexical — semantic search needs a real embedding provider. PDF text uses a system Unicode font when found; complex Devanagari shaping is not supported by ReportLab.
