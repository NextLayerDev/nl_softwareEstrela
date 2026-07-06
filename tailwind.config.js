/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/web/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        // Paleta da marca Estrela (dourado / creme / marrom escuro)
        gold: {
          50: "#FBF8F3",
          100: "#F6F2E8", // creme (fundo)
          200: "#ECE3CC",
          300: "#DEC98F",
          400: "#CDA94B",
          500: "#B98A19", // dourado primário
          600: "#A4790F",
          700: "#8C660E",
          800: "#6E4F0C",
          900: "#4D370A",
        },
        sidebar: {
          DEFAULT: "#211B0F", // marrom quase preto
          texto: "#D8CBAC", // creme do texto da sidebar
          hover: "#3A2F1C",
        },
        marca: {
          fundo: "#F6F2E8",
          borda: "#E4DCCA",
        },
        ok: { DEFAULT: "#1F7A40", bg: "#E3F2E8" },
        aviso: { DEFAULT: "#8A5B00", bg: "#FBF1D8" },
        critico: { DEFAULT: "#B3261E", bg: "#FBE4E2" },
        info: { DEFAULT: "#1D4ED8", bg: "#E5EDFB" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(33 27 15 / 0.04), 0 1px 3px 0 rgb(33 27 15 / 0.08)",
        "card-hover": "0 4px 12px -2px rgb(33 27 15 / 0.12)",
        drawer: "0 0 24px -4px rgb(33 27 15 / 0.30)",
      },
      borderRadius: {
        xl2: "0.875rem",
      },
      transitionTimingFunction: {
        suave: "cubic-bezier(0.2, 0, 0, 1)",
      },
    },
  },
  plugins: [],
};
