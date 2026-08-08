from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    test_database_url: str = ""
    fernet_key: str
    alpaca_endpoint: str = "https://paper-api.alpaca.markets"
    finnhub_api_key: str = ""  # optional; research endpoints degrade gracefully without it
    openai_api_key: str = ""  # optional; the AI advisory layer degrades gracefully without it
    openai_model: str = "gpt-5.6"  # model used for every AI feature; swappable via OPENAI_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()
