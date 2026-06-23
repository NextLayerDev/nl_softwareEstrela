#!/usr/bin/env bash
# Restauração de um dump .sql.gz para o banco do Estrela Gestão.
# Descomprime e injeta via psql dentro do container "db".
#   ./scripts/restore-estrela.sh /backup/estrela_gestao/estrela_20260623_020000.sql.gz
#
# ATENÇÃO: sobrescreve dados existentes. Pare o app antes:
#   docker compose -f docker-compose.prod.yml stop app
set -euo pipefail

# --- Configuração ----------------------------------------------------------
DB_CONTAINER="${DB_CONTAINER:-estrela_softwarelocal-db-1}"
DB_USER="${DB_USER:-estrela}"
DB_NAME="${DB_NAME:-estrela_gestao}"
# ---------------------------------------------------------------------------

DUMP_FILE="${1:-}"

if [ -z "${DUMP_FILE}" ]; then
    echo "Uso: $0 <arquivo.sql.gz>" >&2
    exit 1
fi

if [ ! -f "${DUMP_FILE}" ]; then
    echo "[restore] ERRO: arquivo não encontrado: ${DUMP_FILE}" >&2
    exit 1
fi

echo "[restore] ATENÇÃO: isto vai restaurar ${DUMP_FILE} sobre ${DB_NAME}."
read -r -p "Confirma? (digite 'sim'): " CONFIRM
if [ "${CONFIRM}" != "sim" ]; then
    echo "[restore] Cancelado."
    exit 0
fi

echo "[restore] Restaurando ${DUMP_FILE} -> ${DB_NAME} (container ${DB_CONTAINER})..."
zcat "${DUMP_FILE}" | docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}"

echo "[restore] Concluído. Suba o app novamente:"
echo "  docker compose -f docker-compose.prod.yml start app"
