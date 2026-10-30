import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import timedelta
from typing import Optional

import jwt

from .config import get_settings
from .db import utcnow

COOKIE = "ns_token"


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, s, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(s), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def create_token(user_id) -> str:
    s = get_settings()
    exp = utcnow() + timedelta(minutes=s.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "exp": exp}, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> Optional[str]:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])["sub"]
    except Exception:
        return None


def new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---- signed, expiring, user-bound URLs for private audio
def _sig(asset_id: str, uid: str, exp: int) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"{asset_id}.{uid}.{exp}".encode(), hashlib.sha256).hexdigest()


def sign_audio(asset_id, uid) -> str:
    exp = int(time.time()) + get_settings().signed_url_ttl_seconds
    return f"/api/audio/{asset_id}?uid={uid}&exp={exp}&sig={_sig(str(asset_id), str(uid), exp)}"


def verify_audio_sig(asset_id: str, uid: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(_sig(asset_id, uid, exp), sig)
