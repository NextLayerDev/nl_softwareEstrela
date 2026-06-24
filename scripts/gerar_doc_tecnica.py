"""Consolida toda a documentação técnica (CLAUDE.md + docs/*.md) em um único PDF branded.

Converte cada Markdown em HTML (tabelas, código), agrupa em capítulos e renderiza com WeasyPrint.

Uso (WeasyPrint precisa das libs nativas):
    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run python scripts/gerar_doc_tecnica.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import markdown  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
SAIDA = DOCS / "documentacao-tecnica-estrela-gestao.pdf"

# Ordem dos capítulos: (caminho, título exibido)
CAPITULOS = [
    (RAIZ / "CLAUDE.md", "Contexto-raiz e padrões de código"),
    (DOCS / "00-indice-e-guia.md", "Índice e guia do projeto"),
    (DOCS / "01-regras-de-negocio.md", "Regras de negócio"),
    (DOCS / "02-arquitetura-e-fundacao.md", "Arquitetura e fundação"),
    (DOCS / "03-modelo-de-dados-e-migrations.md", "Modelo de dados e migrations"),
    (DOCS / "04-etl-importacao-planilhas.md", "ETL — importação de planilhas"),
    (DOCS / "05-modulo-estoque.md", "Módulo de estoque"),
    (DOCS / "06-modulo-pedidos-e-financeiro.md", "Módulo de pedidos e financeiro"),
    (DOCS / "07-frontend-ui-pwa.md", "Frontend, UI e PWA"),
    (DOCS / "08-infra-deploy-e-go-live.md", "Infraestrutura, deploy e go-live"),
    (DOCS / "dicionario-dados.md", "Dicionário de dados"),
    (DOCS / "runbook-servidor.md", "Runbook do servidor"),
    (DOCS / "go-live-checklist.md", "Checklist de go-live"),
    (DOCS / "disaster-recovery.md", "Disaster recovery"),
    (DOCS / "lgpd-operador.md", "LGPD — operador"),
    (DOCS / "nobreak-nut.md", "Nobreak e NUT"),
]

PALETA = {
    "dourado": "#B98A19",
    "dourado_esc": "#8C660E",
    "creme": "#F6F2E8",
    "sidebar": "#211B0F",
    "borda": "#E4DCCA",
}


def _md_para_html(caminho: Path) -> str:
    md = markdown.Markdown(
        extensions=["extra", "sane_lists", "toc", "admonition"], output_format="html"
    )
    return md.convert(caminho.read_text(encoding="utf-8"))


def construir_html() -> str:
    hoje = date.today().strftime("%d/%m/%Y")

    sumario = "".join(
        f'<li><span class="cap-num">{i + 1:02d}</span> {titulo}</li>'
        for i, (_, titulo) in enumerate(CAPITULOS)
    )

    capitulos_html = []
    for i, (caminho, titulo) in enumerate(CAPITULOS):
        corpo = _md_para_html(caminho)
        capitulos_html.append(
            f"""
            <section class="capitulo">
              <div class="cap-cab">
                <span class="cap-badge">{i + 1:02d}</span>
                <span class="cap-titulo">{titulo}</span>
                <span class="cap-arq">{caminho.name}</span>
              </div>
              <div class="md">{corpo}</div>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
  @page {{
    size: A4; margin: 18mm 16mm 18mm 16mm;
    @bottom-center {{ content: "Estrela Gestão · Documentação técnica"; font-size: 8pt; color: #9a8e72; }}
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
  .sumario ol, .sumario ul {{ list-style: none; padding: 0; }}
  .sumario li {{ padding: 2mm 0; border-bottom: 1px solid {PALETA["borda"]}; font-size: 11pt; }}
  .cap-num {{ display: inline-block; width: 9mm; color: {PALETA["dourado"]}; font-weight: 700; }}
  section.capitulo {{ page-break-before: always; }}
  .cap-cab {{ display: flex; align-items: baseline; gap: 3mm; border-bottom: 2px solid {PALETA["dourado"]};
    padding-bottom: 2.5mm; margin-bottom: 4mm; }}
  .cap-badge {{ background: {PALETA["dourado"]}; color: #fff; font-weight: 700; font-size: 10pt;
    padding: 0.5mm 2.5mm; border-radius: 3px; }}
  .cap-titulo {{ font-size: 15pt; font-weight: 700; color: {PALETA["sidebar"]}; }}
  .cap-arq {{ margin-left: auto; font-size: 8pt; color: #9a8e72; font-family: monospace; }}
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
    <h2>Documentação técnica completa — Sistema de Estoque e Pedidos (Fase 1)</h2>
    <div class="meta">
      Estrela América do Sul &nbsp;·&nbsp; FastAPI + HTMX + PostgreSQL &nbsp;·&nbsp; 100% local e offline<br>
      Consolidado em {hoje} &nbsp;·&nbsp; {len(CAPITULOS)} documentos
    </div>
  </div>

  <div class="sumario">
    <h1>Conteúdo</h1>
    <ul>{sumario}</ul>
  </div>

  {"".join(capitulos_html)}
</body></html>"""


def main() -> None:
    from weasyprint import HTML

    html = construir_html()
    HTML(string=html, base_url=str(RAIZ)).write_pdf(str(SAIDA))
    print(f"PDF gerado: {SAIDA} ({SAIDA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
