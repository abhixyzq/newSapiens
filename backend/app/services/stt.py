"""Speech-to-text provider interface.

Implemented: `openai` (Whisper-compatible /audio/transcriptions endpoint) and `faster_whisper` (local, optional dep).
Neither does speaker diarization; segments come back with speaker=None unless a provider supplies it.
`none` (default) raises ProviderNotConfigured -> the session waits in `awaiting_transcription`; a transcript can
still be imported manually via POST /api/sessions/{id}/transcript."""
from dataclasses import dataclass, field
from typing import Optional, Protocol

import httpx

from ..config import get_settings


class ProviderNotConfigured(Exception):
    pass


class ProviderError(Exception):
    """Transient provider failure; the job runner retries these."""


@dataclass
class STTSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker: Optional[str] = None
    confidence: Optional[float] = None
    language: Optional[str] = None


@dataclass
class STTResult:
    segments: list[STTSegment] = field(default_factory=list)
    language: Optional[str] = None
    duration_s: Optional[float] = None


class STTProvider(Protocol):
    name: str

    def transcribe(self, path: str, language: Optional[str] = None) -> STTResult: ...


class NoSTT:
    name = "none"

    def transcribe(self, path, language=None):
        raise ProviderNotConfigured("No speech-to-text provider configured (set STT_PROVIDER). "
                                    "You can import a transcript manually instead.")


class OpenAIWhisper:
    name = "openai"

    def transcribe(self, path, language=None):
        s = get_settings()
        if not s.openai_api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not set")
        data = {"model": s.stt_model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        try:
            with open(path, "rb") as f:
                r = httpx.post(f"{s.openai_base_url}/audio/transcriptions", data=data,
                               headers={"Authorization": f"Bearer {s.openai_api_key}"},
                               files={"file": (path.split("/")[-1].split("\\")[-1], f)}, timeout=600)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"STT HTTP {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"STT rejected the audio: {r.text[:300]}")
        j = r.json()
        segs = [STTSegment(int(x["start"] * 1000), int(x["end"] * 1000), x["text"].strip(),
                           confidence=_conf(x.get("avg_logprob")), language=j.get("language"))
                for x in j.get("segments", []) if x.get("text", "").strip()]
        if not segs and j.get("text"):
            segs = [STTSegment(0, int((j.get("duration") or 0) * 1000), j["text"].strip(), language=j.get("language"))]
        return STTResult(segs, j.get("language"), j.get("duration"))


class FasterWhisper:
    name = "faster_whisper"
    _model = None

    def transcribe(self, path, language=None):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ProviderNotConfigured("pip install faster-whisper to use STT_PROVIDER=faster_whisper")
        if FasterWhisper._model is None:
            FasterWhisper._model = WhisperModel(get_settings().stt_model or "small", compute_type="int8")
        it, info = FasterWhisper._model.transcribe(path, language=language, vad_filter=True)
        segs = [STTSegment(int(x.start * 1000), int(x.end * 1000), x.text.strip(),
                           confidence=_conf(x.avg_logprob), language=info.language)
                for x in it if x.text.strip()]
        return STTResult(segs, info.language, info.duration)


def _conf(avg_logprob):
    if avg_logprob is None:
        return None
    import math
    return round(max(0.0, min(1.0, math.exp(avg_logprob))), 3)


_override: Optional[STTProvider] = None


def set_provider(p: Optional[STTProvider]):  # used by tests / embedding apps
    global _override
    _override = p


def get_stt() -> STTProvider:
    if _override:
        return _override
    return {"openai": OpenAIWhisper, "faster_whisper": FasterWhisper}.get(get_settings().stt_provider, NoSTT)()
