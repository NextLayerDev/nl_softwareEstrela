# 02 — Arquitetura e Fundação · Estrela Gestão

> Responsável: Tech Lead / Backend Sênior. Entregar primeiro — é a base que todos os outros marcos usam.
> Espelha o `CLAUDE.md`; aqui com mais detalhe de decisões e do scaffolding inicial.

---

## 1. Stack definitiva

| Camada | Tecnologia | Observação |
|---|---|---|
| Linguagem | **Python 3.12** | |
| Pacotes | **uv** (Astral) | `uv add`, `uv run` |
| Web/API | **FastAPI** | |
| ASGI | **Uvicorn** (dev) / **Gunicorn + UvicornWorker** (prod) | |
| Banco | **PostgreSQL 16** | extensão `pg_trgm` |
| ORM | **SQLAlchemy 2.0 (sync)** + **psycopg 3** | sync, não async — §2 |
| Migrations | **Alembic** | |
| Schemas | **Pydantic v2** + **pydantic-settings** | |
| Auth | **PyJWT** + **passlib[argon2]** | RBAC via dependencies |
| UI | **Jinja2** + **HTMX** + **Alpine.js** + **Tailwind** | server-rendered |
| PWA | manifest + service worker network-first | modo aplicativo nos terminais |
| Agendamento | **APScheduler** | jobs internos |
| Impressão/Export | **WeasyPrint** (PDF) + **openpyxl** (XLSX) | pedidos, separação, relatórios |
| Testes | **pytest** + **httpx** + **pytest-cov** | |
| Lint/format | **Ruff** | |
| Container | **Docker** + **Docker Compose** | |
| Proxy | **Caddy** | HTTPS interno |
| Manutenção remota | **Tailscale** | |

---

## 2. Decisões de arquitetura (e por quê)

**Sync, não async.** Volume baixo (10 usuários) e transações críticas de estoque (reserva/baixa) pedem
previsibilidade. SQLAlchemy sync evita pegadinhas de sessão/transação async. FastAPI roda endpoints `def`
em threadpool — performance sobra.

**PostgreSQL puro, sem Supabase self-hosted.** Menos partes móveis numa máquina que roda sozinha no cliente
por meses. Supabase só volta na Fase 2 (cloud).

**Jinja2 + HTMX, não SPA.** Stack única em Python, sem build de JS, server-rendered (latência mínima na rede
local). HTMX entrega busca instantânea e atualização parcial de tabela. A camada **service** fica pronta para
expor JSON na Fase 2 (catálogo/WhatsApp).

**Estoque append-only.** `estoque_fisico`/`estoque_reservado` são saldos materializados, sempre derivados de
`movimentacoes_estoque`. Garante auditabilidade total (doc `05`).

**Offline-first radical.** Sem CDN em runtime: HTMX, Alpine, Tailwind e fontes servidos de `static/`.

---

## 3. Estrutura do projeto

```
estrela-gestao/
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── docker-compose.prod.yml
├── Dockerfile
├── Caddyfile
├── alembic.ini
├── alembic/versions/
├── app/
│   ├── main.py
│   ├── core/        # config, database, security, errors, templates
│   ├── deps/        # db (get_db), auth (get_current_user, require_role)
│   ├── models/      # SQLAlchemy ORM (1 arquivo por agregado)
│   ├── schemas/     # Pydantic
│   ├── repositories/
│   ├── services/
│   ├── controllers/
│   ├── routers/     # endpoints JSON (Fase 2)
│   ├── web/
│   │   ├── routes/        # rotas que devolvem HTML
│   │   └── templates/     # base, páginas, fragmentos _*.html
│   ├── static/      # css, js, icons, manifest.webmanifest, sw.js
│   └── importer/    # ETL planilhas
├── tests/
└── scripts/         # seed.py, import_planilhas.py
```

---

## 4. Padrão de 4 camadas (obrigatório)

Fluxo: **Rota → Controller → Service → Repository → Model/DB**.

- **Rota** (`web/routes/` HTML, `routers/` JSON): método, path, dependency de auth/RBAC, chama controller.
- **Controller**: valida entrada (Pydantic), chama service(s), monta resposta (render template ou JSON).
- **Service**: **toda regra de negócio**. Recebe `Session`. **Não faz commit** (quem fecha é a rota/UoW).
- **Repository**: só queries SQLAlchemy.

> Exemplos completos de cada camada estão no `CLAUDE.md` (§4). Seguir aquele estilo à risca.

**Convenções** (resumo — detalhe no `CLAUDE.md` §5): arquivos `snake_case`; models `PascalCase` singular;
repos/services como singletons de módulo (`produto_repo`, `estoque_service`); schemas `XCreate/XUpdate/XRead`;
imports absolutos a partir de `app.`; erros de domínio em `core/errors.py` com handler global; **`Decimal`** para
dinheiro; type hints em tudo.

---

## 5. Setup inicial (passo a passo do scaffolding)

```bash
# 1. Projeto e dependências
uv init estrela-gestao && cd estrela-gestao
uv add fastapi "uvicorn[standard]" gunicorn sqlalchemy "psycopg[binary]" alembic \
       pydantic pydantic-settings pyjwt "passlib[argon2]" jinja2 python-multipart \
       apscheduler openpyxl weasyprint
uv add --dev pytest httpx pytest-cov ruff

# 2. Estrutura de pastas (criar com __init__.py)
mkdir -p app/{core,deps,models,schemas,repositories,services,controllers,routers} \
         app/web/{routes,templates} app/static/{css,js,icons} app/importer tests scripts

# 3. Postgres via Docker
docker compose up -d db

# 4. Alembic
uv run alembic init alembic
# editar alembic/env.py para usar Base e DATABASE_URL do settings

# 5. Primeira migration (após criar os models base — doc 03)
uv run alembic revision --autogenerate -m "schema inicial"
uv run alembic upgrade head

# 6. Rodar
uv run uvicorn app.main:app --reload
```

**`pyproject.toml` — configurar Ruff:**
```toml
[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

**`.env.example`:**
```
DATABASE_URL=postgresql+psycopg://estrela:senha@localhost:5432/estrela_gestao
JWT_SECRET=troque-isto
JWT_EXPIRES_MIN=480
ENV=dev
```

**`docker-compose.yml` (dev — só o banco):**
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: estrela
      POSTGRES_PASSWORD: senha
      POSTGRES_DB: estrela_gestao
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes: { pgdata: {} }
```

---

## 6. Bootstrap do app (`app/main.py`)

Responsabilidades mínimas:
- Criar o `FastAPI`.
- Montar `StaticFiles` em `/static`.
- Incluir routers (`web/routes/*` e `routers/*`).
- Registrar **exception handlers globais** (`core/errors.py`): domínio → 4xx amigável (JSON nas rotas API,
  fragmento/flash nas rotas web); inesperado → 500 sem stack trace.
- Middleware de autenticação por cookie (lê JWT, injeta usuário).
- `core/templates.py`: instância única de `Jinja2Templates(directory="app/web/templates")`.

---

## 7. Definition of Done do marco 02

- [ ] Projeto roda (`uvicorn`) e responde num healthcheck (`/health`).
- [ ] Postgres sobe via Docker e o app conecta.
- [ ] `core/` completo: config, database (engine sync + SessionLocal + Base), security (argon2 + JWT), errors, templates.
- [ ] `deps/` com `get_db` e esqueleto de `require_role`.
- [ ] Alembic configurado e primeira migration aplicável.
- [ ] Ruff limpo; estrutura de pastas criada; README com os comandos.
- [ ] Layout base Jinja2 (`base.html`) com a paleta da marca e HTMX/Alpine locais carregando.
