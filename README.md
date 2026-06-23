# Estrela Gestão — Sistema de Estoque e Pedidos (Local)

Sistema **100% local e offline** de controle de estoque e pedidos para a **Estrela América do Sul**.
Backend **Python / FastAPI** · interface **Jinja2 + HTMX + Tailwind** · **PostgreSQL 16**.

## Estrutura

- **`CLAUDE.md`** — contexto-raiz que o agente Claude Code lê antes de qualquer coisa.
- **`docs/`** — planejamento completo para o time, dividido por frente:
  - `00-indice-e-guia.md` — comece por aqui (mapa, papéis, princípios).
  - `01-regras-de-negocio.md` — regras do cliente (áudios + planilha real).
  - `02-arquitetura-e-fundacao.md` — stack, estrutura, padrões, setup.
  - `03-modelo-de-dados-e-migrations.md` — schema e migrations.
  - `04-etl-importacao-planilhas.md` — importação da planilha `CONTROLE.xlsx`.
  - `05-modulo-estoque.md` — estoque, movimentações, inventário, localização.
  - `06-modulo-pedidos-e-financeiro.md` — pedidos, separação, contas a receber.
  - `07-frontend-ui-pwa.md` — telas, HTMX, modo aplicativo nos terminais.
  - `08-infra-deploy-e-go-live.md` — servidor local, Docker, backup, go-live.
  - `dicionario-dados.md` — mapa coluna→campo da planilha real.

## Como começar

1. Leia `docs/00-indice-e-guia.md` e `docs/01-regras-de-negocio.md`.
2. Tech Lead executa `docs/02-arquitetura-e-fundacao.md` (scaffolding).
3. Demais frentes seguem seus marcos em paralelo a partir do marco 02.
4. O agente Claude Code usa o `CLAUDE.md` como contexto-raiz.
