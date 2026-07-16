#!/usr/bin/env bash
# Entrypoint do container app (produção).
# 1) Ajusta o schema com o migrar_seguro (NÃO com `alembic upgrade head` cru).
# 2) Sobe o Gunicorn com worker Uvicorn.
set -euo pipefail

# Por que não `alembic upgrade head` aqui:
# num rollback, a imagem ANTIGA sobe contra um banco que a imagem NOVA já migrou. O
# `upgrade head` cru manda o Alembic resolver uma revision que não existe no histórico
# daquele código, morre com "Can't locate revision identified by ...", e o
# `restart: always` do compose transforma isso num CRASHLOOP — o sistema fica fora do ar
# exatamente na hora em que o rollback deveria estar salvando o dia.
# O migrar_seguro distingue banco vazio / atrás / à frente e só sobe sem migrar no
# terceiro caso (expand/contract). Ver scripts/migrar_seguro.py.
echo "[entrypoint] Ajustando o schema (migrar_seguro)..."
python /app/scripts/migrar_seguro.py

echo "[entrypoint] Iniciando Gunicorn (3 workers Uvicorn) em 0.0.0.0:8000..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w 3 \
    -b 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile -
