#!/usr/bin/env bash
# Backup diário do banco do Estrela Gestão.
# Faz pg_dump do container "db", comprime com gzip e aplica rotação de 14 dias.
# Uso típico: cron na madrugada no host (mini PC).
#   0 2 * * * /opt/estrela/scripts/backup-estrela.sh >> /var/log/estrela-backup.log 2>&1
set -euo pipefail

# --- Configuração (ajuste conforme o ambiente do cliente) -------------------
DB_CONTAINER="${DB_CONTAINER:-estrela_softwarelocal-db-1}"
DB_USER="${DB_USER:-estrela}"
DB_NAME="${DB_NAME:-estrela_gestao}"
BACKUP_DIR="${BACKUP_DIR:-/backup/estrela_gestao}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
# ---------------------------------------------------------------------------

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="${BACKUP_DIR}/estrela_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[backup] Iniciando pg_dump de ${DB_NAME} (container ${DB_CONTAINER}) -> ${OUTFILE}"
docker exec -i "${DB_CONTAINER}" pg_dump -U "${DB_USER}" "${DB_NAME}" | gzip > "${OUTFILE}"

# Valida que o arquivo não ficou vazio.
if [ ! -s "${OUTFILE}" ]; then
    echo "[backup] ERRO: dump vazio, removendo ${OUTFILE}" >&2
    rm -f "${OUTFILE}"
    exit 1
fi

echo "[backup] OK: $(du -h "${OUTFILE}" | cut -f1) em ${OUTFILE}"

echo "[backup] Aplicando rotação (>${RETENTION_DAYS} dias) em ${BACKUP_DIR}"
find "${BACKUP_DIR}" -name 'estrela_*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] Concluído."
