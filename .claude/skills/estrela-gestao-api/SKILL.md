---
name: estrela-gestao-api
description: Padrões e mapa do código do sistema Estrela Gestão (FastAPI + SQLAlchemy 2.0 sync + Jinja2/HTMX + PostgreSQL, local/offline). Use SEMPRE que for criar ou alterar qualquer feature deste projeto — endpoints, models, services, telas, ETL, estoque, pedidos, financeiro — para seguir a arquitetura de 4 camadas, o RBAC, o estoque append-only e as convenções já estabelecidas.
---

# Estrela Gestão — guia de desenvolvimento

Sistema 100% local/offline de estoque e pedidos. Stack: **Python 3.12 (uv)**, **FastAPI sync**,
**SQLAlchemy 2.0 + psycopg 3**, **Alembic**, **Pydantic v2**, **Jinja2 + HTMX + Alpine + Tailwind
(standalone, sem Node)**, **PostgreSQL**, **argon2 + JWT (cookie httpOnly)**. Sem CDN em runtime.

## Arquitetura — 4 camadas (NUNCA pule)
`Rota → Controller → Service → Repository → Model`
- **Rota** (`app/web/routes/<modulo>.py` para HTML; `app/routers/` para JSON Fase 2): define método/path,
  `Depends(require_role(...))`, chama o controller. O router é **auto-registrado** pelo nome do arquivo
  via `_registrar_routers()` em `app/main.py` — para uma rota nova aparecer, o nome do módulo precisa
  estar na lista `web_modulos` desse arquivo.
- **Controller** (`app/controllers/`): valida entrada (Pydantic/Form), chama service(s), monta resposta
  (render de template ou JSON). Sem regra de negócio.
- **Service** (`app/services/`): TODA a regra de negócio. Recebe `db: Session`. **NÃO faz commit**
  (o `get_db` commita no fim do request → atomicidade tudo-ou-nada por request).
- **Repository** (`app/repositories/`): só queries SQLAlchemy. Singleton de módulo (`x_repo = XRepository()`).

## Regras invioláveis
- **Estoque só muda via `app/services/estoque_service.py`** (append-only). Saldo vive em `ProdutoVariacao`
  (`estoque_fisico`/`estoque_reservado`/`estoque_modo`/`rotulo_aprox`); `.disponivel = fisico - reservado`.
  Toda operação cria `MovimentacaoEstoque` com `saldo_apos`. Métodos: `entrada/reservar/baixar/estornar/ajustar`.
  EXATO bloqueia venda além do disponível; APROXIMADO não bloqueia (rótulo MUITO/POUCO/TEM/ACABOU).
- **Dinheiro em `Decimal`** / `Numeric(12,2)`. Nunca float.
- **RBAC** em toda rota: `from app.deps.auth import require_role, get_current_user`. Perfis:
  `admin, vendedor, financeiro, funcionario`. `preco_custo`/margem/valorização ocultos para vendedor e
  funcionário (`PERFIS_SEM_CUSTO` em `app/models/enums.py`).
- **Numeração de pedido** sem buracos: `db.scalar(select(func.nextval('pedido_numero_seq')))`.
- **Português (BR)** em toda a UI.

## Templates / HTMX
- `from app.core.templates import templates`. **Assinatura (Starlette ≥1.3)**:
  `templates.TemplateResponse(request, "modulo/pagina.html", {ctx})` — `request` é o **1º posicional**;
  não coloque `"request"` dentro do dict. Sempre passe `"user": usuario` (a `base.html` monta a sidebar
  por perfil).
- Páginas estendem `{% extends "base.html" %}`. Fragmentos HTMX começam com `_` e **não** têm html/head/body.
- Filtro Jinja `moeda` disponível. Classes de componente em `app/static/css/input.css`:
  `btn-primario/-secundario/-perigo`, `card`, `input`, `label`, `tabela`, `selo-muito/-pouco/-tem/-acabou`,
  `alerta-erro/-ok`. Cores da marca: `gold-*`, `sidebar`, `marca-fundo/-borda`, `ok/aviso/critico/info`.
  Após mexer em templates, recompile: `./tailwindcss -i app/static/css/input.css -o app/static/css/output.css --minify`.
- Busca instantânea: `hx-get` + `hx-trigger="keyup changed delay:250ms"` + `hx-target` no tbody,
  devolvendo o fragmento `_linhas.html`. Busca usa pg_trgm: `Modelo.campo.op("%")(termo)` + índice GIN.

## Erros
`from app.core.errors import RegraNegocioError (422), NaoEncontradoError (404), PermissaoNegadaError (403),
NaoAutenticadoError (401)`. Handler global em `main.py` devolve JSON em `/api/*`, fragmento HTMX em
requisições `HX-Request`, e página `erro.html`/redirect para `/login` no resto. Nunca vaze stack trace.

## Comandos
```bash
uv run uvicorn app.main:app --reload      # dev (prefixe DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib se usar WeasyPrint)
uv run alembic upgrade head               # migrations  | alembic check confere modelo×migration
uv run python scripts/seed.py             # dados de dev (senha estrela123)
uv run python scripts/import_planilhas.py --file data/CONTROLE.xlsx [--dry-run]  # ETL
uv run pytest                             # testes (fixtures em tests/conftest.py: db + usuario_<perfil>)
uv run ruff check . && uv run ruff format .
```

## Mapa de módulos (app/web/routes/)
`auth` (login/logout) · `dashboard` (KPIs) · `estoque` (busca, localização/tablet, entrada, ajuste,
movimentações, inventário) · `produtos`/`clientes`/`usuarios` (CRUD) · `pedidos` (ciclo + impressão) ·
`separacao` (fila + conferência) · `financeiro` (contas a receber, baixas) · `relatorios` (vendas/ABC/
valorização + export XLSX) · `importacao` (upload→prévia→carga, reusa `app/importer/`).

## Migrations — cuidado com ENUMs
Os tipos ENUM do Postgres **não** somem com `drop_table`: no `downgrade()` faça `op.execute("DROP TYPE IF
EXISTS <nome>")` para cada enum. Índices GIN trigram e compostos ficam declarados em `__table_args__` nos
models (`Produto`, `Pedido`) para `alembic check` ficar limpo.

## Jobs
`app/jobs.py::iniciar_scheduler()` (APScheduler) é chamado pelo `lifespan` do `main.py` **só em produção**
(`ENV=prod`); agenda `financeiro_service.marcar_atrasados`.

## Fase 2 (preparado, não implementado)
`origem` de pedido (`local/catalogo/whatsapp`), `sync_outbox`, `publicar_catalogo`/`produto_imagens`.
Os services já expõem a lógica para virar API JSON em `app/routers/`.
