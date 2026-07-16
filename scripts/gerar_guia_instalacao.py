"""Gera o PDF do Guia de Instalação Local em Servidor (Estrela Gestão).

Renderiza `docs/guia-instalacao-servidor.md` em PDF com WeasyPrint, reusando a identidade visual
da marca (paleta dourada, capa ★, @page A4 com rodapé paginado) — mesmo padrão dos outros PDFs
do projeto (`gerar_doc_tecnica.py`, `gerar_pdf.py`).

Uso (WeasyPrint precisa das libs nativas — no macOS):
    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run python scripts/gerar_guia_instalacao.py
No Linux (ou dentro de um container com WeasyPrint) não é preciso o DYLD_...
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import markdown  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
FONTE = DOCS / "guia-instalacao-servidor.md"
SAIDA = DOCS / "guia-instalacao-servidor.pdf"

# Capítulos do sumário (número, título) — espelham as seções do Markdown.
SUMARIO = [
    ("Visão geral e requisitos"),
    ("Preparar o servidor (Ubuntu/Debian)"),
    ("Baixar o sistema"),
    ("Configurar os segredos (.env.prod)"),
    ("Subir a stack"),
    ("Configurar o acesso na rede (DNS + HTTPS)"),
    ("Criar o admin e trocar senhas"),
    ("Importar os dados reais (ETL)"),
    ("Agendar backup diário"),
    ("Manutenção remota (Tailscale)"),
    ("Configurar os 10 terminais (PWA)"),
    ("Verificação final (go-live)"),
    ("Operação do dia a dia (resumo)"),
    ("Troubleshooting"),
]

PALETA = {
    "dourado": "#B98A19",
    "dourado_esc": "#8C660E",
    "creme": "#F6F2E8",
    "sidebar": "#211B0F",
    "borda": "#E4DCCA",
}


def construir_html() -> str:
    hoje = date.today().strftime("%d/%m/%Y")

    md = markdown.Markdown(
        extensions=["extra", "sane_lists", "toc", "admonition"], output_format="html"
    )
    corpo = md.convert(FONTE.read_text(encoding="utf-8"))

    sumario = "".join(
        f'<li><span class="cap-num">{i + 1:02d}</span> {titulo}</li>'
        for i, titulo in enumerate(SUMARIO, start=1)
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
  @page {{
    size: A4; margin: 18mm 16mm 18mm 16mm;
    @bottom-center {{ content: "Estrela Gestão · Guia de instalação local em servidor"; font-size: 8pt; color: #9a8e72; }}
    @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 8pt; color: #9a8e72; }}
  }}
  @page capa {{ margin: 0; @bottom-center {{ content: none; }} @bottom-right {{ content: none; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #2b2410; font-size: 10pt;
    line-height: 1.5; margin: 0; }}
  .capa {{ page: capa; height: 297mm; background: {PALETA["sidebar"]}; color: {PALETA["creme"]};
    display: flex; flex-direction: column; justify-content: center; padding: 40mm; }}
  .capa .estrela {{ color: {PALETA["dourado"]}; font-size: 52pt; }}
  .capa h1 {{ font-size: 32pt; margin: 6mm 0 2mm; }}
  .capa h2 {{ font-size: 14pt; font-weight: 400; color: #cdbf9d; margin: 0 0 14mm; }}
  .capa .meta {{ font-size: 10pt; color: #cdbf9d; border-top: 1px solid #4a3f28; padding-top: 6mm; }}
  .sumario {{ page-break-after: always; padding-top: 4mm; }}
  .sumario h1 {{ color: {PALETA["dourado_esc"]}; font-size: 20pt; border: 0; }}
  .sumario ul {{ list-style: none; padding: 0; }}
  .sumario li {{ padding: 2mm 0; border-bottom: 1px solid {PALETA["borda"]}; font-size: 11pt; }}
  .cap-num {{ display: inline-block; width: 9mm; color: {PALETA["dourado"]}; font-weight: 700; }}
  .md h1 {{ font-size: 15pt; color: {PALETA["dourado_esc"]}; margin: 6mm 0 2mm;
    border-bottom: 1px solid {PALETA["borda"]}; padding-bottom: 1mm; }}
  .md h2 {{ font-size: 12.5pt; color: {PALETA["sidebar"]}; margin: 5mm 0 2mm; }}
  .md h3 {{ font-size: 11pt; color: {PALETA["dourado_esc"]}; margin: 4mm 0 1.5mm; }}
  .md p, .md li {{ font-size: 9.5pt; }}
  .md code {{ font-family: "SF Mono", Menlo, monospace; font-size: 8.5pt;
    background: {PALETA["creme"]}; padding: 0.3mm 1mm; border-radius: 2px; }}
  .md pre {{ background: {PALETA["sidebar"]}; color: #ece3cc; padding: 3mm 4mm; border-radius: 2mm;
    overflow-x: auto; font-size: 8pt; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }}
  .md pre code {{ background: transparent; color: inherit; padding: 0; }}
  .md table {{ border-collapse: collapse; width: 100%; margin: 3mm 0; font-size: 8.5pt; }}
  .md th {{ background: {PALETA["creme"]}; border: 1px solid {PALETA["borda"]}; padding: 1.5mm 2mm;
    text-align: left; color: {PALETA["sidebar"]}; }}
  .md td {{ border: 1px solid {PALETA["borda"]}; padding: 1.5mm 2mm; vertical-align: top; }}
  .md blockquote {{ border-left: 3px solid {PALETA["dourado"]}; background: {PALETA["creme"]};
    margin: 3mm 0; padding: 2mm 4mm; color: #43381c; }}
  .md a {{ color: {PALETA["dourado_esc"]}; }}
  .md ul, .md ol {{ padding-left: 6mm; }}
</style></head>
<body>
  <div class="capa">
    <div class="estrela">★</div>
    <h1>Estrela Gestão</h1>
    <h2>Guia de instalação local em servidor (Docker) — Ubuntu/Debian</h2>
    <div class="meta">
      PostgreSQL 16 + FastAPI/Gunicorn + Caddy &nbsp;·&nbsp; 100% local e offline<br>
      Gerado em {hoje}
    </div>
  </div>

  <div class="sumario">
    <h1>Conteúdo</h1>
    <ul>{sumario}</ul>
  </div>

  <div class="md">{corpo}</div>
</body></html>"""


def main() -> None:
    from weasyprint import HTML

    html = construir_html()
    HTML(string=html, base_url=str(RAIZ)).write_pdf(str(SAIDA))
    print(f"PDF gerado: {SAIDA} ({SAIDA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
