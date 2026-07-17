#!/usr/bin/env bash
# Instala (ou reinstala) o agente de deploy do Estrela Gestão no mini PC do cliente.
#
#   sudo DB_PASSWORD_AGENTE='...' bash /opt/estrela/deploy/instalar-agente.sh
#
# IDEMPOTENTE: rodar de novo atualiza o código do agente, as tabelas e a unidade systemd
# sem estragar nada. É assim que o agente é atualizado — ele não se atualiza sozinho, de
# propósito: um componente que tem o socket do Docker e se auto-atualiza a partir da rede
# é uma backdoor com boas intenções.
#
# O QUE ESTE SCRIPT MONTA
#   usuário estrela-agente (nologin, grupo docker)   -> quem roda o agente
#   /opt/estrela-agente/                             -> código + venv (root:root, 0755)
#   /etc/estrela-agente/                             -> agente.env (0640) + cosign.pub
#   /var/lib/estrela-agente/                         -> env.tag (o único arquivo escrito)
#   schema `agente` no Postgres                      -> allowlist + status, FORA do Alembic
#   role estrela_agente                              -> role dedicado, ≠ role da app
#
# POR QUE O SCHEMA `agente` FICA FORA DO ALEMBIC
#   O Alembic roda de DENTRO do container do app, com o role da app. Se estas tabelas
#   fossem migrations, a app teria que ser dona delas — e a app é justamente de quem o
#   agente não confia. A allowlist não pode ser escrita por quem pede o deploy; se pudesse,
#   um INSERT forjado cadastraria uma imagem qualquer e o cosign viraria enfeite.
#   Consequência: `agente.*` não existir é um estado NORMAL (agente não instalado), e o
#   saude_service/deploy_service já tratam isso.

set -euo pipefail

# --------------------------------------------------------------- parâmetros
PROJETO_DIR="${PROJETO_DIR:-/opt/estrela}"
AGENTE_DIR="${AGENTE_DIR:-/opt/estrela-agente}"
CONF_DIR="${CONF_DIR:-/etc/estrela-agente}"
ESTADO_DIR="${ESTADO_DIR:-/var/lib/estrela-agente}"
USUARIO="${USUARIO:-estrela-agente}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJETO_DIR}/docker-compose.prod.yml}"
ENV_PROD="${ENV_PROD:-${PROJETO_DIR}/.env.prod}"

DB_NAME="${DB_NAME:-estrela_gestao}"
DB_SUPER="${DB_SUPER:-estrela}"          # role dono do banco (o da app)
DB_ROLE_AGENTE="${DB_ROLE_AGENTE:-estrela_agente}"

# Versão e hash do cosign. O binário é BAIXADO da internet e passa a verificar tudo que
# entra no servidor: se ele vier adulterado, a corrente de confiança inteira cai junto.
# Por isso o sha256 é conferido e o `set -e` mata o script se não bater.
# Conferido em 2026-07-17 contra o cosign_checksums.txt do release oficial:
#   curl -sL https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt
# Ao trocar COSIGN_VERSAO, pegue o sha256 de LÁ — nunca do arquivo que você acabou de
# baixar (isso só provaria que o download é igual a si mesmo).
COSIGN_VERSAO="${COSIGN_VERSAO:-v2.4.1}"
COSIGN_SHA256="${COSIGN_SHA256:-8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b}"
# Só amd64 por ora: é o que o mini PC é. Num host arm64 o sha não bate e o script para —
# fail-closed. Se um dia for arm64, o sha do cosign-linux-arm64 v2.4.1 é
# 3b2e2e3854d0356c45fe6607047526ccd04742d20bd44afb5be91fa2a6e7cb4a.
COSIGN_ARCH="${COSIGN_ARCH:-amd64}"
COSIGN_BIN="${COSIGN_BIN:-/usr/local/bin/cosign}"

log()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
erro() { printf '\n\033[1;31mERRO: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || erro "Rode como root (sudo)."
command -v docker >/dev/null || erro "docker não encontrado."
docker compose version >/dev/null 2>&1 || erro "'docker compose' (v2) não encontrado."
[ -f "${COMPOSE_FILE}" ] || erro "compose não encontrado em ${COMPOSE_FILE}."
[ -f "${ENV_PROD}" ] || erro ".env.prod não encontrado em ${ENV_PROD}."

# A senha do role do agente nunca é gerada em silêncio aqui sem ser mostrada: se o
# operador não passar uma, geramos e exibimos UMA vez.
DB_PASSWORD_AGENTE="${DB_PASSWORD_AGENTE:-}"
if [ -z "${DB_PASSWORD_AGENTE}" ]; then
    DB_PASSWORD_AGENTE="$(openssl rand -hex 24)"
    SENHA_GERADA=1
fi

# ------------------------------------------------------- usuário e diretórios
log "Usuário de sistema e diretórios"
if ! id -u "${USUARIO}" >/dev/null 2>&1; then
    # --no-create-home é o motivo de a unidade systemd precisar de RuntimeDirectory +
    # HOME/DOCKER_CONFIG: sem HOME gravável, o docker CLI falha.
    useradd --system --no-create-home --shell /usr/sbin/nologin "${USUARIO}"
    info "usuário ${USUARIO} criado"
else
    info "usuário ${USUARIO} já existe"
fi

getent group docker >/dev/null || erro "grupo 'docker' não existe (o Docker está instalado?)."
usermod -aG docker "${USUARIO}"
info "${USUARIO} está no grupo docker (equivale a root no host — daí o hardening da unit)"

install -d -o root -g root -m 0755 "${AGENTE_DIR}"
install -d -o root -g "${USUARIO}" -m 0750 "${CONF_DIR}"
# O agente PRECISA de escrita no DIRETÓRIO (os.replace = rename(2)), não só no arquivo.
install -d -o "${USUARIO}" -g "${USUARIO}" -m 0750 "${ESTADO_DIR}"

# O `docker compose --env-file` roda como ${USUARIO} e precisa LER o .env.prod. Leitura
# só pelo grupo; escrita continua sendo só do root.
chgrp "${USUARIO}" "${ENV_PROD}"
chmod 0640 "${ENV_PROD}"
info ".env.prod legível pelo agente (0640 root:${USUARIO})"

# Backup: o diretório precisa existir e ser gravável pelo agente, senão a etapa de backup
# aborta todo deploy — que é o comportamento correto, mas por um motivo bobo.
install -d -m 0750 -o "${USUARIO}" -g "${USUARIO}" "${BACKUP_DIR:-/backup/estrela_gestao}"

# ------------------------------------------------------------------- cosign
log "cosign ${COSIGN_VERSAO}"
instalar_cosign() {
    local url tmp sha
    url="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSAO}/cosign-linux-${COSIGN_ARCH}"
    tmp="$(mktemp)"
    info "baixando ${url}"
    curl -fsSL --retry 3 -o "${tmp}" "${url}" || erro "falha ao baixar o cosign."
    sha="$(sha256sum "${tmp}" | cut -d' ' -f1)"
    if [ "${sha}" != "${COSIGN_SHA256}" ]; then
        rm -f "${tmp}"
        erro "sha256 do cosign NÃO confere.
    esperado: ${COSIGN_SHA256}
    obtido:   ${sha}
  Isto é sério: o cosign é o que verifica tudo que entra neste servidor. Ou o
  COSIGN_SHA256 deste script está desatualizado para a versão ${COSIGN_VERSAO} (confira
  em https://github.com/sigstore/cosign/releases e atualize a variável), ou o download
  foi adulterado. NÃO contorne isto com um sha copiado do arquivo baixado."
    fi
    install -o root -g root -m 0755 "${tmp}" "${COSIGN_BIN}"
    rm -f "${tmp}"
    info "cosign instalado em ${COSIGN_BIN} (sha256 conferido)"
}

if [ -x "${COSIGN_BIN}" ] && "${COSIGN_BIN}" version 2>/dev/null | grep -q "${COSIGN_VERSAO#v}"; then
    info "cosign ${COSIGN_VERSAO} já instalado"
else
    instalar_cosign
fi

if [ ! -f "${CONF_DIR}/cosign.pub" ]; then
    cat >&2 <<AVISO

  ATENÇÃO: ${CONF_DIR}/cosign.pub não existe.
  O agente NÃO faz deploy nenhum sem a chave pública — ele falha fechado, de propósito.
  Copie a chave pública do par usado pelo pipeline de release:

      sudo install -o root -g ${USUARIO} -m 0640 cosign.pub ${CONF_DIR}/cosign.pub

AVISO
else
    chown root:"${USUARIO}" "${CONF_DIR}/cosign.pub"
    chmod 0640 "${CONF_DIR}/cosign.pub"
    info "chave pública do cosign presente"
fi

# --------------------------------------------------------------- código+venv
log "Código do agente e virtualenv"
command -v python3 >/dev/null || erro "python3 não encontrado."
install -o root -g root -m 0755 "${PROJETO_DIR}/scripts/agente/agente.py" "${AGENTE_DIR}/agente.py"

if [ ! -x "${AGENTE_DIR}/venv/bin/python" ]; then
    python3 -m venv "${AGENTE_DIR}/venv"
fi
# SÓ psycopg. O agente não importa app.* e não tem dependência de runtime da aplicação:
# ele precisa funcionar exatamente quando a aplicação está quebrada.
"${AGENTE_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${AGENTE_DIR}/venv/bin/pip" install --quiet "psycopg[binary]>=3.2"
info "venv pronto (apenas psycopg)"

# ----------------------------------------------------------------- Postgres
log "Schema 'agente' no Postgres"
DB_CID="$(docker compose --project-directory "${PROJETO_DIR}" -f "${COMPOSE_FILE}" \
          --env-file "${ENV_PROD}" ps -q db || true)"
[ -n "${DB_CID}" ] || erro "container do banco não encontrado (a stack está no ar?)."
info "container do banco: ${DB_CID:0:12}"

# psql pelo docker exec: o schema é criado ANTES de o agente existir, e assim o instalador
# não precisa de rede até o Postgres.
psql_exec() { docker exec -i "${DB_CID}" psql -v ON_ERROR_STOP=1 -U "${DB_SUPER}" -d "${DB_NAME}" "$@"; }

psql_exec <<SQL
-- Idempotente: dá para rodar o instalador quantas vezes for preciso.
CREATE SCHEMA IF NOT EXISTS agente;

-- Singleton. O CHECK é o que impede a linha 2 de existir: sem ele, um INSERT distraído
-- criaria um segundo "estado do servidor" e as consultas (WHERE id=1) passariam a ler
-- um estado que ninguém atualiza.
CREATE TABLE IF NOT EXISTS agente.servidor_status (
    id                smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    heartbeat_em      timestamptz,
    versao_atual      text,
    -- Piso de versão. Sobe a cada deploy bem-sucedido e NUNCA desce sozinho.
    versao_minima     text,
    imagem_atual      text,
    -- É o que torna o rollback possível offline. A poda de imagens preserva esta.
    imagem_anterior   text,
    schema_a_frente   boolean NOT NULL DEFAULT false,
    disco_livre_bytes bigint
);
INSERT INTO agente.servidor_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ALLOWLIST. A app NÃO escreve aqui: é a fronteira que faz o cosign valer alguma coisa.
-- Nomes das colunas seguem o que o app/services/deploy_service.py já consulta
-- (publicado_em, git_sha) — a app é a parte já escrita e testada.
CREATE TABLE IF NOT EXISTS agente.releases_disponiveis (
    tag             text PRIMARY KEY,
    -- Repositório da imagem. Fica AQUI e não em deploys: se o agente lesse a origem de
    -- deploys, um INSERT forjado escolheria de onde baixar.
    origem          text NOT NULL,
    -- Opcional. Quando preenchido, o agente exige que o digest assinado bata com este —
    -- pega uma tag reapontada para outra imagem depois do cadastro.
    imagem_digest   text,
    git_sha         text,
    alembic_head    text,
    rollback_seguro boolean,
    publicado_em    timestamptz DEFAULT now()
);

-- Colunas acrescentadas depois (o CREATE ... IF NOT EXISTS acima não altera tabela
-- existente, então uma reinstalação sobre uma versão antiga precisa disto).
ALTER TABLE agente.releases_disponiveis ADD COLUMN IF NOT EXISTS origem text;
ALTER TABLE agente.releases_disponiveis ADD COLUMN IF NOT EXISTS imagem_digest text;
ALTER TABLE agente.releases_disponiveis ADD COLUMN IF NOT EXISTS git_sha text;
ALTER TABLE agente.servidor_status ADD COLUMN IF NOT EXISTS versao_minima text;

-- Role DEDICADO do agente. Separado do role da app de propósito: são dois níveis de
-- confiança diferentes, e o dia em que a app for comprometida a diferença é o que
-- impede o atacante de cadastrar a própria imagem na allowlist.
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_ROLE_AGENTE}') THEN
        CREATE ROLE ${DB_ROLE_AGENTE} LOGIN;
    END IF;
END
\$\$;
ALTER ROLE ${DB_ROLE_AGENTE} WITH PASSWORD '${DB_PASSWORD_AGENTE}';

-- Permissões do AGENTE: dono da própria casa.
GRANT CONNECT ON DATABASE ${DB_NAME} TO ${DB_ROLE_AGENTE};
GRANT USAGE ON SCHEMA agente TO ${DB_ROLE_AGENTE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agente TO ${DB_ROLE_AGENTE};
-- deploys mora no schema public (é model do Alembic); o agente atualiza o andamento.
GRANT USAGE ON SCHEMA public TO ${DB_ROLE_AGENTE};
GRANT SELECT, UPDATE ON TABLE public.deploys TO ${DB_ROLE_AGENTE};
-- Precisa ler alembic_version e produtos? NÃO: quem sonda a saúde é a app, via
-- /health/ready. O agente só olha o resultado HTTP. Menos privilégio, menos superfície.

-- Permissões da APP sobre o schema do agente: SOMENTE LEITURA.
-- É isto que faz a allowlist ser uma allowlist. Se a app pudesse escrever aqui, a
-- separação inteira seria teatro: bastaria um INSERT para cadastrar qualquer imagem.
GRANT USAGE ON SCHEMA agente TO ${DB_SUPER};
GRANT SELECT ON agente.servidor_status, agente.releases_disponiveis TO ${DB_SUPER};
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON agente.servidor_status, agente.releases_disponiveis FROM ${DB_SUPER};

-- Em `deploys` a app precisa de INSERT (solicitar), SELECT (a tela) e UPDATE (cancelar).
GRANT SELECT, INSERT, UPDATE ON TABLE public.deploys TO ${DB_SUPER};
SQL
info "schema agente criado/atualizado; app tem SOMENTE SELECT na allowlist"

# ATENÇÃO: o ${DB_SUPER} é o dono do banco. Um dono de tabela pode se re-conceder
# privilégio (o REVOKE acima não segura o OWNER). Como as tabelas de `agente` são criadas
# nesta conexão, elas nascem com ${DB_SUPER} de dono — então trocamos o dono para o role
# do agente, e aí o REVOKE passa a valer de verdade.
psql_exec <<SQL
ALTER TABLE agente.servidor_status OWNER TO ${DB_ROLE_AGENTE};
ALTER TABLE agente.releases_disponiveis OWNER TO ${DB_ROLE_AGENTE};
ALTER SCHEMA agente OWNER TO ${DB_ROLE_AGENTE};
GRANT USAGE ON SCHEMA agente TO ${DB_SUPER};
GRANT SELECT ON agente.servidor_status, agente.releases_disponiveis TO ${DB_SUPER};
SQL
info "tabelas de agente.* pertencem a ${DB_ROLE_AGENTE} (o REVOKE agora vale de fato)"

# ------------------------------------------------- endereço do banco no host
# O container db NÃO publica porta (e não deve). Da rede bridge, o host alcança o IP do
# container direto. O container db nunca é recriado num deploy, então este IP é estável;
# ainda assim o agente reconecta sozinho se ele mudar.
DB_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "${DB_CID}" | awk '{print $1}')"
[ -n "${DB_IP}" ] || erro "não foi possível descobrir o IP do container do banco."
info "banco alcançável em ${DB_IP}:5432"

# ------------------------------------------------------------ CA do Caddy
# O gate do deploy é um GET https://sistema.local/health/ready. Com `tls internal` o
# certificado é assinado por uma CA local — sem este arquivo, o agente teria que ignorar a
# verificação TLS, e aí qualquer coisa que responda "ok" no lugar do app faria um deploy
# quebrado ser marcado como sucesso.
CADDY_CID="$(docker compose --project-directory "${PROJETO_DIR}" -f "${COMPOSE_FILE}" \
             --env-file "${ENV_PROD}" ps -q caddy || true)"
CA_DEST="${CONF_DIR}/caddy-root.crt"
if [ -n "${CADDY_CID}" ] && docker cp \
        "${CADDY_CID}:/data/caddy/pki/authorities/local/root.crt" "${CA_DEST}" 2>/dev/null; then
    chmod 0644 "${CA_DEST}"
    info "CA interna do Caddy exportada para ${CA_DEST} (gate com TLS verificado)"
else
    CA_DEST=""
    info "AVISO: não foi possível exportar a CA do Caddy; o gate rodará SEM verificar TLS."
fi

# ----------------------------------------------------------------- agente.env
log "Configuração (${CONF_DIR}/agente.env)"
if [ -f "${CONF_DIR}/agente.env" ]; then
    info "agente.env já existe; preservando (edite à mão se precisar)"
else
    umask 077
    cat > "${CONF_DIR}/agente.env" <<CONF
# Gerado por instalar-agente.sh em $(date -Iseconds). Contém segredo: 0640 root:${USUARIO}.
ESTRELA_DSN=postgresql://${DB_ROLE_AGENTE}:${DB_PASSWORD_AGENTE}@${DB_IP}:5432/${DB_NAME}
ESTRELA_PROJETO_DIR=${PROJETO_DIR}
ESTRELA_COMPOSE_FILE=${COMPOSE_FILE}
ESTRELA_ENV_FILE=${ENV_PROD}
ESTRELA_ENV_TAG=${ESTADO_DIR}/env.tag
ESTRELA_COSIGN_PUB=${CONF_DIR}/cosign.pub
ESTRELA_COSIGN_BIN=${COSIGN_BIN}
ESTRELA_BACKUP_SCRIPT=${PROJETO_DIR}/scripts/backup-estrela.sh
ESTRELA_FLAG_DOWNGRADE=${CONF_DIR}/permitir-downgrade
ESTRELA_SAUDE_URL=https://sistema.local/health/ready
ESTRELA_SAUDE_CA=${CA_DEST}
ESTRELA_ALERTA_URL=
ESTRELA_DISCO_PATH=/var/lib
ESTRELA_DISCO_MIN_BYTES=3221225472
ESTRELA_DISCO_MIN_PCT=10
CONF
    umask 022
    info "agente.env criado"
fi
chown root:"${USUARIO}" "${CONF_DIR}/agente.env"
chmod 0640 "${CONF_DIR}/agente.env"

# ------------------------------------------------------------------ systemd
log "Unidade systemd"
install -o root -g root -m 0644 "${PROJETO_DIR}/deploy/estrela-agente.service" \
        /etc/systemd/system/estrela-agente.service
systemctl daemon-reload
systemctl enable estrela-agente >/dev/null
systemctl restart estrela-agente
sleep 3
systemctl is-active --quiet estrela-agente \
    || erro "o agente não subiu. Veja: journalctl -u estrela-agente -n 50 --no-pager"
info "estrela-agente ativo"

# -------------------------------------------------------------------- final
log "Instalação concluída"
cat <<FIM

  Agente:   systemctl status estrela-agente
  Log:      journalctl -u estrela-agente -f
  Teste:    sudo -u ${USUARIO} env \$(grep -v '^#' ${CONF_DIR}/agente.env | xargs) \\
                ${AGENTE_DIR}/venv/bin/python ${AGENTE_DIR}/agente.py --dry-run --once

  FALTA FAZER (o agente não faz deploy sem isto):

  1) Chave pública do cosign em ${CONF_DIR}/cosign.pub, se ainda não estiver lá.

  2) Cadastrar as versões liberadas na allowlist. A app NÃO consegue fazer isto (só tem
     SELECT), e é esse o ponto: nada roda neste servidor sem alguém com acesso ao servidor
     ter autorizado a versão antes.

     docker exec -i ${DB_CID:0:12} psql -U ${DB_SUPER} -d ${DB_NAME} <<'SQL'
     INSERT INTO agente.releases_disponiveis
         (tag, origem, git_sha, alembic_head, rollback_seguro, publicado_em)
     VALUES ('v0.1.0', 'ghcr.io/nextlayerdev/nl_softwareestrela',
             '<sha do commit>', '9c311b4bb27f', true, now())
     ON CONFLICT (tag) DO UPDATE SET origem = EXCLUDED.origem;
     SQL

  3) Alerta fora de banda: preencha ESTRELA_ALERTA_URL em ${CONF_DIR}/agente.env com um
     tópico ntfy PRIVADO (nome longo e aleatório — tópico do ntfy.sh é público para quem
     souber o nome) e reinicie o serviço.

FIM

if [ "${SENHA_GERADA:-0}" = "1" ]; then
    cat <<SENHA
  A senha do role ${DB_ROLE_AGENTE} foi gerada automaticamente e gravada em
  ${CONF_DIR}/agente.env. Ela NÃO será mostrada de novo:

      ${DB_PASSWORD_AGENTE}

SENHA
fi
