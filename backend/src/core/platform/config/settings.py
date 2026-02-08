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


settings = Settings()