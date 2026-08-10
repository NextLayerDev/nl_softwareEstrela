/* Orçamento Inteligente — o parser da linha única e as contas de dinheiro.
 *
 * A tela toda gira em torno de UM campo de texto. A pessoa escreve o que o cliente
 * está levando, do jeito que falaria, e isto aqui traduz:
 *
 *   "caneta 10"          -> 1 x caneta, R$ 10,00
 *   "2 caneta 10"        -> 2 x caneta, R$ 10,00
 *   "2x caneta 10"       -> idem ("x" colado ou solto)
 *   "caneta 10,50"       -> vírgula ou ponto, tanto faz
 *   "caneta"             -> sem preço: usa o preço de tabela do catálogo
 *   "7891234567890"      -> só dígitos (8 a 14): é o leitor de código de barras
 *
 * DINHEIRO É INTEIRO, EM CENTAVOS, do começo ao fim. Ponto flutuante erra centavo
 * (0.1 + 0.2 !== 0.3), e um orçamento que fecha diferente do pedido destrói a
 * confiança na tela inteira.
 *
 * Exposto em window.Orcamento porque o Alpine do template chama estas funções — e a
 * CSP do sistema (script-src 'self') não permite módulo externo nem CDN.
 */
(function () {
  "use strict";

  /** "12,50" / "12.50" / "1.234,50" -> centavos inteiros. null se não for número. */
  function parseMoedaBR(texto) {
    if (typeof texto !== "string") return null;
    const limpo = texto.trim();
    if (!limpo || !/^[\d.,]+$/.test(limpo)) return null;

    let normalizado;
    if (limpo.includes(",")) {
      // pt-BR: o ponto é separador de milhar e a vírgula é o decimal.
      normalizado = limpo.replace(/\./g, "").replace(",", ".");
    } else {
      // Sem vírgula, um ponto só com 1 ou 2 casas é decimal ("12.5"); o resto é milhar.
      const partes = limpo.split(".");
      normalizado =
        partes.length === 2 && partes[1].length <= 2 ? limpo : limpo.replace(/\./g, "");
    }
    const numero = Number(normalizado);
    if (!Number.isFinite(numero) || numero < 0) return null;
    return Math.round(numero * 100);
  }

  function formatarBRL(centavos) {
    const sinal = centavos < 0 ? "-" : "";
    const abs = Math.abs(centavos);
    const inteiro = Math.floor(abs / 100).toString();
    const cents = String(abs % 100).padStart(2, "0");
    const comMilhar = inteiro.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return `${sinal}R$ ${comMilhar},${cents}`;
  }

  function ehNumero(token) {
    return /^[\d.,]+$/.test(token) && parseMoedaBR(token) !== null;
  }

  /* Quantidade é sempre pequena e inteira. Não aceita ponto de propósito: em pt-BR
     "1.234" é mil duzentos e trinta e quatro, o que nunca é quantidade de balcão —
     esse token pertence ao nome ou ao preço. */
  function comoQtd(token) {
    const limpo = token.replace(/x$/i, "");
    if (!/^\d{1,4}$/.test(limpo)) return null;
    const n = Number(limpo);
    return n > 0 ? n : null;
  }

  /**
   * @returns {{tipo:'vazio'}
   *          |{tipo:'codigo', codigo:string}
   *          |{tipo:'item', nome:string, qtd:number, precoCentavos:number}
   *          |{tipo:'parcial', nome:string, qtd:number}}
   */
  function parseLinha(entrada) {
    const texto = (entrada || "").trim().replace(/\s+/g, " ");
    if (!texto) return { tipo: "vazio" };

    // Leitor de código de barras: o scanner "digita" os dígitos e manda Enter.
    // EAN-8, EAN-13, UPC, DUN-14 — tudo entre 8 e 14 dígitos, e mais nada.
    if (/^\d{8,14}$/.test(texto)) return { tipo: "codigo", codigo: texto };

    let tokens = texto.split(" ");

    // --- quantidade (primeiro token) ---
    // Só é quantidade se sobrar NOME depois dela. Assim "caneta 2 10" continua sendo
    // o produto "caneta 2" a R$ 10,00, e não dez unidades de coisa nenhuma.
    let qtd = 1;
    const candidata = comoQtd(tokens[0]);
    if (candidata !== null && tokens.length >= 2) {
      if (tokens.slice(1).some((t) => !ehNumero(t))) {
        qtd = candidata;
        tokens = tokens.slice(1);
      }
    }
    // "2 x caneta 10" — o "x" pode vir separado.
    if (tokens.length >= 2 && /^x$/i.test(tokens[0])) tokens = tokens.slice(1);

    // --- preço (último token) ---
    let precoCentavos = null;
    const ultimo = tokens[tokens.length - 1];
    if (tokens.length >= 2 && ehNumero(ultimo)) {
      precoCentavos = parseMoedaBR(ultimo);
      if (precoCentavos !== null) tokens = tokens.slice(0, -1);
    }

    const nome = tokens.join(" ").trim();
    if (!nome) return { tipo: "vazio" };
    if (precoCentavos === null) return { tipo: "parcial", nome, qtd };
    return { tipo: "item", nome, qtd, precoCentavos };
  }

  /** O trecho que vai para o autocomplete: de "2 caneta 10", busca "caneta". */
  function termoBusca(entrada) {
    const r = parseLinha(entrada);
    return r.tipo === "item" || r.tipo === "parcial" ? r.nome : "";
  }

  /**
   * Traduz a linha em português, para a pessoa conferir ANTES do Enter.
   *
   * Existe por uma armadilha real: "10 caneta" significa DEZ canetas, não "dez reais
   * de caneta". Quem pensa em reais digita exatamente isso e erraria o orçamento sem
   * nenhum aviso.
   */
  function espelho(entrada) {
    const r = parseLinha(entrada);
    switch (r.tipo) {
      case "vazio":
        return { texto: "escreva o nome e o preço — exemplo: caneta 10 — e aperte Enter", forte: false };
      case "codigo":
        return { texto: `código ${r.codigo} — procurando no catálogo`, forte: true };
      case "parcial":
        return { texto: `${r.qtd} × ${r.nome} · preço vem da tabela`, forte: true };
      default: {
        const total = r.qtd * r.precoCentavos;
        return {
          texto:
            r.qtd === 1
              ? `1 × ${r.nome} · ${formatarBRL(r.precoCentavos)}`
              : `${r.qtd} × ${r.nome} · ${formatarBRL(r.precoCentavos)} cada · soma ${formatarBRL(total)}`,
          forte: true,
        };
      }
    }
  }

  /* Chave de agrupamento do carrinho: "Caneta AZUL" e "caneta azul" são a mesma linha.
     Tira acento (NFD + faixa de diacríticos combinantes) para "cartão" casar com "cartao". */
  function normalizarNome(nome) {
    return (nome || "")
      .trim()
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  window.Orcamento = {
    parseLinha,
    termoBusca,
    espelho,
    parseMoedaBR,
    formatarBRL,
    normalizarNome,
  };
})();
