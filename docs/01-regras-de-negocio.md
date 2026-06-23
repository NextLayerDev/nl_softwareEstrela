# 01 — Regras de Negócio · Estrela Gestão

> Fonte: 3 áudios do cliente + análise da planilha real `CONTROLE.xlsx`.
> **Todos do time leem este documento antes de codar.** Aqui está como a Estrela realmente opera —
> é o que o sistema precisa respeitar para ser adotado de verdade.

---

## 1. Como o cliente opera hoje (planilha `CONTROLE.xlsx`)

A planilha tem **14 abas** divididas em três grupos:

**A) Catálogo de estoque** (por categoria): `CANETAS PLÁSTICAS`, `CANETAS METÁLICAS`, `COPOS E GARRAFAS`,
`BLOCOS E CADERNOS`, `CHAVEIROS`, `ELETRÔNICOS`, `OUTROS`. ~**88 produtos** distribuídos.

**B) Pedidos por cliente**: `LUCIANO`, `CLAUDEMIR`, `LEONARDO` (abas com histórico de pedidos), `EM ABERTO`
(pedidos/pagamentos pendentes), `NOTAS` (notas fiscais de fornecedor / entradas).

**C) Apoio**: `COLETA` (dados da empresa para coleta/envio), `BOVESPA` (anotações soltas).

### Estrutura de cada produto no catálogo (blocos, não linhas)

Cada produto é um **bloco** com cabeçalho repetido `CÓDIGO | DESCRIÇÃO | COD ALTERNATIVO | CORES | QUANTIDADE`:

```
CÓDIGO   DESCRIÇÃO              COD ALT   CORES      QUANTIDADE
K708     CANETA ESFEROGRÁFICA             BRANCO     156
         COM SUPORTE                      PRETO      212
1.2                                       LARANJA    14          ← "1.2" = preço (R$ 1,20)
                                          VERDE      42
                                          AZUL       180
                                          ...        ...         2.000 EM CADA CAIXA (nota)
```

Observações que viram **requisito**:
- O **código** aparece uma vez; a **descrição** ocupa várias linhas (nome + detalhe + medida + andar).
- Um número solto na coluna CÓDIGO abaixo do código (ex.: `1.2`, `0.9`, `16`, `15`) é **preço unitário**.
  Quando há dois (ex.: `1.8` e `1.6`), são os **dois preços** (pouca vs. muita quantidade) — ver §3.
- As **cores são empilhadas**, cada uma com sua **quantidade própria**.
- A coluna QUANTIDADE mistura **número exato** (`156`), **aproximação** (`tem`, `TEM`, `TEM MUITO`, `TEM POUCO`)
  e **vazio/zerado** (`-`, `ACABOU`). Ver §2.
- Notas livres aparecem em colunas à direita: `2.000 EM CADA CAIXA`, `adicionado ao catálogo`, `2º andar`,
  `3º e 4º andar à direita`, `chega em abril`, `PEDIDOS MUITO GRANDES`.

### Estrutura de cada pedido (abas de cliente)

```
2024-11-01   claudemir   241464              ← data, cliente, nº do pedido
CODIGO   DESCRICAO            QUANT.  V.UNIT  SUB.TOTAL
FA12     KIT CHURRASCO        40      19       760
JSC1140  STANLEY AZUL MARINHO 225     16       3600   ← mesmo SKU, cor no nome
JSC1140  STANLEY LARANJA      280     13.9     3892   ← preço varia por cor/quantidade
...                            TOTAL            9852
```

Observações que viram **requisito**:
- O pedido referencia **SKU + cor** (a cor está embutida na descrição).
- **O mesmo SKU sai com preços unitários diferentes** no mesmo pedido (Stanley a 13,9 / 15,5 / 16),
  conforme cor e quantidade negociada. O sistema **deve permitir preço unitário editável por item**.
- Cada pedido tem **número** (ex.: `241464`), data e cliente.

---

## 2. Estoque: contagem aproximada → exata (Áudio 1)

> *"A gente acaba sabendo de cabeça... coloco 'tem muito', 'tem pouco'... só de olhar as pilhas já sei.
> Mas aí a gente alinha e coloca a quantidade exata, e conforme vai saindo as vendas a gente vai abatendo."*

**Requisitos:**
- O produto pode ter estoque em **modo aproximado** (`tem muito`, `tem pouco`, `tem`, `acabou`) **ou exato** (número).
- O sistema deve suportar **os dois estados** por variação:
  - Campo `estoque_modo`: `EXATO` | `APROXIMADO`.
  - Quando `APROXIMADO`, guardar um rótulo (`MUITO`, `POUCO`, `TEM`, `ACABOU`) — sem número confiável.
  - Quando `EXATO`, guardar o número e **abater automaticamente a cada venda**.
- A **migração inicial** entra com o que a planilha tem (muitos virão como aproximado). A contagem exata é
  feita aos poucos via **inventário** (doc `05`): ao contar, a variação vira `EXATO` e passa a abater.
- Na UI, mostrar aproximados com selo (ex.: 🟢 "tem muito" / 🟡 "tem pouco" / 🔴 "acabou") e exatos com o número.
- **Importante:** "muito/pouco" **não é** estoque mínimo. É o que o operador enxerga antes de contar. Confirmar
  com o cliente se querem converter isso em faixas (ex.: pouco = abaixo de X) — Q aberta no §6.

---

## 3. Preços: atacado com 2 (às vezes 3) faixas por quantidade (Áudio 2)

> *"A gente só vende atacado... um preço pra uma caixa só, e outro pra muita quantidade. Coloco 'pouca
> quantidade' ou 'muita quantidade'. Oficialmente dois preços."*

**Requisitos:**
- Cada produto tem **até 3 níveis de preço de venda** por faixa de quantidade:
  - `preco_pouca_qtd` (ex.: 1 caixa) e `preco_muita_qtd` (volume) — **obrigatórios os dois**.
  - `preco_promocional` opcional (o "às vezes três").
- O **limite que separa as faixas** (a partir de quantos cai no preço de "muita quantidade") precisa ser
  **configurável por produto** (campo `qtd_corte_atacado`) — confirmar valor-padrão com o cliente.
- No **pedido**, ao informar a quantidade do item, o sistema **sugere** o preço da faixa correspondente,
  mas o **vendedor pode editar o preço unitário** (a planilha mostra negociação fina por cor).
- Vender é **sempre atacado**; não há varejo unitário.
- A **margem** é calculada sobre `preco_custo` (vem das notas de fornecedor — aba `NOTAS`); custo **não aparece**
  para Vendedor/Funcionário.

---

## 4. Cadastro de produtos, SKUs e localização (Áudio 3)

> *"Cada mês chegam produtos novos... preciso alimentar novos SKUs. Código, descrição, código alternativo
> (a caixa vem com outro código quando troca de fábrica — copo Stanley, canetas), as cores, quantas unidades
> vêm em cada caixa (75 copos por caixa → vendo 3 caixas), e ONDE FICA. Quero um tablet lá embaixo pros
> funcionários: são 10 andares de estoque e eles se perdem — 4º andar, lado direito, sala 2."*

**Campos obrigatórios do produto (confirmados pelo cliente):**
| Campo | Origem na planilha | Observação |
|---|---|---|
| **Código (SKU)** | coluna CÓDIGO | único, interno |
| **Código alternativo** | coluna COD ALTERNATIVO | da caixa/fábrica secundária; pode existir ou não |
| **Descrição** | coluna DESCRIÇÃO (multi-linha) | nome + detalhes + medida |
| **Categoria** | a aba de origem | Canetas plásticas, Copos, etc. |
| **Cores** | coluna CORES (empilhada) | cada cor é uma **variação** com saldo próprio |
| **Unidades por caixa** | nota "2.000 EM CADA CAIXA", "75 copos" | `unidades_por_caixa` — permite vender por caixa |
| **Localização física** | hoje na descrição ("2º andar", "4º andar dir., sala 2") | **campo próprio** `localizacao` |
| **Preço(s)** | número solto na col. CÓDIGO | pouca/muita qtd (ver §3) |
| **Quantidade** | coluna QUANTIDADE | por variação; exato ou aproximado (ver §2) |

**Requisitos-chave:**
- **Cadastro de novos SKUs é mensal e frequente** → tela de produto rápida e tela de importação recorrente.
- **Localização física é destaque**: precisa ficar **muito visível e pesquisável**. Haverá um **tablet no estoque**
  para os funcionários consultarem "onde fica o produto X" → tela de consulta de localização simples, grande,
  busca por código/descrição/cor, mostrando **andar / lado / sala**. (Detalhe de UI no doc `07`.)
- **Venda por caixa**: como vêm N unidades por caixa, o pedido pode ser em caixas → o sistema converte para
  unidades no estoque (`unidades_por_caixa`). Mostrar os dois (ex.: "3 caixas = 225 un").
- **Código alternativo** deve ser **pesquisável** (o funcionário pode procurar pelo código da caixa).

---

## 5. Resumo das regras que viram comportamento do sistema

1. Produto tem **variações por cor**, cada uma com saldo próprio (exato ou aproximado).
2. Estoque começa aproximado e vira exato via inventário; exato **abate automático** nas vendas.
3. **Dois preços de atacado** por produto (pouca/muita qtd), com corte configurável; **3º preço** opcional;
   **preço unitário editável no item** do pedido.
4. Produto guarda **código alternativo**, **unidades por caixa** e **localização física** (campo próprio).
5. **Consulta de localização** para tablet no estoque — busca rápida, visual, mostra andar/lado/sala.
6. Pedido referencia **SKU + cor**, permite **venda por caixa**, mesmo SKU com preços diferentes por linha.
7. Cadastro mensal de novos SKUs → cadastro rápido + importação recorrente de planilha.
8. Margem sobre custo (das notas de fornecedor); **custo oculto** para Vendedor/Funcionário.

---

## 6. Perguntas abertas para o cliente (confirmar antes de fechar a modelagem)

| # | Pergunta | Impacta |
|---|---|---|
| Q1 | "Tem muito / tem pouco" deve virar faixa numérica (ex.: pouco = abaixo de X) ou continuar só rótulo visual? | Estoque aproximado |
| Q2 | Qual a quantidade de corte padrão entre "pouca" e "muita" quantidade? É por produto ou geral? | Preços |
| Q3 | Vendas saem em **caixa**, em **unidade**, ou ambos? Pode vender caixa fracionada? | Pedido/estoque |
| Q4 | A cor é sempre obrigatória no item do pedido? Há produto sem cor? | Variações |
| Q5 | Reserva o estoque ao **confirmar** o pedido ou só **abate ao faturar**? | Estoque |
| Q6 | Emite **Nota Fiscal** pelo sistema ou continua emitindo fora? (módulo opcional, R$ 400/mês) | Escopo |
| Q7 | Localização: o padrão é **andar + lado + sala**? Existe prateleira/corredor? | Campo localização |
| Q8 | Os pedidos em aberto (aba `EM ABERTO`) viram **contas a receber** no sistema? | Financeiro |
| Q9 | Quantos clientes ativos e quantos terão acesso ao sistema (perfil Vendedor)? | RBAC/licenças |
| Q10 | Confirmar a lista de **categorias** definitiva (as 7 abas de hoje). | Cadastro |

> Estas Q substituem/expandem as Q1–Q16 do planejamento anterior, agora ancoradas nos áudios e na planilha real.
