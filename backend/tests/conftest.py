import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp(prefix="ns-test-")
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/test.db", STORAGE_DIR=f"{_tmp}/storage", JOB_BACKEND="inline", SCHEDULER_ENABLED="false",
                  RATE_LIMIT_PER_MINUTE="100000", AUTH_RATE_LIMIT_PER_MINUTE="100000", STT_PROVIDER="none", LLM_PROVIDER="rule_based",
                  EMBEDDING_PROVIDER="hash", SECRET_KEY="test-secret-key-test-secret-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


class User:
    def __init__(self, client, name="Alice"):
        self.email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
        self.name = name
        self.c = TestClient(app)  # own cookie jar
        r = self.c.post("/api/auth/register", json={"email": self.email, "password": "correct-horse-battery", "name": name, "timezone": "UTC"})
        assert r.status_code == 201, r.text
        self.token = r.json()["token"]
        self.me = r.json()["user"]
        self.ws = self.me["workspaces"][0]["id"]
        self.h = {}

    def use(self, ws_id):
        self.h = {"X-Workspace-Id": ws_id}
        return self

    def _req(self, m, url, kw):
        kw["headers"] = {**self.h, **kw.pop("headers", {})}
        return getattr(self.c, m)(url, **kw)

    def get(self, url, **kw):
        return self._req("get", url, kw)

    def post(self, url, **kw):
        return self._req("post", url, kw)

    def patch(self, url, **kw):
        return self._req("patch", url, kw)

    def delete(self, url, **kw):
        return self._req("delete", url, kw)


@pytest.fixture()
def alice(client):
    return User(client, "Alice")


@pytest.fixture()
def bob(client):
    return User(client, "Bob")


@pytest.fixture()
def carol(client):
    return User(client, "Carol")


def days_ago(n, hour=10):
    d = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=None) - timedelta(days=n)
    return d


TRANSCRIPT = """[00:00:05] Priya: Good morning everyone, let's start the website redesign review.
[00:00:20] Rahul: The homepage mockups are ready. We are blocked on the pricing copy though.
[00:00:40] Priya: We decided to go with the three tier pricing model for the launch.
[00:01:05] Priya: Rahul will send the final pricing copy by Friday.
[00:01:30] Rahul: I will update the homepage mockups tomorrow.
[00:01:50] Priya: What about the analytics integration for the dashboard?
[00:02:10] Priya: Maybe we could add a dark mode idea for the dashboard later.
[00:02:30] Priya: Meena, please review the contract draft by 30 January.
"""

TRANSCRIPT2 = """[00:00:05] Sam: Quick sales sync about the enterprise leads.
[00:00:30] Sam: We decided to offer a ten percent discount to enterprise customers.
[00:00:50] Dev: Dev will prepare the discount proposal by Monday.
"""


def make_session(u: User, title="Website redesign review", when=None, kind="meeting", text=TRANSCRIPT, visibility="private"):
    when = when or days_ago(10)
    r = u.post("/api/sessions", json={"title": title, "kind": kind, "source": "transcript_import", "occurred_at": when.isoformat() + "Z", "visibility": visibility,
                                      "consent": {"upload_rights": True}})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = u.post(f"/api/sessions/{sid}/transcript", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready", r.json()
    return sid
