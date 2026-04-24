"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite+aiosqlite:///./local/trevor.db"

    # Redis / ARQ
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    dev_auth_bypass: bool = False
    keycloak_url: str = ""
    keycloak_realm: str = "karectl"
    keycloak_client_id: str = "trevor"

    # S3
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_quarantine_bucket: str = "trevor-quarantine"
    s3_release_bucket: str = "trevor-release"
    s3_region: str = "us-east-1"

    # App
    app_title: str = "trevor"
    app_version: str = "0.1.0"
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
