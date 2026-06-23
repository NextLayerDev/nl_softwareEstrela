#!/usr/bin/env bash
# Cópia offsite criptografada dos backups locais.
# Usa rclone para sincronizar o diretório de backup com um remote criptografado
# (placeholder: "b2_encrypted" — configure com: rclone config, tipo "crypt" sobre
# um remote Backblaze B2 / S3). Só executa se houver internet (ping de checagem).
#   30 2 * * * /opt/estrela/scripts/backup-offsite.sh >> /var/log/estrela-offsite.log 2>&1
set -euo pipefail

# --- Configuração ----------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/backup/estrela_gestao}"
RCLONE_REMOTE="${RCLONE_REMOTE:-b2_encrypted}"
RCLONE_PATH="${RCLONE_PATH:-estrela_gestao}"
PING_HOST="${PING_HOST:-1.1.1.1}"
# ---------------------------------------------------------------------------

# Checa conectividade antes de tentar o sync (operação é offline-first).
if ! ping -c 1 -W 3 "${PING_HOST}" >/dev/null 2>&1; then
    echo "[offsite] Sem internet (ping ${PING_HOST} falhou). Pulando offsite."
    exit 0
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "[offsite] ERRO: rclone não instalado." >&2
    exit 1
fi

echo "[offsite] Internet OK. Sincronizando ${BACKUP_DIR} -> ${RCLONE_REMOTE}:${RCLONE_PATH}"
rclone sync "${BACKUP_DIR}" "${RCLONE_REMOTE}:${RCLONE_PATH}" \
    --transfers 4 \
    --checkers 8 \
    --fast-list \
    --log-level INFO

echo "[offsite] Concluído."
