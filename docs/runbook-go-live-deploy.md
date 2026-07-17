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
| Imagem `ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.1` | ✅ publicada, **assinada**, pacote público |
| Aba `/deploy` (perfil `dev`), agente, `migrar_seguro.py` | ✅ código na `main` |
| **Compose puxando a imagem (Fase 7)** | ❌ **falta aplicar no servidor** |
| **Agente instalado (Fase 9)** | ❌ **falta aplicar no servidor** |
| **Botões exercitados (Fase 10)** | ❌ **falta ensaiar no servidor** |

**Servidor:** `estrelaserver` via Tailscale (`100.93.92.88`). Projeto em `/opt/estrela`.

---

## 1. Antes de abrir a janela (sem downtime, faça hoje)

### 1.1 O backup diário provavelmente NUNCA rodou — isto é bloqueante

O `scripts/backup-estrela.sh` tem `DB_CONTAINER="${DB_CONTAINER:-estrela_softwarelocal-db-1}"`
— o nome do diretório de **desenvolvimento**. No servidor o projeto vive em `/opt/estrela`, então
o container se chama `estrela-db-1`. Se o cron nunca conseguiu achar o container, **não existe
backup nenhum**.

Isso é bloqueante porque **o agente exige backup antes de qualquer deploy** (regra dura: falhou o
backup, aborta). Sem consertar, nenhum botão vai funcionar.

```bash
ssh estrela@estrelaserver
ls -la /backup/estrela_gestao/          # tem algum .sql.gz? de quando?
tail -20 /var/log/estrela-backup.log    # o que o cron reclamou?
docker ps --format '{{.Names}}'         # qual é o nome REAL do container do banco?
```

**Se estiver quebrado**, conserte antes da janela. Não edite o script (ele é versionado): passe o
nome certo pelo ambiente do cron.

```bash
sudo crontab -l    # veja a linha do backup
# corrija para algo como:
#   0 2 * * * DB_CONTAINER=estrela-db-1 /opt/estrela/scripts/backup-estrela.sh >> /var/log/estrela-backup.log 2>&1
```

Depois **rode uma vez à mão** e confirme que gera um dump não-vazio:

```bash
sudo DB_CONTAINER=estrela-db-1 /opt/estrela/scripts/backup-estrela.sh
ls -lh /backup/estrela_gestao/ | tail -2
```

> Um dump truncado por disco cheio passa na validação do script (ele só checa `-s`, "não-vazio").
> Confira o tamanho: um banco com as fotos em `bytea` não gera um dump de 2 KB.

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
docker pull ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.1
docker images | grep nl_softwareestrela
```

Se o pull funcionar **sem `docker login`**, a parte mais frágil do desenho está confirmada.

### 1.4 Anote a versão que está rodando hoje

Para saber para onde voltar se tudo der errado:

```bash
cd /opt/estrela
git rev-parse --short HEAD
docker compose -f docker-compose.prod.yml ps
```

---

## 2. Fase 7 — o compose passa a puxar a imagem (~2 min de janela)

**O que muda:** o serviço `app` deixa de ser buildado no servidor e passa a consumir
`${APP_IMAGEM}`. Ganha healthcheck, o Caddy passa a esperar o app ficar saudável, os logs ganham
rotação e o Postgres passa a escutar em `127.0.0.1:5432` (para o agente, que roda fora do Docker).

```bash
cd /opt/estrela
git fetch origin main && git status        # a árvore está limpa?
git pull --ff-only origin main
```

Acrescente ao `/opt/estrela/.env.prod` (veja `.env.prod.example`):

```bash
APP_IMAGEM=ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.1
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
$COMPOSE exec app printenv APP_VERSION      # deve ser 0.1.1, NUNCA "dev"

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
cd /opt/estrela
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

### 3.3 Verificar antes de deixar rodando

```bash
sudo systemctl status estrela-agente --no-pager
sudo journalctl -u estrela-agente -n 40 --no-pager

# o argv que ele executaria, sem executar nada:
sudo -u estrela-agente /opt/estrela-agente/venv/bin/python \
     /opt/estrela-agente/agente.py --dry-run

# o cosign confere a imagem que está publicada?
cosign verify --key /etc/estrela-agente/cosign.pub \
  ghcr.io/nextlayerdev/nl_softwareestrela:v0.1.1
```

Deve terminar com *"The signatures were verified against the specified public key"*. Já foi
conferido daqui contra esta mesma chave — se falhar no servidor, o problema é a chave que foi
copiada, não a imagem.

> ⚠️ **Use `v0.1.1` ou mais nova, nunca a `v0.1.0`.** A `v0.1.0` foi publicada antes de o pipeline
> assinar e dá `no signatures found` — o agente a recusa (falha fechado, e está certo em recusar).

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
para o admin da Estrela, nem digitando a URL):

```
https://sistema.local/deploy
```

**Roteiro do ensaio — faça os três, nesta ordem:**

1. **Atualizar** para uma versão nova (publique uma `v0.1.2` de teste antes: `git tag -a v0.1.2 -m '...' && git push origin v0.1.2`, e espere o release.yml ficar verde).
   Acompanhe: a tela mostra "atualizando", o WebSocket cai por ~20–60 s (isso é esperado e
   inerente a qualquer self-update — o app está sendo recriado), e a tela **volta sozinha**. O log
   fica em `deploys.log`, no Postgres, que é o único container que não é recriado.

2. **Reverter** para a `v0.1.1` (que é a que o servidor já estará rodando). Confirme que:
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
