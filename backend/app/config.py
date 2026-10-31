from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-insecure-change-me-to-a-long-secret-key-32-bytes"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "development"
    secret_key: str = DEFAULT_SECRET
    database_url: str = "sqlite:///./newsapiens.db"
    cors_origins: str = "http://localhost:3000"
    cookie_secure: bool = False
    access_token_minutes: int = 60 * 12
    register_enabled: bool = True
    api_token: str = ""  # static bearer token for the stateless /api/process endpoint (wearable companion app)

    storage_dir: str = "./storage"
    max_upload_mb: int = 500
    signed_url_ttl_seconds: int = 900

    job_backend: str = "thread"
    scheduler_enabled: bool = True

    stt_provider: str = "none"
    stt_languages: str = "en,hi"  # default preferred languages (ISO codes, comma-separated); users can override in Settings
    stt_model: str = "whisper-1"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3"
    gemini_api_key: str = ""
    gemini_stt_model: str = "gemini-2.5-flash"
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"

    llm_provider: str = "rule_based"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_fallback_model: str = ""  # Groq model (uses GROQ_API_KEY) tried when the main LLM is rate-limited/down
    anthropic_api_key: str = ""

    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 256

    rate_limit_per_minute: int = 240
    auth_rate_limit_per_minute: int = 10
    pdf_font_path: str = ""

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_prod and s.secret_key == DEFAULT_SECRET:
        raise RuntimeError("SECRET_KEY must be set in production")
    return s
