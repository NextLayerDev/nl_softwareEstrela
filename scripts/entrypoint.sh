#!/usr/bin/env bash
# Entrypoint do container app (produção).
# 1) Aplica migrations do Alembic.
# 2) Sobe o Gunicorn com worker Uvicorn.
set -euo pipefail

echo "[entrypoint] Aplicando migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Iniciando Gunicorn (3 workers Uvicorn) em 0.0.0.0:8000..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w 3 \
    -b 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile -
