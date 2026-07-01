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

    # Storage de imagens (MinIO / S3-compatible). S3_ENDPOINT_URL é o endpoint usado pelo
    # servidor para enviar/apagar objetos (na VPS, aponta pro hostname interno do serviço —
    # mais rápido, sem sair da rede). S3_PUBLIC_URL é o domínio público usado para montar a
    # URL salva no banco e exibida no <img src="">.
    S3_ENDPOINT_URL: str = "https://api-nextpy-minio.1nwz76.easypanel.host"
    S3_PUBLIC_URL: str = "https://api-nextpy-minio.1nwz76.easypanel.host"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = "estrela-uploads"

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
