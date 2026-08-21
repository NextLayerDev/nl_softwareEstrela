/* Resumo do pedido em imagem — a "planilha amarela" desenhada no <canvas>.
 *
 * Vive num arquivo só porque DUAS telas mostram o mesmo resumo: o carrinho em
 * /pedidos/novo (antes de existir pedido) e o card "Resumo do pedido" no detalhe
 * (depois que ele existe). Duplicar o desenho era garantir que um dia os dois
 * divergissem — e o resumo é o que o cliente recebe no WhatsApp.
 *
 * Contrato de `dados`:
 *   { titulo, cliente, data, numero,
 *     itens: [{ codigo, descricao, qtd, precoCentavos, subtotalCentavos }],
 *     descontoCentavos, totalCentavos }
 * O subtotal vem PRONTO de quem chama: a tela nova calcula do carrinho, o detalhe
 * recebe do servidor. Este módulo não recalcula dinheiro — só desenha.
 *
 * Exposto em window.ResumoPedido porque a CSP (script-src 'self') não permite módulo
 * externo, e o Alpine dos templates chama daqui.
 */
(function () {
  "use strict";

  const { formatarBRL } = window.Orcamento;


  /* ---------------------------------------------------------------- resumo em PNG
   * Desenhado à mão no <canvas> em vez de "printar" o DOM com uma biblioteca: a CSP é
   * `default-src 'self'` e o sistema roda offline num mini-PC, então não há npm nem CDN
   * de onde tirar um html-to-image. Canvas é API do navegador — custa zero dependência.
   */
  const RESUMO = {
    largura: 900,
    margem: 24,
    alturaLinha: 34,
    alturaCabecalho: 96,
    fundo: "#F6F2E8", // creme da marca
    faixa: "#B98A19", // dourado da marca
    texto: "#211B0F",
    grade: "#D9CFB4",
  };

  function desenharResumo(canvas, dados) {
    const { largura, margem, alturaLinha, alturaCabecalho } = RESUMO;
    // +2 linhas: uma para o total e uma de respiro. Com +3 sobrava um vazio do
    // tamanho de duas linhas embaixo de um orçamento de item único.
    const linhasExtras = dados.descontoCentavos > 0 ? 3 : 2;
    const altura = alturaCabecalho + (dados.itens.length + linhasExtras) * alturaLinha + margem;

    // devicePixelRatio: sem isto o PNG sai borrado em tela retina e ilegível no zoom
    // de quem recebe no WhatsApp.
    const escala = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = largura * escala;
    canvas.height = altura * escala;
    const ctx = canvas.getContext("2d");
    ctx.scale(escala, escala);

    ctx.fillStyle = RESUMO.fundo;
    ctx.fillRect(0, 0, largura, altura);

    ctx.fillStyle = RESUMO.faixa;
    ctx.fillRect(0, 0, largura, 8);

    ctx.fillStyle = RESUMO.texto;
    ctx.font = "bold 26px Inter, system-ui, sans-serif";
    ctx.fillText(dados.titulo, margem, 48);
    ctx.font = "15px Inter, system-ui, sans-serif";
    // O número do pedido entra na linha do cliente: é por ele que o cliente cobra
    // ("o 265652 saiu?"), e sem ele o PNG de dois pedidos parecidos é indistinguível.
    const meta = [dados.cliente, dados.data, dados.numero].filter(Boolean).join("  ·  ");
    ctx.fillText(meta, margem, 74);

    const colunas = [
      { rotulo: "Código", x: margem, alinhar: "left" },
      { rotulo: "Descrição", x: margem + 120, alinhar: "left" },
      { rotulo: "Qtd", x: largura - margem - 300, alinhar: "right" },
      { rotulo: "Unit.", x: largura - margem - 170, alinhar: "right" },
      { rotulo: "Subtotal", x: largura - margem, alinhar: "right" },
    ];

    let y = alturaCabecalho;
    ctx.font = "bold 14px Inter, system-ui, sans-serif";
    colunas.forEach((c) => {
      ctx.textAlign = c.alinhar;
      ctx.fillText(c.rotulo, c.x, y);
    });
    ctx.textAlign = "left";

    ctx.strokeStyle = RESUMO.grade;
    ctx.lineWidth = 1;
    y += 10;
    ctx.beginPath();
    ctx.moveTo(margem, y);
    ctx.lineTo(largura - margem, y);
    ctx.stroke();

    ctx.font = "14px Inter, system-ui, sans-serif";
    dados.itens.forEach((item) => {
      y += alturaLinha;
      const valores = [
        item.codigo || "—",
        item.descricao,
        String(item.qtd),
        formatarBRL(item.precoCentavos),
        formatarBRL(item.subtotalCentavos),
      ];
      colunas.forEach((c, i) => {
        ctx.textAlign = c.alinhar;
        let texto = valores[i];
        // A descrição é a única coluna que pode estourar; corta com reticência em vez
        // de invadir a coluna de quantidade.
        if (i === 1) {
          const limite = largura - margem - 320 - c.x;
          while (ctx.measureText(texto).width > limite && texto.length > 4) {
            texto = `${texto.slice(0, -2)}…`;
          }
        }
        ctx.fillText(texto, c.x, y);
      });
      ctx.textAlign = "left";
      ctx.strokeStyle = RESUMO.grade;
      ctx.beginPath();
      ctx.moveTo(margem, y + 10);
      ctx.lineTo(largura - margem, y + 10);
      ctx.stroke();
    });

    y += alturaLinha + 12;
    ctx.textAlign = "right";
    if (dados.descontoCentavos > 0) {
      ctx.font = "14px Inter, system-ui, sans-serif";
      ctx.fillText(`Desconto: ${formatarBRL(dados.descontoCentavos)}`, largura - margem, y);
      y += alturaLinha - 8;
    }
    ctx.font = "bold 20px Inter, system-ui, sans-serif";
    ctx.fillText(`Total: ${formatarBRL(dados.totalCentavos)}`, largura - margem, y);
    ctx.textAlign = "left";
  }

  async function gerarPng(dados) {
    // Sem isto a Inter local ainda não está pronta e o canvas desenha com a fonte de
    // fallback — o PNG sai com outra cara da tela.
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const canvas = document.createElement("canvas");
    desenharResumo(canvas, dados);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  }

  async function copiar(dados) {
    const blob = await gerarPng(dados);
    if (!blob) return "";
    // Copiar direto para a área de transferência é o caminho curto para o WhatsApp;
    // onde o navegador não deixa, cai no download.
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      return "Resumo copiado como imagem.";
    } catch {
      baixarBlob(blob);
      return "Resumo baixado.";
    }
  }

  async function baixar(dados) {
    const blob = await gerarPng(dados);
    if (!blob) return "";
    baixarBlob(blob);
    return "Resumo baixado.";
  }

  function baixarBlob(blob) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "resumo-pedido.png";
    a.click();
    URL.revokeObjectURL(url);
  }

  window.ResumoPedido = { desenhar: desenharResumo, gerarPng, copiar, baixar };
})();
