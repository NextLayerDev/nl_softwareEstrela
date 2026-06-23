# syntax=docker/dockerfile:1
# Imagem de produção do Estrela Gestão (FastAPI + Gunicorn/Uvicorn).
# Base slim + libs nativas do WeasyPrint (Pango/Cairo/GDK-Pixbuf) para geração de PDF.

FROM python:3.12-slim

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
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

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

# Entrypoint aplica migrations e sobe o servidor de produção.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
