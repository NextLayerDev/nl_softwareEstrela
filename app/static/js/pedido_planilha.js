/* Pedido em planilha — a planilha amarela do resumo, editável célula a célula.
 *
 * Um componente Alpine só serve as duas telas:
 *   - /pedidos/planilha: planilha em branco; "Salvar pedido" cria e JÁ CONFIRMA;
 *   - a lista em modo planilha: cada pedido aberto é esta mesma planilha — rascunho
 *     editável (Salvar / Salvar e confirmar), fechado só leitura.
 *
 * Como uma planilha se comporta:
 *   - CODIGO: ao sair da célula, o servidor casa o código (mesma escada da colagem) e a
 *     linha ganha descrição, cor e o preço da faixa para a quantidade. Código de produto
 *     com várias cores abre a lista de cores para escolher.
 *   - DESCRICAO: a partir de 2 letras, busca no catálogo (o mesmo /pedidos/busca-item
 *     da tela nova — o fragmento chama `selecionar($el)`, que existe aqui também).
 *   - Enter desce, Tab avança, ↑/↓ andam entre linhas, Ctrl+Backspace apaga a linha, e
 *     sempre sobra uma linha em branco no fim. Colar do Excel espalha as células.
 *
 * DINHEIRO EM CENTAVOS INTEIROS, como o resto do pedido (Orcamento.parseMoedaBR /
 * formatarBRL). O preço é sempre o da célula — é o que o cliente recebe.
 */
(function () {
  "use strict";

  const { parseMoedaBR, formatarBRL } = window.Orcamento;

  const COLUNAS = ["codigo", "descricao", "qtd", "preco"];
  const CABECALHO_COLUNA = /c[óo]d|descri|quant|qtd|unit|sub\.?\s*total/i;

  let sequencia = 0;
  const novaChave = () => `l${Date.now().toString(36)}${(sequencia++).toString(36)}`;

  /** "1050" (centavos) -> "10,50" — o formato da célula, sem "R$". */
  function centavosParaCelula(centavos) {
    if (centavos === null || centavos === undefined) return "";
    return formatarBRL(centavos).replace("R$ ", "");
  }

  function linhaVazia() {
    return {
      chave: novaChave(),
      itemId: null,
      identidadeMudou: true,
      variacaoId: null,
      codigo: "",
      descricao: "",
      cor: "",
      qtd: "",
      preco: "",
      precoDoCatalogo: false,
      descontoCentavos: 0,
      situacao: null, // "catalogo" | "avulso" | "duvida" | null
      consultado: "",
      consultando: false,
      opcoes: [],
    };
  }

  const vazia = (l) => !l.codigo.trim() && !l.descricao.trim() && !l.qtd.trim() && !l.preco.trim();

  function qtdDe(l) {
    const n = Number(l.qtd);
    return Number.isInteger(n) && n >= 1 ? n : null;
  }

  function precoDe(l) {
    const c = parseMoedaBR(l.preco);
    return c === null ? null : c;
  }

  function subtotalDe(l) {
    const q = qtdDe(l);
    const p = precoDe(l);
    if (q === null || p === null) return null;
    return Math.max(0, q * p - (l.descontoCentavos || 0));
  }

  function rotuloCatalogo(descricao, cor) {
    return cor && cor !== "padrão" ? `${descricao} · ${cor}` : descricao;
  }

  /** Divide a linha colada em células, escolhendo o separador que a colagem usou. */
  function celulas(linha) {
    if (linha.includes("\t")) return linha.split("\t").map((c) => c.trim());
    if (linha.includes(";")) return linha.split(";").map((c) => c.trim());
    if (/ {2,}/.test(linha)) return linha.split(/ {2,}/).map((c) => c.trim());
    return [linha.trim()];
  }

  async function postarJson(url, corpo) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(corpo),
      credentials: "same-origin",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  /** Total geral da lista em modo planilha, refeito quando um pedido é regravado. */
  function recalcularTotalGeral() {
    const tabela = document.getElementById("tabela-planilha");
    if (!tabela) return;
    let total = 0;
    let itens = 0;
    tabela.querySelectorAll("tbody[data-total-centavos]").forEach((tb) => {
      if (tb.dataset.cancelado !== "1") total += Number(tb.dataset.totalCentavos) || 0;
      itens += Number(tb.dataset.itens) || 0;
    });
    const celTotal = document.getElementById("planilha-total-geral");
    const celItens = document.getElementById("planilha-itens-geral");
    if (celTotal) celTotal.textContent = formatarBRL(total);
    if (celItens) celItens.textContent = String(itens);
  }

  /** Enquanto houver planilha com alteração não salva, o realtime não refaz a lista. */
  function pausarRealtime(chave, pausar) {
    const alvo = document.getElementById("lista-planilha");
    if (!alvo) return;
    const pausados = new Set((alvo.dataset.rtPausado || "").split(",").filter(Boolean));
    if (pausar) pausados.add(chave);
    else pausados.delete(chave);
    if (pausados.size) alvo.dataset.rtPausado = [...pausados].join(",");
    else delete alvo.dataset.rtPausado;
  }

  function editor(inicial, avisoInicial) {
    const cfg = inicial || {};
    const modo = cfg.pedidoId ? "editar" : "criar";
    // A raiz da planilha, guardada no init. `this.$root` é resolvido a partir do
    // elemento que disparou o evento — e o botão da lista de cores/busca some do DOM no
    // próprio clique (a lista fecha), o que deixaria o $root sem planilha para achar.
    let raiz = null;

    return {
      modo,
      pedidoId: cfg.pedidoId || null,
      editavel: modo === "criar" ? true : Boolean(cfg.editavel),
      numero: modo === "criar" ? "NOVO" : cfg.numero || "RASCUNHO",
      data: cfg.data || new Date().toLocaleDateString("pt-BR"),
      cliente: cfg.cliente || "",
      clienteId: null,
      clienteVinculado: Boolean(cfg.clienteVinculado),
      descontoCentavos: cfg.descontoCentavos || 0,
      linhas: [],
      original: "",
      salvando: "",
      erro: "",
      aviso: avisoInicial || "",
      recado: "",
      criado: null, // { id, numero } depois de criar
      mostrarErros: false,
      // Lista ancorada numa célula: busca de descrição, cores do código, clientes.
      lista: null, // { tipo, chave, top, left, largura }
      listaHtml: "",
      buscando: false,
      _timerBusca: null,
      _chaveRt: novaChave(),

      init() {
        raiz = this.$root;
        this._carregar();
        this.$watch("sujo", (v) => {
          if (this.modo === "editar") pausarRealtime(this._chaveRt, v);
        });
        // Scroll da folha: a lista ancorada perderia a célula de vista.
        this.$nextTick(() => {
          const rolagem = this.$refs.rolagem;
          if (rolagem) rolagem.addEventListener("scroll", () => this.fecharLista());
        });
      },

      _carregar() {
        this.linhas = this._comSobra(
          (cfg.itens || []).map((it) => ({
            ...linhaVazia(),
            itemId: it.itemId,
            identidadeMudou: false,
            variacaoId: it.variacaoId,
            codigo: it.codigo || "",
            descricao: it.variacaoId ? rotuloCatalogo(it.descricao, it.cor) : it.descricao,
            cor: it.cor || "",
            qtd: String(it.qtd),
            preco: centavosParaCelula(it.precoCentavos),
            descontoCentavos: it.descontoCentavos || 0,
            situacao: it.variacaoId ? "catalogo" : "avulso",
            consultado: it.codigo || "",
          }))
        );
        this.original = this._foto();
      },

      destroy() {
        pausarRealtime(this._chaveRt, false);
      },

      // ------------------------------------------------------------ estado
      _foto() {
        return JSON.stringify([
          this.cliente,
          this.clienteId,
          this.linhas
            .filter((l) => !vazia(l))
            .map((l) => [l.itemId, l.variacaoId, l.codigo, l.descricao, l.qtd, l.preco]),
        ]);
      },

      get sujo() {
        return this.editavel && this._foto() !== this.original;
      },

      _comSobra(lista) {
        const saida = [...lista];
        while (saida.length > 1 && vazia(saida[saida.length - 1]) && vazia(saida[saida.length - 2])) {
          saida.pop();
        }
        if (!saida.length || !vazia(saida[saida.length - 1])) saida.push(linhaVazia());
        return saida;
      },

      garantirSobra() {
        this.linhas = this._comSobra(this.linhas);
      },

      get preenchidas() {
        return this.linhas.filter((l) => !vazia(l));
      },

      get totalItensCentavos() {
        return this.preenchidas.reduce((s, l) => s + (subtotalDe(l) || 0), 0);
      },

      get totalCentavos() {
        return Math.max(0, this.totalItensCentavos - this.descontoCentavos);
      },

      subtotalTexto(l) {
        if (vazia(l)) return "";
        const s = subtotalDe(l);
        return s === null ? "—" : formatarBRL(s);
      },

      precoTexto(l) {
        const p = precoDe(l);
        return p === null ? "—" : formatarBRL(p);
      },

      moeda(c) {
        return formatarBRL(c);
      },

      erros(l) {
        if (vazia(l)) return {};
        const e = {};
        if (!l.descricao.trim() && !l.codigo.trim()) e.descricao = "Falta a descrição.";
        if (l.situacao === "duvida") e.codigo = "Escolha a cor do produto.";
        if (qtdDe(l) === null) e.qtd = "Quantidade inteira, a partir de 1.";
        if (precoDe(l) === null) e.preco = "Informe o valor unitário.";
        return e;
      },

      erroEm(l, col) {
        return this.mostrarErros && Boolean(this.erros(l)[col]);
      },

      get temErro() {
        return this.preenchidas.some((l) => Object.keys(this.erros(l)).length > 0);
      },

      get consultando() {
        return this.linhas.some((l) => l.consultando);
      },

      // ------------------------------------------------------------ foco
      focar(indice, col) {
        this.$nextTick(() => {
          const i = Math.max(0, Math.min(indice, this.linhas.length - 1));
          const el = raiz.querySelector(`[data-l="${i}"][data-c="${col}"]`);
          if (el) {
            el.focus();
            if (el.select) el.select();
          }
        });
      },

      teclar(evento, indice, col) {
        const l = this.linhas[indice];
        if (this.lista && this.lista.chave === l.chave && (col === "descricao" || col === "codigo")) {
          if (evento.key === "Escape") {
            evento.preventDefault();
            this.fecharLista();
            return;
          }
          if (evento.key === "ArrowDown") {
            // Desce para dentro da lista (os botões do fragmento).
            const primeiro = this.$refs.lista && this.$refs.lista.querySelector("button");
            if (primeiro) {
              evento.preventDefault();
              primeiro.focus();
              return;
            }
          }
        }
        if ((evento.ctrlKey || evento.metaKey) && evento.key === "Backspace") {
          evento.preventDefault();
          this.apagar(l.chave);
          this.focar(indice, col);
          return;
        }
        if (evento.key === "Enter") {
          evento.preventDefault();
          this.fecharLista();
          if (col === "codigo") this.consultarCodigo(l);
          this.focar(indice + 1, col);
          return;
        }
        if (evento.key === "ArrowDown") {
          evento.preventDefault();
          this.fecharLista();
          this.focar(indice + 1, col);
        } else if (evento.key === "ArrowUp") {
          evento.preventDefault();
          this.fecharLista();
          this.focar(indice - 1, col);
        }
      },

      apagar(chave) {
        this.fecharLista();
        this.linhas = this._comSobra(this.linhas.filter((l) => l.chave !== chave));
      },

      // ------------------------------------------------------------ edição
      mudouCodigo(l) {
        l.identidadeMudou = true;
        this.garantirSobra();
      },

      mudouDescricao(l, el) {
        l.identidadeMudou = true;
        // Descrição reescrita à mão deixa de ser o item do catálogo.
        if (l.situacao === "catalogo") {
          l.variacaoId = null;
          l.cor = "";
          l.precoDoCatalogo = false;
        }
        l.situacao = l.descricao.trim() ? "avulso" : null;
        this.garantirSobra();
        const termo = l.descricao.trim();
        if (termo.length >= 2) this.buscarDescricao(l, termo, el);
        else this.fecharLista();
      },

      mudouQtd(l) {
        l.qtd = l.qtd.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
        this.garantirSobra();
      },

      mudouPreco(l) {
        l.preco = l.preco.replace(/[^\d.,]/g, "");
        l.precoDoCatalogo = false;
        this.garantirSobra();
      },

      saiuPreco(l) {
        const c = precoDe(l);
        if (c !== null) l.preco = centavosParaCelula(c);
      },

      /** Quantidade mudou numa linha com preço do catálogo: a faixa pode ter mudado. */
      async saiuQtd(l) {
        const q = qtdDe(l);
        if (q === null || !l.variacaoId || !l.precoDoCatalogo) return;
        const qtdPedida = l.qtd;
        const [r] = await this._resolver([{ variacao_id: l.variacaoId, qtd: q }]);
        if (r && r.situacao === "ok" && l.precoDoCatalogo && l.qtd === qtdPedida) {
          l.preco = centavosParaCelula(r.preco_centavos);
        }
      },

      async _resolver(linhas) {
        try {
          const resp = await postarJson("/pedidos/planilha/resolver", { linhas });
          return resp.ok ? resp.linhas : [];
        } catch (_) {
          return [];
        }
      },

      _aplicarCatalogo(l, r) {
        const preencherPreco = !l.preco.trim() || l.precoDoCatalogo;
        l.variacaoId = r.variacao_id;
        l.codigo = r.codigo || l.codigo;
        l.consultado = l.codigo;
        l.cor = r.cor || "";
        l.descricao = rotuloCatalogo(r.descricao, r.cor);
        if (preencherPreco && r.preco_centavos !== null && r.preco_centavos !== undefined) {
          l.preco = centavosParaCelula(r.preco_centavos);
          l.precoDoCatalogo = true;
        }
        if (!l.qtd.trim()) l.qtd = "1";
        l.situacao = "catalogo";
        l.opcoes = [];
        l.identidadeMudou = true;
      },

      async consultarCodigo(l) {
        const codigo = l.codigo.trim();
        if (codigo === (l.consultado || "").trim()) return;
        l.consultado = codigo;
        if (!codigo) {
          l.variacaoId = null;
          l.opcoes = [];
          l.situacao = l.descricao.trim() ? "avulso" : null;
          return;
        }
        l.consultando = true;
        const [r] = await this._resolver([{ codigo, descricao: l.descricao.trim(), qtd: qtdDe(l) || 1 }]);
        l.consultando = false;
        if (l.codigo.trim() !== codigo) return; // mudou enquanto a consulta rodava
        this._aplicarResolucao(l, r);
      },

      _aplicarResolucao(l, r) {
        if (r && r.situacao === "ok") {
          this._aplicarCatalogo(l, r);
        } else if (r && r.situacao === "duvida") {
          l.variacaoId = null;
          l.situacao = "duvida";
          l.opcoes = r.opcoes || [];
          if (!l.descricao.trim() && l.opcoes.length) l.descricao = l.opcoes[0].descricao;
          const el = raiz.querySelector(`[data-chave="${l.chave}"] [data-c="codigo"]`);
          if (el && document.activeElement && raiz.contains(document.activeElement)) {
            this.abrirLista("cores", l, el);
          }
        } else {
          l.variacaoId = null;
          l.opcoes = [];
          l.situacao = l.descricao.trim() ? "avulso" : null;
        }
      },

      escolherCor(l, opcao) {
        this.fecharLista();
        l.variacaoId = opcao.variacao_id;
        // O "1" entra antes do foco, para ficar selecionado e ser substituído pelo que a
        // pessoa digitar (se entrasse depois, "60" viraria "160").
        if (!l.qtd.trim()) l.qtd = "1";
        this.consultarVariacao(l);
        this.focar(this.linhas.indexOf(l), "qtd");
      },

      async consultarVariacao(l) {
        const [r] = await this._resolver([{ variacao_id: l.variacaoId, qtd: qtdDe(l) || 1 }]);
        if (r && r.situacao === "ok") this._aplicarCatalogo(l, r);
      },

      // ------------------------------------------------------------ listas ancoradas
      abrirLista(tipo, l, el) {
        const caixa = raiz.getBoundingClientRect();
        const cel = (el.closest("td") || el).getBoundingClientRect();
        this.lista = {
          tipo,
          chave: l ? l.chave : null,
          top: cel.bottom - caixa.top,
          left: cel.left - caixa.left,
          largura: Math.max(cel.width, 460),
        };
      },

      fecharLista() {
        this.lista = null;
        this.listaHtml = "";
        clearTimeout(this._timerBusca);
      },

      buscarDescricao(l, termo, el) {
        clearTimeout(this._timerBusca);
        this.abrirLista("busca", l, el);
        this.buscando = true;
        this._timerBusca = setTimeout(async () => {
          try {
            const resp = await fetch(`/pedidos/busca-item?q=${encodeURIComponent(termo)}`, {
              credentials: "same-origin",
            });
            if (this.lista && this.lista.chave === l.chave) this.listaHtml = await resp.text();
          } catch (_) {
            this.listaHtml = '<p class="px-2 py-3 text-sm text-gray-400">Busca indisponível.</p>';
          } finally {
            this.buscando = false;
          }
        }, 250);
      },

      /** Usado pelo :class do fragmento de busca (destaca a variação já escolhida). */
      get variacaoId() {
        const l = this.lista && this.linhas.find((x) => x.chave === this.lista.chave);
        return l ? l.variacaoId : null;
      },

      /** Clique num resultado da busca (o fragmento chama `selecionar($el)`). */
      selecionar(el) {
        const l = this.lista && this.linhas.find((x) => x.chave === this.lista.chave);
        if (!l) return;
        const d = el.dataset;
        this.fecharLista();
        const preencherPreco = !l.preco.trim() || l.precoDoCatalogo;
        l.variacaoId = Number(d.id);
        l.codigo = d.codigo || "";
        l.consultado = l.codigo;
        l.cor = d.cor || "";
        l.descricao = rotuloCatalogo(d.descricao || "", d.cor);
        l.situacao = "catalogo";
        l.identidadeMudou = true;
        l.opcoes = [];
        if (!l.qtd.trim()) l.qtd = "1";
        if (preencherPreco) l.precoDoCatalogo = true;
        this.garantirSobra();
        this.focar(this.linhas.indexOf(l), "qtd");
        // O preço é o do servidor (faixa de atacado pela quantidade).
        if (preencherPreco) this.consultarVariacao(l);
      },

      buscarCliente(el) {
        this.clienteId = null;
        clearTimeout(this._timerBusca);
        const termo = this.cliente.trim();
        if (termo.length < 2) {
          this.fecharLista();
          return;
        }
        this.abrirLista("cliente", null, el);
        this.buscando = true;
        this._timerBusca = setTimeout(async () => {
          try {
            const resp = await fetch(`/pedidos/busca-cliente?q=${encodeURIComponent(termo)}`, {
              credentials: "same-origin",
            });
            if (this.lista && this.lista.tipo === "cliente") this.listaHtml = await resp.text();
          } catch (_) {
            this.fecharLista();
          } finally {
            this.buscando = false;
          }
        }, 250);
      },

      /** Clique numa sugestão de cliente (o fragmento chama `vincular(...)`). */
      vincular(id, nome) {
        this.clienteId = Number(id);
        this.cliente = nome;
        this.fecharLista();
      },

      // ------------------------------------------------------------ colar do Excel
      colar(evento, indice, col) {
        const texto = (evento.clipboardData || window.clipboardData).getData("text");
        if (!texto || (!texto.includes("\t") && !texto.includes("\n"))) return;
        evento.preventDefault();
        this.fecharLista();

        const inicio = COLUNAS.indexOf(col);
        const entrada = [];
        texto
          .replace(/\r/g, "")
          .split("\n")
          .filter((ln) => ln.trim())
          .forEach((ln) => {
            const cels = celulas(ln);
            const cheias = cels.filter(Boolean);
            if (!cheias.length) return;
            // Linha de data (cabeçalho do bloco), cabeçalho de coluna e rodapé de TOTAL.
            if (/^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$/.test(cheias[0])) return;
            if (cheias.length >= 2 && cheias.filter((c) => CABECALHO_COLUNA.test(c)).length >= 2) return;
            if (cheias.some((c) => /^total$/i.test(c))) return;
            const valores = {};
            cels.forEach((cel, i) => {
              const alvo = COLUNAS[inicio + i];
              if (!alvo) return;
              if (alvo === "qtd") {
                const n = parseMoedaBR(cel.replace(/[^\d.,]/g, ""));
                valores.qtd = n === null ? "" : String(Math.trunc(n / 100));
              } else if (alvo === "preco") {
                const n = parseMoedaBR(cel.replace(/[^\d.,]/g, ""));
                valores.preco = n === null ? "" : centavosParaCelula(n);
              } else {
                valores[alvo] = cel;
              }
            });
            entrada.push(valores);
          });
        if (!entrada.length) return;

        const lista = [...this.linhas];
        const consultar = [];
        entrada.forEach((valores, i) => {
          const pos = indice + i;
          if (pos >= lista.length) lista.push(linhaVazia());
          const l = lista[pos];
          COLUNAS.forEach((c) => {
            if (valores[c] !== undefined) l[c] = valores[c];
          });
          if (valores.preco !== undefined) l.precoDoCatalogo = false;
          if (valores.codigo !== undefined || valores.descricao !== undefined) {
            l.variacaoId = null;
            l.identidadeMudou = true;
            l.situacao = l.descricao.trim() ? "avulso" : null;
            l.consultado = "";
          }
          if (l.codigo.trim()) consultar.push(l.chave);
        });
        this.linhas = this._comSobra(lista);
        this.recadoTemporario(`${entrada.length} linha${entrada.length > 1 ? "s" : ""} colada${entrada.length > 1 ? "s" : ""}.`);
        // Pelas chaves, e não pelos objetos: as linhas novas só ficam reativas depois de
        // entrar em `this.linhas` — mexer no objeto cru não redesenharia a planilha.
        this._consultarEmLote(this.linhas.filter((l) => consultar.includes(l.chave)));
      },

      async _consultarEmLote(linhas) {
        if (!linhas.length) return;
        linhas.forEach((l) => {
          l.consultando = true;
          l.consultado = l.codigo.trim();
        });
        const codigos = linhas.map((l) => l.codigo.trim());
        const respostas = await this._resolver(
          linhas.map((l) => ({ codigo: l.codigo.trim(), descricao: l.descricao.trim(), qtd: qtdDe(l) || 1 }))
        );
        linhas.forEach((l, i) => {
          l.consultando = false;
          if (l.codigo.trim() !== codigos[i]) return;
          const r = respostas[i];
          if (r && r.situacao === "ok") this._aplicarCatalogo(l, r);
          else if (r && r.situacao === "duvida") {
            l.situacao = "duvida";
            l.opcoes = r.opcoes || [];
          }
        });
      },

      recadoTemporario(texto) {
        this.recado = texto;
        setTimeout(() => {
          if (this.recado === texto) this.recado = "";
        }, 2500);
      },

      // ------------------------------------------------------------ resumo em imagem
      dadosResumo() {
        return {
          titulo: "Pedido",
          cliente: this.cliente.trim() || "CONSUMIDOR",
          data: this.data,
          numero: this.modo === "criar" ? "RASCUNHO" : this.numero,
          itens: this.preenchidas
            .filter((l) => subtotalDe(l) !== null)
            .map((l) => ({
              codigo: l.codigo.trim(),
              descricao: l.descricao.trim() || l.codigo.trim(),
              qtd: qtdDe(l),
              precoCentavos: precoDe(l),
              subtotalCentavos: subtotalDe(l),
            })),
          descontoCentavos: this.descontoCentavos,
          totalCentavos: this.totalCentavos,
        };
      },

      async imagem(qual) {
        const dados = this.dadosResumo();
        if (!dados.itens.length) return;
        const texto = await (qual === "copiar"
          ? window.ResumoPedido.copiar(dados)
          : window.ResumoPedido.baixar(dados));
        this.recadoTemporario(texto);
      },

      // ------------------------------------------------------------ salvar
      async salvar(confirmar) {
        this.fecharLista();
        this.mostrarErros = true;
        this.erro = "";
        this.aviso = "";
        if (!this.preenchidas.length) {
          this.erro = "Adicione ao menos um item.";
          return;
        }
        if (this.temErro) {
          this.erro = "Confira as células marcadas em vermelho.";
          return;
        }
        const corpo = {
          cliente_id: this.clienteId,
          cliente_nome: this.cliente.trim() || null,
          confirmar: this.modo === "criar" ? true : Boolean(confirmar),
          itens: this.preenchidas.map((l) => ({
            item_id: l.itemId,
            identidade_mudou: l.identidadeMudou,
            variacao_id: l.variacaoId,
            codigo: l.codigo.trim(),
            descricao: l.descricao.trim(),
            qtd: qtdDe(l),
            preco_unit: (precoDe(l) / 100).toFixed(2),
          })),
        };
        const url = this.modo === "criar" ? "/pedidos/planilha" : `/pedidos/${this.pedidoId}/planilha`;
        this.salvando = confirmar ? "confirmar" : "salvar";
        try {
          const resp = await postarJson(url, corpo);
          if (!resp.ok) {
            this.erro = resp.erro || "Não consegui salvar.";
            return;
          }
          this.mostrarErros = false;
          if (this.modo === "criar") {
            this.criado = { id: resp.pedido_id, numero: resp.numero };
            this.aviso = resp.aviso || "";
            this._zerar();
          } else {
            this.original = this._foto(); // não é mais "sujo": o bloco vai ser refeito
            pausarRealtime(this._chaveRt, false);
            // O bloco é refeito do servidor; o aviso (confirmação que não passou) vai
            // junto na troca para aparecer na planilha nova.
            this.$dispatch("planilha-salva", { pedidoId: this.pedidoId, aviso: resp.aviso || "" });
          }
        } catch (_) {
          this.erro = "Sem resposta do servidor. Tente de novo.";
        } finally {
          this.salvando = "";
        }
      },

      _zerar() {
        this.cliente = "";
        this.clienteId = null;
        this.linhas = this._comSobra([]);
        this.original = this._foto();
        this.$nextTick(() => {
          const el = raiz.querySelector("[data-c='cliente']");
          if (el) el.focus();
        });
      },

      descartar() {
        this.fecharLista();
        this.mostrarErros = false;
        this.erro = "";
        this.clienteId = null;
        this.cliente = cfg.cliente || "";
        this._carregar();
      },
    };
  }

  window.PedidoPlanilha = { editor, recalcularTotalGeral };
})();
