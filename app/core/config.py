from __future__ import annotations

import re
from functools import lru_cache

from pydantic import field_validator, model_validator
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

    # HTTPS de verdade na frente da app (Caddy terminando TLS)? Controla a flag `Secure` do
    # cookie de sessão. Default False porque o servidor da cliente hoje serve por HTTP na LAN
    # — e um cookie `Secure` sobre HTTP NUNCA volta ao servidor: o login entra em loop
    # infinito (o usuário faz login, o cookie é setado mas o navegador não o reenvia). Ligue
    # (HTTPS_ENABLED=true) SÓ quando houver TLS de fato terminando na frente, senão o
    # cookie de sessão trafega sem a proteção que a flag anuncia.
    HTTPS_ENABLED: bool = False

    # Realtime (WebSocket). O barramento é o próprio Postgres via LISTEN/NOTIFY: funciona
    # entre os workers do Gunicorn e sobrevive ao job/importador, que commitam fora do request.
    REALTIME_ENABLED: bool = True
    REALTIME_CHANNEL: str = "estrela_eventos"

    # Identidade do build, injetada como build-arg na imagem (ver Dockerfile). Em dev
    # ficam vazias e o app/core/versao.py cai no git local. NUNCA entram no validador de
    # produção: build sem versão é feio na tela, não é motivo para o sistema não subir.
    APP_VERSION: str = ""
    GIT_SHA: str = ""
    BUILD_DATE: str = ""
    APP_TAG: str = ""

    # Consulta ao CI do GitHub para o card da aba /deploy. O repositório é PÚBLICO, então
    # o token é opcional — sem ele o limite é 60 req/h por IP, e o job consulta 1x a cada
    # 5 min. Fixos em config (nunca vêm de input do usuário) para não virar SSRF.
    GITHUB_OWNER: str = "NextLayerDev"
    GITHUB_REPO: str = "nl_softwareEstrela"
    GITHUB_TOKEN_LEITURA: str = ""
    CI_CACHE_TTL_SEG: int = 300

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
        """Hosts aceitos, com o loopback SEMPRE incluído quando a checagem está ligada.

        O HEALTHCHECK do container bate em http://127.0.0.1:8000/health. Sem o loopback na
        lista, restringir ALLOWED_HOSTS a "sistema.local" faria o TrustedHostMiddleware
        recusar o próprio healthcheck: o container nunca ficaria healthy, o
        `depends_on: service_healthy` do Caddy travaria e o sistema inteiro não subiria —
        por uma mudança que parece endurecer a segurança.
        """
        hosts = [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]
        if "*" in hosts:
            return hosts
        for loopback in ("127.0.0.1", "localhost"):
            if loopback not in hosts:
                hosts.append(loopback)
        return hosts

    @property
    def github_habilitado(self) -> bool:
        """Sem owner/repo, o card do CI nem tenta a rede (e a aba continua 100% local)."""
        return bool(self.GITHUB_OWNER and self.GITHUB_REPO)

    @field_validator("GITHUB_OWNER", "GITHUB_REPO")
    @classmethod
    def _github_sem_barra(cls, v: str) -> str:
        """Anti-SSRF: owner/repo entram numa URL. Só o alfabeto que o GitHub aceita.

        Sem isso, um valor como "../../algum/outro" no .env redirecionaria a consulta
        para outro endpoint da API.
        """
        v = v.strip()
        if v and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", v):
            raise ValueError("GITHUB_OWNER/GITHUB_REPO aceitam apenas [A-Za-z0-9._-].")
        return v

    @property
    def libpq_url(self) -> str:
        """DATABASE_URL sem o dialeto do SQLAlchemy, para o psycopg cru do listener.

        O DATABASE_URL é 'postgresql+psycopg://…' e o psycopg não entende o '+psycopg'.
        """
        from sqlalchemy.engine import make_url

        return (
            make_url(self.DATABASE_URL)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )

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
