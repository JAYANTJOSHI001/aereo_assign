"""Application configuration management via pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and .env file."""

    DATABASE_URL: str = "sqlite:///./data/app.db"
    STORAGE_DIR: str = "./storage"
    MAX_RECIPIENTS: int = 1000
    WORKERS: int = 4
    EAGER: bool = False
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
