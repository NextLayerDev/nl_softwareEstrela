# 03 — Modelo de Dados e Migrations · Estrela Gestão

> Responsável: Backend (DB). Depende dos docs `01` (regras) e `02` (fundação).
> Este modelo já incorpora os áudios e a planilha real: variações por cor, estoque exato/aproximado,
> dois preços de atacado, código alternativo, unidades por caixa e localização física.

---

## 1. Visão geral das entidades

```
usuarios
categorias
fornecedores
produtos ─┬─< produto_variacoes (cor + saldo próprio)
          ├─< produto_codigos_alt (código alternativo / da caixa)
          └─< produto_imagens (Fase 2)
clientes ─< pedidos ─< pedido_itens
movimentacoes_estoque   (append-only; liga a produto_variacao)
inventarios ─< inventario_itens
contas_receber
auditoria
sync_outbox (Fase 2)
```

A grande diferença para um catálogo comum: **o saldo de estoque vive na VARIAÇÃO (cor)**, não no produto.
O produto guarda os dados comerciais (preços, caixa, localização, códigos); a variação guarda cor e estoque.

---

## 2. Tabelas (campos e regras)

### `produtos`
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| codigo | str único, index | SKU interno (ex.: `K708`, `JSC1140`) |
| descricao | text | nome + detalhes (multi-linha vira texto) |
| categoria_id | FK categorias | |
| **unidades_por_caixa** | int | ex.: 2000, 75 — permite venda por caixa |
| **localizacao** | str, index | "4º andar, lado direito, sala 2" |
| **preco_pouca_qtd** | Numeric(12,2) | 1º preço (caixa avulsa) |
| **preco_muita_qtd** | Numeric(12,2) | 2º preço (volume) |
| **preco_promocional** | Numeric(12,2) null | 3º preço opcional |
| **qtd_corte_atacado** | int null | a partir de quanto aplica `preco_muita_qtd` |
| preco_custo | Numeric(12,2) | das notas de fornecedor; oculto p/ vendedor |
| ativo | bool | |
| publicar_catalogo | bool | Fase 2 |

### `produto_variacoes`  ← saldo de estoque mora aqui
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| produto_id | FK produtos | |
| cor | str | "BRANCO", "AZUL MARINHO"… (pode ser "ÚNICA" se sem cor) |
| **estoque_modo** | enum `EXATO`/`APROXIMADO` | ver doc 01 §2 |
| **estoque_fisico** | int | usado quando `EXATO` |
| **estoque_reservado** | int | comprometido por pedidos |
| **rotulo_aprox** | enum null `MUITO`/`POUCO`/`TEM`/`ACABOU` | usado quando `APROXIMADO` |
| estoque_minimo | int default 0 | alerta de reposição (quando exato) |
| ativo | bool | |
| **imagem_filename** | str null | foto da cor (upload em `/produtos`); servida em `/uploads/variacoes/`. A property `imagem_url` monta a URL. Exibida no estoque, na Localização (tablet), no pedido e na separação para o funcionário reconhecer o modelo. |

> Saldo disponível (modo exato) = `estoque_fisico - estoque_reservado`.
> Toda mudança de `estoque_fisico`/`estoque_reservado` gera `movimentacoes_estoque` (append-only).

### `produto_codigos_alt`
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| produto_id | FK | |
| codigo_alt | str, index | código da caixa/fábrica secundária (ex.: `K-803`) |
| fornecedor_id | FK fornecedores null | de qual fábrica |

> Tabela própria desde o dia 1 (um produto pode ganhar mais códigos depois). **Pesquisável** junto com o SKU.

### `categorias`
`id, nome` — semear com as 7 da planilha: Canetas Plásticas, Canetas Metálicas, Copos e Garrafas,
Blocos e Cadernos, Chaveiros, Eletrônicos, Outros. (Confirmar lista — Q10 do doc 01.)

### `fornecedores`
`id, nome, cnpj, contato` — origem da aba `NOTAS`.

### `clientes`
`id, nome, cnpj_cpf, telefone, endereco, condicao_pagto_padrao, limite_credito null, ativo`.
Origem: abas de cliente (`LUCIANO`, `CLAUDEMIR`, `LEONARDO`…).

### `pedidos`
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| numero | int único (sequence) | sem buracos; ex.: 241464 |
| cliente_id | FK | |
| vendedor_id | FK usuarios | |
| status | enum | `rascunho/confirmado/separacao/faturado/entregue/cancelado` |
| desconto_total | Numeric(12,2) | |
| total | Numeric(12,2) | |
| observacao | text | |
| origem | enum | `local/catalogo/whatsapp` (Fase 2) |
| criado_em | timestamp | |

### `pedido_itens`
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| pedido_id | FK | |
| produto_variacao_id | FK | SKU + cor |
| **qtd** | int | em unidades (converter de caixa na entrada) |
| **qtd_caixas** | int null | se vendido por caixa, registrar |
| **preco_unit** | Numeric(12,2) | **editável** — sugerido pela faixa, ajustável |
| desconto | Numeric(12,2) | |
| subtotal | Numeric(12,2) | |

> Mesmo SKU pode aparecer em vários itens (cores diferentes, preços diferentes) — é esperado.

### `movimentacoes_estoque`  (append-only — nunca UPDATE/DELETE)
| Campo | Tipo | Observação |
|---|---|---|
| id | PK | |
| produto_variacao_id | FK | |
| tipo | enum | `entrada/saida/ajuste/reserva/estorno` |
| qtd | int | |
| origem | enum | `pedido/inventario/importacao/manual` |
| ref_id | int null | id do pedido/inventário relacionado |
| usuario_id | FK | |
| saldo_apos | int | saldo da variação após a operação |
| motivo | str null | obrigatório em ajuste |
| criado_em | timestamp | |

### `inventarios` / `inventario_itens`
- `inventarios`: `id, status(aberto/aplicado), criado_em, criado_por`.
- `inventario_itens`: `id, inventario_id, produto_variacao_id, qtd_sistema, qtd_contada`.
- Ao **aplicar** (aprovação Admin): gera `ajuste` em movimentações e marca a variação como `EXATO`.

### `contas_receber`
`id, pedido_id, parcela, valor, vencimento, status(pendente/pago/atrasado), forma_pagamento,
baixado_em null, baixado_por null`. Origem futura: aba `EM ABERTO`.

### `auditoria`
`id, usuario_id, entidade, entidade_id, acao, antes(JSONB), depois(JSONB), criado_em`.
Cobrir produtos, pedidos, estoque e financeiro.

### `sync_outbox` (Fase 2)
`id, entidade, entidade_id, payload(JSONB), status, tentativas`.

---

## 3. Índices e extensões (críticos para busca)

- `CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- **GIN trigram** em `produtos.descricao` e em `produtos.localizacao` (busca do tablet).
- Índice em `produtos.codigo`, `produto_codigos_alt.codigo_alt`, `produto_variacoes.cor`.
- Índice composto em `pedidos (vendedor_id, criado_em)` e `(cliente_id, criado_em)`.
- Índice em `movimentacoes_estoque (produto_variacao_id, criado_em)`.

```sql
CREATE INDEX ix_produtos_descricao_trgm ON produtos USING gin (descricao gin_trgm_ops);
CREATE INDEX ix_produtos_localizacao_trgm ON produtos USING gin (localizacao gin_trgm_ops);
```

---

## 4. Numeração de pedidos sem buracos

Usar **sequence** dedicada no Postgres (`CREATE SEQUENCE pedido_numero_seq START 1;`) lida no service ao
confirmar/criar. Não derivar de `count(*)` (gera buraco em concorrência/cancelamento).

---

## 5. Migrations (Alembic)

- 1 migration por entrega coesa; nome descritivo (`adicionar_variacoes_e_precos`).
- A migration inicial cria todas as tabelas-núcleo + extensão `pg_trgm` + índices GIN (em `op.execute`).
- **Nunca** editar migration já aplicada em produção; criar nova.
- Toda migration tem `upgrade()` e `downgrade()` válidos.

---

## 6. Definition of Done do marco 03

- [ ] Todos os models acima criados no padrão SQLAlchemy 2.0 (`Mapped`/`mapped_column`).
- [ ] Enums definidos (estoque_modo, rotulo_aprox, tipo_mov, status_pedido, status_conta).
- [ ] Migration inicial aplicável (`alembic upgrade head` limpo) com extensão + índices GIN.
- [ ] Sequence de número de pedido criada.
- [ ] `scripts/seed.py` popula: 1 usuário por perfil, as 7 categorias, e ~5 produtos com variações,
      preços e localização (espelhando a planilha) para os outros marcos trabalharem.
- [ ] Relacionamentos navegáveis (produto → variações, pedido → itens) testados num teste rápido.
