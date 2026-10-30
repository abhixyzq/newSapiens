from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .deps import rate_limit_global
from .routers import auth, chat, intelligence, orgs, reports, sessions, tasks, workspace
from .services import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    s = get_settings()
    if s.scheduler_enabled and s.job_backend != "inline":
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="NEW SAPIENS API", version="0.1.0", lifespan=lifespan,
              description="AI Work Intelligence Platform — capture, transcribe, understand, remember, analyze, report.",
              dependencies=[Depends(rate_limit_global)])

s = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Cookie auth + browsers: reject cross-site state-changing requests (Origin must be an allowed origin when present)."""
    if request.method in UNSAFE and request.cookies.get("ns_token"):
        origin = request.headers.get("origin")
        host_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin not in get_settings().cors_list and origin != host_origin:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


for r in (auth, orgs, sessions, intelligence, tasks, chat, reports, workspace):
    app.include_router(r.router)


@app.get("/api/health", tags=["meta"])
def health():
    st = get_settings()
    return {"status": "ok", "stt_provider": st.stt_provider, "llm_provider": st.llm_provider, "embedding_provider": st.embedding_provider,
            "database": st.database_url.split(":")[0]}
