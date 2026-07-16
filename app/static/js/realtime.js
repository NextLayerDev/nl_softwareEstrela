// Cliente de tempo real do Estrela Gestão (offline, sem dependências externas).
//
// O servidor manda eventos JSON pequenos (só ids/primitivos) por um WebSocket. Este cliente:
//   1. mantém a conexão viva (reconexão com backoff, heartbeat, volta ao ficar online);
//   2. atualiza o Alpine.store("rt") — conexão e contador de não-lidos;
//   3. dispara CustomEvent "rt:<tipo>" no window (mesma convenção do $dispatch dos templates);
//   4. mostra toast dos eventos não-silenciosos;
//   5. re-busca fragmentos via htmx nos elementos que se registram (ver abaixo).
//
// O evento NÃO carrega HTML: quem re-renderiza é o endpoint HTTP de sempre, que já aplica o
// RBAC. Assim a regra de "vendedor não vê custo" não é duplicada aqui.
//
// Como uma tela se inscreve — dois jeitos:
//
//   a) Re-disparar um request htmx que já existe (preserva os filtros da busca):
//        <input hx-get="/estoque/busca" hx-trigger="keyup changed delay:250ms, rt-refresh"
//               data-rt-trigger="estoque.movimentado,produto.atualizado">
//
//   b) Buscar um fragmento direto:
//        <tbody id="fila-tbody" data-rt="pedido.confirmado,pedido.cancelado"
//               data-rt-get="/separacao/fila" data-rt-swap="innerHTML">
//
// Em ambos, rajadas viram uma atualização só (debounce) — faturar um pedido de 40 itens
// dispara 40 eventos de estoque e mexe no DOM uma vez.

(() => {
  const DEBOUNCE_MS = 250;
  const HEARTBEAT_MS = 25000;
  const BACKOFF_MAX_MS = 30000;
  const TOAST_MS = 5000;

  let socket = null;
  let tentativa = 0;
  let heartbeat = null;
  let fechandoDeProposito = false;

  const store = () => window.Alpine && window.Alpine.store("rt");

  // ------------------------------------------------------------------ refresh
  const pendentes = new Map(); // elemento -> timer

  // Filtro opcional por campo do payload: data-rt-se="pedido_id=42" só reage aos eventos
  // daquele pedido. Sem isso, um tique em QUALQUER pedido mexeria na tela de todo mundo.
  function combina(el, envelope) {
    const cond = el.dataset.rtSe;
    if (!cond) return true;
    const [campo, valor] = cond.split("=");
    return String((envelope.dados || {})[campo]) === valor;
  }

  function elementosPara(envelope) {
    const alvos = [];
    document.querySelectorAll("[data-rt], [data-rt-trigger]").forEach((el) => {
      const lista = el.dataset.rt || el.dataset.rtTrigger || "";
      const tipos = lista.split(",").map((t) => t.trim()).filter(Boolean);
      const casa = tipos.includes(envelope.tipo) || tipos.includes("*");
      if (casa && combina(el, envelope)) alvos.push(el);
    });
    return alvos;
  }

  function atualizar(el) {
    if (!window.htmx) return;
    if (el.dataset.rtTrigger !== undefined) {
      // O próprio elemento já sabe buscar (com hx-include, filtros etc.).
      window.htmx.trigger(el, "rt-refresh");
      return;
    }
    const url = el.dataset.rtGet;
    if (!url) return;
    window.htmx.ajax("GET", url, {
      target: el.dataset.rtTarget || el,
      swap: el.dataset.rtSwap || "innerHTML",
    });
  }

  function agendar(el) {
    clearTimeout(pendentes.get(el));
    pendentes.set(el, setTimeout(() => {
      pendentes.delete(el);
      atualizar(el);
    }, DEBOUNCE_MS));
  }

  // Troca cirúrgica de UMA linha, quando o evento diz a qual item se refere. Evita refazer a
  // lista inteira (o que perderia a busca do operador e a posição do scroll).
  //   <tbody data-rt-linha="estoque.movimentado" data-rt-linha-key="variacao_id"
  //          data-rt-linha-get="/estoque/linha/" data-rt-linha-alvo="#linha-">
  function trocarLinha(envelope) {
    document.querySelectorAll("[data-rt-linha]").forEach((el) => {
      const tipos = el.dataset.rtLinha.split(",").map((t) => t.trim());
      if (!tipos.includes(envelope.tipo)) return;
      const id = (envelope.dados || {})[el.dataset.rtLinhaKey];
      if (id === undefined || id === null) return;
      // Só troca se a linha estiver na tela; senão não interessa a este operador.
      const alvo = document.querySelector(el.dataset.rtLinhaAlvo + id);
      if (!alvo || !window.htmx) return;
      window.htmx.ajax("GET", el.dataset.rtLinhaGet + id, { target: alvo, swap: "outerHTML" });
    });
  }

  function atualizarTudo() {
    document.querySelectorAll("[data-rt], [data-rt-trigger]").forEach(agendar);
  }

  // ------------------------------------------------------------------- toasts
  function toast(envelope) {
    const area = document.getElementById("rt-toasts");
    if (!area) return;
    const texto = descrever(envelope);
    if (!texto) return;

    const div = document.createElement("div");
    div.className = "rt-toast " + (envelope.tipo === "estoque.alerta_minimo" ? "alerta-erro" : "alerta-ok");
    div.setAttribute("role", "status");
    div.textContent = texto;
    area.appendChild(div);

    const sair = () => {
      div.classList.add("rt-toast-saindo");
      setTimeout(() => div.remove(), 200);
    };
    div.addEventListener("click", sair);
    setTimeout(sair, TOAST_MS);
  }

  // Texto em pt-BR de cada evento. Sem entrada aqui = sem toast (só atualiza a tela).
  function descrever(e) {
    const d = e.dados || {};
    const pedido = d.numero ? "#" + d.numero : "";
    switch (e.tipo) {
      case "pedido.confirmado":
        return `Pedido ${pedido} confirmado${d.cliente ? " — " + d.cliente : ""}.`;
      case "pedido.faturado":
        return `Pedido ${pedido} faturado.`;
      case "pedido.cancelado":
        return `Pedido ${pedido} cancelado.`;
      case "pedido.status_alterado":
        return `Pedido ${pedido}: ${d.status}.`;
      case "separacao.concluida":
        return `Separação do pedido ${pedido} concluída.`;
      case "estoque.alerta_minimo":
        return `Estoque baixo: ${d.codigo || ""}${d.cor ? " (" + d.cor + ")" : ""}.`;
      case "conta.baixada":
        return "Recebimento registrado.";
      case "conta.atrasada":
        return `${d.quantidade} conta(s) marcada(s) como atrasada(s).`;
      case "inventario.aplicado":
        return `Inventário aplicado (${d.itens_ajustados} itens).`;
      case "importacao.concluida":
        return "Importação concluída.";
      default:
        return null;
    }
  }

  // ------------------------------------------------------------------ conexão
  function tratar(envelope) {
    // O servidor só manda sessao.invalidada para o dono da sessão: o token morreu
    // (perfil trocado, usuário desativado ou senha resetada).
    if (envelope.tipo === "sessao.invalidada") {
      window.location = "/login";
      return;
    }

    const s = store();
    if (s) {
      s.ultimo = envelope;
      if (!envelope.silencioso) s.naoLidos += 1;
    }

    window.dispatchEvent(new CustomEvent("rt:" + envelope.tipo, { detail: envelope }));
    if (!envelope.silencioso) toast(envelope);
    trocarLinha(envelope);
    elementosPara(envelope).forEach(agendar);
  }

  function conectar() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;

    const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocolo}//${location.host}/ws`);

    socket.onopen = () => {
      tentativa = 0;
      const s = store();
      if (s) s.conectado = true;
      // Reconectou: pode ter perdido evento enquanto esteve fora. Re-busca tudo que está
      // inscrito para a tela convergir sozinha — é por isso que não guardamos histórico.
      atualizarTudo();
      clearInterval(heartbeat);
      heartbeat = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, HEARTBEAT_MS);
    };

    socket.onmessage = (ev) => {
      try {
        tratar(JSON.parse(ev.data));
      } catch (_) {
        /* frame inválido: ignora */
      }
    };

    socket.onclose = (ev) => {
      clearInterval(heartbeat);
      const s = store();
      if (s) s.conectado = false;
      // 4001 = a sessão foi invalidada pelo servidor. Não adianta reconectar.
      if (ev.code === 4001) {
        window.location = "/login";
        return;
      }
      if (fechandoDeProposito) return;
      tentativa += 1;
      const espera = Math.min(BACKOFF_MAX_MS, 1000 * 2 ** tentativa) + Math.random() * 500;
      setTimeout(conectar, espera);
    };

    socket.onerror = () => socket && socket.close();
  }

  document.addEventListener("alpine:init", () => {
    window.Alpine.store("rt", {
      conectado: false,
      naoLidos: 0,
      ultimo: null,
      limpar() {
        this.naoLidos = 0;
      },
    });
  });

  // Volta a ligar assim que a rede/aba voltar, sem esperar o backoff.
  window.addEventListener("online", conectar);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") conectar();
  });
  window.addEventListener("beforeunload", () => {
    fechandoDeProposito = true;
    if (socket) socket.close();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", conectar);
  } else {
    conectar();
  }
})();
