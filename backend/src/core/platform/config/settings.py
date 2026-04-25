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

    # Providers - GNews (testing first)
    gnews_api_key: str | None = None
    gnews_enabled: bool = True

    # LLM fallback categoriser
    anthropic_api_key: str | None = None
    fallback_model: str = "claude-haiku-4-5-20251001"

    # TTS
    tts_provider: str = "elevenlabs"
    tts_voice: str = "JBFqnCBsd6RMkjVDRZzb"   # ElevenLabs "George" — British male
    tts_model: str = "eleven_turbo_v2_5"
    tts_audio_format: str = "mp3_44100_128"
    elevenlabs_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()