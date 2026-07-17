#!/usr/bin/env bash
# Backup diário do banco do Estrela Gestão.
# Faz pg_dump do container "db", comprime com gzip e aplica rotação de 14 dias.
# Uso típico: cron na madrugada no host (mini PC).
#   0 2 * * * /opt/estrela/scripts/backup-estrela.sh >> /var/log/estrela-backup.log 2>&1
set -euo pipefail

# --- Configuração (ajuste conforme o ambiente do cliente) -------------------
DB_USER="${DB_USER:-estrela}"
DB_NAME="${DB_NAME:-estrela_gestao}"
BACKUP_DIR="${BACKUP_DIR:-/backup/estrela_gestao}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PROJETO_DIR="${PROJETO_DIR:-/opt/estrela}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJETO_DIR}/docker-compose.prod.yml}"
# ---------------------------------------------------------------------------

# O nome do container é DERIVADO, não chutado. O Compose o monta a partir do nome do
# diretório do projeto, então um default fixo só funciona por coincidência: o antigo era
# "estrela_softwarelocal-db-1" (o diretório de DESENVOLVIMENTO), enquanto no servidor o
# projeto vive em /opt/estrela e o container se chama "estrela-db-1". Resultado: o cron
# falhava todas as noites contra um container inexistente.
#
# Não adianta o operador rodar `export DB_CONTAINER=...` no shell: o cron roda com
# ambiente mínimo e não herda nada. Por isso a resolução tem de acontecer AQUI dentro.
resolver_container() {
    local id
    if [ -n "${DB_CONTAINER:-}" ]; then
        echo "${DB_CONTAINER}"; return 0
    fi
    if [ -f "${COMPOSE_FILE}" ]; then
        id="$(docker compose -f "${COMPOSE_FILE}" ps -q db 2>/dev/null | head -1)"
        if [ -n "${id}" ]; then echo "${id}"; return 0; fi
    fi
    # Última tentativa: um container de postgres em execução com "db" no nome.
    id="$(docker ps --filter 'name=db' --format '{{.Names}}' | head -1)"
    if [ -n "${id}" ]; then echo "${id}"; return 0; fi
    return 1
}

if ! DB_CONTAINER="$(resolver_container)"; then
    echo "[backup] ERRO: não achei o container do banco." >&2
    echo "[backup]   Tentei: \$DB_CONTAINER, 'docker compose -f ${COMPOSE_FILE} ps -q db'" >&2
    echo "[backup]   e 'docker ps --filter name=db'. A stack está de pé?" >&2
    exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="${BACKUP_DIR}/estrela_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[backup] Iniciando pg_dump de ${DB_NAME} (container ${DB_CONTAINER}) -> ${OUTFILE}"
# pipefail: sem ele, o gzip devolveria 0 e um pg_dump que morreu no meio passaria como
# sucesso — o modo clássico de descobrir que o backup era lixo só na hora do restore.
set -o pipefail
docker exec -i "${DB_CONTAINER}" pg_dump -U "${DB_USER}" "${DB_NAME}" | gzip > "${OUTFILE}"

# Valida que o arquivo não ficou vazio.
if [ ! -s "${OUTFILE}" ]; then
    echo "[backup] ERRO: dump vazio, removendo ${OUTFILE}" >&2
    rm -f "${OUTFILE}"
    exit 1
fi

# "Não-vazio" não é o mesmo que "íntegro": um dump truncado por disco cheio passa no -s.
# O gzip guarda um CRC — se o arquivo foi cortado, o -t reprova. É a diferença entre ter
# um backup e ter um arquivo.
if ! gzip -t "${OUTFILE}" 2>/dev/null; then
    echo "[backup] ERRO: dump corrompido/truncado (gzip -t falhou), removendo ${OUTFILE}" >&2
    rm -f "${OUTFILE}"
    exit 1
fi

# O dump tem de conter o fim do pg_dump. Um dump interrompido pode até descomprimir.
# `gzip -dc` e não `zcat`: no BSD/macOS o zcat procura .Z e falha no arquivo errado — o
# script é de Linux, mas um teste rodado fora dele passaria por engano.
if ! gzip -dc "${OUTFILE}" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "[backup] ERRO: o dump não terminou (falta o marcador de fim), removendo ${OUTFILE}" >&2
    rm -f "${OUTFILE}"
    exit 1
fi

echo "[backup] OK: $(du -h "${OUTFILE}" | cut -f1) em ${OUTFILE}"

echo "[backup] Aplicando rotação (>${RETENTION_DAYS} dias) em ${BACKUP_DIR}"
find "${BACKUP_DIR}" -name 'estrela_*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] Concluído."
