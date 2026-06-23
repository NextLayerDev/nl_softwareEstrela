# Runbook do Servidor — Estrela Gestão

> Guia operacional do mini PC que roda o Estrela Gestão no cliente.
> Stack: PostgreSQL 16 + App (FastAPI/Gunicorn) + Caddy (HTTPS interno), tudo em Docker Compose.
> Acesso remoto de manutenção **somente via Tailscale**.

---

## 0. Convenções

- Diretório do projeto no servidor: `/opt/estrela` (ajuste se diferente).
- Arquivo de compose de produção: `docker-compose.prod.yml`.
- Variáveis sensíveis: `/opt/estrela/.env.prod` (NÃO versionado).
- Todos os comandos `docker compose` abaixo assumem:

```bash
cd /opt/estrela
export COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
```

---

## 1. Subir / derrubar a stack

```bash
# Subir tudo (db, app, caddy) em segundo plano
$COMPOSE up -d

# Conferir estado dos serviços
$COMPOSE ps

# Derrubar (mantém volumes/dados)
$COMPOSE down

# Derrubar e APAGAR volumes (PERIGO: apaga o banco) — só em recriação limpa
$COMPOSE down -v
```

> O serviço `app` aplica as migrations (`alembic upgrade head`) automaticamente no
> entrypoint antes de iniciar o Gunicorn. Não é preciso rodar migrations à mão no fluxo normal.

---

## 2. Ver logs

```bash
# Logs de todos os serviços (segue em tempo real)
$COMPOSE logs -f

# Apenas o app
$COMPOSE logs -f app

# Apenas o banco
$COMPOSE logs -f db

# Últimas 200 linhas do app
$COMPOSE logs --tail=200 app
```

---

## 3. Restart e operações pontuais

```bash
# Reiniciar só o app (ex.: após ajuste de variável)
$COMPOSE restart app

# Reiniciar o Caddy (ex.: após editar Caddyfile)
$COMPOSE restart caddy

# Abrir um shell no container do app
$COMPOSE exec app bash

# Aplicar migrations manualmente (raro — só se desativar no entrypoint)
$COMPOSE exec app alembic upgrade head

# Abrir o psql no banco
$COMPOSE exec db psql -U estrela -d estrela_gestao
```

---

## 4. Atualização de versão (deploy)

Janela combinada com o cliente, fora do horário comercial. Acesso via Tailscale.

```bash
cd /opt/estrela

# 1) Trazer o novo código/imagem (git pull da tag desejada, ou pull de imagem)
git fetch --tags && git checkout vX.Y.Z

# 2) Reconstruir a imagem do app e subir
$COMPOSE build app
$COMPOSE up -d

# 3) As migrations rodam no entrypoint do app automaticamente.
#    Conferir nos logs que "alembic upgrade head" terminou sem erro:
$COMPOSE logs --tail=100 app

# 4) Smoke test: abrir https://sistema.local e logar; checar dashboard.
```

> Faça um **backup manual antes de atualizar** (ver §6): `./scripts/backup-estrela.sh`.

---

## 5. Rollback para tag anterior

Se a nova versão quebrar:

```bash
cd /opt/estrela

# 1) Voltar o código para a tag estável anterior
git checkout vX.Y.(Z-1)

# 2) Reconstruir e subir
$COMPOSE build app
$COMPOSE up -d

# 3) Se a migration nova for incompatível e precisar reverter o schema:
$COMPOSE exec app alembic downgrade -1     # ou alembic downgrade <revisao>

# 4) Se o schema ficou inconsistente, restaurar o último dump bom (ver §7 do
#    disaster-recovery.md): para o app, restaura o dump, sobe o app.
```

> Regra de ouro: migrations devem ser **retrocompatíveis** sempre que possível, para
> permitir rollback de código sem mexer no banco.

---

## 6. Backup

```bash
# Backup manual imediato (gera estrela_AAAAMMDD_HHMMSS.sql.gz em /backup/estrela_gestao)
/opt/estrela/scripts/backup-estrela.sh

# Backup automático: agendado no cron do host (madrugada)
#   0 2 * * * /opt/estrela/scripts/backup-estrela.sh >> /var/log/estrela-backup.log 2>&1

# Offsite criptografado (rclone) — só roda com internet
#   30 2 * * * /opt/estrela/scripts/backup-offsite.sh >> /var/log/estrela-offsite.log 2>&1
```

### Teste de backup (trimestral)

1. Escolher um dump recente em `/backup/estrela_gestao`.
2. Restaurar em um banco/instância de teste (NUNCA em produção):
   - Subir um Postgres temporário ou usar máquina de homologação.
   - `DB_CONTAINER=<container-teste> ./scripts/restore-estrela.sh <dump>`
3. Validar: contagem de produtos/pedidos, login, dashboard.
4. Registrar a data do teste no controle interno.

---

## 7. Recuperação de falha do mini PC (RTO < 2 h)

Se o mini PC falhar (hardware):

1. Provisionar outra máquina com Docker + Docker Compose.
2. `git clone` do projeto em `/opt/estrela` (mesma tag em produção).
3. Recriar `/opt/estrela/.env.prod` (a partir do `.env.prod.example` + segredos do cofre).
4. Subir a stack: `$COMPOSE up -d` (cria banco vazio + migrations).
5. Restaurar o último dump bom:
   ```bash
   $COMPOSE stop app
   ./scripts/restore-estrela.sh /backup/estrela_gestao/<ultimo>.sql.gz
   $COMPOSE start app
   ```
6. Reapontar o DNS/IP fixo de `sistema.local` para a nova máquina.
7. Smoke test nos terminais.

> Detalhes completos em `disaster-recovery.md`.

---

## 8. Saúde e diagnóstico rápido

```bash
# Containers de pé?
$COMPOSE ps

# Banco aceitando conexão?
$COMPOSE exec db pg_isready -U estrela -d estrela_gestao

# Caddy servindo?
curl -k https://localhost/ -I

# Uso de disco (atenção ao /backup e ao volume do Postgres)
df -h
docker system df
```

---

## 9. Contatos

| Papel | Responsável | Contato |
|---|---|---|
| Suporte / Operadora | NextLayer (DevOps) | (preencher: telefone / e-mail / canal) |
| Responsável Estrela | (preencher) | (preencher) |
| Tailscale / acesso remoto | NextLayer | tailnet: (preencher) |
| Provedor de internet | (preencher) | (preencher) |

> Manter este quadro atualizado. Em incidente: 1) abrir acesso Tailscale, 2) coletar logs (§2),
> 3) acionar o contato de suporte.
