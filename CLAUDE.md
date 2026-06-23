# CLAUDE.md — Estrela Gestão (Sistema de Estoque e Pedidos)

> **Para o agente Claude Code:** este arquivo é o contexto-raiz do projeto. Leia-o inteiro antes de
> escrever qualquer código. Salve-o como `CLAUDE.md` na raiz do repositório. Sempre que houver dúvida
> de padrão, este documento vence. Para operações de Git, consulte a skill `git-workflow`. O documento
> de requisitos completo do cliente (`planejamento-sistema-estoque-pedidos.md`) é a referência funcional;
> aqui está o que você precisa para **construir**.

---

## 1. O que estamos construindo

Sistema **100% local e offline** de **controle de estoque e pedidos** para a empresa Estrela (atacado import/export),
rodando em um servidor instalado no cliente e acessado por **até 10 terminais** pela rede interna, via navegador
em **modo aplicativo** (PWA — ícone próprio, tela cheia, sem cara de site).

- **Não depende de internet** para operar. Internet só para manutenção remota e backup offsite.
- **Sem instalação nas estações** — só o servidor roda o sistema.
- Volume: **80–100 pedidos/dia**, estoque com **centenas de milhares de SKUs** e alto giro.
- **4 perfis de acesso:** Admin, Vendedor, Financeiro, Funcionário.
- Nasce preparado para a **Fase 2** (catálogo web + WhatsApp com IA), sem retrabalho de modelagem.

A "cara" do produto já foi prototipada e aprovada (dashboard com cards/KPIs, tabelas densas, paleta dourada
da marca — **dourado primário `#B98A19`**, fundo creme `#F6F2E8`, sidebar marrom escuro `#211B0F`).

---

## 2. Stack definitiva

| Camada | Tecnologia | Observação |
|---|---|---|
| Linguagem | **Python 3.12** | |
| Gerenciador de pacotes | **uv** (Astral) | Rápido; `uv add`, `uv run`. Alternativa: Poetry |
| Framework web/API | **FastAPI** | |
| Servidor ASGI | **Uvicorn** (dev) / **Gunicorn + UvicornWorker** (prod) | |
| Banco | **PostgreSQL 16** (container) | extensão `pg_trgm` para busca |
| ORM | **SQLAlchemy 2.0 (sync)** + **psycopg 3** | **Sync**, não async — ver §4 |
| Migrations | **Alembic** | |
| Validação/Schemas | **Pydantic v2** + **pydantic-settings** | config via `.env` |
| Auth | **PyJWT** + **passlib[argon2]** | RBAC via dependencies do FastAPI |
| Templates (UI) | **Jinja2** + **HTMX** + **Alpine.js** | server-rendered; HTMX p/ interatividade |
| CSS | **Tailwind CSS** (Standalone CLI, sem Node) | build do CSS sem depender de JS toolchain |
| PWA | `manifest.webmanifest` + service worker (network-first) | habilita o "modo programa" nos terminais |
| Agendamento | **APScheduler** | jobs internos (ex.: checagens); backup via pgBackRest fora do app |
| Testes | **pytest** + **httpx** (TestClient) + **pytest-cov** | |
| Lint + Format | **Ruff** | lint e format numa ferramenta só |
| Container | **Docker** + **Docker Compose** | app + Postgres |
| Proxy | **Caddy** | HTTPS interno automático (`https://sistema.local`) |
| Acesso remoto (manutenção) | **Tailscale** | sem expor portas à internet |

**Por que NÃO usar async:** o volume é baixo (10 usuários) e o sistema é CRUD-pesado com transações críticas
(reserva e baixa de estoque). SQLAlchemy **sync** dá transações previsíveis, menos pegadinhas e é mais do que
suficiente em performance. FastAPI executa endpoints `def` (sync) em threadpool automaticamente.

**Por que NÃO Supabase / ORM mágico:** PostgreSQL puro + SQLAlchemy cobre 100% do caso com controle total e
operação simples numa máquina que precisa rodar sozinha por meses.

**Por que Jinja2 + HTMX em vez de SPA:** stack única em Python, sem build de JS, server-rendered (latência mínima
na rede local), e HTMX entrega busca instantânea e atualização parcial de tabelas sem a complexidade de um frontend
separado. A camada de **services** já fica pronta para expor **JSON** na Fase 2 (catálogo/WhatsApp).

---

## 3. Estrutura do projeto

```
estrela-gestao/
├── CLAUDE.md                  # este arquivo
├── pyproject.toml             # deps (uv)
├── .env.example
├── docker-compose.yml         # app + postgres (+ caddy em prod)
├── Dockerfile
├── alembic.ini
├── alembic/
│   └── versions/              # migrations
├── app/
│   ├── main.py                # cria o FastAPI, monta routers/web, static, middlewares
│   ├── core/
│   │   ├── config.py          # Settings (pydantic-settings)
│   │   ├── database.py        # engine + SessionLocal + Base
│   │   └── security.py        # hash de senha (argon2), criar/validar JWT
│   ├── deps/
│   │   ├── db.py              # get_db() -> Session
│   │   └── auth.py            # get_current_user(), require_role(...)
│   ├── models/                # SQLAlchemy ORM (1 arquivo por agregado)
│   ├── schemas/               # Pydantic (request/response/DTOs)
│   ├── repositories/          # acesso a dados (queries SQLAlchemy)
│   ├── services/              # regras de negócio (lógica central)
│   ├── controllers/           # handlers: validam, chamam service, montam resposta
│   ├── routers/               # endpoints JSON (Fase 2 e integrações)
│   ├── web/
│   │   ├── routes/            # rotas que devolvem HTML (Jinja2 + HTMX)
│   │   └── templates/         # .html (base, páginas, fragmentos htmx)
│   ├── static/
│   │   ├── css/               # output do Tailwind
│   │   ├── js/                # htmx.min.js, alpine.min.js
│   │   ├── icons/             # ícones do PWA (Estrela Gestão)
│   │   ├── manifest.webmanifest
│   │   └── sw.js             # service worker (network-first)
│   └── importer/              # ETL das planilhas (staging, validação, carga)
├── tests/
│   ├── conftest.py            # fixtures: db de teste, client, usuários por perfil
│   ├── test_estoque.py
│   ├── test_pedidos.py
│   └── ...
└── scripts/
    ├── seed.py                # dados de exemplo p/ dev
    └── import_planilhas.py    # CLI do ETL (--dry-run)
```

---

## 4. Padrão de camadas (siga sempre)

Fluxo de toda feature: **Rota → Controller → Service → Repository → (Model/DB)**.

1. **Rota** (`routers/` para JSON, `web/routes/` para HTML): define método, path, dependency de auth/RBAC,
   e chama o controller.
2. **Controller** (`controllers/`): valida entrada (Pydantic), chama o(s) service(s), monta a resposta
   (JSON ou render de template). Sem regra de negócio aqui.
3. **Service** (`services/`): **toda a lógica de negócio**. Orquestra repositories, aplica regras
   (reserva de estoque, limites por perfil, numeração de pedido). Recebe a `Session` por parâmetro.
4. **Repository** (`repositories/`): só acesso a dados — queries SQLAlchemy. Sem regra de negócio.

> Crie sempre os arquivos das 4 camadas + schema. Não pule camadas, mesmo em CRUD simples.

### Exemplos de referência (estilo a seguir)

**Model** (`app/models/produto.py`):
```python
from sqlalchemy import String, Integer, Numeric, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class Produto(Base):
    __tablename__ = "produtos"
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String, unique=True, index=True)
    descricao: Mapped[str] = mapped_column(String, index=True)
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"))
    unidade: Mapped[str] = mapped_column(String, default="UN")
    fator_conversao: Mapped[int] = mapped_column(Integer, default=1)
    preco_custo: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    preco_venda: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    estoque_minimo: Mapped[int] = mapped_column(Integer, default=0)
    estoque_fisico: Mapped[int] = mapped_column(Integer, default=0)
    estoque_reservado: Mapped[int] = mapped_column(Integer, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    publicar_catalogo: Mapped[bool] = mapped_column(Boolean, default=False)
```

**Schema** (`app/schemas/produto.py`):
```python
from pydantic import BaseModel, ConfigDict

class ProdutoCreate(BaseModel):
    codigo: str
    descricao: str
    preco_venda: float
    estoque_minimo: int = 0
    # ...

class ProdutoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    codigo: str
    descricao: str
    estoque_fisico: int
    estoque_reservado: int
    @property
    def disponivel(self) -> int:
        return self.estoque_fisico - self.estoque_reservado
```

**Repository** (`app/repositories/produto_repo.py`):
```python
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.produto import Produto

class ProdutoRepository:
    def get_by_codigo(self, db: Session, codigo: str) -> Produto | None:
        return db.scalar(select(Produto).where(Produto.codigo == codigo))

    def busca_rapida(self, db: Session, termo: str, limit: int = 20) -> list[Produto]:
        # usa pg_trgm para descrição + match exato/parcial de códigos
        stmt = (
            select(Produto)
            .where(Produto.descricao.op("%")(termo) | Produto.codigo.ilike(f"{termo}%"))
            .order_by(func.similarity(Produto.descricao, termo).desc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

produto_repo = ProdutoRepository()
```

**Service** (`app/services/estoque_service.py`) — exemplo da regra crítica de estoque:
```python
from sqlalchemy.orm import Session
from app.models.produto import Produto
from app.models.movimentacao import MovimentacaoEstoque, TipoMov
from app.core.errors import RegraNegocioError

class EstoqueService:
    def reservar(self, db: Session, produto: Produto, qtd: int, usuario_id: int, pedido_id: int) -> None:
        disponivel = produto.estoque_fisico - produto.estoque_reservado
        if qtd > disponivel:
            raise RegraNegocioError(f"Estoque insuficiente para {produto.codigo}: disp. {disponivel}, pedido {qtd}")
        produto.estoque_reservado += qtd
        db.add(MovimentacaoEstoque(
            produto_id=produto.id, tipo=TipoMov.RESERVA, qtd=qtd,
            origem="pedido", ref_id=pedido_id, usuario_id=usuario_id,
            saldo_apos=produto.estoque_fisico - produto.estoque_reservado,
        ))
        # commit é responsabilidade da rota/uow, não do service

estoque_service = EstoqueService()
```

**Controller + Rota web (HTMX)** (`app/web/routes/estoque.py`):
```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.deps.db import get_db
from app.deps.auth import require_role
from app.core.templates import templates
from app.repositories.produto_repo import produto_repo

router = APIRouter()

@router.get("/estoque/busca", response_class=HTMLResponse)
def busca_estoque(request: Request, q: str = "", db=Depends(get_db),
                  user=Depends(require_role("admin", "vendedor", "financeiro", "funcionario"))):
    produtos = produto_repo.busca_rapida(db, q) if q else []
    # devolve só o fragmento da tabela (HTMX troca o tbody)
    return templates.TemplateResponse("estoque/_linhas.html",
                                      {"request": request, "produtos": produtos})
```

---

## 5. Convenções

**Nomenclatura**
- Arquivos: `snake_case.py` (`produto_repo.py`, `estoque_service.py`).
- Models: classe `PascalCase` singular (`Produto`, `Pedido`, `MovimentacaoEstoque`).
- Repositories: classe `XRepository`, instância módulo-singleton `x_repo = XRepository()`.
- Services: classe `XService`, instância `x_service = XService()`.
- Schemas Pydantic: `XCreate`, `XUpdate`, `XRead`.
- Rotas: `router = APIRouter()`; funções com nome de ação (`criar_pedido`, `busca_estoque`).
- Templates: páginas `modulo/pagina.html`; fragmentos HTMX começam com `_` (`_linhas.html`).

**Imports**
- Sempre absolutos a partir de `app.` (`from app.services.estoque_service import estoque_service`).
- `from __future__ import annotations` no topo quando ajudar com type hints.

**Erros** — exceções de domínio em `app/core/errors.py` (`RegraNegocioError`, `NaoEncontradoError`,
`PermissaoNegadaError`) tratadas por um `exception_handler` global que devolve JSON nas rotas de API e
um fragmento/flash de erro nas rotas web. **Nunca** vazar stack trace para o usuário.

**Transações / Unit of Work** — a `Session` vem do `get_db` (uma por request). Services **não fazem commit**;
quem fecha a transação é a rota/controller no fim do fluxo (ou um helper `with uow(db): ...`). Em operações de
estoque, garantir atomicidade: ou tudo aplica, ou nada.

**Tipagem** — type hints em tudo. Ruff configurado para checar imports e estilo.

**Dinheiro** — `Numeric(12, 2)` no banco; nunca `float` para cálculo financeiro (usar `Decimal`).

---

## 6. Modelo de dados (entidades e regras)

Entidades principais (detalhe completo no documento de requisitos, seção 3):

`usuarios`, `categorias`, `fornecedores`, `produtos`, `produto_codigos_empresa` (N códigos de empresa por produto),
`produto_imagens` (Fase 2), `clientes`, `pedidos`, `pedido_itens`, `movimentacoes_estoque` (append-only),
`inventarios` / `inventario_itens`, `contas_receber`, `auditoria`, `sync_outbox` (Fase 2).

**Regras invioláveis de modelagem:**
- **Estoque nunca é editado direto.** `estoque_fisico` / `estoque_reservado` são saldos materializados,
  sempre derivados de uma `movimentacoes_estoque` (tipos: entrada, saida, ajuste, reserva, estorno).
  Toda alteração gera movimentação imutável com usuário, data/hora, origem e `saldo_apos`.
- **`produto_codigos_empresa` em tabela própria desde o dia 1** (mesmo que hoje seja 1:1) — confirmar Q1 do cliente.
- **Numeração de pedido** sequencial e sem buracos visíveis (sequence dedicada no Postgres).
- **`pedidos.origem`** já prevê `local | catalogo | whatsapp` (Fase 2).
- Índices: **GIN trigram** em `produtos.descricao`; índice em todos os campos de código; compostos em
  pedidos por (vendedor, data) e (cliente, data).
- **Auditoria** (`auditoria`) registra antes/depois (JSONB) em produtos, pedidos, estoque e financeiro.

**Ciclo de vida do estoque no pedido:**
`Confirmar` → reserva (`estoque_reservado += qtd`). `Faturar` → baixa definitiva
(`estoque_fisico -= qtd`, `estoque_reservado -= qtd`). `Cancelar` → estorno das reservas. (Validar Q4 do cliente.)

---

## 7. RBAC — matriz de permissões

Implementar via dependency `require_role(*roles)`. Perfis: `admin`, `vendedor`, `financeiro`, `funcionario`.

| Funcionalidade | Admin | Vendedor | Financeiro | Funcionário |
|---|---|---|---|---|
| Produtos (CRUD) | ✅ | 👁 | 👁 | 👁 |
| Preço de custo / margem | ✅ | ❌ | ✅ | ❌ |
| Estoque: entradas de mercadoria | ✅ | ❌ | ❌ | ✅ |
| Estoque: ajustes manuais | ✅ | ❌ | 👁 | ❌ (solicita) |
| Inventário (contagem) | ✅ | ❌ | ❌ | ✅ (Admin aprova) |
| Pedidos: criar/editar | ✅ todos | ✅ próprios | 👁 | ❌ |
| Separação de pedidos (fila) | ✅ | ❌ | ❌ | ✅ |
| Faturar / baixar recebimento | ✅ | ❌ | ✅ | ❌ |
| Contas a receber | ✅ | ❌ | ✅ | ❌ |
| Clientes (CRUD) | ✅ | ✅ | 👁 | ❌ |
| Relatórios de vendas | ✅ todos | 👁 próprios | ✅ | ❌ |
| Relatórios financeiros | ✅ | ❌ | ✅ | ❌ |
| Usuários, config, importações | ✅ | ❌ | ❌ | ❌ |
| Log de auditoria | ✅ | ❌ | ❌ | ❌ |

Vendedor e Funcionário **não veem `preco_custo`** — filtrar no schema de resposta/template por perfil.

---

## 8. Telas / módulos (mapa de implementação)

Protótipos aprovados (referência visual): login, dashboard (Admin), estoque, novo pedido (Vendedor),
fila de separação (Funcionário), financeiro (contas a receber). Implementar como páginas Jinja2:

| Módulo | Rota base | Perfis | Destaques |
|---|---|---|---|
| Login | `/login` | todos | argon2 + JWT (cookie httpOnly) |
| Dashboard | `/` | Admin (visão), demais reduzido | KPIs do dia, gráfico 7 dias, alertas de mínimo |
| Estoque | `/estoque` | todos (níveis distintos) | busca HTMX instantânea, status visual, movimentações |
| Produtos | `/produtos` | Admin CRUD | código + códigos de empresa, categorias |
| Pedidos | `/pedidos` | Vendedor/Admin | criação com saldo em tempo real, reserva, impressão |
| Separação | `/separacao` | Funcionário | fila de confirmados, conferência item a item |
| Financeiro | `/financeiro` | Financeiro/Admin | contas a receber, baixas, exportação XLSX |
| Clientes | `/clientes` | Vendedor/Admin | cadastro, condição de pagamento |
| Relatórios | `/relatorios` | conforme perfil | vendas, ABC, valorização, export |
| Importação | `/importacao` | Admin | upload planilha, preview, relatório de erros |
| Usuários | `/usuarios` | Admin | CRUD + reset de senha |

Impressão (pedido A4 e lista de separação): gerar HTML com CSS `@media print` ou PDF via `weasyprint`.
Exportação XLSX: `openpyxl`.

---

## 9. ETL das planilhas (módulo `importer/`)

Maior risco do projeto. Pipeline em 6 etapas (detalhe na seção 4 do planejamento):
1. **Coleta** das planilhas reais (estoque + 1 mês de pedidos + planilhas de fornecedor).
2. **Dicionário de dados** (`docs/dicionario-dados.md`): cada coluna → campo canônico.
3. **Staging**: ler bruto (`openpyxl`/`pandas`) para tabelas `staging_*` sem transformação destrutiva.
4. **Limpeza/validação**: normalização pt-BR (vírgula decimal), dedup por código, detecção de órfãos/conflitos,
   estoques negativos. Saída: **relatório de inconsistências em XLSX** para o cliente decidir.
5. **Carga definitiva**: importador **idempotente** com `--dry-run`; gera movimentação tipo `importacao`
   como saldo inicial (rastreável).
6. **Importador no produto**: tela `/importacao` reaproveita o mesmo motor para entradas recorrentes.

CLI: `uv run python scripts/import_planilhas.py --file caminho.xlsx --dry-run`.

---

## 10. Modo aplicativo / PWA (terminais)

O cliente quer abrir "como um programa". Entregar PWA instalável:
- `static/manifest.webmanifest`: `name="Estrela Gestão"`, `display="standalone"`, `theme_color="#B98A19"`,
  `background_color="#211B0F"`, ícones 192/512.
- `static/sw.js`: service worker **network-first** (sempre busca a versão do servidor; cai para cache só offline).
  Evita terminal preso em versão antiga.
- No go-live, criar atalho em modo app: `chrome --app=https://sistema.local` (ou Edge) com ícone na área de trabalho.
- Atualização permanece **centralizada no servidor** — terminais nunca precisam reinstalar.

---

## 11. Setup do ambiente

**Dependências (uv):**
```bash
uv init
uv add fastapi uvicorn[standard] gunicorn sqlalchemy psycopg[binary] alembic \
       pydantic pydantic-settings pyjwt "passlib[argon2]" jinja2 python-multipart \
       apscheduler openpyxl weasyprint
uv add --dev pytest httpx pytest-cov ruff
```

**.env.example:**
```
DATABASE_URL=postgresql+psycopg://estrela:senha@localhost:5432/estrela_gestao
JWT_SECRET=troque-isto
JWT_EXPIRES_MIN=480
ENV=dev
```

**Comandos (padronizar no README):**
```bash
uv run uvicorn app.main:app --reload          # dev server
uv run alembic revision --autogenerate -m "msg"  # nova migration
uv run alembic upgrade head                    # aplicar migrations
uv run pytest --cov=app                        # testes + cobertura
uv run ruff check . && uv run ruff format .    # lint + format
uv run python scripts/seed.py                  # popular dados de dev
docker compose up -d                           # postgres (+ app/caddy em prod)
```

**Tailwind (sem Node):** baixar o binário standalone do Tailwind e gerar o CSS:
```bash
./tailwindcss -i app/static/css/input.css -o app/static/css/output.css --watch
```

---

## 12. Git workflow

Seguir **integralmente** a skill `git-workflow`. Resumo operacional:
- Branches a partir de `dev`: `dev/feat-*`, `dev/fix-*`, `dev/chore-*`.
- **Conventional Commits**: `feat(estoque): adicionar reserva na confirmação do pedido`.
- PR de `dev/*` → `dev` (squash). PR `dev` → `main` (release) + tag SemVer.
- Nunca commitar direto em `main`/`dev`. Nunca `WIP`.
- Abrir um PR por feature, com descrição (o que muda / por quê / como testar).

---

## 13. Testes e Definition of Done

**Testes (pytest):** priorizar os fluxos críticos —
- Estoque: reserva, baixa, estorno, ajuste; saldo nunca fica negativo; movimentação sempre criada.
- Pedido: total/desconto, limite de desconto por perfil, numeração sem buraco.
- RBAC: cada perfil acessa só o que deve (testar 403 nos bloqueios).
- Importador: idempotência (rodar 2x não duplica), detecção de inconsistências.
- `conftest.py`: banco de teste isolado (transação com rollback por teste) + fixtures de usuário por perfil.

**Uma feature só está "pronta" quando:**
- [ ] 4 camadas + schema implementadas no padrão.
- [ ] Migration criada e aplicável (`alembic upgrade head` limpo).
- [ ] Testes dos caminhos críticos passando.
- [ ] RBAC aplicado e testado.
- [ ] `ruff check` e `ruff format` limpos.
- [ ] Sem `print` de debug; erros tratados pelo handler global.
- [ ] Tela/HTMX funcionando (quando aplicável) e exportação/impressão se previstas.

---

## 14. Roadmap de sprints (execução)

| Sprint | Foco | Entregas |
|---|---|---|
| **S0** | Descoberta | Receber planilhas; `dicionario-dados.md`; responder Q1–Q16; modelo de dados aprovado; wireframes confirmados |
| **S1** | Fundação | Projeto (uv) + Docker + Postgres; `core` (config/db/security); auth + RBAC (login, cookie JWT, `require_role`); CRUD produtos/clientes/usuários; busca rápida (pg_trgm); base de templates (layout, sidebar, paleta); importador v1 (staging + relatório de inconsistências) |
| **S2** | Estoque | Movimentações (append-only); entradas; ajustes; inventário (contagem + aprovação); alertas de mínimo; posição em tempo real; tela `/estoque` com busca HTMX e histórico |
| **S3** | Pedidos | Fluxo completo (rascunho→confirmado→separação→faturado→entregue→cancelado); reserva/baixa/estorno; descontos com limite por perfil; impressão (pedido A4 + lista de separação); fila `/separacao` do Funcionário |
| **S4** | Financeiro + Relatórios | Contas a receber na fatura; baixas (Pix/boleto/dinheiro); inadimplência; relatórios (vendas, ABC, valorização) + export XLSX; dashboard com KPIs e gráfico |
| **S5** | Go-live | PWA + service worker + ícones; empacotar com Docker no servidor local; migração definitiva dos dados; backup (pgBackRest) + Tailscale + monitoramento; configurar **modo aplicativo nos 10 terminais**; treinamento por perfil; 1 semana de operação assistida |
| **Fase 2** | Catálogo + WhatsApp | Expor services como API JSON; `sync_outbox`; catálogo web + imagens (Bunny.net/CDN); Omni Resposta (WhatsApp IA) |

---

## 15. Primeiros passos (faça nesta ordem)

Quando eu disser para começar o S1, execute na sequência:

1. `uv init` + adicionar dependências (§11) + configurar **Ruff** no `pyproject.toml`.
2. Criar a **estrutura de pastas** da §3 (com `__init__.py`).
3. `docker-compose.yml` com **Postgres 16** + volume; subir e validar conexão.
4. `app/core/`: `config.py` (Settings), `database.py` (engine sync + `SessionLocal` + `Base`), `security.py` (argon2 + JWT).
5. `app/core/errors.py` + handlers globais no `main.py`.
6. Models base: `usuario`, `categoria`, `fornecedor`, `produto`, `produto_codigos_empresa`, `cliente`.
7. **Alembic** init + primeira migration (`autogenerate`) + habilitar extensão `pg_trgm` + índice GIN trigram em `produtos.descricao`.
8. Auth: login (`/login`), hash de senha, emissão de JWT em cookie httpOnly; `deps/auth.py` com `get_current_user` e `require_role`.
9. Layout base (Jinja2): `templates/base.html` com sidebar e paleta da marca; incluir HTMX e Alpine.
10. Primeiro CRUD ponta a ponta (**Produtos**) nas 4 camadas + tela com **busca HTMX** — serve de molde para o resto.
11. `scripts/seed.py` com um usuário de cada perfil + alguns produtos.
12. Importador v1 em `importer/` + `scripts/import_planilhas.py --dry-run` (quando as planilhas chegarem).

Ao final do S1, **criar uma skill do projeto** (`estrela-gestao-api`) nos moldes da `profissao-laser-api`, documentando estes padrões para acelerar os próximos sprints.

---

## 16. Regras críticas (não errar)

- **Offline-first**: nada na Fase 1 pode depender de internet. Sem CDNs externos em runtime — HTMX, Alpine,
  Tailwind e fontes servidos localmente de `static/`.
- **Estoque só muda via movimentação** (append-only). Nunca `UPDATE` direto de saldo sem registrar movimentação.
- **Atomicidade** em reserva/baixa/estorno: transação fecha tudo ou nada.
- **Numeração de pedido** sem buracos (sequence Postgres).
- **`preco_custo` oculto** para Vendedor e Funcionário (no schema e no template).
- **Decimal** para dinheiro, nunca float.
- **Auditoria** em produtos, pedidos, estoque e financeiro.
- **Senhas** com argon2; **JWT** em cookie httpOnly; sistema **não exposto à internet** (acesso externo só via Tailscale).
- **Service worker network-first** para o PWA não prender versão antiga.
- Mensagens, labels e textos de UI em **português (BR)**.
