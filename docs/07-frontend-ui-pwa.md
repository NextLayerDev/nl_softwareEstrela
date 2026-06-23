# 07 — Frontend, UI e PWA · Estrela Gestão

> Responsável: Frontend. Depende do doc `02` (fundação). Roda em paralelo a partir do fim do marco 02.
> Stack: **Jinja2 + HTMX + Alpine.js + Tailwind**, server-rendered, **100% local** (assets servidos de `static/`).

---

## 1. Identidade visual (já aprovada nos protótipos)

Paleta da marca Estrela (estrela dourada):
- **Dourado primário** `#B98A19` — botões, destaques, barras.
- **Dourado escuro** `#8C660E` — textos de ênfase, valores.
- **Creme** `#F6F2E8` — fundo geral.
- **Sidebar** `#211B0F` (marrom quase preto) — menu lateral, texto creme `#D8CBAC`.
- **Cartões** brancos, bordas `#E4DCCA`.
- Status: ok/pago verde `#1F7A40` (fundo `#E3F2E8`), atenção âmbar, crítico/atrasado vermelho `#B3261E`,
  aguardando azul.

Tipografia: sem serifa, leitura densa de tabelas. Formatação enxuta. Tudo em **português (BR)**.

> Definir tokens no `tailwind.config` (cores `gold`, `cream`, `sidebar`…) e usar utilitárias. Configurar o
> **Tailwind Standalone CLI** (sem Node) — ver doc `02` §5.

---

## 2. Telas (mapa — protótipos de referência existem)

| Tela | Rota | Perfil | Notas |
|---|---|---|---|
| Login | `/login` | todos | card central, logo Estrela, JWT em cookie |
| Dashboard | `/` | Admin (cheio) | KPIs do dia, gráfico 7 dias, alertas de mínimo, últimos pedidos |
| Estoque | `/estoque` | todos | busca HTMX instantânea, status (número/selo), movimentações |
| **Localização (tablet)** | `/estoque/localizacao` | funcionario | **tela grande e simples** — ver §4 |
| Produtos | `/produtos` | admin | SKU + cód. alternativo + cores + preços + localização + caixa |
| Novo pedido | `/pedidos/novo` | vendedor, admin | saldo em tempo real, sugestão de preço, venda por caixa |
| Separação | `/separacao` | funcionario | fila + conferência item a item + localização |
| Financeiro | `/financeiro` | financeiro, admin | contas a receber, baixas, export |
| Clientes | `/clientes` | vendedor, admin | cadastro |
| Relatórios | `/relatorios` | conforme perfil | vendas, ABC, valorização, export XLSX |
| Importação | `/importacao` | admin | upload, preview, relatório de erros (motor do doc 04) |
| Usuários | `/usuarios` | admin | CRUD + reset de senha |

---

## 3. Padrões HTMX

- **Busca instantânea**: input com `hx-get="/estoque/busca" hx-trigger="keyup changed delay:250ms"
  hx-target="#linhas"`. O servidor devolve **só o fragmento** (`_linhas.html`) — troca o `tbody`.
- **Ações** (confirmar pedido, baixar conta, conferir item): `hx-post` devolvendo o fragmento atualizado.
- **Templates**: páginas em `templates/<modulo>/<pagina>.html` estendem `base.html`; fragmentos começam com `_`.
- **Alpine** só para interações locais leves (abrir modal, toggle), sem estado de negócio.
- **Feedback de erro**: o handler global (doc 02) devolve um fragmento de alerta; exibir via `hx-target` de erro
  ou um container de flash.

Estrutura sugerida de templates:
```
templates/
├── base.html                 # layout: sidebar + topbar + bloco de conteúdo
├── _flash.html               # mensagens
├── login.html
├── dashboard.html
├── estoque/
│   ├── index.html
│   ├── _linhas.html          # fragmento da tabela (HTMX)
│   └── localizacao.html      # tela do tablet
├── pedidos/
│   ├── novo.html
│   └── _item_linha.html
├── separacao/
│   ├── index.html
│   └── _item_check.html
├── financeiro/index.html
└── ...
```

---

## 4. Tela de localização (tablet) — requisito destacado pelo cliente

Áudio 3: 10 andares, funcionários se perdem, querem um tablet no estoque. Esta tela é **especial**:
- **Fonte grande**, alto contraste, poucos elementos — usável em pé, num tablet, por funcionário novo.
- Um **campo de busca** dominante (código, cód. alternativo, descrição ou cor).
- Resultado em **cartões grandes**: descrição + cor + **LOCALIZAÇÃO em destaque** (ex.: "4º ANDAR · LADO
  DIREITO · SALA 2") + indicação de saldo (número ou selo).
- Sem ações de edição — é **consulta**. Pode ter modo "tela cheia/quiosque".
- Funciona offline como o resto (PWA).

---

## 5. PWA — "abrir como programa" nos terminais

Requisito do cliente: abrir como um programa, não como site.
- `static/manifest.webmanifest`: `name="Estrela Gestão"`, `short_name="Estrela"`, `display="standalone"`,
  `theme_color="#B98A19"`, `background_color="#211B0F"`, ícones 192/512 (estrela dourada).
- `static/sw.js`: service worker **network-first** (busca sempre a versão do servidor; cache só fallback offline).
  Evita terminal preso em versão antiga.
- `base.html` referencia o manifest e registra o service worker.
- No go-live (doc 08), criar atalho `chrome --app=https://sistema.local` com ícone na área de trabalho de cada terminal.
- Atualização **centralizada no servidor**; terminais nunca reinstalam.

---

## 6. Acessibilidade e performance

- Tabelas densas com cabeçalho fixo; paginação server-side (estoque tem 12k+ linhas).
- Atalhos de teclado nas telas de alto volume (novo pedido, busca de estoque).
- Imagens e ícones locais; nada de fontes/CDN externas (offline-first).

---

## 7. Definition of Done do marco 07

- [ ] `base.html` com sidebar/topbar e a paleta da marca; HTMX/Alpine/Tailwind locais.
- [ ] Login funcional (cookie JWT) e navegação por perfil (menu muda conforme papel).
- [ ] Estoque com busca HTMX instantânea e status (número/selo).
- [ ] **Tela de localização (tablet)** grande, simples e rápida.
- [ ] Novo pedido com saldo em tempo real, sugestão de preço e venda por caixa.
- [ ] Fila de separação com conferência e impressão.
- [ ] Financeiro com baixas e export.
- [ ] PWA instalável (manifest + service worker network-first) — abre em modo aplicativo.
- [ ] Telas em português, formatação enxuta, paginação server-side onde há volume.
