# Relatório de QA — Varredura completa (navegação real)

Ambiente: instância local (`localhost:8011`, banco de teste). Percorridos os 4 perfis
(admin, vendedor, financeiro, funcionário) e todos os módulos.

> **STATUS: todos os achados (F1–F10) foram corrigidos e reverificados no navegador,
> e publicados na `main`** (4 fases de commits). Extra: cache-busting de assets
> (`?v=mtime`) para terminais não ficarem presos em CSS antigo. Detalhe por item abaixo.

## Resumo

| ID | Severidade | Categoria | Título |
|----|-----------|-----------|--------|
| F2 | 🔴 Crítico | UX/Funcional | Impossível selecionar a variação ao adicionar item no pedido |
| F3 | 🟠 Alto | Funcional | "Confirmar" do pedido fica desabilitado após adicionar itens (precisa recarregar) |
| F4 | 🟠 Alto | Funcional | "Concluir separação" não avança o status — pedido fica preso na fila |
| F1 | 🟡 Médio | UX | Entrada/Ajuste de estoque duplica a linha na tabela |
| F6 | 🟡 Médio | RBAC/UX | Botão "Novo produto" aparece para quem não pode (vendedor/funcionário) |
| F7 | 🔵 Baixo | UX | Pedido faturado não tem ação "marcar entregue" na interface |
| F8 | 🔵 Baixo | UX | Aplicar inventário não avisa quantos itens ficaram sem contagem |
| F9 | 🔵 Baixo | Privacidade | Dashboard mostra "Vendas faturadas hoje" (receita) ao funcionário |
| F10 | 🔵 Baixo | UX | Busca de item do pedido mostra botões Entrada/Ajustar e não limpa após adicionar |

---

## 🔴 Crítico

### F2 — Adicionar item ao pedido: não há como selecionar a variação
**Onde:** `/pedidos/{id}` (detalhe, "Adicionar item") — `app/web/templates/pedidos/detalhe.html`
**Reproduzir:** Novo pedido → digite "K708" na busca → aparecem as linhas do estoque.
**Esperado:** clicar numa linha seleciona o produto/variação e preenche o campo "Variação (ID)".
**Obtido:** clicar na linha **não faz nada**; não há botão "Selecionar" (apesar do texto da tela dizer "use o botão 'Selecionar'"); e o **ID da variação não é exibido** em lugar nenhum. O usuário só consegue adicionar item se souber/adivinhar o ID interno do banco e digitá-lo à mão. Na prática, o fluxo central de montar pedido fica inviável para um usuário comum.
**Causa provável:** o fragmento de resultado reaproveita `estoque/_linhas.html` (linhas de estoque com botões Histórico/Entrada/Ajustar), que não tem ação de seleção nem mostra o ID. Falta um botão "Selecionar" que faça `$dispatch`/preencha `variacaoId` (Alpine) e dispare o saldo.
**Sugestão:** criar um fragmento próprio para a busca dentro do pedido com um botão "Selecionar" por linha que seta `variacaoId` e chama o saldo; ocultar Entrada/Ajustar nesse contexto.

---

## 🟠 Alto

### F3 — "Confirmar" continua desabilitado após adicionar itens
**Onde:** `/pedidos/{id}` — botão "Confirmar" no cabeçalho.
**Reproduzir:** Novo pedido (sem itens) → adicionar 1 item (HTMX) → tentar Confirmar.
**Esperado:** com ≥1 item, "Confirmar" habilita.
**Obtido:** o botão segue `disabled` (confirmado via DOM: `disabled=true`). Só habilita ao **recarregar a página**.
**Causa:** o "Confirmar" está fora do alvo do HTMX (`#bloco-itens`), que é a única parte trocada ao adicionar item; o botão renderizado como desabilitado (pedido sem itens) nunca é re-renderizado.
**Sugestão:** ao adicionar/remover item, atualizar também o botão (OOB swap `hx-swap-oob`, ou incluir o cabeçalho de ações no fragmento trocado), ou habilitar o botão via Alpine quando a tabela tiver itens.

### F4 — "Concluir separação" não avança o status
**Onde:** `/separacao/{id}` → "Concluir separação"; lógica em `app/services/pedido_service.py` (`concluir_separacao`).
**Reproduzir:** Separar um pedido confirmado → marcar todos os itens → "Concluir separação".
**Esperado:** o pedido sai da fila de separação (vai para um estado tipo "separado/pronto p/ faturar").
**Obtido:** o pedido **permanece na fila com status "separacao"**; concluir não muda nada de efeito visível. Os pedidos de seed #134/#135 estão presos do mesmo jeito.
**Causa provável:** `concluir_separacao` mantém `status = SEPARACAO` (não há transição de saída).
**Sugestão:** definir um estado pós-separação (ex.: `separado`/`aguardando_faturamento`) e removê-lo da fila do funcionário, ou marcar um flag de "separação concluída".

---

## 🟡 Médio

### F1 — Entrada/Ajuste de estoque duplica a linha na tabela
**Onde:** `/estoque` — `app/web/templates/estoque/index.html` (`hx-swap="afterbegin"`).
**Reproduzir:** Estoque → "Entrada" numa linha → registrar.
**Esperado:** a linha da variação reflete o novo saldo (sem duplicar).
**Obtido:** a linha atualizada é **inserida no topo** e a antiga (com saldo desatualizado) permanece — a mesma variação aparece 2× na tabela até recarregar/buscar. Confunde o operador.
**Sugestão:** trocar o `tbody` inteiro, ou usar swap que substitua a linha existente (OOB por `id` da variação), em vez de `afterbegin`.

### F6 — Botão "Novo produto" visível para quem não tem permissão
**Onde:** `/produtos` — `app/web/templates/produtos/index.html` (bloco de ações na topbar).
**Reproduzir:** logar como **vendedor** → Produtos → clicar "Novo produto".
**Esperado:** vendedor (read-only em produtos) não deveria ver a ação.
**Obtido:** o botão aparece e leva a um **403** (o backend bloqueia corretamente, mas a UI oferece a ação). Provável regressão do overhaul, ao mover "Novo produto" para `{% block acoes %}` sem o guard `{% if pode_editar %}`.
**Sugestão:** envolver o `{% block acoes %}` de Produtos com `{% if pode_editar %}`.

---

## 🔵 Baixo / melhorias

- **F7** — Pedido **faturado** não exibe ação "marcar entregue"; o estado `entregue` parece inalcançável pela UI. Confirmar se é intencional.
- **F8** — Ao **aplicar inventário**, itens não contados são (corretamente) mantidos, mas não há aviso de "X itens não foram contados" — risco de aplicar sem perceber.
- **F9** — O **dashboard do funcionário** mostra "Vendas faturadas hoje" (receita do dia). Avaliar ocultar dados financeiros para esse perfil.
- **F10** — Na busca de item do pedido, os resultados mostram botões **Entrada/Ajustar** (ações de estoque, fora de contexto e potencialmente perigosas) e a busca **não limpa** após adicionar o item. (Relacionado a F2.)
- **Consistência** — `financeiro` acessa `/clientes` por URL (200, condizente com a matriz 👁) mas o link não aparece no menu dele.

---

## ✅ O que foi verificado e está correto

- **Estoque append-only:** toda mudança gera movimentação imutável com saldo_apos (entrada/ajuste/importação no histórico). Ajustar oculto para funcionário; entrada permitida.
- **Inventário:** abrir → contar → aplicar ajusta só os contados (AZUL 190→200) e **preserva os não contados**; gera movimentação `inventario`.
- **Pedido:** numeração atribuída na confirmação (#203); reserva e **baixa** de estoque corretas (AZUL 200→195 ao faturar); preço automático por faixa; subtotal correto.
- **Financeiro:** conta a receber gerada na fatura com vencimento certo (30 dias); **baixa** (Pix) e **marcar vencidas** funcionam; "recebimentos de hoje" atualiza.
- **Relatórios:** Vendas, Curva ABC e **Valorização** (somando o estoque das variações: K708=723) corretos.
- **RBAC backend:** sólido nos 4 perfis (todas as rotas proibidas retornam 403; permitidas 200). `preco_custo` **oculto** para vendedor/funcionário.
- **UI nova (overhaul):** sidebar agrupada + drawer mobile, abas (Estoque/Relatórios), modal de reset acessível (foco/Esc/return-focus), breadcrumbs, empty-states, página 403 estilizada — tudo funcionando, sem erros de console.
