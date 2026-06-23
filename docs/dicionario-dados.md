# Dicionário de Dados — `CONTROLE.xlsx` (Estrela)

> Mapa coluna→campo para o ETL (doc `04`). Baseado na análise da planilha real entregue pelo cliente.
> Refinar com o cliente nas perguntas abertas (doc `01` §6).

---

## Abas e seus papéis

| Aba | Papel | Uso no ETL |
|---|---|---|
| `CANETAS PLÁSTICAS` | catálogo (categoria) | importar produtos |
| `CANETAS METÁLICAS` | catálogo | importar produtos |
| `COPOS E GARRAFAS` | catálogo | importar produtos |
| `BLOCOS E CADERNOS` | catálogo | importar produtos |
| `CHAVEIROS` | catálogo | importar produtos |
| `ELETRÔNICOS` | catálogo | importar produtos |
| `OUTROS` | catálogo | importar produtos |
| `LUCIANO`, `CLAUDEMIR`, `LEONARDO` | pedidos por cliente | histórico (opcional) |
| `EM ABERTO` | pendências de pagamento | contas a receber (futuro) |
| `NOTAS` | notas de fornecedor / entradas | custo + fornecedores |
| `COLETA` | dados da empresa | referência (não importar) |
| `BOVESPA` | anotações soltas | ignorar |

---

## Catálogo (abas de produto) — layout por blocos

Cabeçalho repetido a cada produto: `CÓDIGO | DESCRIÇÃO | COD ALTERNATIVO | CORES | QUANTIDADE` (+ colunas livres à direita).

| Coluna planilha | Campo canônico | Regra de parsing |
|---|---|---|
| `CÓDIGO` (1ª linha do bloco) | `produtos.codigo` | string; identifica início do produto |
| `CÓDIGO` (número solto abaixo) | `produtos.preco_pouca_qtd` / `preco_muita_qtd` | 1 número = 1 preço; 2 números = pouca/muita qtd |
| `DESCRIÇÃO` (multi-linha) | `produtos.descricao` | concatenar linhas; extrair localização e "X em cada caixa" |
| `COD ALTERNATIVO` | `produto_codigos_alt.codigo_alt` | quando presente (ex.: `K-820`→`K-803`) |
| `CORES` (empilhada) | `produto_variacoes.cor` | uma variação por cor |
| `QUANTIDADE` | `produto_variacoes.estoque_*` | número→EXATO+saldo; "TEM MUITO/POUCO/TEM"→APROXIMADO+rótulo; "-"/"ACABOU"→ACABOU; "375 UNID"→EXATO+375 |
| col. livre "2.000 EM CADA CAIXA" | `produtos.unidades_por_caixa` | extrair dígitos |
| col. livre "andar/lado/sala" (ou na descrição) | `produtos.localizacao` | extrair via regex de "andar" |
| col. livre "adicionado ao catálogo" / "chega em abril" / "PEDIDOS MUITO GRANDES" | `produtos` (observação) | texto livre |
| (aba de origem) | `produtos.categoria_id` | nome da aba → categoria |

### Rótulos de quantidade aproximada (normalização)
| Texto na planilha | `estoque_modo` | `rotulo_aprox` |
|---|---|---|
| número (`156`, `42`) | EXATO | — (saldo = número) |
| `375 UNID` | EXATO | — (saldo = 375) |
| `TEM MUITO` | APROXIMADO | MUITO |
| `TEM POUCO` | APROXIMADO | POUCO |
| `TEM` / `tem` / `TEM` | APROXIMADO | TEM |
| `-` / `ACABOU` / vazio | APROXIMADO | ACABOU |

---

## Pedidos (abas de cliente) — opcional

Bloco: linha `data | cliente | nº do pedido`, depois `CODIGO | DESCRICAO | QUANT. | V.UNIT | SUB.TOTAL` até `TOTAL`.

| Coluna | Campo | Observação |
|---|---|---|
| data / cliente / nº | `pedidos.criado_em` / `cliente_id` / `numero` | |
| `CODIGO` | resolver para `produto` (e cor pela descrição) | a cor está no nome (ex.: "STANLEY AZUL MARINHO") |
| `QUANT.` | `pedido_itens.qtd` | |
| `V.UNIT` | `pedido_itens.preco_unit` | mesmo SKU com preços diferentes por linha |
| `SUB.TOTAL` | `pedido_itens.subtotal` | conferência |

---

## Notas de fornecedor (`NOTAS`) — custo

Bloco com fornecedor + `NF nº`, depois linhas `CODIGO | DESCRICAO | PREÇO | QUANTIDADE | RESTANTE | ...`.

| Coluna | Campo |
|---|---|
| fornecedor | `fornecedores.nome` |
| `PREÇO` | `produtos.preco_custo` (do item) |
| `QUANTIDADE` / `RESTANTE` | entrada de estoque / saldo de nota |

---

## Pontos de atenção para o parser

- Abas têm **número de colunas diferente** (7 a 13) — não assumir layout fixo; localizar colunas pelo header.
- Há **erros de digitação** nas notas livres ("adiocionado ao catálogo") — não depender de match exato.
- Alguns blocos têm **cor sem quantidade** ou **quantidade sem cor** → mandar para o relatório de inconsistências.
- Preço pode estar **ausente** em alguns produtos → marcar para revisão, não inventar.
- A planilha tem ~**88 produtos** nas abas de catálogo; conferir contagem após import.
