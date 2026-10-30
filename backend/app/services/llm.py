"""LLM provider seam. `rule_based` means: no LLM; callers use their deterministic path.
Implemented over plain HTTP: `openai` (any OpenAI-compatible server) and `anthropic`.
Customer data is only sent to the provider you configure; nothing is sent when LLM_PROVIDER=rule_based."""
import json
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
            r = httpx.post(f"{s.llm_base_url or s.openai_base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {s.openai_api_key}"}, timeout=180)
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"LLM HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AnthropicLLM(LLM):
    name = "anthropic"

    def complete(self, system, user, *, json_mode=False, max_tokens=4000):
        s = get_settings()
        if not (s.anthropic_api_key and s.llm_model):
            raise ProviderNotConfigured("ANTHROPIC_API_KEY and LLM_MODEL are required")
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=180,
                           headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                           json={"model": s.llm_model, "max_tokens": max_tokens, "system": system,
                                 "messages": [{"role": "user", "content": user}]})
        except httpx.HTTPError as e:
            raise ProviderError(str(e))
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"LLM HTTP {r.status_code}")
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])


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
