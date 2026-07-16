<!--
Estrela Gestão — template de PR.

Lembre que este sistema roda OFFLINE num mini PC dentro da cliente, com 10 terminais
dependendo dele para faturar 80–100 pedidos/dia. Não há staging e não há "rollback do
banco". O que passar daqui vira uma imagem que alguém vai aplicar em produção clicando
num botão. Preencha pensando em quem vai clicar.

Apague as seções que realmente não se aplicam. Não apague a de "Impacto no servidor".
-->

## O que muda

<!-- Uma frase, no imperativo. É o que vai virar o título/subject do commit de squash. -->

## Por quê

<!-- O problema, não a solução. Se houver issue, referencie: Closes #123 -->

## Como testar

<!--
Passo a passo reproduzível. Perfil necessário (admin/vendedor/financeiro/funcionario/dev),
rota, e o que se deve ver.

  uv run alembic upgrade head
  uv run python scripts/seed.py
  uv run uvicorn app.main:app --reload
-->

---

## Impacto no servidor da cliente

> Esta seção é lida na hora de aprovar a Release. Se estiver errada, o deploy quebra
> num sábado. Marque com honestidade.

- [ ] **Tem migration nova?**
  - Revision: `_______` (gerada com `uv run alembic revision` — **nunca** escolha o id à mão: já houve colisão)
  - [ ] `alembic upgrade head` roda limpo num banco vindo da versão anterior
  - [ ] É **expand/contract**? (a imagem ANTERIOR continua subindo com o schema NOVO)
        Se **não** for, diga aqui por quê e assuma: o rollback desta versão deixa de ser
        trivial. O agente **nunca** faz downgrade do banco nem restaura backup sozinho —
        restaurar apagaria em silêncio os pedidos feitos entre o backup e a falha.

- [ ] **Variável nova ou renomeada no `.env.prod`?**
  - Nome(s): `_______`
  - [ ] Adicionada ao `.env.prod.example`
  - [ ] Repassada explicitamente no `docker-compose.prod.yml` (o `extra="ignore"` do
        pydantic engole a var ausente **em silêncio** — a tela mostra "não configurado"
        e nenhum erro sobe)
  - [ ] Tem default seguro, **ou** está anotado que precisa existir no mini PC ANTES do deploy

- [ ] **Muda `Dockerfile`, `.dockerignore`, `entrypoint.sh` ou os composes?**
      Descreva. Lembre que a imagem vai para um registry **público**.

- [ ] **Nada disso. É só código; o deploy é trocar a imagem e pronto.**

---

## Definition of Done (CLAUDE.md §13)

<!-- Marque só o que se aplica; um fix de CSS não precisa das 4 camadas. -->

- [ ] 4 camadas + schema no padrão (Rota → Controller → Service → Repository), sem pular camada
- [ ] Migration criada e aplicável (`uv run alembic upgrade head` limpo)
- [ ] Testes dos caminhos críticos passando (`uv run pytest`)
- [ ] RBAC aplicado e testado (403 nos bloqueios; `dev` é superusuário; `/deploy` é só `dev`)
- [ ] `uv run ruff check .` e `uv run ruff format --check .` limpos
- [ ] Sem `print` de debug; erros pelo handler global (nunca vazar stack trace ao usuário)
- [ ] Tela/HTMX funcionando, e impressão/exportação se previstas
- [ ] Textos de UI em **português (BR)**

### Regras invioláveis tocadas por este PR

- [ ] **Estoque** só muda via `movimentacoes_estoque` (append-only) — nenhum `UPDATE` de saldo sem movimentação
- [ ] **Dinheiro** em `Decimal`/`Numeric(12,2)` — nunca `float`
- [ ] **`preco_custo`** oculto para vendedor e funcionário (no schema **e** no template)
- [ ] **Offline-first**: nenhum CDN/asset externo em runtime
- [ ] **Auditoria** registrada (produtos, pedidos, estoque, financeiro)
- [ ] Payload de evento (`emitir`) só com ids/primitivos — nunca HTML nem senha

## Notas para o revisor

<!-- Trade-off assumido, dívida deixada, o que você olhou três vezes. -->
