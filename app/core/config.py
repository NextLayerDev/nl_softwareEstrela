from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://estrela:senha@localhost:5432/estrela_gestao"
    JWT_SECRET: str = "troque-isto"
    JWT_EXPIRES_MIN: int = 480
    JWT_ALGORITHM: str = "HS256"
    ENV: str = "dev"

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
