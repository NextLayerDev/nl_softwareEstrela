from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Segredos triviais/de exemplo que NUNCA podem ir para produção. Se o .env.prod não for
# preenchido, o startup falha em vez de subir com uma chave que qualquer um conhece.
_SEGREDOS_FRACOS = {
    "",
    "troque-isto",
    "troque-isto-por-uma-chave-longa-e-aleatoria",
    "dev-secret-nao-usar-em-producao-troque-isto",
    "dev-only-inseguro-troque-em-producao-com-openssl",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Em dev, estes defaults deixam a app subir sem .env. Em produção, o validador abaixo
    # exige valores fortes vindos do .env.prod — nenhum segredo de verdade fica no código.
    DATABASE_URL: str = "postgresql+psycopg://estrela:senha@localhost:5432/estrela_gestao"
    JWT_SECRET: str = "dev-only-inseguro-troque-em-producao-com-openssl"
    JWT_EXPIRES_MIN: int = 120
    JWT_ALGORITHM: str = "HS256"
    ENV: str = "dev"

    # Hosts aceitos pelo TrustedHostMiddleware em produção (barra Host-header spoofing).
    # Em dev não é aplicado (o TestClient usa "testserver"). Aceita curingas (*.easypanel.host).
    # "*" = desliga a checagem (qualquer host). Por ora liberado; dá pra restringir depois
    # setando ALLOWED_HOSTS no .env.prod (ex.: "sistema.local,*.easypanel.host").
    ALLOWED_HOSTS: str = "*"

    # Legado MinIO/S3: as fotos das variações agora moram no Postgres (bytea) — ver
    # app/core/imagens.py e a migration b7e2c9f4a1d8. Estas settings só são lidas pela
    # migration de backfill (para baixar as fotos antigas do bucket, se o servidor
    # conseguir alcançá-lo). Em runtime NADA usa mais S3; podem ficar vazias.
    S3_ENDPOINT_URL: str = "https://api-nextpy-minio.1nwz76.easypanel.host"
    S3_PUBLIC_URL: str = "https://api-nextpy-minio.1nwz76.easypanel.host"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = "estrela-uploads"
    S3_URL_EXPIRA_SEG: int = 3600

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @model_validator(mode="after")
    def _exige_segredos_fortes_em_prod(self) -> Settings:
        """Fail-fast: em produção, recusa segredos triviais/de exemplo ou banco com senha padrão."""
        if self.ENV != "prod":
            return self
        # Só os segredos críticos de auth/banco são fatais. Credenciais S3 ausentes NÃO
        # derrubam a app (imagem é feature opcional; o upload falha com erro amigável).
        erros: list[str] = []
        if self.JWT_SECRET in _SEGREDOS_FRACOS or len(self.JWT_SECRET) < 32:
            erros.append("JWT_SECRET fraco ou ausente (gere: openssl rand -hex 32).")
        if ":senha@" in self.DATABASE_URL:
            erros.append("DATABASE_URL usa a senha padrão 'senha'.")
        if erros:
            raise RuntimeError("Configuração insegura para produção: " + " ".join(erros))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
