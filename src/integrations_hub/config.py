from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/integrations_hub"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/integrations_hub"

    # Delivery worker
    delivery_poll_interval_seconds: float = 2.0
    delivery_max_attempts: int = 5
    delivery_backoff_base_seconds: float = 2.0
    delivery_timeout_seconds: float = 10.0

    # Slack connector
    slack_bot_token: str = ""
    slack_default_channel: str = "#integrations"

    log_level: str = "INFO"

    model_config = {"env_prefix": "IH_"}


settings = Settings()
