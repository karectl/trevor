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

    # Agent
    agent_openai_base_url: str = ""
    agent_model_name: str = "gpt-4o"
    agent_api_key: str = ""
    agent_llm_enabled: bool = False
    agent_min_cell_count: int = 10
    agent_dominance_p: int = 70

    # Release
    presigned_url_ttl: int = 604800  # 7 days

    # Admin
    stuck_request_hours: int = 72

    # App
    app_title: str = "trevor"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "console"
    secret_key: str = "dev-secret-key-change-in-prod"  # noqa: S105
    max_upload_size_mb: int = 500

    # OpenTelemetry
    otel_enabled: bool = False
    otel_exporter_endpoint: str = "http://otel-collector:4317"
    otel_service_name: str = "trevor"


def get_settings() -> Settings:
    return Settings()
