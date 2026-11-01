import io
import os

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AudioAsset, ExtractedTask, RecordingSession, TranscriptionSegment
from app.services import stt

AUTH = {"Authorization": "Bearer test-api-token"}
LINES = ["Good morning, let's review the launch.",
         "We decided to go with the three tier pricing model.",
         "Rahul will send the final pricing copy by Friday."]


class FakeSTT:
    name = "fake"

    def __init__(self, lines=LINES):
        self.lines, self.paths = lines, []

    def transcribe(self, path):
        assert os.path.exists(path)
        self.paths.append(path)
        segs = [stt.STTSegment(i * 5000, i * 5000 + 4000, t) for i, t in enumerate(self.lines)]
        return stt.STTResult(segs, "en", len(self.lines) * 5.0)


@pytest.fixture()
def fake_stt():
    p = FakeSTT()
    stt.set_provider(p)
    yield p
    stt.set_provider(None)


def counts():
    with SessionLocal() as db:
        return [db.scalar(select(func.count()).select_from(m)) for m in (RecordingSession, AudioAsset, TranscriptionSegment, ExtractedTask)]


def post(client, name="clip.m4a", data=b"fake-audio", headers=AUTH, **form):
    return client.post("/api/process", headers=headers, files={"file": (name, io.BytesIO(data), "audio/mp4")}, data=form)


def test_process_returns_extraction_and_persists_nothing(client, fake_stt):
    before = counts()
    r = post(client, occurred_at="2026-10-30T10:00:00")
    assert r.status_code == 200, r.text
    j = r.json()
    assert [s["text"] for s in j["segments"]] == LINES
    assert j["summary"]["executive_summary"] and j["language"] == "en"
    assert any("pricing" in d["text"].lower() for d in j["decisions"])
    assert any("pricing copy" in t["title"].lower() for t in j["tasks"])
    assert counts() == before
    assert fake_stt.paths and not os.path.exists(fake_stt.paths[0])


def test_process_requires_token(client, fake_stt):
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert not fake_stt.paths


def test_process_open_without_configured_token(client, fake_stt, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "api_token", "")
    assert post(client, headers={}).status_code == 200


def test_process_rejects_bad_input(client, fake_stt):
    assert post(client, name="notes.txt").status_code == 415
    assert post(client, data=b"").status_code == 422
    assert post(client, occurred_at="not-a-date").status_code == 422
    assert all(not os.path.exists(p) for p in fake_stt.paths)


def test_process_silence_and_unconfigured_stt(client):
    stt.set_provider(FakeSTT(lines=[]))
    try:
        j = post(client).json()
        assert j["segments"] == [] and j["summary"] is None and j["tasks"] == []
    finally:
        stt.set_provider(None)
    assert post(client).status_code == 503  # STT_PROVIDER=none in tests


def test_llm_json_tolerates_fences_and_trailing_prose():
    from app.services.llm import parse_json
    assert parse_json('```json\n{"a": {"b": 1}}\n```\nHope {this} helps') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        parse_json("sorry, no json")
    with pytest.raises(ValueError):
        parse_json('{"truncated": [1, 2')


def test_ask_answers_from_sent_memories(client):
    from app.services import llm

    class FakeLLM(llm.LLM):
        name = "fake"
        def complete(self, system, user, **kw):
            self.user = user
            return " You decided on three tier pricing (2026-10-30). "

    f = FakeLLM()
    llm.set_provider(f)
    try:
        r = client.post("/api/ask", headers=AUTH, json={"question": "What did I decide?", "today": "2026-10-31",
                        "memories": ["2026-10-30: decided three tier pricing", "x" * 20000],
                        "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
    finally:
        llm.set_provider(None)
    assert r.status_code == 200 and r.json()["answer"] == "You decided on three tier pricing (2026-10-30)."
    assert "three tier pricing" in f.user and "TODAY: 2026-10-31" in f.user and "ASSISTANT: hello" in f.user
    assert "x" * 3001 not in f.user  # each memory capped
    assert client.post("/api/ask", headers=AUTH, json={"question": "hi"}).status_code == 503  # rule_based: no LLM
    assert client.post("/api/ask", headers=AUTH, json={"question": ""}).status_code == 422


def test_process_events_use_evidence_offsets(client, fake_stt):
    import json
    from app.services import llm

    class FakeLLM(llm.LLM):
        name = "fake"
        def complete(self, system, user, **kw):
            return json.dumps({"summary": {"title": "Launch review", "executive_summary": "Pricing review."},
                               "decisions": [{"text": "Three tier pricing", "evidence": [1], "confidence": 0.9}],
                               "events": [{"title": "Pricing decision", "description": "Picked three tiers.", "category": "work",
                                           "importance": 0.9, "evidence": [1, 2]},
                                          {"title": "Invented", "category": "work", "importance": 1, "evidence": [99]}]})
    llm.set_provider(FakeLLM())
    try:
        j = post(client, occurred_at="2026-10-30T10:00:00").json()
    finally:
        llm.set_provider(None)
    assert [e["title"] for e in j["events"]] == ["Pricing decision"]  # uncited event dropped
    assert j["events"][0]["start_ms"] == 5000 and j["events"][0]["end_ms"] == 14000 and j["processed_at"]


def test_day_summary(client):
    from app.services import llm

    class FakeLLM(llm.LLM):
        name = "fake"
        def complete(self, system, user, **kw):
            self.user = user
            return '{"summary": "You picked three tier pricing.", "highlights": ["Pricing decided", ""]}'
    body = {"today": "2026-10-31", "items": ["10:30 Pricing decision — picked three tiers"]}
    assert client.post("/api/day", headers=AUTH, json=body).status_code == 503  # no LLM: phone keeps event titles
    f = FakeLLM()
    llm.set_provider(f)
    try:
        r = client.post("/api/day", headers=AUTH, json=body)
    finally:
        llm.set_provider(None)
    assert r.json() == {"summary": "You picked three tier pricing.", "highlights": ["Pricing decided"]}
    assert "10:30 Pricing decision" in f.user
    assert client.post("/api/day", headers=AUTH, json={"today": "x", "items": []}).status_code == 422


def test_attach_source_aligns_by_time_overlap():
    segs = [stt.STTSegment(0, 4000, "I will go to college"), stt.STTSegment(4000, 9000, "at five tomorrow")]
    stt._attach_source(segs, [(0, 2500, "मैं कॉलेज"), (2500, 4200, "जाऊंगा"), (4300, 9000, "कल पाँच बजे")])
    assert [g.source_text for g in segs] == ["मैं कॉलेज जाऊंगा", "कल पाँच बजे"]
    stt._attach_source(segs, [])
    assert segs[0].source_text is None


def test_whisper_hindi_heard_as_urdu_is_devanagari_and_english(tmp_path, monkeypatch):
    import json
    from app.services import llm
    f = tmp_path / "a.webm"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(stt.OpenAIWhisper, "_post", lambda self, p, ep: calls.append(ep) or {
        "language": "Urdu", "duration": 4.0, "segments": [{"start": 0, "end": 4, "text": "کل میٹنگ ہے"}]})

    class FakeLLM(llm.LLM):
        name = "fake"
        def complete(self, system, user, **kw):
            assert "Spoken language: Hindi" in user and "Devanagari" in user and "کل میٹنگ ہے" in user
            return json.dumps({"lines": [{"original": "कल मीटिंग है", "english": "There is a meeting tomorrow"}]})
    llm.set_provider(FakeLLM())
    try:
        r = stt.OpenAIWhisper().transcribe(str(f))
    finally:
        llm.set_provider(None)
    g = r.segments[0]
    assert (r.language, g.text, g.source_text, calls) == ("Hindi", "There is a meeting tomorrow", "कल मीटिंग है", ["transcriptions"])
    assert [stt._spoken(x) for x in ("english", "en", "Punjabi", None)] == ["English", "English", "Hindi", "Hindi"]
