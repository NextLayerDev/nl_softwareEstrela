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
        aviso: { DEFAULT: "#B8860B", bg: "#FBF1D8" },
        critico: { DEFAULT: "#B3261E", bg: "#FBE4E2" },
        info: { DEFAULT: "#1D4ED8", bg: "#E5EDFB" },
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};
