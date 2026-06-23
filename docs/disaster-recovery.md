# Plano de Recuperação de Desastres (DR) — Estrela Gestão

> Como recuperar o sistema após uma falha (hardware, corrupção de dados, perda do local).
> Cenário: sistema 100% local em mini PC, sem RAID/ECC. A estratégia de DR é
> **backup + restore rápido** em qualquer máquina com Docker.

---

## 1. Objetivos (RPO / RTO)

| Métrica | Alvo | Como é garantido |
|---|---|---|
| **RPO** (perda máxima de dados) | **até 24 h** (pior caso) | Backup `pg_dump` diário (madrugada) + offsite criptografado diário quando há internet |
| **RTO** (tempo de retorno) | **< 2 h** (falha de mini PC) / **< 4 h** (perda total do local) | Restore de dump via Docker em máquina nova |

> Reduzir o RPO abaixo de 24 h exige backups mais frequentes (ex.: a cada 4 h). Avaliar com o cliente
> se o volume justificar. Hoje, a janela de 24 h é a aceita no escopo enxuto.

---

## 2. Camadas de backup (3-2-1 adaptado)

1. **Diário local**: `pg_dump | gzip` em `/backup/estrela_gestao/estrela_AAAAMMDD_HHMMSS.sql.gz` (HD externo). Retenção 14 dias.
2. **Mídia externa rotacionada**: cópia semanal para segundo HD, um mantido **fora do prédio**.
3. **Offsite na nuvem**: `rclone sync` criptografado (remote `b2_encrypted` — Backblaze B2/S3), diário quando há internet.

Scripts: `scripts/backup-estrela.sh`, `scripts/backup-offsite.sh`, `scripts/restore-estrela.sh`.

---

## 3. Cenários e procedimentos

### 3.1 Falha de hardware do mini PC (RTO < 2 h)

1. Provisionar máquina substituta com **Docker + Docker Compose**.
2. `git clone` do projeto em `/opt/estrela` na **mesma tag** que estava em produção.
3. Recriar `/opt/estrela/.env.prod` (do `.env.prod.example` + segredos do cofre).
4. Subir a stack:
   ```bash
   cd /opt/estrela
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
   ```
   Isso cria o banco vazio e aplica migrations (entrypoint).
5. Restaurar o último dump válido:
   ```bash
   docker compose -f docker-compose.prod.yml stop app
   ./scripts/restore-estrela.sh /backup/estrela_gestao/<ultimo>.sql.gz
   docker compose -f docker-compose.prod.yml start app
   ```
6. Reapontar `sistema.local` (IP fixo) para a nova máquina (roteador/Avahi).
7. Validar (ver §4).

### 3.2 Corrupção / perda lógica de dados (exclusão indevida, etc.)

1. Identificar o último dump **anterior ao incidente**.
2. `docker compose ... stop app` (evita escrita concorrente).
3. Restaurar esse dump (`restore-estrela.sh`).
4. `start app` e validar.

> O dump restaura o banco inteiro. Restauração parcial (uma tabela) exige extração manual do dump.

### 3.3 Perda total do local (incêndio, furto) — RTO < 4 h

1. Obter o backup **offsite** (rclone) numa máquina nova com internet:
   ```bash
   rclone sync b2_encrypted:estrela_gestao /backup/estrela_gestao
   ```
2. Seguir o procedimento de **3.1** (máquina nova) usando o dump baixado.
3. Reconfigurar rede/terminais conforme `go-live-checklist.md` (fases 2, 3 e 5).

---

## 4. Validação pós-restore (obrigatória)

Após qualquer restore, confirmar:

- [ ] App sobe e `https://sistema.local` responde.
- [ ] Login funciona (um usuário de cada perfil).
- [ ] Dashboard carrega KPIs.
- [ ] Contagem de **produtos**, **pedidos** e **clientes** bate com o esperado:
  ```bash
  docker compose -f docker-compose.prod.yml exec db \
    psql -U estrela -d estrela_gestao -c \
    "select 'produtos' t, count(*) from produtos
     union all select 'pedidos', count(*) from pedidos
     union all select 'clientes', count(*) from clientes;"
  ```
- [ ] Saldos de estoque coerentes (sem negativos inesperados).
- [ ] Registrar data/horário do restore e responsável.

---

## 5. Teste de DR (trimestral)

- Fazer um restore **real** (não apenas "backup verde") em instância de teste.
- Cronometrar o tempo total (validar o RTO).
- Documentar problemas e ajustar este plano.

---

## 6. Itens críticos a proteger fora do servidor

- `.env.prod` (segredos `DB_PASSWORD`, `JWT_SECRET`) — guardar em cofre de senhas.
- Configuração do remote rclone (`rclone.conf`) com a chave de criptografia do crypt.
- Esta documentação e o `runbook-servidor.md`.

> Sem a chave do rclone crypt, o backup offsite é **irrecuperável** (por design). Guardar com cuidado.
