from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5433/support"
    redis_url: str = "redis://localhost:6379/0"
    payment_provider_url: str = "http://localhost:9001"
    jwt_secret: str = "change-me-in-real-use"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "support-resolution-agent"
    jwt_audience: str = "support-api"
    jwt_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
