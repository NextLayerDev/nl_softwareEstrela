/* Novo pedido — o carrinho da tela única.
 *
 * O pedido inteiro é montado aqui no navegador (cliente, itens, desconto) e só vai ao
 * servidor no clique em "Criar pedido", num POST só. Antes disso a tela abria um
 * rascunho vazio e batia no servidor a cada item lançado, o que no balcão significava
 * esperar a rede no meio da conversa com o cliente.
 *
 * DINHEIRO É INTEIRO, EM CENTAVOS, do começo ao fim — mesma regra do orcamento.js, pelo
 * mesmo motivo: ponto flutuante erra centavo (0.1 + 0.2 !== 0.3), e um total que fecha
 * diferente do pedido gravado destrói a confiança na tela.
 *
 * O preço mostrado aqui é uma PRÉVIA. Quem decide o preço de verdade é o servidor, a
 * partir do catálogo — o navegador só manda o que o vendedor digitou por cima. Por isso
 * `precoEditado`: sem ele, mandaríamos de volta o preço que nós mesmos calculamos, e a
 * tabela do catálogo deixaria de mandar no preço.
 *
 * Exposto em window.PedidoNovo porque o Alpine do template chama estas funções — e a
 * CSP do sistema (script-src 'self') não permite módulo externo nem CDN.
 */
(function () {
  "use strict";

  const { parseMoedaBR, formatarBRL } = window.Orcamento;

  const inteiro = (valor, padrao = 0) => {
    const n = Number.parseInt(valor, 10);
    return Number.isFinite(n) ? n : padrao;
  };

  /** Centavos a partir de um campo de texto em pt-BR. Ilegível vale zero. */
  const centavos = (texto) => parseMoedaBR(String(texto ?? "")) ?? 0;

  /** Reais com ponto decimal, do jeito que o Decimal do Python lê. */
  const paraDecimal = (cents) => (cents / 100).toFixed(2);

  /* Mesma regra do `pedido_service.sugerir_preco`: se o produto tem tabela, a faixa
     que vale para a quantidade manda; senão, atacado a partir do corte. Duplicada aqui
     de propósito — é a única forma de a prévia acompanhar a quantidade sem uma ida ao
     servidor por tecla digitada. O servidor recalcula do zero no submit, então uma
     divergência vira preço certo no pedido, nunca preço errado gravado. */
  function precoSugerido(produto, qtd) {
    const daTabela = faixaPara(produto.faixas, qtd);
    if (daTabela !== null) return daTabela.preco;
    const corte = produto.qtdCorte;
    if (corte && qtd >= corte) return produto.precoMuita;
    return produto.precoPouca;
  }

  /** A faixa de maior minQtd que ainda seja <= qtd. null abaixo da primeira. */
  function faixaPara(faixas, qtd) {
    let escolhida = null;
    for (const faixa of faixas || []) {
      if (faixa.minQtd <= qtd) escolhida = faixa;
      else break; // ordenado por minQtd: daqui pra frente é tudo maior
    }
    return escolhida;
  }

  /** "10 a 49 un" / "50+ un" — o mesmo rótulo que o servidor monta. */
  function rotuloFaixa(faixas, qtd) {
    const escolhida = faixaPara(faixas, qtd);
    if (escolhida === null) return "";
    const i = faixas.indexOf(escolhida);
    const seguinte = faixas[i + 1];
    return seguinte ? `${escolhida.minQtd} a ${seguinte.minQtd - 1} un` : `${escolhida.minQtd}+ un`;
  }

  /* Lê o data-faixas do resultado da busca. Preço vem em string e passa pelo
     parseMoedaBR — dinheiro nunca por Number(). Texto ilegível vira "sem tabela". */
  function faixasDe(dataset) {
    try {
      return JSON.parse(dataset.faixas || "[]").map(([minQtd, preco]) => ({
        minQtd: Number(minQtd),
        preco: centavos(preco),
      }));
    } catch {
      return [];
    }
  }

  /* O piso e o limite de desconto deixaram de barrar a venda (quem está no balcão
     fecha negócio difícil na frente do cliente), mas continuam valendo como aviso. O
     servidor calcula o mesmo texto em `aviso_de_preco`; aqui é só para a pessoa ver
     enquanto digita. */
  function avisoDePreco(item) {
    if (!item.precoMinimo || item.precoMinimo <= 0) return "";
    const efetivo = item.qtd > 0
      ? Math.round((item.qtd * item.precoCentavos - item.descontoCentavos) / item.qtd)
      : item.precoCentavos;
    if (efetivo >= item.precoMinimo) return "";
    return `abaixo do preço mínimo (${formatarBRL(item.precoMinimo)})`;
  }

  function subtotalDe(item) {
    return Math.max(0, item.qtd * item.precoCentavos - item.descontoCentavos);
  }

  /** Texto do orçamento pronto para colar no WhatsApp. */
  function textoOrcamento(itens, subtotal, desconto, total) {
    const linhas = itens.map(
      (item, i) =>
        `${i + 1}. ${item.descricao}${item.cor ? ` · ${item.cor}` : ""}\n` +
        `   ${item.qtd}x ${formatarBRL(item.precoCentavos)} = ${formatarBRL(subtotalDe(item))}`
    );
    const partes = ["🛒 *Orçamento*", "", ...linhas, "", `Subtotal: ${formatarBRL(subtotal)}`];
    if (desconto > 0) partes.push(`Desconto: ${formatarBRL(desconto)}`);
    partes.push(`*Total: ${formatarBRL(total)}*`);
    return partes.join("\n");
  }

  /* O desenho da planilha mora em resumo_pedido.js — o detalhe do pedido mostra o
     mesmo resumo, e duplicar o canvas era garantir que um dia os dois divergissem. */
  const { ResumoPedido } = window;

  window.PedidoNovo = {
    /** Componente Alpine da tela inteira. */
    carrinho() {
      return {
        itens: [],
        descontoTexto: "",
        aba: "catalogo", // catalogo | avulso | colar
        ocupado: false,

        /* ------------------------------------------------------------ cliente
         * O cliente é OPCIONAL: quem chega no balcão raramente está cadastrado, e parar
         * a venda para preencher um cadastro completo é o que fazia o vendedor abandonar
         * o sistema. Digitando nome ou telefone, quem já existe aparece na lista; sem
         * nada preenchido, o pedido é de CONSUMIDOR. */
        clienteId: "",
        clienteRotulo: "",

        buscarCliente(termo) {
          if (this.clienteId) return; // já vinculado: não sugere mais
          const caixa = document.getElementById("sug-cliente");
          if (!termo || termo.trim().length < 2) {
            caixa.innerHTML = "";
            return;
          }
          htmx.ajax("GET", `/pedidos/busca-cliente?q=${encodeURIComponent(termo.trim())}`, {
            target: "#sug-cliente",
          });
        },

        vincular(id, nome, telefone) {
          this.clienteId = id;
          this.clienteRotulo = nome;
          document.getElementById("cliente_nome").value = nome;
          document.getElementById("cliente_telefone").value = telefone;
          document.getElementById("sug-cliente").innerHTML = "";
        },

        desvincular() {
          this.clienteId = "";
          this.clienteRotulo = "";
          document.getElementById("sug-cliente").innerHTML = "";
          this.$nextTick(() => document.getElementById("cliente_nome").focus());
        },

        // seleção corrente do catálogo
        sel: null,
        qtdTexto: "1",
        porCaixa: false,
        precoTexto: "",
        descontoItemTexto: "",

        // item avulso
        avulso: { nome: "", codigo: "", detalhe: "", qtd: "1", preco: "" },

        // colagem
        textoColado: "",
        colando: false,

        /* ------------------------------------------------------------ catálogo */
        /* `_busca_resultado.html` marca a linha escolhida com
           `:class="String(variacaoId) === '...'"`. O detalhe guarda isso num campo
           solto; aqui a seleção inteira vive em `sel`, então o id é derivado. Sem este
           getter o fragmento quebraria em toda linha da busca — a mesma busca serve as
           duas telas justamente por causa deste contrato. */
        get variacaoId() {
          return this.sel ? String(this.sel.variacaoId) : "";
        },

        selecionar(el) {
          const d = el.dataset;
          this.sel = {
            variacaoId: inteiro(d.id),
            codigo: d.codigo || "",
            descricao: d.descricao || "",
            cor: d.cor || "",
            img: d.img || "",
            precoPouca: centavos(d.precoPouca),
            precoMuita: centavos(d.precoMuita),
            precoMinimo: centavos(d.precoMinimo),
            qtdCorte: inteiro(d.qtdCorte, 0),
            faixas: faixasDe(d),
            unidadesCaixa: inteiro(d.unidadesCaixa, 0),
            disponivel: d.disponivel || "",
          };
          this.qtdTexto = "1";
          this.porCaixa = false;
          this.precoTexto = "";
          this.descontoItemTexto = "";
        },

        /** Unidades que a linha vai lançar — em caixa, multiplica pelo fator. */
        get qtdEfetiva() {
          const n = Math.max(inteiro(this.qtdTexto, 1), 1);
          if (this.porCaixa && this.sel?.unidadesCaixa > 0) return n * this.sel.unidadesCaixa;
          return n;
        },

        get precoPrevisto() {
          if (!this.sel) return 0;
          if (this.precoTexto.trim() !== "") return centavos(this.precoTexto);
          return precoSugerido(this.sel, this.qtdEfetiva);
        },

        get faixaPrevista() {
          const daTabela = rotuloFaixa(this.sel?.faixas, this.qtdEfetiva);
          if (daTabela) return daTabela;
          if (!this.sel?.qtdCorte) return "varejo";
          return this.qtdEfetiva >= this.sel.qtdCorte ? "atacado" : "varejo";
        },

        get avisoPrevisto() {
          if (!this.sel) return "";
          return avisoDePreco({
            qtd: this.qtdEfetiva,
            precoCentavos: this.precoPrevisto,
            descontoCentavos: centavos(this.descontoItemTexto),
            precoMinimo: this.sel.precoMinimo,
          });
        },

        adicionarDoCatalogo() {
          if (!this.sel) return;
          const editado = this.precoTexto.trim() !== "";
          this.empilhar({
            tipo: "catalogo",
            variacaoId: this.sel.variacaoId,
            codigo: this.sel.codigo,
            descricao: this.sel.descricao,
            cor: this.sel.cor,
            img: this.sel.img,
            qtd: this.qtdEfetiva,
            qtdCaixas: this.porCaixa ? Math.max(inteiro(this.qtdTexto, 1), 1) : null,
            precoCentavos: this.precoPrevisto,
            precoEditado: editado,
            descontoCentavos: centavos(this.descontoItemTexto),
            precoMinimo: this.sel.precoMinimo,
          });
          this.sel = null;
          this.qtdTexto = "1";
          this.porCaixa = false;
          this.precoTexto = "";
          this.descontoItemTexto = "";
          this.$nextTick(() => document.getElementById("busca-item")?.focus());
        },

        /* ------------------------------------------------------------ avulso */
        adicionarAvulso() {
          const nome = this.avulso.nome.trim();
          if (!nome) return;
          this.empilhar({
            tipo: "avulso",
            variacaoId: null,
            codigo: this.avulso.codigo.trim(),
            descricao: nome,
            detalhe: this.avulso.detalhe.trim(),
            cor: "",
            img: "",
            qtd: Math.max(inteiro(this.avulso.qtd, 1), 1),
            qtdCaixas: null,
            precoCentavos: centavos(this.avulso.preco),
            precoEditado: true,
            descontoCentavos: 0,
            precoMinimo: 0,
          });
          this.avulso = { nome: "", codigo: "", detalhe: "", qtd: "1", preco: "" };
        },

        /* ------------------------------------------------------------ colagem */
        async colarItens() {
          if (!this.textoColado.trim() || this.colando) return;
          this.colando = true;
          try {
            const corpo = new FormData();
            corpo.append("texto", this.textoColado);
            const resposta = await fetch("/pedidos/resolver-colagem", {
              method: "POST",
              body: corpo,
            });
            if (!resposta.ok) throw new Error("falha ao resolver a colagem");
            const { linhas } = await resposta.json();
            linhas.forEach((linha) => {
              // O preço da planilha é o documento da venda: entra como digitado, para o
              // servidor não sobrescrever pelo preço de tabela.
              this.empilhar({
                tipo: linha.variacao_id ? "catalogo" : "avulso",
                variacaoId: linha.variacao_id,
                codigo: linha.codigo || "",
                descricao: linha.descricao,
                detalhe: "",
                cor: linha.cor || "",
                img: "",
                qtd: Math.max(linha.qtd, 1),
                qtdCaixas: null,
                precoCentavos: Math.round(Number(linha.preco_unit) * 100),
                precoEditado: true,
                descontoCentavos: 0,
                precoMinimo: 0,
                aviso: linha.aviso || "",
              });
            });
            this.textoColado = "";
          } finally {
            this.colando = false;
          }
        },

        /* ------------------------------------------------------------ carrinho */
        /* Mesma regra do servidor: mesma variação PELO MESMO PREÇO vira uma linha só.
           Preço diferente abre linha nova de propósito — são negociações distintas do
           mesmo item, e juntá-las esconderia isso do faturamento. Item avulso nunca
           funde: sem chave de catálogo, dois nomes iguais podem ser coisas diferentes. */
        empilhar(novo) {
          if (novo.tipo === "catalogo") {
            const igual = this.itens.find(
              (i) =>
                i.tipo === "catalogo" &&
                i.variacaoId === novo.variacaoId &&
                i.precoCentavos === novo.precoCentavos
            );
            if (igual) {
              igual.qtd += novo.qtd;
              igual.descontoCentavos += novo.descontoCentavos;
              if (novo.qtdCaixas) igual.qtdCaixas = (igual.qtdCaixas || 0) + novo.qtdCaixas;
              return;
            }
          }
          this.itens.push({ chave: `l${Date.now()}_${this.itens.length}`, ...novo });
        },

        remover(chave) {
          this.itens = this.itens.filter((i) => i.chave !== chave);
        },

        subtotalItem(item) {
          return subtotalDe(item);
        },

        avisoItem(item) {
          return item.aviso || avisoDePreco(item);
        },

        get subtotal() {
          return this.itens.reduce((s, i) => s + subtotalDe(i), 0);
        },

        get desconto() {
          return Math.min(centavos(this.descontoTexto), this.subtotal);
        },

        get total() {
          return Math.max(0, this.subtotal - this.desconto);
        },

        moeda(cents) {
          return formatarBRL(cents);
        },

        /* ------------------------------------------------------------ submit */
        /* O hidden que vai no POST. Linha não editada manda `preco_unit: null` de
           propósito: assim o servidor resolve o preço pelo catálogo, e a tabela continua
           mandando no preço mesmo que a prévia daqui esteja desatualizada. */
        get itensJson() {
          return JSON.stringify(
            this.itens.map((i) =>
              i.tipo === "avulso"
                ? {
                    tipo: "avulso",
                    nome: i.descricao,
                    codigo: i.codigo || "",
                    detalhe: i.detalhe || "",
                    qtd: i.qtd,
                    preco_unit: paraDecimal(i.precoCentavos),
                    desconto: paraDecimal(i.descontoCentavos),
                  }
                : {
                    tipo: "catalogo",
                    variacao_id: i.variacaoId,
                    qtd: i.qtdCaixas ? null : i.qtd,
                    qtd_caixas: i.qtdCaixas,
                    preco_unit: i.precoEditado ? paraDecimal(i.precoCentavos) : null,
                    desconto: paraDecimal(i.descontoCentavos),
                  }
            )
          );
        },

        /* ------------------------------------------------------------ extras */
        async copiarOrcamento() {
          const texto = textoOrcamento(this.itens, this.subtotal, this.desconto, this.total);
          try {
            await navigator.clipboard.writeText(texto);
            this.avisar("Orçamento copiado.");
          } catch {
            this.avisar("Não consegui copiar — o navegador bloqueou.");
          }
        },

        async gerarResumo() {
          if (!this.itens.length) return;
          const nome = document.getElementById("cliente_nome")?.value.trim();
          this.avisar(
            await ResumoPedido.copiar({
              titulo: "Orçamento",
              cliente: nome || "CONSUMIDOR",
              data: new Date().toLocaleDateString("pt-BR"),
              numero: "RASCUNHO",
              // O módulo do resumo não recalcula dinheiro: o subtotal vai pronto.
              itens: this.itens.map((i) => ({ ...i, subtotalCentavos: subtotalDe(i) })),
              descontoCentavos: this.desconto,
              totalCentavos: this.total,
            })
          );
        },

        recado: "",
        avisar(texto) {
          this.recado = texto;
          setTimeout(() => {
            this.recado = "";
          }, 2500);
        },
      };
    },
  };
})();
