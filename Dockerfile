# syntax=docker/dockerfile:1
# Imagem de produção do Estrela Gestão (FastAPI + Gunicorn/Uvicorn).
# Base slim + libs nativas do WeasyPrint (Pango/Cairo/GDK-Pixbuf) para geração de PDF.

# Base pinada por digest: `python:3.12-slim` é tag móvel, e dois builds do MESMO commit
# do nosso código produziriam imagens diferentes — o que corrói justamente a garantia que
# o deploy por digest existe para comprar. O Dependatbot atualiza o digest via PR.
# Corresponde a python:3.12-slim em 2026-07-16.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# Evita prompts de apt e melhora logs do Python no container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependências nativas:
#  - WeasyPrint: libpango-1.0-0, libpangoft2-1.0-0, libcairo2, libgdk-pixbuf-2.0-0, libffi-dev
#  - utilidades: fontes, tzdata, e cliente de saúde do banco (não obrigatório)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        fonts-dejavu-core \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fuso horário do cliente (Brasil).
ENV TZ=America/Sao_Paulo

# Instala o uv (gerenciador de pacotes Astral) a partir da imagem oficial.
# Também pinado por digest: `:latest` aqui significava que a ferramenta que resolve as
# dependências podia mudar sozinha entre dois builds do mesmo código.
COPY --from=ghcr.io/astral-sh/uv@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /usr/local/bin/

WORKDIR /app

# 1) Instala dependências primeiro (camada cacheável) usando o lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Copia o restante do projeto.
COPY . .

# 3) Instala o próprio projeto (sem dev) já com o código presente.
RUN uv sync --frozen --no-dev

# As dependências ficam no virtualenv .venv criado pelo uv; expõe no PATH.
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

# Identidade do build. Fica no FIM de propósito: um ARG só invalida o cache a partir do
# ponto em que é consumido, então o `uv sync` acima continua vindo do cache mesmo com a
# versão mudando a cada release. Sem isto, a aba /deploy não tem o que mostrar — não há
# .git na imagem (o .dockerignore o exclui).
ARG APP_VERSION=dev
ARG GIT_SHA=""
ARG BUILD_DATE=""
ARG APP_TAG=""
ENV APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA} \
    BUILD_DATE=${BUILD_DATE} \
    APP_TAG=${APP_TAG}

# Labels OCI + os que o agente de deploy lê antes de trocar a imagem (ele falha FECHADO
# se não conseguir lê-los).
LABEL org.opencontainers.image.title="Estrela Gestão" \
      org.opencontainers.image.description="Sistema local de estoque e pedidos" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/NextLayerDev/nl_softwareEstrela" \
      org.opencontainers.image.licenses="UNLICENSED"

# Liveness. Usa urllib (stdlib): a imagem slim NÃO tem curl nem wget — o comando de
# healthcheck do guia de instalação (§5) depende de curl e provavelmente já falha hoje.
# Aponta para /health (estático) e nunca para /health/ready: uma piscada do banco marcaria
# o container unhealthy e o `depends_on: service_healthy` do Caddy travaria a subida.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Entrypoint aplica migrations e sobe o servidor de produção.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
