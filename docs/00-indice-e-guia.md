# 00 — Índice e Guia do Projeto · Estrela Gestão

> **Sistema de Estoque e Pedidos — Estrela América do Sul**
> Backend **Python / FastAPI** · interface **Jinja2 + HTMX + Tailwind** · PostgreSQL 16 · 100% local/offline.
> Este pacote de documentos é o plano de execução para um time inteiro. Cada arquivo cobre uma área.

---

## Como usar estes documentos

Cada `.md` é um **marco** (milestone) autocontido, escrito para ser entregue a uma pessoa ou dupla.
A ordem dos números é a ordem recomendada de execução, mas vários rodam em paralelo após o marco 02.

| Doc | Marco | Responsável sugerido | Depende de |
|---|---|---|---|
| `00-indice-e-guia.md` | Visão geral e papéis | Tech Lead | — |
| `01-regras-de-negocio.md` | Regras do cliente (áudios + planilha) | Todos leem | — |
| `02-arquitetura-e-fundacao.md` | Stack, estrutura, padrões, setup | Tech Lead / Backend Sr | 01 |
| `03-modelo-de-dados-e-migrations.md` | Schema, entidades, migrations | Backend (DB) | 01, 02 |
| `04-etl-importacao-planilhas.md` | Importar a planilha real | Backend (Dados) | 01, 03 |
| `05-modulo-estoque.md` | Estoque, movimentações, localização | Backend (Estoque) | 03 |
| `06-modulo-pedidos-e-financeiro.md` | Pedidos, separação, contas a receber | Backend (Pedidos) | 03, 05 |
| `07-frontend-ui-pwa.md` | Telas, HTMX, modo aplicativo nos terminais | Frontend | 02 |
| `08-infra-deploy-e-go-live.md` | Servidor local, Docker, backup, go-live | DevOps | 02 |

> O documento `CLAUDE.md` (entregue à parte) é o contexto-raiz que o agente Claude Code lê.
> Estes marcos detalham cada frente; o `CLAUDE.md` resume os padrões transversais.

---

## Divisão de trabalho recomendada (time de 3–4 + Claude Code)

- **Tech Lead** — marcos 02 e 08; revisa PRs; mantém `CLAUDE.md` e a skill do projeto.
- **Eduardo (dev principal)** — execução no dia a dia com Claude Code: marcos 03 → 05 → 06.
- **Dev de dados** — marco 04 (ETL), o mais sensível; trabalha junto do cliente nas planilhas.
- **Frontend** — marco 07 (telas Jinja2/HTMX, PWA), em paralelo a partir do fim do 02.
- **Tobias** — code review (padrões dos marcos 02 e 01) e testes.

---

## Princípios inegociáveis (valem para todos)

1. **Offline-first.** Nada na Fase 1 depende de internet. Sem CDN em runtime — HTMX, Alpine, Tailwind e fontes locais.
2. **Estoque só muda via movimentação** (append-only). Saldo é sempre derivado, nunca editado direto.
3. **4 camadas sempre:** Rota → Controller → Service → Repository. Sem pular camada.
4. **Dinheiro em `Decimal`/`Numeric(12,2)`**, nunca `float`.
5. **RBAC** em toda rota (`require_role`). Vendedor e Funcionário não veem custo.
6. **Tudo em português (BR)** na UI; código e commits seguem convenção (Conventional Commits).
7. **Conventional Commits + PR por feature** (skill `git-workflow`).

---

## Estado do levantamento

✅ **Já temos:** a planilha real (`CONTROLE.xlsx`) e 3 áudios do cliente com as regras de operação — destrinchados no doc `01`.
⏳ **Ainda pendente do cliente:** confirmação das perguntas abertas (listadas no fim do doc `01`), principalmente preço por faixa de quantidade e o significado exato de "muito/pouco".

---

## Glossário rápido

- **SKU / código** — código interno do produto (ex.: `K708`, `JSC1140`).
- **Código alternativo** — código da caixa/fábrica secundária do mesmo produto (ex.: Stanley, canetas).
- **Variação** — combinação produto + cor (cada cor tem saldo próprio).
- **Localização** — andar/lado/sala onde o produto fica no estoque físico (10 andares).
- **Movimentação** — registro imutável de qualquer mudança de estoque.
- **Reserva** — estoque comprometido por um pedido confirmado, ainda não faturado.
