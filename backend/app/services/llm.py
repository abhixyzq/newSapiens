"""LLM provider seam. `rule_based` means: no LLM; callers use their deterministic path.
Implemented over plain HTTP: `openai` (any OpenAI-compatible server) and `anthropic`.
Customer data is only sent to the provider you configure; nothing is sent when LLM_PROVIDER=rule_based."""
import json
import time
from typing import Optional

import httpx

from ..config import get_settings
from .stt import ProviderError, ProviderNotConfigured


class LLM:
    name = "rule_based"

    def complete(self, system: str, user: str, *, json_mode=False, max_tokens=4000) -> str:
        raise ProviderNotConfigured("no LLM configured")


class OpenAICompat(LLM):
    name = "openai"

    def complete(self, system, user, *, json_mode=False, max_tokens=4000):
        s = get_settings()
        if not (s.openai_api_key and s.llm_model):
            raise ProviderNotConfigured("OPENAI_API_KEY and LLM_MODEL are required")
        body = {"model": s.llm_model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            return _chat(s.llm_base_url or s.openai_base_url, s.openai_api_key, body)
        except ProviderError:
            if not (s.llm_fallback_model and s.groq_api_key):
                raise
            # ponytail: one hard-wired Groq fallback; its free tier is 8k tokens/min, so very long prompts still fail here
            return _chat("https://api.groq.com/openai/v1", s.groq_api_key, {**body, "model": s.llm_fallback_model})


class AnthropicLLM(LLM):
    name = "anthropic"

    def complete(self, system, user, *, json_mode=False, max_tokens=4000):
        s = get_settings()
        if not (s.anthropic_api_key and s.llm_model):
            raise ProviderNotConfigured("ANTHROPIC_API_KEY and LLM_MODEL are required")
        r = _post("https://api.anthropic.com/v1/messages",
                  headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                  json={"model": s.llm_model, "max_tokens": max_tokens, "system": system,
                        "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in r.json()["content"])


def _chat(base_url: str, key: str, body: dict) -> str:
    r = _post(f"{base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    content = r.json()["choices"][0]["message"].get("content")
    if not content:
        raise ProviderError("empty LLM reply")
    return content


def _post(url: str, **kw) -> httpx.Response:
    """POST with a short backoff on 429/5xx: free-tier per-minute quotas trip on a burst of asks. A per-day quota won't clear
    by waiting, so that fails at once."""
    for wait in (2, 5, 10, 0):
        try:
            r = httpx.post(url, timeout=180, **kw)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code != 429 and r.status_code < 500:
            r.raise_for_status()
            return r
        if "per day" in r.text.lower():
            break
        time.sleep(wait)
    raise ProviderError(f"LLM HTTP {r.status_code}: {r.text[:200]}")


_override: Optional[LLM] = None


def set_provider(p: Optional[LLM]):
    global _override
    _override = p


def get_llm() -> LLM:
    if _override:
        return _override
    return {"openai": OpenAICompat, "anthropic": AnthropicLLM}.get(get_settings().llm_provider, LLM)()


def llm_enabled() -> bool:
    return get_llm().name != "rule_based"


def parse_json(text: str):
    """First JSON object in the reply; tolerates code fences and prose before/after it. Raises ValueError if none."""
    i = text.find("{")
    if i < 0:
        raise ValueError("no JSON object in LLM reply")
    return json.JSONDecoder().raw_decode(text[i:])[0]
