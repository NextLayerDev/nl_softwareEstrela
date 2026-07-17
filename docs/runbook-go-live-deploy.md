# Runbook — ativar o deploy pelo painel no servidor da Estrela

> **Para quem executa:** este documento é auto-suficiente. Ele cobre as Fases 7, 9 e 10 do
> plano de CI/CD — as três únicas que tocam o servidor da cliente. Tudo o mais (CI, release,
> imagem no GHCR, aba `/deploy`, agente) **já está pronto e na `main`**.
>
> **Leia a seção "Antes de abrir a janela" inteira antes de digitar qualquer comando.**
> Metade dos abortos previstos acontece por causa de coisas que dá para checar sem janela.

---

## 0. O que existe hoje e o que falta

| Peça | Estado |
|---|---|
| CI (lint, 307 testes, guardas de migration, asserção do `.dockerignore`) | ✅ rodando, verde |
| Segurança (gitleaks, bandit, pip-audit, CodeQL) | ✅ rodando, verde |
| `main` protegida (PR + `ci-ok` + `seguranca-ok`, `enforce_admins: true`) | ✅ |
| Imagem `ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.2` | ✅ publicada, **assinada**, pública, **com o fix do login** |
| Aba `/deploy` (perfil `dev`), agente, `migrar_seguro.py` | ✅ código na `main` |
| **Compose puxando a imagem (Fase 7)** | ❌ **falta aplicar no servidor** |
| **Agente instalado (Fase 9)** | ❌ **falta aplicar no servidor** |
| **Botões exercitados (Fase 10)** | ❌ **falta ensaiar no servidor** |

**Servidor:** `estrelaserver` via Tailscale (`100.93.92.88`). Projeto em `/opt/estrela/nl_softwareEstrela`.

---

## 1. Antes de abrir a janela (sem downtime, faça hoje)

### 1.1 A rotina de backup — provavelmente NUNCA rodou. É bloqueante.

**Por que é bloqueante:** o agente **exige um backup bem-sucedido antes de qualquer deploy**
(regra dura: falhou o backup, aborta). Sem esta seção resolvida, nenhum botão da aba funciona.

**Por que provavelmente nunca rodou** (dois erros que se somam):

1. O `backup-estrela.sh` tinha `DB_CONTAINER` fixo em `estrela_softwarelocal-db-1` — o nome do
   diretório de **desenvolvimento**. No servidor o projeto vive em `/opt/estrela/nl_softwareEstrela`, então o
   container é `estrela-db-1`. O `docker exec` falhava toda noite contra um container inexistente.
2. O guia mandava consertar com `export DB_CONTAINER=...` e dava a linha do cron **sem** a
   variável. `export` vale só para a sua sessão; **o cron roda com ambiente mínimo e não herda
   nada**. Então mesmo seguindo o guia à risca, o cron caía no default errado.

**Já corrigido no código** (não precisa configurar `DB_CONTAINER`): o script agora **descobre** o
container via `docker compose ps -q db`, e valida o dump de verdade — `gzip -t` (pega truncado por
disco cheio, que passava no antigo "não-vazio") e o marcador `PostgreSQL database dump complete`
(pega dump interrompido). Se ele disser "Concluído", existe um dump íntegro.

#### 1.1.1 Diagnóstico (o que está lá hoje)

```bash
ssh estrela@estrelaserver
ls -la /backup/estrela_gestao/          # tem algum .sql.gz? de quando?
tail -20 /var/log/estrela-backup.log    # o que o cron vem reclamando?
crontab -l                              # a linha do backup existe?
docker ps --format '{{.Names}}'         # como o container se chama de verdade?
```

#### 1.1.2 Instalar a rotina

```bash
cd /opt/estrela/nl_softwareEstrela
git pull --ff-only origin main          # traz o script corrigido

sudo mkdir -p /backup/estrela_gestao
sudo chown estrela:estrela /backup/estrela_gestao
chmod +x /opt/estrela/nl_softwareEstrela/scripts/backup-estrela.sh /opt/estrela/nl_softwareEstrela/scripts/restore-estrela.sh
sudo touch /var/log/estrela-backup.log && sudo chown estrela:estrela /var/log/estrela-backup.log
```

Agende no cron do usuário `estrela` (que está no grupo `docker`) — **não** no do root:

```bash
crontab -e
```

```cron
# PATH explícito: o cron não tem o seu. Sem isto o script não acha o docker — e falha
# em silêncio, que é exatamente como este backup passou meses sem rodar.
PATH=/usr/local/bin:/usr/bin:/bin

0 2 * * * /opt/estrela/nl_softwareEstrela/scripts/backup-estrela.sh >> /var/log/estrela-backup.log 2>&1
```

#### 1.1.3 Provar que funciona — três testes, não um

```bash
# 1. roda à mão (prova que o script funciona)
/opt/estrela/nl_softwareEstrela/scripts/backup-estrela.sh
ls -lh /backup/estrela_gestao/ | tail -2

# 2. roda SEM o seu ambiente (prova que o CRON vai funcionar). Este é o que importa:
#    o teste 1 passa mesmo quando o cron falharia.
env -i /bin/bash -lc '/opt/estrela/nl_softwareEstrela/scripts/backup-estrela.sh' ; echo "saida: $?"

# 3. o dump tem tamanho de gente? Um banco com as fotos em bytea não gera 2 KB.
du -h /backup/estrela_gestao/*.sql.gz | tail -2
```

Espere `saida: 0` no teste 2. Se der erro de `docker: command not found`, o `PATH` do crontab
não está certo.

#### 1.1.4 Testar o RESTORE (backup não testado não é backup)

Não restaure por cima da produção. Suba um Postgres descartável e mande o dump nele:

```bash
DUMP=$(ls -t /backup/estrela_gestao/*.sql.gz | head -1)
docker run -d --rm --name pg-teste -e POSTGRES_PASSWORD=teste -e POSTGRES_USER=estrela \
  -e POSTGRES_DB=estrela_gestao postgres:16.4
sleep 8
zcat "$DUMP" | docker exec -i pg-teste psql -U estrela -d estrela_gestao > /tmp/restore.log 2>&1
echo "saida do restore: $?"

# o dado chegou? (produtos e pedidos com números plausíveis)
docker exec -i pg-teste psql -U estrela -d estrela_gestao -c \
  "SELECT (SELECT count(*) FROM produtos) AS produtos, (SELECT count(*) FROM pedidos) AS pedidos;"

docker stop pg-teste
```

Se os números baterem com a produção, o backup **é** um backup. Só depois disso siga.

#### 1.1.5 Offsite (opcional, só se o local tiver internet)

```cron
30 2 * * * /opt/estrela/nl_softwareEstrela/scripts/backup-offsite.sh >> /var/log/estrela-offsite.log 2>&1
```

O `backup-offsite.sh` faz `rclone` para um remote criptografado e **sai limpo se estiver
offline** (testa a conexão antes). Exige o rclone configurado; sem isso, ignore.

> **Retenção:** o script apaga dumps com mais de 14 dias. Confira que `/backup` tem espaço para
> 14 deles — e lembre que o mesmo disco guarda as imagens Docker que permitem o rollback offline.

### 1.2 Espaço em disco

O deploy pull-based guarda a imagem atual **e a anterior** — é isso que permite reverter sem
internet. Sem espaço, o agente aborta no pré-flight.

```bash
df -h /            # precisa de folga; a imagem tem ~400-600 MB
docker images | head
docker system df
```

> **Nunca** rode `docker image prune -a` neste servidor. Higiene para qualquer sysadmin, mas aqui
> ela apaga a rede de segurança do rollback offline. O agente faz poda seletiva, preservando a
> imagem atual e a anterior.

### 1.3 Confirme que o servidor alcança o GHCR

O pacote é **público** — não precisa de token. Mas o servidor precisa de internet no momento do
deploy:

```bash
curl -sI https://ghcr.io/v2/ | head -1          # espere 401 (normal, é o realm de auth)
docker pull ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.2
docker images | grep nl_softwareestrela
```

Se o pull funcionar **sem `docker login`**, a parte mais frágil do desenho está confirmada.

### 1.4 Anote a versão que está rodando hoje

Para saber para onde voltar se tudo der errado:

```bash
cd /opt/estrela/nl_softwareEstrela
git rev-parse --short HEAD
docker compose -f docker-compose.prod.yml ps
```

---

## 2. Fase 7 — o compose passa a puxar a imagem (~2 min de janela)

**O que muda:** o serviço `app` deixa de ser buildado no servidor e passa a consumir
`${APP_IMAGEM}`. Ganha healthcheck, o Caddy passa a esperar o app ficar saudável, os logs ganham
rotação e o Postgres passa a escutar em `127.0.0.1:5432` (para o agente, que roda fora do Docker).

```bash
cd /opt/estrela/nl_softwareEstrela
git fetch origin main && git status        # a árvore está limpa?
git pull --ff-only origin main
```

Acrescente ao `/opt/estrela/nl_softwareEstrela/.env.prod` (veja `.env.prod.example`):

```bash
APP_IMAGEM=ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.2
ALLOWED_HOSTS=*
```

> `APP_IMAGEM` **sempre** com tag imutável (`vX.Y.Z`), nunca `:latest` — tag móvel destrói saber o
> que está rodando e o rollback junto. Quem reescreve essa linha a partir de agora é o agente, em
> `/var/lib/estrela-agente/env.tag`.

O bind mount `/backup/estrela_gestao:/backup:ro` já vem no compose (é o que faz a aba enxergar o
último backup). Garanta que o diretório existe:

```bash
sudo mkdir -p /backup/estrela_gestao
```

Suba:

```bash
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
$COMPOSE pull
$COMPOSE up -d --wait
$COMPOSE ps
```

**Verificar (não pule):**

```bash
# 1. o app está healthy? (o healthcheck usa urllib — a imagem slim NÃO tem curl)
docker inspect --format '{{.State.Health.Status}}' $($COMPOSE ps -q app)

# 2. a versão certa subiu?
$COMPOSE exec app printenv APP_VERSION      # deve ser 0.1.2, NUNCA "dev"

# 3. readiness (é o gate que o agente vai usar)
$COMPOSE exec app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"

# 4. o sistema responde pela LAN
curl -sk https://sistema.local/health
```

**Se der errado — reverter (~30 s):** volte o `APP_IMAGEM` para a tag anterior e `up -d`. Se a
imagem nova estiver quebrada e não houver anterior, use o override de build:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.build.yml --env-file .env.prod up -d --build
```

> Isso **não** é contingência offline: o build puxa base, apt e PyPI. Sem internet, a saída é
> `docker load` de um `docker save` feito antes.

---

## 3. Fase 9 — instalar o agente (~15 min de janela)

**O agente nunca está no caminho crítico do app.** `systemctl stop estrela-agente` e o sistema
continua operando normalmente — só os botões param de funcionar. Isso é invariante do desenho.

### 3.1 A chave pública do cosign (faça primeiro, é o que mais esquece)

O agente **não faz deploy nenhum** sem ela — falha fechado, de propósito. Ela é a única fronteira
de confiança real: a checagem de "quem clicou é dev" e a allowlist são defesa contra atacante
*sem* RCE, porque a aplicação é dona das tabelas e poderia forjá-las.

```bash
sudo mkdir -p /etc/estrela-agente
sudo tee /etc/estrela-agente/cosign.pub > /dev/null <<'PUB'
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE5I4fePzOuNkfmUbVv37Ma9+gboQo
/dggl2CCuhvGotXgEsPKMm4ExgYOO6LauJkgOpaO7YsMyiWHg3cSo4SNvA==
-----END PUBLIC KEY-----
PUB
```

> É a mesma chave da variable `COSIGN_PUBLIC_KEY` do repositório. A privada vive só em GitHub
> Secrets (`COSIGN_PRIVATE_KEY`) e nunca tocou este servidor.

### 3.2 Rodar o instalador

Ele é **idempotente** — rodar de novo atualiza código, tabelas e a unidade. É assim que o agente é
atualizado: ele **não** se auto-atualiza, de propósito (um componente com o socket do Docker que se
atualiza sozinho pela rede é uma backdoor com boas intenções).

```bash
cd /opt/estrela/nl_softwareEstrela
sudo bash deploy/instalar-agente.sh
```

O que ele monta:

| Caminho | O quê |
|---|---|
| usuário `estrela-agente` | nologin, grupo `docker` |
| `/opt/estrela-agente/` | código + venv (root:root) |
| `/etc/estrela-agente/` | `agente.env` (0640) + `cosign.pub` |
| `/var/lib/estrela-agente/` | `env.tag` — o único arquivo que o agente escreve |
| schema `agente` no Postgres | allowlist + status, **fora do Alembic** |
| role `estrela_agente` | dedicado, ≠ do role da app |

> Se você não passar `DB_PASSWORD_AGENTE`, ele gera uma e **mostra uma vez**. Anote.
>
> O schema `agente` fica fora do Alembic porque o Alembic roda dentro do container com o role da
> app — e a app é justamente de quem o agente não confia. A allowlist não pode ser escrita por quem
> pede o deploy.

#### 3.2.1 Apontar o gate de saúde para um endereço que responde

O agente confere `/health/ready` depois de cada deploy e reverte se falhar. O default é
`https://sistema.local/health/ready` — que **não responde neste servidor** (o `sistema.local` não
resolve por DNS). Com o `Caddyfile` da `v0.1.2` respondendo em qualquer host/IP na porta 80, aponte
o gate para o loopback via HTTP, em `/etc/estrela-agente/agente.env`:

```bash
ESTRELA_SAUDE_URL=http://localhost/health/ready
```

> HTTP e não HTTPS de propósito: evita depender do cert interno do Caddy e do DNS. O gate roda no
> host, então `localhost:80` bate no Caddy, que encaminha para o app. Se você deixar o default
> `https://sistema.local/...`, **todo deploy vai falhar o gate e auto-reverter** — e o sintoma
> aparece como "o sistema não sobe", não como "a URL do gate está errada".

Confirme que o endereço responde antes de seguir:

```bash
curl -s http://localhost/health/ready ; echo
# {"pronto": true, "motivo": "ok"}
```

### 3.3 Verificar antes de deixar rodando

```bash
sudo systemctl status estrela-agente --no-pager
sudo journalctl -u estrela-agente -n 40 --no-pager

# o argv que ele executaria, sem executar nada:
sudo -u estrela-agente /opt/estrela-agente/venv/bin/python \
     /opt/estrela-agente/agente.py --dry-run

# o cosign confere a imagem que está publicada?
cosign verify --key /etc/estrela-agente/cosign.pub \
  ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.2
```

Deve terminar com *"The signatures were verified against the specified public key"*. Já foi
conferido daqui contra esta mesma chave — se falhar no servidor, o problema é a chave que foi
copiada, não a imagem.

> ⚠️ **Use a `v0.1.2` (ou mais nova). Nunca a `v0.1.0`/`v0.1.1`.** A `v0.1.0` não foi assinada
> (`no signatures found` → o agente recusa). A `v0.1.1` está assinada, **mas não tem o fix do
> loop de login** (cookie/HSTS Secure sobre HTTP): subir ela na LAN por HTTP trava o login. A
> `v0.1.2` é a primeira com o `c936c42` dentro e é a que deve rodar.

### 3.4 O deploy manual de ensaio (com humano no teclado)

Antes de deixar qualquer botão ativo, faça um deploy **pelo CLI do agente**. Se o agente estiver
errado, é aqui que se descobre — não com o admin clicando.

```bash
sudo -u estrela-agente /opt/estrela-agente/venv/bin/python \
     /opt/estrela-agente/agente.py --once
```

Acompanhe em outro terminal:

```bash
sudo journalctl -u estrela-agente -f
```

---

## 4. Fase 10 — ensaio dos botões (~10 min, indivisível)

Só faça isto **depois** que a Fase 9 estiver verificada. Abra a aba como `dev` (ela não aparece
para o admin da Estrela, nem digitando a URL). Com o `Caddyfile` da `v0.1.2`, o sistema responde
por IP tanto em HTTP quanto HTTPS — use o que os terminais usam:

```
http://<ip-do-servidor>/deploy      (ou https://<ip>/deploy, aceitando o cert interno)
```

**Roteiro do ensaio — faça os três, nesta ordem:**

1. **Atualizar** para uma versão nova (publique uma `v0.1.2` de teste antes: `git tag -a v0.1.2 -m '...' && git push origin v0.1.2`, e espere o release.yml ficar verde).
   Acompanhe: a tela mostra "atualizando", o WebSocket cai por ~20–60 s (isso é esperado e
   inerente a qualquer self-update — o app está sendo recriado), e a tela **volta sozinha**. O log
   fica em `deploys.log`, no Postgres, que é o único container que não é recriado.

2. **Reverter** para a versão anterior (a que o servidor estava rodando antes). Confirme que:
   - se a versão-alvo cruza migration, aparece **aviso vermelho** e ele **exige digitar a versão**;
   - o agente **não** faz downgrade do banco (deixa o schema à frente, de propósito);
   - o `migrar_seguro.py` sobe o app sem migrar nesse caso, em vez de crashloopar.

3. **Forçar uma falha** e ver a auto-reversão disparar. Ex.: aponte o `ESTRELA_SAUDE_URL` para uma
   porta errada por um minuto, dispare um deploy e confirme que o agente:
   - reverte a **imagem** (uma vez só, nunca o banco);
   - grava `falhou_revertido` no histórico;
   - **manda o alerta no ntfy** — confira o celular. Sem isso, "o rollback também falhou" é
     descoberto quando a Estrela liga.

**Configure o alerta antes:** `ESTRELA_ALERTA_URL` em `/etc/estrela-agente/agente.env`
(ex.: `https://ntfy.sh/<um-topico-privado-e-aleatorio>`).

---

## 5. Regras que não podem ser quebradas

- **O agente NUNCA restaura o banco automaticamente.** Contraria o pedido literal, e é deliberado:
  restaurar o dump apagaria **em silêncio** todos os pedidos e baixas entre o backup e a falha. Uma
  atualização às 14h que falhe às 14h04 destruiria a manhã inteira, e o operador nem saberia — o
  sistema "voltou funcionando". Fora do ar é visível e recuperável; dado sumido em silêncio não é.
  O backup existe para o **humano** decidir.
- **Rollback aqui não é a Vercel.** Lá é instantâneo porque não há banco. Aqui, voltar a imagem
  depois de uma migration dá código velho contra schema novo. Por isso o aviso vermelho.
- **Migration nos dados reais pode levar 20 min**, não os 2 s do seed (as fotos são `bytea`). O
  pré-flight roda num container efêmero: se a migration falhar, o app **antigo continua no ar** e
  ninguém percebe. É o ganho central do desenho — não o remova.
- **Nunca fixe `-p` no compose.** O projeto herda o nome do diretório; fixar renomearia o volume
  `pgdata` e o Postgres subiria **vazio**.

---

## 6. Se der errado

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| App em crashloop após deploy | migration aplicou e o código não tolera | `docker compose logs app`; volte o `APP_IMAGEM` e `up -d`; o banco fica à frente (esperado) |
| `Can't locate revision` | o `migrar_seguro.py` não está sendo usado | confirme que o `entrypoint.sh` o chama; era exatamente este o bug que ele existe para matar |
| Agente "não instalado" na aba | schema `agente` não existe | rode o instalador; ausência é estado normal, não erro |
| Botão não faz nada, fica "aguardando o agente" | serviço parado | `systemctl status estrela-agente`; `journalctl -u estrela-agente -n 50` |
| `cosign verify` falha | imagem não assinada (publicada antes do PR #7) ou chave errada | republique a versão; confira `/etc/estrela-agente/cosign.pub` contra a variable do repo |
| Caddy 502 | app não ficou healthy | `docker inspect --format '{{.State.Health.Status}}'`; veja os logs do app |
| Container nunca fica healthy após restringir `ALLOWED_HOSTS` | healthcheck bate em `127.0.0.1` | o `allowed_hosts_list` já força o loopback; confirme que a var chegou: `$COMPOSE exec app printenv ALLOWED_HOSTS` |

**Desligar tudo e voltar ao mundo antigo:** `sudo systemctl stop estrela-agente` (o sistema opera
igual, só sem botões) e, se preciso, `APP_IMAGEM` na tag anterior + `up -d`.

---

## 7. Depois do go-live

- **Trocar a senha do usuário `dev`** (`dev@estrela.local`). Hoje é `estrela123`, escrita no
  `seed.py`, que está num **repositório público**. Ele passa em tudo e tem os botões de deploy.
- Rotacionar `DB_PASSWORD` e `JWT_SECRET`: eles já estiveram em layers de imagem antes do
  `.dockerignore` existir.
- `docs/disaster-recovery.md`: o DR deixou de ser auto-suficiente. Antes era clone + build + dump
  (tudo offline); agora pressupõe imagem disponível. Registre o `docker save` da tag corrente ao
  lado do dump.
- Considere Docker rootless/Podman: o grupo `docker` é root-equivalente. A arquitetura reduziu
  drasticamente **quem** tem esse poder (de uma web app inteira para um script sem porta aberta),
  mas não o eliminou.
