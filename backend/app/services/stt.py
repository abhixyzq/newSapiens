"""Speech-to-text provider interface.

Implemented: `openai` (Whisper-compatible /audio/translations endpoint), `groq` (same API, hosted Whisper), `gemini` (generateContent with inline audio), `deepgram` (pre-recorded API) and `faster_whisper` (local, optional dep).
Transcripts are always English: Whisper-family transcribe then the LLM translates (Whisper translates if no LLM), Gemini translates; Deepgram does not.
Only English and Hindi (incl. Hinglish) are supported: any other detected language is treated as Hindi.
`source_text` keeps the words as spoken (native script) when they differ from the English `text`; None = same as `text`.
None does speaker diarization; segments come back with speaker=None unless a provider supplies it.
`none` (default) raises ProviderNotConfigured -> the session waits in `awaiting_transcription`; a transcript can
still be imported manually via POST /api/sessions/{id}/transcript."""
import base64
import json
import mimetypes
import re
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
    source_text: Optional[str] = None  # as spoken, before translation; None when the speech was already English


@dataclass
class STTResult:
    segments: list[STTSegment] = field(default_factory=list)
    language: Optional[str] = None
    duration_s: Optional[float] = None


class STTProvider(Protocol):
    name: str

    def transcribe(self, path: str) -> STTResult: ...


def _english(lang: Optional[str]) -> bool:
    return (lang or "").lower() in ("en", "english")


def _spoken(detected: Optional[str]) -> str:
    """English or Hindi only. Whisper hears spoken Hindi as Urdu (or Punjabi, Nepali...): anything not English is Hindi."""
    return "English" if _english(detected) else "Hindi"


TRANSLATE_BATCH = 60  # lines per LLM call
TRANSLATE_PROMPT = ('You clean up speech-recognition transcripts. Return JSON {"lines": [{"original": "...", "english": "..."}]} '
                    "with exactly one entry per input line, in order. \"original\": the same words as spoken, written in the "
                    "script normally used for the spoken language (Devanagari for Hindi; English words may stay in Latin "
                    "letters). Speech recognition often uses the wrong script, e.g. Hindi written in Urdu script: transliterate "
                    "it, do not add, drop or reword anything. \"english\": a natural English translation of that line.")


def _translate(segs: list[STTSegment]):
    """One LLM call per batch: `source_text` = the line in its proper script, `text` = English. Raises ValueError on a bad reply."""
    from . import llm  # llm imports this module's exceptions
    model = llm.get_llm()
    if model.name == "rule_based":
        raise ProviderNotConfigured("no LLM for translation")
    for i in range(0, len(segs), TRANSLATE_BATCH):
        batch = segs[i:i + TRANSLATE_BATCH]
        user = ("Spoken language: Hindi (may be mixed with English)\nEvery \"original\" MUST be written in Devanagari script, "
                "never Urdu/Arabic script.\n\n") + "\n".join(f"{n + 1}. {g.text}" for n, g in enumerate(batch))
        for _ in range(2):  # models sometimes echo the input script; one retry
            out = llm.parse_json(model.complete(TRANSLATE_PROMPT, user, json_mode=True, max_tokens=8000))["lines"]
            if len(out) != len(batch):
                raise ValueError("translation line count mismatch")
            if not any(re.search("[\u0600-\u06FF]", str(o["original"])) for o in out):
                break
        for g, o in zip(batch, out):
            g.source_text, g.text = str(o["original"]).strip() or g.text, str(o["english"]).strip() or g.text


def _attach_source(segs: list[STTSegment], orig: list[tuple[int, int, str]]):
    """Whisper's transcribe and translate passes split speech differently: each original-language piece goes to the
    English segment it overlaps most (by time)."""
    if not segs:
        return
    parts: dict[int, list[str]] = {}
    for start, end, text in orig:
        best = max(range(len(segs)), key=lambda i: (min(end, segs[i].end_ms) - max(start, segs[i].start_ms), -abs(start - segs[i].start_ms)))
        parts.setdefault(best, []).append(text)
    for i, g in enumerate(segs):
        g.source_text = " ".join(parts.get(i, [])) or None


class NoSTT:
    name = "none"

    def transcribe(self, path):
        raise ProviderNotConfigured("No speech-to-text provider configured (set STT_PROVIDER). "
                                    "You can import a transcript manually instead.")


class OpenAIWhisper:
    name = "openai"

    def _cfg(self, s):
        return s.openai_api_key, s.openai_base_url, s.stt_model

    def _post(self, path, endpoint):
        s = get_settings()
        key, base, model = self._cfg(s)
        if not key:
            raise ProviderNotConfigured(f"{self.name.upper()}_API_KEY is not set")
        data = {"model": model, "response_format": "verbose_json", "temperature": 0}
        try:
            with open(path, "rb") as f:
                r = httpx.post(f"{base}/audio/{endpoint}", data=data,
                               headers={"Authorization": f"Bearer {key}"},
                               files={"file": (path.split("/")[-1].split("\\")[-1], f)}, timeout=600)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"STT HTTP {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"STT rejected the audio: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _segs(j) -> list[STTSegment]:
        segs = [STTSegment(int(x["start"] * 1000), int(x["end"] * 1000), x["text"].strip(), confidence=_conf(x.get("avg_logprob")))
                for x in j.get("segments") or [] if x.get("text", "").strip()]
        if not segs and (j.get("text") or "").strip():
            segs = [STTSegment(0, int((j.get("duration") or 0) * 1000), j["text"].strip())]
        return segs

    def transcribe(self, path):
        # transcribe first: Whisper's own translate pass leaves Hinglish half-translated and garbles mixed speech
        j = self._post(path, "transcriptions")
        segs, lang = self._segs(j), _spoken(j.get("language"))
        if segs and lang == "Hindi":
            try:
                _translate(segs)
            except (ProviderNotConfigured, ProviderError, ValueError, KeyError, TypeError):  # no/failed LLM: Whisper translates
                orig = [(g.start_ms, g.end_ms, g.text) for g in segs]
                segs = self._segs(self._post(path, "translations"))
                _attach_source(segs, orig)
        for g in segs:
            g.language = lang
        return STTResult(segs, lang, j.get("duration"))


class Groq(OpenAIWhisper):
    """Groq hosts Whisper behind an OpenAI-compatible endpoint (25MB free-tier upload cap)."""
    name = "groq"

    def _cfg(self, s):
        return s.groq_api_key, "https://api.groq.com/openai/v1", s.groq_stt_model


class Deepgram:
    """Deepgram pre-recorded API (batch). Streaming for live lookup is a separate, later piece."""
    name = "deepgram"

    def transcribe(self, path):
        s = get_settings()
        if not s.deepgram_api_key:
            raise ProviderNotConfigured("DEEPGRAM_API_KEY is not set")
        params = {"model": s.deepgram_model, "smart_format": "true", "utterances": "true"}
        params = list(params.items()) + [("detect_language", "en"), ("detect_language", "hi")]
        try:
            with open(path, "rb") as f:
                r = httpx.post("https://api.deepgram.com/v1/listen", params=params, content=f,
                               headers={"Authorization": f"Token {s.deepgram_api_key}",
                                        "Content-Type": "application/octet-stream"}, timeout=600)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"STT HTTP {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"STT rejected the audio: {r.text[:300]}")
        j = r.json()
        ch = (j.get("results", {}).get("channels") or [{}])[0]
        lang = _spoken(ch.get("detected_language"))
        segs = [STTSegment(int(u["start"] * 1000), int(u["end"] * 1000), u["transcript"].strip(),
                           confidence=u.get("confidence"), language=lang)
                for u in j.get("results", {}).get("utterances") or [] if u.get("transcript", "").strip()]
        dur = (j.get("metadata") or {}).get("duration")
        if not segs:
            text = ((ch.get("alternatives") or [{}])[0]).get("transcript", "").strip()
            if text:
                segs = [STTSegment(0, int((dur or 0) * 1000), text, language=lang)]
        return STTResult(segs, lang, dur)


class Gemini:
    """Gemini transcribes by prompting generateContent with inline audio; timestamps are model-estimated, not forced-aligned."""
    name = "gemini"
    # ponytail: inline upload caps at ~20MB per request; switch to the Files API for longer recordings
    MAX_BYTES = 18 * 1024 * 1024
    PROMPT = ('Transcribe this audio verbatim. Return JSON: {"language": "<ISO code>", "segments": '
              '[{"start": "MM:SS", "end": "MM:SS", "text": "<utterance>", "original": "<utterance as spoken>"}]}. Return no segments if there is no speech.')

    def transcribe(self, path):
        s = get_settings()
        if not s.gemini_api_key:
            raise ProviderNotConfigured("GEMINI_API_KEY is not set")
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > self.MAX_BYTES:
            raise RuntimeError("audio too large for inline Gemini transcription (limit 18MB)")
        mime = mimetypes.guess_type(path)[0] or "audio/mp4"
        if mime == "video/webm":  # browser MediaRecorder audio; Gemini rejects it as video
            mime = "audio/webm"
        if mime == "audio/mp4a-latin":
            mime = "audio/mp4"
        prompt = (self.PROMPT + " The speech is English, Hindi, or a mix of both."
                  ' Translate everything into English: every segment "text" must be English. "original" is the same utterance exactly as'
                  ' spoken, untranslated, in its native script (Hindi in Devanagari, never Urdu script). "language" is the original spoken language.')
        body = {"contents": [{"parts": [{"text": prompt},
                                        {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode()}}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        try:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_stt_model}:generateContent",
                           json=body, headers={"x-goog-api-key": s.gemini_api_key}, timeout=600)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"STT HTTP {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"STT rejected the audio: {r.text[:300]}")
        try:
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
            j = json.loads(text)
        except (KeyError, IndexError, ValueError):
            raise ProviderError("Gemini returned an unparseable transcript")
        lang = _spoken(j.get("language"))
        segs = [STTSegment(_ms(x.get("start")), _ms(x.get("end")),
                           x["text"].strip(), language=lang,
                           source_text=str(x.get("original") or "").strip() or None if lang == "Hindi" else None)
                for x in j.get("segments") or [] if str(x.get("text", "")).strip()]
        return STTResult(segs, lang, segs[-1].end_ms / 1000 if segs else None)


class FasterWhisper:
    name = "faster_whisper"
    _model = None

    def transcribe(self, path):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ProviderNotConfigured("pip install faster-whisper to use STT_PROVIDER=faster_whisper")
        if FasterWhisper._model is None:
            FasterWhisper._model = WhisperModel(get_settings().stt_model or "small", compute_type="int8")
        it, info = FasterWhisper._model.transcribe(path, task="translate", vad_filter=True)
        lang = _spoken(info.language)
        segs = [STTSegment(int(x.start * 1000), int(x.end * 1000), x.text.strip(),
                           confidence=_conf(x.avg_logprob), language=lang)
                for x in it if x.text.strip()]
        if segs and lang == "Hindi":
            orig, _ = FasterWhisper._model.transcribe(path, task="transcribe", language="hi", vad_filter=True)
            _attach_source(segs, [(int(x.start * 1000), int(x.end * 1000), x.text.strip()) for x in orig if x.text.strip()])
        return STTResult(segs, lang, info.duration)


def _ms(ts) -> int:
    """Gemini timestamps come back as "MM:SS" (or "HH:MM:SS") strings; fall back to plain seconds."""
    try:
        sec = 0.0
        for part in str(ts or 0).split(":"):
            sec = sec * 60 + float(part)
        return int(sec * 1000)
    except ValueError:
        return 0


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
    return {"openai": OpenAIWhisper, "groq": Groq, "gemini": Gemini, "deepgram": Deepgram, "faster_whisper": FasterWhisper}.get(get_settings().stt_provider, NoSTT)()
