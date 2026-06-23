# 05 — Módulo de Estoque · Estrela Gestão

> Responsável: Backend (Estoque). Depende do doc `03` (modelo).
> Cobre o coração do sistema: movimentações append-only, estoque exato vs aproximado, inventário,
> alertas, e a **consulta de localização para o tablet** do estoque.

---

## 1. Conceito central: saldo derivado de movimentações

O saldo de uma **variação** (produto + cor) **nunca é editado direto**. Toda mudança passa por um service que:
1. valida a regra,
2. ajusta `estoque_fisico`/`estoque_reservado` da variação,
3. grava uma `movimentacoes_estoque` imutável com `saldo_apos`, usuário, origem e (se ajuste) motivo.

Tipos de movimentação: `entrada`, `saida`, `ajuste`, `reserva`, `estorno`.

```python
# app/services/estoque_service.py (núcleo)
class EstoqueService:
    def _registrar(self, db, variacao, tipo, qtd, origem, usuario_id, ref_id=None, motivo=None):
        db.add(MovimentacaoEstoque(
            produto_variacao_id=variacao.id, tipo=tipo, qtd=qtd, origem=origem,
            ref_id=ref_id, usuario_id=usuario_id, motivo=motivo,
            saldo_apos=variacao.estoque_fisico - variacao.estoque_reservado,
        ))

    def entrada(self, db, variacao, qtd, usuario_id, origem="manual", ref_id=None):
        variacao.estoque_modo = "EXATO"          # dar entrada torna o saldo exato
        variacao.estoque_fisico += qtd
        self._registrar(db, variacao, "entrada", qtd, origem, usuario_id, ref_id)

    def reservar(self, db, variacao, qtd, usuario_id, pedido_id):
        if variacao.estoque_modo == "EXATO":
            disp = variacao.estoque_fisico - variacao.estoque_reservado
            if qtd > disp:
                raise RegraNegocioError(f"Estoque insuficiente ({variacao.cor}): disp {disp}, pedido {qtd}")
        variacao.estoque_reservado += qtd
        self._registrar(db, variacao, "reserva", qtd, "pedido", usuario_id, pedido_id)

    def baixar(self, db, variacao, qtd, usuario_id, pedido_id):
        variacao.estoque_fisico -= qtd
        variacao.estoque_reservado -= qtd
        self._registrar(db, variacao, "saida", qtd, "pedido", usuario_id, pedido_id)

    def estornar(self, db, variacao, qtd, usuario_id, pedido_id):
        variacao.estoque_reservado -= qtd
        self._registrar(db, variacao, "estorno", qtd, "pedido", usuario_id, pedido_id)

    def ajustar(self, db, variacao, novo_saldo, usuario_id, motivo):
        if not motivo:
            raise RegraNegocioError("Ajuste exige motivo")
        delta = novo_saldo - variacao.estoque_fisico
        variacao.estoque_modo = "EXATO"
        variacao.estoque_fisico = novo_saldo
        self._registrar(db, variacao, "ajuste", abs(delta), "inventario", usuario_id, motivo=motivo)
```

> **Atomicidade:** a rota que confirma/fatura um pedido abre uma transação e só faz commit no fim. Se um item
> falhar a reserva, **nada** é aplicado.

---

## 2. Estoque exato vs aproximado (regra do Áudio 1)

- Variação nasce, na migração, geralmente como **`APROXIMADO`** (rótulo `MUITO`/`POUCO`/`TEM`/`ACABOU`).
- Vira **`EXATO`** quando: recebe **entrada**, sofre **ajuste**, ou é **contada no inventário**.
- Em `EXATO`, vendas **abatem automaticamente** (reserva → baixa).
- Em `APROXIMADO`, o sistema **não bloqueia** venda por saldo (não há número confiável), mas **avisa** que o
  estoque não está exato; ideal levar essa variação a um inventário.
- Na listagem, exibir:
  - `EXATO`: número + (se ≤ mínimo) selo de alerta.
  - `APROXIMADO`: selo 🟢 muito / 🟡 pouco / ⚪ tem / 🔴 acabou.

---

## 3. Inventário (contagem)

Fluxo (perfil **Funcionário** conta, **Admin** aprova):
1. Funcionário abre inventário (geral, por categoria, ou por produto) → gera `inventario_itens` com `qtd_sistema`.
2. Conta fisicamente e preenche `qtd_contada`.
3. Admin **aplica**: para cada item, `ajustar(novo_saldo=qtd_contada, motivo="inventário #N")`, marcando a
   variação como `EXATO`. Movimentações `ajuste` são geradas em lote.
4. Inventário fica `aplicado` e auditado.

---

## 4. Entradas de mercadoria

- Manual (Admin/Funcionário): escolhe produto+cor, informa quantidade (em unidades **ou caixas** →
  converter por `unidades_por_caixa`), service faz `entrada`.
- Por importação (recorrente): motor do ETL (doc `04`) gera entradas.
- Mostrar conversão na UI: "3 caixas × 75 = 225 unidades".

---

## 5. Alertas e reposição

- Variação `EXATO` com `estoque_fisico ≤ estoque_minimo` → entra no **relatório de reposição** e nos alertas do dashboard.
- Aproximados com rótulo `POUCO`/`ACABOU` também aparecem como atenção (sem número).

---

## 6. Consulta de localização (tablet do estoque) — destaque do cliente

> Áudio 3: *"Quero deixar um tablet lá embaixo pros funcionários... são 10 andares e eles se perdem."*

Endpoint dedicado, **simples e visual** (UI no doc `07`):
- Busca por **código, código alternativo, descrição ou cor** (HTMX, instantânea via pg_trgm).
- Resultado mostra, **grande**: descrição, cor(es) e **LOCALIZAÇÃO** (andar / lado / sala), além de uma
  indicação de saldo (número ou selo).
- Repository com `busca_rapida` cobrindo `produtos.codigo`, `produto_codigos_alt.codigo_alt`,
  `produtos.descricao` (trigram) e `produto_variacoes.cor`.

```python
def busca_localizacao(self, db, termo, limit=15):
    stmt = (select(Produto)
        .where(
            Produto.codigo.ilike(f"{termo}%")
            | Produto.descricao.op("%")(termo)
            | Produto.localizacao.op("%")(termo)
            | Produto.id.in_(select(ProdutoCodigoAlt.produto_id).where(ProdutoCodigoAlt.codigo_alt.ilike(f"{termo}%")))
        ).limit(limit))
    return list(db.scalars(stmt))
```

---

## 7. Endpoints do módulo (resumo)

| Rota | Perfil | Função |
|---|---|---|
| `GET /estoque` | todos (níveis) | lista/paginada com busca |
| `GET /estoque/busca?q=` | todos | fragmento HTMX da tabela |
| `GET /estoque/localizacao` | todos (tablet) | consulta de localização |
| `POST /estoque/entrada` | admin, funcionario | dar entrada |
| `POST /estoque/ajuste` | admin | ajuste com motivo |
| `GET /inventario` / `POST /inventario` | funcionario (abre), admin (aplica) | contagem |
| `GET /estoque/{variacao}/movimentacoes` | admin, financeiro | histórico |

---

## 8. Definition of Done do marco 05

- [ ] `EstoqueService` com entrada/reserva/baixa/estorno/ajuste, sempre gerando movimentação.
- [ ] Saldo nunca negativo em modo `EXATO`; reserva respeita disponível.
- [ ] Transições `APROXIMADO → EXATO` nos pontos certos (entrada/ajuste/inventário).
- [ ] Inventário completo (abrir, contar, aplicar com aprovação) e auditado.
- [ ] Conversão caixa↔unidade na entrada.
- [ ] Consulta de localização rápida e abrangente (código, cód. alt, descrição, cor).
- [ ] Alertas de mínimo + relatório de reposição.
- [ ] Testes: reserva/baixa/estorno, ajuste com motivo, idempotência de saldo, busca de localização.
