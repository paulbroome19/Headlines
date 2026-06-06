from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    env: str = "dev"

    # Infra
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/headlines"
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
    enable_llm_categorise_fallback: bool = True

    # Scheduled ingest
    enable_scheduled_ingest: bool = False
    ingest_interval_minutes: int = 30

    # Public API base URL — used to construct absolute URLs in manifest responses
    public_api_base_url: str = "http://localhost:8000"

    # TTS
    tts_provider: str = "elevenlabs"
    tts_voice: str = "JBFqnCBsd6RMkjVDRZzb"   # ElevenLabs "George" — British male
    tts_model: str = "eleven_turbo_v2_5"
    tts_audio_format: str = "mp3_44100_128"
    elevenlabs_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()