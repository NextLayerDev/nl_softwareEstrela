// Helpers de UI do Estrela Gestão (offline, sem dependências externas).
// Registra um componente Alpine reutilizável de modal acessível:
//  - role=dialog/aria-modal no markup
//  - focus-trap manual (Tab/Shift+Tab) — Alpine aqui é core-only (sem plugin focus)
//  - return-focus para o elemento que abriu o modal
//  - Esc fecha
//
// Uso no template:
//   <div x-data="modal()" @keydown.escape.window="fechar()">
//     <button @click="abrir(123, $event)">Resetar</button>
//     <template x-teleport="body">
//       <div x-show="aberto" x-cloak class="modal-overlay" @click="fechar()">
//         <div class="modal-painel" role="dialog" aria-modal="true" aria-labelledby="t"
//              x-ref="painel" @click.stop @keydown="trapTab($event)">
//           ... usa `dados` para o payload (ex.: id) ...
//         </div>
//       </div>
//     </template>
//   </div>

document.addEventListener("alpine:init", () => {
  window.Alpine.data("modal", () => ({
    aberto: false,
    dados: null,
    _gatilho: null,

    abrir(dados = null, evento = null) {
      this.dados = dados;
      this._gatilho = (evento && evento.currentTarget) || document.activeElement;
      this.aberto = true;
      this.$nextTick(() => {
        const focaveis = this._focaveis();
        (focaveis[0] || this.$refs.painel)?.focus();
      });
    },

    fechar() {
      if (!this.aberto) return;
      this.aberto = false;
      const gatilho = this._gatilho;
      this.dados = null;
      this.$nextTick(() => {
        if (gatilho && typeof gatilho.focus === "function") gatilho.focus();
      });
    },

    _focaveis() {
      const painel = this.$refs.painel;
      if (!painel) return [];
      const seletor =
        'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])';
      return Array.from(painel.querySelectorAll(seletor)).filter(
        (el) => el.offsetParent !== null
      );
    },

    trapTab(evento) {
      if (evento.key !== "Tab") return;
      const focaveis = this._focaveis();
      if (focaveis.length === 0) return;
      const primeiro = focaveis[0];
      const ultimo = focaveis[focaveis.length - 1];
      if (evento.shiftKey && document.activeElement === primeiro) {
        evento.preventDefault();
        ultimo.focus();
      } else if (!evento.shiftKey && document.activeElement === ultimo) {
        evento.preventDefault();
        primeiro.focus();
      }
    },
  }));
});
