# 04 — ETL / Importação das Planilhas · Estrela Gestão

> Responsável: Backend (Dados). Depende dos docs `01` (regras) e `03` (modelo).
> **É o marco mais sensível do projeto.** A planilha real (`CONTROLE.xlsx`) é organizada por blocos, com
> dados mistos (números, "tem muito", notas em colunas soltas). Este parsing precisa ser cuidadoso e idempotente.

---

## 1. Realidade da planilha (o que o parser enfrenta)

A `CONTROLE.xlsx` tem **14 abas** (ver doc `01` §1). Para a **migração de produtos**, importam as 7 abas de
catálogo. Cada produto é um **bloco** separado por um cabeçalho repetido:

```
CÓDIGO   DESCRIÇÃO              COD ALTERNATIVO   CORES      QUANTIDADE
K708     CANETA ESFEROGRÁFICA                     BRANCO     156
         COM SUPORTE                              PRETO      212
1.2                                               LARANJA    14
                                                  VERDE      42      [col. extra: "2.000 EM CADA CAIXA"]
```

Padrões a tratar (todos vistos na planilha real):
- **Código na 1ª linha do bloco**; descrição em **várias linhas** (concatenar).
- **Número solto** na coluna CÓDIGO abaixo do código = **preço** (`1.2`, `0.9`, `16`). Dois números = dois preços
  (pouca/muita qtd). Heurística: valor com decimal pequeno → preço.
- **Cores empilhadas**, cada uma com **quantidade própria**.
- **Quantidade mista**: número (`156`), texto (`tem`, `TEM`, `TEM MUITO`, `TEM POUCO`), vazio, `-`, `ACABOU`.
- **Localização** aparece dentro da descrição (`2º andar`, `3º e 4º andar à direita`) → extrair para campo próprio.
- **Notas** em colunas à direita (`2.000 EM CADA CAIXA`, `adicionado ao catálogo`, `chega em abril`,
  `PEDIDOS MUITO GRANDES`) → extrair `unidades_por_caixa` quando possível; resto vira observação.
- **Código alternativo** quando presente na coluna COD ALTERNATIVO (ex.: `K-820` → `K-803`).
- Abas têm **dimensões e nº de colunas diferentes** (de 7 a 13 colunas) — parser não pode assumir layout fixo.

> Para pedidos (abas de cliente), o ETL é **opcional na Fase 1** (histórico). Estrutura: linha
> `data | cliente | nº`, depois `CODIGO | DESCRICAO | QUANT | V.UNIT | SUB.TOTAL` até `TOTAL`. Importar só se
> o cliente quiser histórico migrado — senão começa zerado.

---

## 2. Pipeline em 6 etapas

**Etapa 1 — Coleta.** A planilha já está em mãos (`CONTROLE.xlsx`). Pedir também notas de fornecedor para custo.

**Etapa 2 — Dicionário de dados** (`docs/dicionario-dados.md`): mapear, por aba, qual coluna é o quê. Já adiantado
neste documento; refinar com o cliente nas Q abertas (doc `01` §6).

**Etapa 3 — Staging.** Ler o bruto para tabelas `staging_produtos` / `staging_variacoes` **sem transformar**.
Guardar aba de origem, nº da linha e valor literal — rastreabilidade total.

**Etapa 4 — Parsing + validação.** O parser de blocos (§3) gera registros canônicos. Validar:
- preço presente? (senão, marcar para revisão)
- quantidade interpretável? (número → exato; texto conhecido → aproximado; desconhecido → revisão)
- código duplicado? código alternativo conflitante?
- cor sem quantidade / quantidade sem cor.
Saída: **relatório de inconsistências em XLSX** (`openpyxl`) com aba/linha/problema → cliente decide. **Não decidir por ele.**

**Etapa 5 — Carga definitiva.** Importador **idempotente** com `--dry-run`:
- `produtos` (com preços, localização extraída, unidades por caixa);
- `produto_variacoes` (cor + estoque: número→`EXATO`+saldo; texto→`APROXIMADO`+rótulo);
- `produto_codigos_alt`;
- movimentação tipo `importacao` como **saldo inicial** das variações exatas (rastreável).
Rodar 2x **não duplica** (chave: `codigo` do produto; `produto_id+cor` da variação).

**Etapa 6 — Importador recorrente.** A tela `/importacao` (doc `07`) reaproveita o mesmo motor para as entradas
mensais de novos SKUs, com preview e relatório de erros — o cliente não depende mais do time para importar.

---

## 3. Esboço do parser de blocos (referência)

```python
# app/importer/parser.py
from dataclasses import dataclass, field
import openpyxl, re

ROTULOS = {"TEM MUITO": "MUITO", "TEM POUCO": "POUCO", "TEM": "TEM",
           "ACABOU": "ACABOU", "-": "ACABOU"}

@dataclass
class VariacaoIn:
    cor: str
    qtd_raw: str
    estoque_modo: str = "APROXIMADO"
    estoque_fisico: int = 0
    rotulo: str | None = None

@dataclass
class ProdutoIn:
    codigo: str
    descricao: str
    categoria: str
    cod_alt: str | None = None
    precos: list[float] = field(default_factory=list)   # [pouca, muita]
    unidades_por_caixa: int | None = None
    localizacao: str | None = None
    obs: str | None = None
    variacoes: list[VariacaoIn] = field(default_factory=list)

def interpreta_qtd(v) -> VariacaoIn:
    s = str(v).strip().upper() if v is not None else ""
    if s.isdigit():
        return VariacaoIn(cor="", qtd_raw=s, estoque_modo="EXATO", estoque_fisico=int(s))
    if s in ROTULOS:
        return VariacaoIn(cor="", qtd_raw=s, estoque_modo="APROXIMADO", rotulo=ROTULOS[s])
    # número com unidade ("375 UNID"): extrair dígitos
    m = re.search(r"\d+", s)
    if m:
        return VariacaoIn(cor="", qtd_raw=s, estoque_modo="EXATO", estoque_fisico=int(m.group()))
    return VariacaoIn(cor="", qtd_raw=s, estoque_modo="APROXIMADO", rotulo="TEM" if s else None)

def parse_aba(ws, categoria: str) -> list[ProdutoIn]:
    produtos, atual = [], None
    for row in ws.iter_rows(values_only=True):
        c = [(x if x is not None else "") for x in row]
        col_codigo = str(c[0]).strip() if c and c[0] else ""
        # novo bloco começa no header
        if col_codigo == "CÓDIGO":
            atual = None
            continue
        # início de produto: tem código "de verdade" (não só número de preço)
        if col_codigo and not _eh_preco(col_codigo):
            atual = ProdutoIn(codigo=col_codigo, descricao=str(c[1] or "").strip(),
                              categoria=categoria, cod_alt=(str(c[2]).strip() or None) if len(c) > 2 else None)
            produtos.append(atual)
        elif atual and _eh_preco(col_codigo):
            atual.precos.append(float(col_codigo.replace(",", ".")))
        # descrição extra
        if atual and c[1] and col_codigo == "" :
            atual.descricao = (atual.descricao + " " + str(c[1]).strip()).strip()
            _extrai_localizacao_e_caixa(atual, str(c[1]))
        # cor + quantidade
        cor = str(c[3]).strip() if len(c) > 3 and c[3] else ""
        if atual and cor and cor != "CORES":
            vi = interpreta_qtd(c[4] if len(c) > 4 else None)
            vi.cor = cor
            atual.variacoes.append(vi)
    return produtos

def _eh_preco(s: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}([.,]\d{1,2})?", s)) and "." in s or "," in s or len(s) <= 2 and s.isdigit()
```

> Este esboço é ponto de partida — ajustar com casos reais. **Cobrir com testes** usando trechos da planilha
> (ver §5). O parser é a parte que mais quebra; testar exaustivamente.

---

## 4. CLI

```bash
uv run python scripts/import_planilhas.py --file CONTROLE.xlsx --dry-run   # valida, gera relatório, não grava
uv run python scripts/import_planilhas.py --file CONTROLE.xlsx             # grava
uv run python scripts/import_planilhas.py --file CONTROLE.xlsx --so-categoria "COPOS E GARRAFAS"
```

Saídas: `relatorio_inconsistencias.xlsx` + log resumo (quantos produtos, variações, exatos vs aproximados, erros).

---

## 5. Testes obrigatórios

- Bloco com **dois preços** → produto com `preco_pouca_qtd` e `preco_muita_qtd` corretos.
- Quantidade `156` → `EXATO`/156; `TEM MUITO` → `APROXIMADO`/`MUITO`; `-`/`ACABOU` → `ACABOU`; `375 UNID` → `EXATO`/375.
- Descrição com `"4º andar à direita"` → `localizacao` extraída, descrição preservada.
- `"2.000 EM CADA CAIXA"` → `unidades_por_caixa = 2000`.
- Código alternativo `K-820 → K-803` → registro em `produto_codigos_alt`.
- **Idempotência**: importar 2x não duplica produto nem variação.
- Variações exatas geram movimentação `importacao` com `saldo_apos` correto.

---

## 6. Definition of Done do marco 04

- [ ] Parser de blocos lê as 7 abas de catálogo sem perder produtos.
- [ ] Staging preserva o bruto (aba/linha/valor).
- [ ] Relatório de inconsistências em XLSX gerado e legível pelo cliente.
- [ ] Carga idempotente com `--dry-run`; variações exatas com movimentação inicial.
- [ ] Localização, unidades por caixa, preços e código alternativo extraídos.
- [ ] Testes do §5 passando.
- [ ] Mesmo motor exposto para a tela `/importacao` (entrega junto do doc 07 ou logo após).
