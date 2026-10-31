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

    def transcribe(self, path, language=None):
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
