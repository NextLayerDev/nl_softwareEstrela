# 06 — Módulo de Pedidos e Financeiro · Estrela Gestão

> Responsável: Backend (Pedidos). Depende dos docs `03` (modelo) e `05` (estoque).
> Cobre o ciclo do pedido (com preço por faixa e venda por caixa), a fila de separação do Funcionário,
> e o contas a receber.

---

## 1. Ciclo de vida do pedido

```
rascunho → confirmado → separacao → faturado → entregue
                   └──────────────→ cancelado
```

- **rascunho**: vendedor monta, edita livremente. Não mexe em estoque.
- **confirmado**: **reserva** o estoque de cada item (doc `05`). Entra na fila de separação.
- **separacao**: funcionário confere item a item (§3).
- **faturado**: **baixa** definitiva do estoque + gera **contas a receber** (§4).
- **entregue**: encerrado.
- **cancelado**: **estorna** reservas (se estava confirmado/separação). Auditado.

> Validar com o cliente (Q5 do doc 01) se a reserva é na confirmação ou só no faturamento. O código acima
> assume reserva na confirmação (mais seguro para atacado de alto giro).

---

## 2. Montagem do pedido (regras dos áudios)

- Item referencia **produto + variação (cor)**. Cor normalmente obrigatória (Q4 do doc 01).
- **Preço por faixa de quantidade** (Áudio 2): ao informar a quantidade, o service **sugere**:
  - `qtd < qtd_corte_atacado` → `preco_pouca_qtd`
  - `qtd ≥ qtd_corte_atacado` → `preco_muita_qtd`
  - há `preco_promocional`? oferecer como opção.
- **Preço unitário é editável** no item (a planilha mostra o mesmo SKU saindo a preços diferentes por cor/negociação).
- **Venda por caixa**: vendedor pode informar **caixas**; o sistema converte para unidades
  (`qtd = qtd_caixas × unidades_por_caixa`) e mostra os dois. Guarda `qtd_caixas` no item.
- **Disponibilidade em tempo real**: ao adicionar item, mostrar saldo da variação (número se `EXATO`, selo se `APROXIMADO`).
- **Desconto** por item e total, com **limite por perfil** (vendedor até X%; acima exige Admin).

```python
# app/services/preco_service.py
def sugerir_preco(produto, qtd) -> Decimal:
    corte = produto.qtd_corte_atacado or 0
    if corte and qtd >= corte:
        return produto.preco_muita_qtd
    return produto.preco_pouca_qtd
```

```python
# app/services/pedido_service.py (confirmar)
def confirmar(self, db, pedido, usuario_id):
    if not pedido.itens:
        raise RegraNegocioError("Pedido sem itens")
    for item in pedido.itens:
        estoque_service.reservar(db, item.variacao, item.qtd, usuario_id, pedido.id)
    pedido.numero = pedido.numero or proximo_numero_pedido(db)   # sequence
    pedido.status = "confirmado"
    # commit é da rota
```

---

## 3. Fila de separação (perfil Funcionário)

- Tela `/separacao` lista pedidos **confirmados**, em ordem de chegada.
- Ao abrir um pedido, o funcionário vê os itens com **localização** (ajuda a achar no estoque — Áudio 3) e
  confere um a um (checkbox HTMX). Barra de progresso.
- **Concluir separação** → status `separacao` finalizada (pronto para faturar). Cada conferência é registrada
  com usuário/data/hora (auditoria).
- Imprimir **lista de separação** (PDF via WeasyPrint) com itens, cores, quantidades e **localização**.

---

## 4. Financeiro — contas a receber

- Ao **faturar**, gerar título(s) em `contas_receber` conforme condição de pagamento do cliente
  (à vista, parcelado, prazo). Origem futura dos pendentes: aba `EM ABERTO`.
- **Baixa de recebimento** (Pix, boleto, dinheiro) com data e usuário → status `pago`.
- Vencido sem baixa → `atrasado` (job diário do APScheduler reavalia).
- Relatórios: recebíveis por período/cliente, inadimplência, recebimentos do dia.
- **Margem** (custo × venda) visível só para Financeiro/Admin.

---

## 5. Impressão e exportação

- **Pedido (A4)** e **lista de separação**: HTML com CSS `@media print` ou PDF (WeasyPrint).
- **Relatórios** exportáveis em **XLSX** (openpyxl): vendas por período/vendedor/cliente/produto, curva ABC,
  valorização de estoque.

---

## 6. Endpoints do módulo (resumo)

| Rota | Perfil | Função |
|---|---|---|
| `GET/POST /pedidos` | vendedor, admin | listar/criar |
| `POST /pedidos/{id}/itens` | vendedor, admin | adicionar item (sugere preço, mostra saldo) |
| `POST /pedidos/{id}/confirmar` | vendedor, admin | reserva estoque |
| `POST /pedidos/{id}/cancelar` | admin (vendedor: próprio rascunho) | estorna |
| `GET /separacao` | funcionario | fila |
| `POST /separacao/{id}/concluir` | funcionario | finaliza separação |
| `POST /pedidos/{id}/faturar` | financeiro, admin | baixa + contas a receber |
| `GET /financeiro` | financeiro, admin | contas a receber |
| `POST /financeiro/{conta}/baixar` | financeiro, admin | registrar recebimento |
| `GET /relatorios/*` | conforme perfil | relatórios + export |

---

## 7. Definition of Done do marco 06

- [ ] Ciclo completo do pedido com reserva/baixa/estorno integrados ao `EstoqueService`.
- [ ] Sugestão de preço por faixa + **preço unitário editável** no item.
- [ ] Venda por **caixa** com conversão para unidades.
- [ ] Numeração de pedido por sequence (sem buracos).
- [ ] Desconto com limite por perfil.
- [ ] Fila de separação com conferência item a item + localização + impressão.
- [ ] Contas a receber na fatura, baixas, status atrasado via job.
- [ ] Relatórios + export XLSX; impressão de pedido e separação em PDF.
- [ ] Testes: totais/descontos, faixa de preço, conversão de caixa, ciclo de status, geração de contas a receber.
