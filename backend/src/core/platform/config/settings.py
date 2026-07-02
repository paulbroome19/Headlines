from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    env: str = "dev"

    # Infra
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/headlines"

    @field_validator("database_url")
    @classmethod
    def _force_psycopg_driver(cls, v: str) -> str:
        # Managed Postgres providers (Railway, Heroku, etc.) inject the URL as
        # postgresql:// or the legacy postgres://, which SQLAlchemy routes to the
        # psycopg2 dialect — not installed here. This app depends on psycopg v3,
        # so normalise the scheme to postgresql+psycopg:// regardless of source.
        if v.startswith("postgresql+"):
            return v  # driver already specified explicitly — leave untouched
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://"):]
        return v
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_events: str = "events"
    redis_consumer_group: str = "headlines"
    redis_consumer_name: str = "consumer-1"

    # Storage
    audio_local_dir: str = ".local/audio"
    audio_storage_provider: str = "local"   # local | s3

    # S3 / S3-compatible storage (required when audio_storage_provider=s3)
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_endpoint_url: str | None = None       # custom endpoint (R2, Spaces, MinIO)
    s3_public_base_url: str | None = None    # CDN / R2 public domain override

    # Providers - GNews (testing first)
    gnews_api_key: str | None = None
    gnews_enabled: bool = True

    # LLM fallback categoriser
    anthropic_api_key: str | None = None
    fallback_model: str = "claude-haiku-4-5-20251001"
    connective_model: str = "claude-sonnet-4-6"
    enable_llm_categorise_fallback: bool = True
    # LLM-PRIMARY categoriser: cluster-level Haiku classification is the primary path
    # (keyword majority survives only as a fallback). Set false to revert to the legacy
    # keyword-majority vote (e.g. if the API key is absent and cost/latency matters).
    enable_llm_primary_categorise: bool = True

    # Scheduled ingest
    enable_scheduled_ingest: bool = False

    # ── One-day ingest BURST override (temporary; docs/ingestion-burst.md) ──────
    # When INGEST_BURST_MODE=true, the scheduler ignores the tiered grid and fires
    # the full pool grid in fast rotation (~ingest_burst_batch pools every
    # ingest_burst_interval_sec) until the daily hard cap is spent, then idles until
    # UTC midnight. One-shot: the burst only applies on the UTC day it was enabled;
    # after midnight it auto-reverts to the normal tiered schedule even if the flag
    # is left on. ingest_daily_hard_cap is the restart-safe ceiling on scheduler
    # requests per UTC day (guards against blowing past the paid GNews budget).
    ingest_burst_mode: bool = False
    ingest_burst_batch: int = 5           # pools fired per tick (~5/min → fast fill)
    ingest_burst_interval_sec: int = 60   # seconds between ticks
    ingest_daily_hard_cap: int = 1000     # hard ceiling on scheduler requests / UTC day

    # Public API base URL — used to construct absolute URLs in manifest responses
    public_api_base_url: str = "http://localhost:8000"

    # ── API protection (issue #19) ────────────────────────────────────────────
    # Single shared app key gating /data/* and /feeds/*. Enforcement is OFF by
    # default so deploying this never breaks the running app — flip REQUIRE_API_KEY
    # to true (with API_ACCESS_KEY set) only once the iOS build that sends the key
    # is live. Audio delivery (/data/segments, /audio/outputs, /dev/api/audio) and
    # /health are always exempt.
    require_api_key: bool = False
    api_access_key: str | None = None

    # Dev/inspection endpoints (/dev/api/*, /data/ingest/test, /data/*/latest,
    # /scripts/*) are disabled in production by default — set true locally to use
    # them via curl. The app's /dev/api/audio/{id}/file download is NEVER gated.
    enable_dev_endpoints: bool = False

    # Per-IP rate limit on expensive (LLM/TTS) endpoints — a cost/DoS backstop,
    # not a quota system. Generous for ~20 users; caps a runaway loop.
    rate_limit_max: int = 20
    rate_limit_window_seconds: int = 60

    # TTS
    tts_provider: str = "elevenlabs"
    tts_voice: str = "19STyYD15bswVz51nqLf"   # ElevenLabs voice (default; override via TTS_VOICE env)
    tts_model: str = "eleven_turbo_v2_5"
    tts_audio_format: str = "mp3_44100_128"
    elevenlabs_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()