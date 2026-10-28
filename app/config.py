from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_base_url: str = "http://localhost:8000"
    database_path: str = "data/patients.db"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    twilio_auth_token: str = ""
    validate_twilio_signatures: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def ensure_data_directory(self) -> None:
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

