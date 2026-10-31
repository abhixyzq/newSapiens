import io
import os

import pytest

from app.services import stt
from tests.conftest import TRANSCRIPT, TRANSCRIPT2, days_ago, make_session


def ask(u, q, chat_id=None):
    r = u.post("/api/chat", json={"message": q, "chat_id": chat_id})
    assert r.status_code == 200, r.text
    return r.json()


def day_phrase(n):
    d = days_ago(n)
    return f"{d.day} {d.strftime('%B')}"


# ------------------------------------------------------------------ auth
def test_auth_flow(client, alice):
    assert alice.c.get("/api/auth/me").json()["email"] == alice.email
    assert client.get("/api/sessions").status_code == 401
    r = alice.c.post("/api/auth/login", json={"email": alice.email, "password": "wrong-password-xx"})
    assert r.status_code == 401
    r = alice.c.post("/api/auth/register", json={"email": alice.email, "password": "correct-horse-battery", "name": "x"})
    assert r.status_code == 409
    r = alice.c.post("/api/auth/register", json={"email": "short@example.com", "password": "short", "name": "x"})
    assert r.status_code == 422
    # bearer token works without cookie
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/api/auth/me", headers={"Authorization": f"Bearer {alice.token}"}).status_code == 200


# ------------------------------------------------------------------ consent
def test_consent_required(alice):
    base = {"title": "x", "source": "mic_desktop", "kind": "conversation"}
    assert alice.post("/api/sessions", json={**base, "consent": {}}).status_code == 422
    assert alice.post("/api/sessions", json={**base, "kind": "meeting", "consent": {"self_recording": True}}).status_code == 422
    assert alice.post("/api/sessions", json={**base, "kind": "meeting", "consent": {"self_recording": True, "participants_notified": True}}).status_code == 201
    assert alice.post("/api/sessions", json={"title": "u", "source": "upload", "consent": {}}).status_code == 422
    assert alice.post("/api/sessions", json={**base, "visibility": "shared", "consent": {"self_recording": True}}).status_code == 422  # individual ws


# ------------------------------------------------------------------ pipeline + extraction
def test_pipeline_extracts_grounded_items(alice):
    sid = make_session(alice)
    d = alice.get(f"/api/sessions/{sid}").json()
    assert d["status"] == "ready" and d["segments"] == 8
    assert d["title"] == "Website redesign review"  # user-given titles are never overwritten
    auto = alice.get(f"/api/sessions/{make_session(alice, title='Untitled session')}").json()
    assert auto["title"] and auto["title"] != "Untitled session"
    assert any("three tier pricing" in x["text"] for x in d["decisions"])
    tasks = {t["title"]: t for t in d["tasks"]}
    pricing = next(t for t in d["tasks"] if "pricing copy" in t["title"].lower())
    assert pricing["assignee"] == "Rahul" and pricing["due_date"] and pricing["status"] == "pending"
    import datetime as dt
    assert dt.date.fromisoformat(pricing["due_date"]).weekday() == 4  # Friday
    meena = next(t for t in d["tasks"] if "contract" in t["title"].lower())
    assert meena["assignee"] == "Meena" and meena["due_date"].endswith("-01-30")
    # first-person statement is a commitment, not an invented task owner
    assert any(c["person"] == "Rahul" and "homepage" in c["text"] for c in d["commitments"])
    assert any(i["kind"] == "risk" for i in d["insights"]) and any(i["kind"] == "idea" for i in d["insights"])
    assert any(i["kind"] == "question" for i in d["insights"])
    # every item cites a real segment
    segs = {s["id"]: s for s in alice.get(f"/api/sessions/{sid}/transcript").json()["segments"]}
    from app.db import SessionLocal
    from app.models import ExtractedDecision, ExtractedTask
    from sqlalchemy import select
    with SessionLocal() as db:
        for m in (ExtractedDecision, ExtractedTask):
            for r in db.scalars(select(m).where(m.session_id == __import__("uuid").UUID(sid))):
                assert r.segment_ids and all(i in segs for i in r.segment_ids)


def test_reprocess_is_idempotent_and_keeps_human_edits(alice):
    sid = make_session(alice)
    tasks = alice.get("/api/tasks").json()["items"]
    t = next(x for x in tasks if "pricing copy" in x["title"].lower())
    alice.patch(f"/api/tasks/{t['id']}", json={"assignee": "Rahul K.", "status": "in_progress"})
    n_before = len(alice.get("/api/tasks").json()["items"])
    for _ in range(2):
        assert alice.post(f"/api/intelligence/process/{sid}").status_code == 202
    after = alice.get("/api/tasks").json()
    assert len(after["items"]) == n_before
    kept = next(x for x in after["items"] if x["id"] == t["id"])
    assert kept["assignee"] == "Rahul K." and kept["status"] == "in_progress"
    assert [h["field"] for h in kept["history"] if h.get("field")] == ["assignee", "status"]


# ------------------------------------------------------------------ Ask Sapiens
def test_meeting_that_exists_has_verifiable_citations(alice):
    sid = make_session(alice)
    out = ask(alice, f"What happened in the meeting on {day_phrase(10)}?")
    m = out["message"]
    assert "Website redesign review" in m["content"] and "three tier pricing" in m["content"]
    assert "Rahul" in m["content"] and "[S" in m["content"]
    assert m["citations"], "answer must carry citations"
    segs = {s["id"]: s for s in alice.get(f"/api/sessions/{sid}/transcript").json()["segments"]}
    for c in m["citations"]:
        assert c["session_id"] == sid and c["available"]
        if c["type"] in ("decision", "task", "commitment", "insight"):
            assert c["segment_id"] in segs  # points at a real transcript line
            seg_text = segs[c["segment_id"]]["text"].lower()
            words = [w for w in c["snippet"].lower().split() if len(w) > 4][:3]
            assert all(w.strip(".,") in seg_text for w in words), (c, seg_text)


def test_meeting_that_does_not_exist(alice):
    make_session(alice)
    out = ask(alice, f"What happened in the meeting on {day_phrase(3)}?")
    assert "couldn't find any meeting" in out["message"]["content"]
    assert out["message"]["citations"] == []


def test_multiple_meetings_same_date_asks_which_one(alice):
    make_session(alice, "Website redesign review", days_ago(10, 9))
    make_session(alice, "Sales sync", days_ago(10, 15), text=TRANSCRIPT2)
    out = ask(alice, f"What happened in the meeting on {day_phrase(10)}?")
    c = out["message"]["content"]
    assert "2 meetings" in c and "Sales sync" in c and "Website redesign review" in c
    assert out["message"]["meta"]["clarify"]
    follow = ask(alice, "the second one", out["chat_id"])
    assert "Sales sync" in follow["message"]["content"] and "ten percent discount" in follow["message"]["content"]
    assert "three tier pricing" not in follow["message"]["content"]


def test_search_by_project_person_and_history(alice):
    sid = make_session(alice)
    make_session(alice, "Sales sync", days_ago(40), text=TRANSCRIPT2, kind="meeting")
    p = alice.post("/api/projects", json={"name": "Website Redesign", "aliases": ["homepage"]})
    assert p.status_code == 201
    alice.post(f"/api/intelligence/process/{sid}")  # re-run so the new project is linked
    assert alice.get(f"/api/sessions/{sid}").json()["project_id"] == p.json()["id"]
    # decision about a topic
    a = ask(alice, "What did we decide about the pricing?")["message"]
    assert "three tier pricing" in a["content"] and a["citations"]
    # person
    a = ask(alice, "What tasks were assigned to Rahul?")["message"]
    assert "pricing copy" in a["content"].lower() and "Meena" not in a["content"]
    # project
    a = ask(alice, "Show decisions for the Website Redesign project")["message"]
    assert "three tier pricing" in a["content"] and "ten percent" not in a["content"]
    # old meeting, from 40 days ago, by explicit "last month"/topic
    a = ask(alice, "When did we last discuss the enterprise discount?")["message"]
    assert "ten percent discount" in a["content"] and a["citations"]
    # pending commitments
    a = ask(alice, "Show all pending commitments")["message"]
    assert "homepage mockups" in a["content"]
    # old-meeting tasks
    a = ask(alice, "What tasks were assigned to Dev?")["message"]
    assert "discount proposal" in a["content"].lower()


def test_unknown_topic_is_not_invented(alice):
    make_session(alice)
    out = ask(alice, "What did we decide about quantum computing?")["message"]
    assert "couldn't find" in out["content"] and out["citations"] == []


def test_ask_anything_gets_general_answer_when_llm_configured(alice):
    from app.services import llm

    class FakeLLM(llm.LLM):
        name = "fake"
        reply = "Hello there!"

        def complete(self, system, user, **kw):
            self.last_user = user
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    make_session(alice)
    fake = FakeLLM()
    llm.set_provider(fake)
    try:
        first = ask(alice, "hi")
        out = first["message"]
        assert out["content"] == "Hello there!" and out["meta"]["mode"] == "llm:fake:general"
        assert "CONVERSATION" not in fake.last_user
        # follow-ups in the same chat carry the earlier turns
        ask(alice, "tell me more", first["chat_id"])
        assert "User: hi\nSapiens: Hello there!" in fake.last_user and fake.last_user.endswith("tell me more")
        # grouped citations still verify and link
        fake.reply = "We chose three tier pricing [S1, S2]."
        out = ask(alice, "What did we decide about the pricing?")["message"]
        assert "[S1][S2]" in out["content"] and len(out["citations"]) == 2 and out["meta"]["mode"] == "llm:fake"
        # matches a word in the records, but the model judges the evidence irrelevant and answers uncited
        fake.reply = "Pricing in economics means..."
        out = ask(alice, "explain pricing theory in economics")["message"]
        assert out["content"].startswith("Pricing in economics") and out["citations"] == [] and out["meta"]["mode"] == "llm:fake:general"
        # model down: deterministic fallback, plus a note that it's the model, not the question
        fake.reply = stt.ProviderError("LLM HTTP 429")
        out = ask(alice, "tell me a joke")["message"]
        assert "couldn't find" in out["content"] and "AI model is unavailable" in out["content"]
    finally:
        llm.set_provider(None)


def test_followup_inherits_scope(alice):
    make_session(alice)
    first = ask(alice, f"What decisions were made on {day_phrase(10)}?")
    assert "three tier pricing" in first["message"]["content"]
    second = ask(alice, "and what tasks?", first["chat_id"])
    assert "pricing copy" in second["message"]["content"].lower()


def test_chat_history_regenerate_export_delete(alice):
    make_session(alice)
    r = ask(alice, "What did we decide about the pricing?")
    cid = r["chat_id"]
    assert any(c["id"] == cid for c in alice.get("/api/chat/history").json())
    assert alice.get("/api/chat/history", params={"q": "pricing"}).json()
    g = alice.post("/api/chat", json={"message": "x", "chat_id": cid, "regenerate": True})
    assert g.status_code == 200 and "three tier pricing" in g.json()["message"]["content"]
    chat = alice.get(f"/api/chat/{cid}").json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    pdf = alice.get(f"/api/chat/{cid}/messages/{chat['messages'][1]['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    s = alice.post("/api/chat/stream", json={"message": "What did we decide about the pricing?"})
    assert "event: token" in s.text and "event: done" in s.text
    assert alice.delete(f"/api/chat/{cid}").status_code == 200
    assert alice.get(f"/api/chat/{cid}").status_code == 404


# ------------------------------------------------------------------ authorization & isolation
def test_private_data_invisible_to_other_users(alice, bob):
    sid = make_session(alice)
    assert bob.get(f"/api/sessions/{sid}").status_code == 404
    assert bob.get(f"/api/sessions/{sid}/transcript").status_code == 404
    assert bob.get("/api/sessions").json()["total"] == 0
    assert bob.delete(f"/api/sessions/{sid}").status_code == 404
    assert bob.get("/api/tasks").json()["total"] == 0
    assert "couldn't find" in ask(bob, "What did we decide about the pricing?")["message"]["content"]
    assert bob.get("/api/search", params={"q": "pricing"}).json()["passages"] == []
    # bob cannot select alice's workspace
    assert bob.get("/api/sessions", headers={"X-Workspace-Id": alice.ws}).status_code == 403


def test_organization_isolation_and_scopes(alice, bob, carol):
    org = alice.post("/api/organizations", json={"name": "Acme"}).json()
    ows = org["workspace_id"]
    alice.use(ows)
    inv = alice.post(f"/api/organizations/{org['id']}/invitations", json={"email": bob.email, "role": "member"}).json()
    assert bob.post("/api/invitations/accept", json={"token": inv["token"]}).status_code == 200
    assert carol.post("/api/invitations/accept", json={"token": inv["token"]}).status_code == 400  # token is single-user/single-use
    bob.use(ows)
    carol_try = carol.get("/api/sessions", headers={"X-Workspace-Id": ows})
    assert carol_try.status_code == 403
    assert carol.get(f"/api/organizations/{org['id']}/members").status_code == 404

    priv = make_session(alice, "Private 1:1", days_ago(2), visibility="private")
    shared = make_session(alice, "All hands", days_ago(2, 14), visibility="shared", text=TRANSCRIPT2)
    ids = {s["id"] for s in bob.get("/api/sessions").json()["items"]}
    assert shared in ids and priv not in ids  # org membership != access to private recordings
    assert bob.get(f"/api/sessions/{priv}").status_code == 404
    assert "ten percent" in ask(bob, "What did we decide about the discount?")["message"]["content"]
    assert "three tier" not in ask(bob, "What did we decide about pricing?")["message"]["content"]
    # admin does not get a back door either
    adm_inv = alice.post(f"/api/organizations/{org['id']}/invitations", json={"email": carol.email, "role": "admin"}).json()
    assert carol.post("/api/invitations/accept", json={"token": adm_inv["token"]}).status_code == 200
    carol.use(ows)
    assert carol.get(f"/api/sessions/{priv}").status_code == 404
    assert carol.get(f"/api/sessions/{shared}").status_code == 200
    # roles: viewer is read-only
    v = alice.post(f"/api/organizations/{org['id']}/invitations", json={"email": bob.email, "role": "viewer"})
    assert alice.patch(f"/api/organizations/{org['id']}/members/{bob.me['id']}", json={"role": "viewer"}).status_code == 200
    assert bob.post("/api/sessions", json={"title": "x", "source": "upload", "consent": {"upload_rights": True}}).status_code == 403
    # only admins read the audit log
    assert bob.get(f"/api/organizations/{org['id']}/audit").status_code == 403
    assert any(e["action"] == "org.invite" for e in carol.get(f"/api/organizations/{org['id']}/audit").json())


def test_team_scope_is_separate_from_org_scope(alice, bob):
    org = alice.post("/api/organizations", json={"name": "Beta"}).json()
    alice.use(org["workspace_id"])
    inv = alice.post(f"/api/organizations/{org['id']}/invitations", json={"email": bob.email}).json()
    bob.post("/api/invitations/accept", json={"token": inv["token"]})
    team = alice.post(f"/api/organizations/{org['id']}/teams", json={"name": "Core"}).json()  # bob NOT in the team
    alice.use(team["id"])
    sid = make_session(alice, "Team only", days_ago(1), visibility="shared")
    assert bob.get("/api/sessions", headers={"X-Workspace-Id": team["id"]}).status_code == 403
    assert bob.use(org["workspace_id"]).get(f"/api/sessions/{sid}").status_code == 404


# ------------------------------------------------------------------ audio, STT, deletion
class FakeSTT:
    name = "fake"
    calls = 0

    def transcribe(self, path, language=None):
        FakeSTT.calls += 1
        assert os.path.getsize(path) > 0
        return stt.STTResult([stt.STTSegment(0, 4000, "Lena will prepare the budget report by Friday.", speaker="Lena", confidence=0.9),
                              stt.STTSegment(4000, 9000, "We decided to postpone the launch to March.", speaker="Omar", confidence=0.8)], "en", 9.0)


def test_audio_upload_chunks_transcription_and_deletion(alice):
    stt.set_provider(FakeSTT())
    try:
        r = alice.post("/api/sessions", json={"title": "Voice note", "kind": "voice_note", "source": "mic_desktop", "consent": {"self_recording": True}})
        sid = r.json()["id"]
        assert alice.post(f"/api/sessions/{sid}/start").json()["status"] == "recording"
        assert alice.post(f"/api/sessions/{sid}/pause").json()["status"] == "paused"
        assert alice.post(f"/api/sessions/{sid}/resume").json()["status"] == "recording"
        assert alice.post(f"/api/sessions/{sid}/upload", files={"file": ("a.exe", b"x", "application/octet-stream")}, data={"chunk_index": 0}).status_code == 415
        up = lambda i, b: alice.post(f"/api/sessions/{sid}/upload", files={"file": ("c.webm", io.BytesIO(b), "audio/webm")}, data={"chunk_index": i})
        assert up(0, b"RIFFaaaa").status_code == 201
        dup = up(0, b"RIFFaaaa")
        assert dup.json()["duplicate"] is True  # idempotent retry
        assert up(1, b"bbbb").status_code == 201
        assert alice.get(f"/api/sessions/{sid}/upload").json()["received"] == [0, 1]
        s = alice.post(f"/api/sessions/{sid}/stop").json()
        assert s["status"] == "ready" and FakeSTT.calls == 1 and s["segments"] == 2
        d = alice.get(f"/api/sessions/{sid}").json()
        assert d["duration_seconds"] == 9.0 and {x["name"] for x in d["speakers"]} == {"Lena", "Omar"}
        assert any("postpone the launch" in x["text"] for x in d["decisions"])
        # signed playback
        url = alice.get(f"/api/sessions/{sid}/audio-url").json()["url"]
        a = alice.c.get(url)
        assert a.status_code == 200 and a.content == b"RIFFaaaabbbb"
        assert alice.c.get(url.replace("sig=", "sig=00")).status_code == 403
        from fastapi.testclient import TestClient
        from app.main import app
        assert TestClient(app).get(url).status_code == 200  # signed link works without a cookie ...
        # ... but is dead once the owner is no longer allowed (different user cannot mint one; tampered uid fails)
        assert alice.c.get(url.replace(alice.me["id"], "00000000-0000-0000-0000-000000000000")).status_code == 403
        # transcript edit keeps the original
        seg = alice.get(f"/api/sessions/{sid}/transcript").json()["segments"][0]
        e = alice.patch(f"/api/transcripts/{seg['id']}", json={"text": "Lena will prepare the budget report by Monday."}).json()
        assert e["original_text"].endswith("by Friday.") and e["edited"]
        assert "Monday" in ask(alice, "budget report")["message"]["content"]
        # deletion: audio only
        from app.db import SessionLocal
        from app.models import AudioAsset
        from app.services.storage import get_storage
        from sqlalchemy import select
        with SessionLocal() as db:
            keys = [x.storage_key for x in db.scalars(select(AudioAsset).where(AudioAsset.session_id == __import__("uuid").UUID(sid)))]
        assert keys and all(get_storage().exists(k) for k in keys)
        assert alice.delete(f"/api/sessions/{sid}", params={"mode": "audio"}).status_code == 200
        assert not any(get_storage().exists(k) for k in keys)
        assert alice.get(f"/api/sessions/{sid}/audio-url").status_code == 404
        assert alice.get(f"/api/sessions/{sid}/transcript").json()["segments"]  # transcript retained
        # deletion: everything
        assert alice.delete(f"/api/sessions/{sid}").status_code == 200
        assert alice.get(f"/api/sessions/{sid}").status_code == 404
        assert "couldn't find" in ask(alice, "What did we decide about the launch to March?")["message"]["content"]
        assert alice.get("/api/search", params={"q": "budget report"}).json()["passages"] == []
    finally:
        stt.set_provider(None)


def test_no_stt_provider_waits_instead_of_faking(alice):
    r = alice.post("/api/sessions", json={"title": "Raw", "source": "upload", "consent": {"upload_rights": True}})
    sid = r.json()["id"]
    up = alice.post(f"/api/sessions/{sid}/upload", files={"file": ("a.wav", b"RIFF1234", "audio/wav")}, data={"finalize": "true"})
    assert up.status_code == 201
    d = alice.get(f"/api/sessions/{sid}").json()
    assert d["status"] == "awaiting_transcription" and "STT" in d["error"] and d["segments"] == 0


def test_sensitive_session_excluded_everywhere(alice):
    sid = make_session(alice)
    assert "three tier" in ask(alice, "What did we decide about pricing?")["message"]["content"]
    assert alice.patch(f"/api/sessions/{sid}", json={"exclude_from_processing": True}).json()["status"] == "excluded"
    assert "couldn't find" in ask(alice, "What did we decide about pricing?")["message"]["content"]
    assert alice.get("/api/tasks").json()["total"] == 0
    assert alice.post(f"/api/intelligence/process/{sid}").status_code == 409


# ------------------------------------------------------------------ tasks
def test_manual_tasks_and_privacy(alice, bob):
    t = alice.post("/api/tasks", json={"title": "Book venue", "assignee": "Alice", "priority": "high"}).json()
    assert t["status"] == "pending" and t["source"] == "manual"
    assert alice.patch(f"/api/tasks/{t['id']}", json={"status": "completed"}).json()["completed_at"]
    assert bob.get("/api/tasks").json()["total"] == 0
    assert bob.patch(f"/api/tasks/{t['id']}", json={"status": "cancelled"}).status_code == 404


# ------------------------------------------------------------------ reports
def test_report_generation_edit_approve_share_download(alice, bob):
    make_session(alice, when=days_ago(0, 8) if True else None)
    r = alice.post("/api/reports/generate", json={"period_type": "daily"})
    assert r.status_code == 201, r.text
    rep = r.json()
    secs = rep["content"]["sections"]
    assert {"executive_summary", "timeline", "decisions", "tasks", "commitments"} <= set(secs)
    assert all(v for v in secs.values()), "no empty sections"
    assert all(i["label"] in ("observed", "confirmed", "ai", "uncertain") for v in secs.values() for i in v)
    assert rep["content"]["appendix"]["sources"] and rep["content"]["appendix"]["confidence_notes"]
    again = alice.post("/api/reports/generate", json={"period_type": "daily"}).json()
    assert again["id"] == rep["id"] and again["revision"] == 2  # regenerate = new revision
    drop = secs["decisions"][0]["id"]
    e = alice.patch(f"/api/reports/{rep['id']}", json={"notes": "Reviewed.", "remove_items": [drop]}).json()
    assert e["content"]["notes"] == "Reviewed." and all(i["id"] != drop for i in e["content"]["sections"].get("decisions", []))
    assert alice.post(f"/api/reports/{rep['id']}/share", json={"user_ids": [bob.me["id"]]}).status_code == 409  # approve first
    assert alice.post(f"/api/reports/{rep['id']}/approve").json()["status"] == "approved"
    assert alice.post(f"/api/reports/{rep['id']}/share", json={"user_ids": [bob.me["id"]]}).status_code == 422  # bob isn't in alice's workspace
    pdf = alice.get(f"/api/reports/{rep['id']}/download")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 3000
    assert bob.get(f"/api/reports/{rep['id']}").status_code == 404
    assert any(x["id"] == rep["id"] for x in alice.get("/api/reports").json())


def test_empty_day_report_is_minimal_not_fabricated(alice):
    rep = alice.post("/api/reports/generate", json={"period_type": "daily", "anchor_date": "2020-01-01"}).json()
    assert list(rep["content"]["sections"]) == ["executive_summary"]
    assert "No recordings" in rep["content"]["sections"]["executive_summary"][0]["text"]
    assert alice.get(f"/api/reports/{rep['id']}/download").content.startswith(b"%PDF")


def test_scheduler_generates_daily_report_once(alice):
    from app.services import scheduler
    from app.db import SessionLocal, utcnow
    make_session(alice, when=utcnow())
    with SessionLocal() as db:
        from app.models import User
        u = db.get(User, __import__("uuid").UUID(alice.me["id"]))
        from sqlalchemy import select
        from app.models import Workspace
        ws = db.scalar(select(Workspace).where(Workspace.owner_user_id == u.id))
        ws.report_time = "00:00"
        db.commit()
        first = scheduler.run_daily_reports(db)
        second = scheduler.run_daily_reports(db)
    assert first >= 1
    assert sum(1 for r in alice.get("/api/reports").json() if r["auto_generated"]) == 1 and second >= 0


# ------------------------------------------------------------------ misc
def test_dashboard_search_analytics_projects(alice):
    from app.db import utcnow
    make_session(alice, when=utcnow())
    o = alice.get("/api/dashboard/overview").json()
    assert o["stats"]["sessions"] == 1 and o["stats"]["decisions_identified"] >= 1 and o["timeline"][0]["status"] == "ready"
    assert not any("confirm" in r["text"] for r in o["recommendations"]), "AI tasks are auto-accepted"
    s = alice.get("/api/search", params={"q": "pricing"}).json()
    assert s["passages"] and s["decisions"]
    a = alice.get("/api/analytics").json()
    assert a["per_day"] and a["tasks"]["open_now"] >= 1 and "productivity" not in str(a).lower().replace("no productivity scoring", "")
    ents = alice.get("/api/memory/entities").json()
    assert {e["name"] for e in ents} >= {"Priya", "Rahul"}


def test_empty_dashboard_has_no_fake_data(alice):
    o = alice.get("/api/dashboard/overview").json()
    assert o["stats"]["sessions"] == 0 and o["timeline"] == [] and o["recommendations"] == []


def test_entity_resolution_does_not_merge_similar_names(alice):
    make_session(alice, text="[00:00:01] Rahul: Rahul will send the deck by Friday.\n[00:00:09] Rahul Kumar: Rahul Kumar will review the budget by Monday.\n")
    ents = {e["name"]: e for e in alice.get("/api/memory/entities", params={"type": "person"}).json()}
    assert "Rahul" in ents and "Rahul Kumar" in ents and ents["Rahul Kumar"]["needs_review"]


def test_account_export_and_deletion(client):
    from tests.conftest import User
    u = User(client, "Dana")
    sid = make_session(u)
    ex = u.c.get("/api/auth/export").json()
    assert ex["sessions"] and ex["segments"] and ex["decisions"]
    assert u.c.request("DELETE", "/api/auth/me", json={"password": "wrong-wrong-wrong"}).status_code == 403
    assert u.c.request("DELETE", "/api/auth/me", json={"password": "correct-horse-battery"}).status_code == 200
    assert u.c.post("/api/auth/login", json={"email": u.email, "password": "correct-horse-battery"}).status_code == 401
    from app.db import SessionLocal
    from app.models import RecordingSession, TranscriptionSegment
    from sqlalchemy import select, func
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingSession).where(RecordingSession.id == __import__("uuid").UUID(sid))) == 0
        assert db.scalar(select(func.count()).select_from(TranscriptionSegment).where(TranscriptionSegment.session_id == __import__("uuid").UUID(sid))) == 0


def test_timezones_are_real_not_silently_utc(alice):
    from datetime import datetime, timezone
    from app.services.rag import tzinfo, local
    assert local(datetime(2026, 1, 1, 6, 38), tzinfo("Asia/Kolkata")).strftime("%H:%M") == "12:08"
    assert local(datetime(2026, 7, 1, 12, 0), tzinfo("America/New_York")).strftime("%H:%M") == "08:00"
    r = alice.patch("/api/auth/me", json={"timezone": "Asia/Kolkata"})
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Kolkata"
    assert alice.patch("/api/auth/me", json={"timezone": "Mars/Olympus"}).status_code == 422
    # a recording at 20:00 UTC on day D is already day D+1 in Kolkata: "yesterday"/date questions must follow the user's zone
    from tests.conftest import make_session, days_ago
    sid = make_session(alice, "Late call", days_ago(5, 20))
    d = days_ago(5, 20)
    local_day = local(d, tzinfo("Asia/Kolkata"))
    q = f"What happened in the meeting on {local_day.day} {local_day.strftime('%B')}?"
    out = alice.post("/api/chat", json={"message": q}).json()["message"]["content"]
    assert "Late call" in out and local_day.strftime("%H:%M") in out


def test_daily_dashboard_uses_speech_time(alice):
    from datetime import timedelta
    from tests.conftest import days_ago, make_session
    when = days_ago(3, 9)
    make_session(alice, "Morning sync", when)
    d = when.strftime("%Y-%m-%d")  # alice is on UTC
    o = alice.get("/api/dashboard/overview", params={"date": d}).json()
    assert o["date"] == d and o["speech_seconds"] > 0 and not o["listening"]
    ev = o["events"]
    assert ev and ev[0]["time"] == (when + timedelta(seconds=5)).strftime("%H:%M")  # first segment at 00:00:05, not processing time
    assert [m["at"] for m in o["moments"]] == sorted(m["at"] for m in o["moments"])
    dec = next(m for m in o["moments"] if m["kind"] == "decision")
    assert dec["time"] == (when + timedelta(seconds=40)).strftime("%H:%M") and dec["start_ms"] == 40000
    assert o["daily_summary"] and o["daily_summary"]["highlights"]  # rule-based: event titles, no AI prose
    empty = alice.get("/api/dashboard/overview", params={"date": days_ago(2).strftime("%Y-%m-%d")}).json()
    assert empty["events"] == [] and empty["moments"] == [] and empty["daily_summary"] is None
    assert alice.get("/api/dashboard/overview", params={"date": "31-10-2026"}).status_code == 422


# ------------------------------------------------------------------ reminders
def test_spoken_time_sets_reminder_that_fires_once(alice):
    import datetime as dt
    from app.services.scheduler import fire_reminders
    sid = make_session(alice, title="Errands", kind="conversation", when=days_ago(0), text="[00:01] Alice: Rahul will send the invoice tomorrow at 5 pm.")
    t = next(t for t in alice.get("/api/tasks").json()["items"] if "invoice" in t["title"].lower())
    at = dt.datetime.fromisoformat(t["remind_at"].rstrip("Z"))
    assert at == dt.datetime.combine(days_ago(0).date() + dt.timedelta(days=1), dt.time(17, 0))  # workspace tz is UTC
    assert fire_reminders(now_utc=at - dt.timedelta(minutes=1)) == 0
    assert fire_reminders(now_utc=at + dt.timedelta(minutes=1)) == 1
    assert fire_reminders(now_utc=at + dt.timedelta(minutes=2)) == 0
    assert alice.patch(f"/api/tasks/{t['id']}", json={"remind_at": (at + dt.timedelta(hours=1)).isoformat()}).status_code == 200
    assert alice.delete(f"/api/tasks/{t['id']}").status_code == 200
    assert fire_reminders(now_utc=at + dt.timedelta(hours=2)) == 0  # deleted tasks never remind
    assert any(n["kind"] == "reminder" and "invoice" in n["title"].lower() for n in alice.get("/api/notifications").json())
