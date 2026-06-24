"""Monta o PDF de demonstração do fluxo real do Estrela Gestão a partir das capturas.

Reúne capa + visão geral + uma seção por tela (captura real + "como funciona") + diagrama de
fluxo do pedido, renderizado com WeasyPrint usando a paleta da marca.

Uso (WeasyPrint precisa das libs nativas):
    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run python scripts/gerar_pdf.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DOCS = Path(__file__).resolve().parent.parent / "docs"
CAPTURAS = DOCS / "capturas"
SAIDA = DOCS / "fluxo-sistema-estrela-gestao.pdf"

# (slug do PNG, título, perfis, descrição "como funciona")
SECOES = [
    (
        "01-login",
        "Login e modo aplicativo (PWA)",
        ["todos"],
        "Porta de entrada do sistema. A autenticação usa senha com Argon2 e emite um token JWT "
        "guardado em cookie httpOnly. O sistema é instalável como aplicativo (PWA): nos terminais ele "
        "abre em tela cheia, com ícone próprio e funcionamento offline, sem cara de site.",
    ),
    (
        "02-painel",
        "Painel (dashboard)",
        ["admin", "financeiro", "vendedor"],
        "Visão do dia: vendas faturadas, pedidos, alertas de estoque (itens no/abaixo do mínimo ou "
        "marcados como pouco/acabou), contas a receber, gráfico dos últimos 7 dias e os últimos pedidos. "
        "Os indicadores se ajustam ao perfil — o vendedor vê apenas os próprios números.",
    ),
    (
        "03-estoque",
        "Estoque",
        ["todos"],
        "Posição de estoque com busca instantânea (HTMX + índice trigram do PostgreSQL) por código, "
        "descrição ou cor, agora com a foto de cada cor na primeira coluna. Cada cor é uma variação com saldo "
        "próprio. O saldo aparece como número quando é EXATO ou como selo visual (muito / pouco / tem / "
        "acabou) quando ainda é APROXIMADO.",
    ),
    (
        "04-localizacao-tablet",
        "Localização (tablet do estoque)",
        ["todos"],
        "Tela pensada para um tablet no estoque: busca dominante e cartões grandes com a FOTO da cor e a "
        "LOCALIZAÇÃO em destaque (andar / lado / sala). Com a imagem, o funcionário reconhece o modelo certo "
        "(ex.: entre vários modelos de caneta plástica) e o acha entre os 10 andares — atendendo ao pedido do "
        "cliente. É consulta pura, em alto contraste e legível em pé.",
    ),
    (
        "05-produtos",
        "Produtos",
        ["admin (edita)", "demais (veem)"],
        "Catálogo com código (SKU), códigos alternativos da fábrica/caixa, categorias, variações por cor, "
        "preços de atacado (pouca e muita quantidade, mais promocional) e localização. O preço de custo é "
        "oculto para Vendedor e Funcionário, tanto na tela quanto na resposta da API.",
    ),
    (
        "06-produto-novo",
        "Cadastro de produto",
        ["admin"],
        "Formulário de criação/edição com gestão de variações (cores) e de códigos alternativos na mesma "
        "tela. A faixa de atacado (a partir de qual quantidade aplicar o preço de volume) é configurável "
        "por produto.",
    ),
    (
        "06b-produto-imagens",
        "Imagens por cor (cadastro)",
        ["admin"],
        "Na edição do produto, o admin envia uma foto para cada cor (variação). É daqui que vêm as imagens "
        "exibidas no estoque, na tela de Localização (tablet), no pedido e na separação — o cliente sobe as "
        "fotos reais dos seus modelos por aqui.",
    ),
    (
        "07-clientes",
        "Clientes",
        ["admin", "vendedor"],
        "Cadastro de clientes com condição de pagamento padrão (à vista, prazo em dias ou parcelado), que "
        "alimenta a geração das contas a receber no faturamento.",
    ),
    (
        "08-usuarios",
        "Usuários",
        ["admin"],
        "Gestão de usuários e reset de senha, restrita ao Admin. Cada usuário tem um dos quatro perfis, que "
        "define exatamente o que ele acessa.",
    ),
    (
        "09-pedidos",
        "Pedidos",
        ["admin", "vendedor"],
        "Lista de pedidos com seus estados. O vendedor enxerga apenas os próprios pedidos; o Admin, todos. "
        "Daqui se cria um novo pedido ou se abre um existente.",
    ),
    (
        "10-pedido-novo",
        "Novo pedido",
        ["admin", "vendedor"],
        "Montagem do pedido com saldo em tempo real ao escolher a variação, sugestão de preço pela faixa de "
        "quantidade (preço editável), venda por caixa (converte caixas em unidades) e desconto com limite "
        "por perfil — acima do limite, exige aprovação do Admin.",
    ),
    (
        "11-pedido-detalhe",
        "Pedido faturado",
        ["admin", "vendedor"],
        "Detalhe do pedido com itens, cores, quantidades, preços e total. Ao confirmar, o sistema reserva o "
        "estoque; ao faturar, dá baixa definitiva e gera as contas a receber. Há impressão em A4.",
    ),
    (
        "12b-separacao-fila",
        "Fila de separação",
        ["admin", "funcionário"],
        "Pedidos confirmados entram na fila em ordem de chegada, prontos para o funcionário separar.",
    ),
    (
        "12-separacao-conferencia",
        "Conferência da separação",
        ["admin", "funcionário"],
        "Conferência item a item com checkbox (HTMX) e barra de progresso, mostrando a localização de cada "
        "produto. Pode imprimir a lista de separação para levar ao estoque.",
    ),
    (
        "13-financeiro",
        "Financeiro — contas a receber",
        ["admin", "financeiro"],
        "Contas a receber geradas no faturamento conforme a condição de pagamento do cliente. Permite dar "
        "baixa do recebimento (Pix, boleto ou dinheiro); um job diário marca os títulos vencidos como "
        "atrasados.",
    ),
    (
        "14-relatorios",
        "Relatórios",
        ["conforme perfil"],
        "Central de relatórios. Vendas ficam disponíveis para Admin, Financeiro e Vendedor (este, só os "
        "próprios); margem e valorização, apenas para Admin e Financeiro.",
    ),
    (
        "15-relatorio-vendas",
        "Relatório de vendas",
        ["admin", "financeiro", "vendedor"],
        "Vendas por período/cliente/produto, com exportação em XLSX (openpyxl).",
    ),
    (
        "16-relatorio-abc",
        "Curva ABC",
        ["admin", "financeiro", "vendedor"],
        "Classificação ABC dos produtos por valor de venda acumulado (A ≈ 80%, B ≈ 15%, C ≈ 5%), para "
        "priorizar os itens que mais pesam no faturamento.",
    ),
    (
        "17-relatorio-valorizacao",
        "Valorização de estoque",
        ["admin", "financeiro"],
        "Valor imobilizado em estoque (saldo físico × custo) por produto. Por envolver custo, fica restrito "
        "a Admin e Financeiro.",
    ),
    (
        "18-importacao",
        "Importação de planilha",
        ["admin"],
        "Importa a planilha CONTROLE.xlsx (ou planilhas de entrada recorrente): upload, prévia com validação "
        "e relatório de inconsistências, e só então a carga — que é idempotente (rodar de novo não duplica).",
    ),
    (
        "19-menu-vendedor",
        "Perfis e permissões (RBAC)",
        ["exemplo: vendedor"],
        "Cada perfil vê só o que pode usar. Nesta tela, o mesmo sistema visto pelo Vendedor: sem Financeiro, "
        "sem Usuários e sem Importação. Toda rota é protegida no servidor — não é só esconder o menu.",
    ),
]

PALETA = {
    "dourado": "#B98A19",
    "dourado_esc": "#8C660E",
    "creme": "#F6F2E8",
    "sidebar": "#211B0F",
    "borda": "#E4DCCA",
}


def _img(slug: str) -> str:
    return f"capturas/{slug}.png"


def _secao_html(slug, titulo, perfis, descricao, numero) -> str:
    badges = "".join(f'<span class="badge">{p}</span>' for p in perfis)
    return f"""
    <section class="tela">
      <div class="tela-cab">
        <div class="num">{numero:02d}</div>
        <div>
          <h2>{titulo}</h2>
          <div class="badges">{badges}</div>
        </div>
      </div>
      <img src="{_img(slug)}" alt="{titulo}" />
      <p class="como"><strong>Como funciona —</strong> {descricao}</p>
    </section>
    """


def construir_html() -> str:
    hoje = date.today().strftime("%d/%m/%Y")
    secoes = "".join(_secao_html(s[0], s[1], s[2], s[3], i + 1) for i, s in enumerate(SECOES))

    diagrama = """
    <section class="tela">
      <div class="tela-cab"><div class="num">★</div><div><h2>Fluxo do pedido e regra de estoque</h2></div></div>
      <div class="fluxo">
        <span class="passo">Rascunho</span><span class="seta">→</span>
        <span class="passo">Confirmado</span><span class="seta">→</span>
        <span class="passo">Separação</span><span class="seta">→</span>
        <span class="passo">Faturado</span><span class="seta">→</span>
        <span class="passo">Entregue</span>
        <span class="seta">/</span><span class="passo cancel">Cancelado</span>
      </div>
      <ul class="legenda">
        <li><b>Confirmado</b> — reserva o estoque das variações do pedido.</li>
        <li><b>Separação</b> — funcionário confere item a item, com a localização.</li>
        <li><b>Faturado</b> — baixa definitiva do estoque e geração das contas a receber.</li>
        <li><b>Cancelado</b> — estorna as reservas (volta o saldo).</li>
      </ul>
      <p class="como"><strong>Estoque append-only —</strong> o saldo nunca é editado direto: toda mudança
      (entrada, reserva, baixa, ajuste, estorno) gera uma movimentação imutável com data, usuário, origem e o
      saldo resultante. Isso garante rastreabilidade total e atomicidade — ou a operação inteira é aplicada, ou
      nada é.</p>
      <p class="como"><strong>Local e offline —</strong> tudo roda em um servidor na empresa, acessado por até
      10 terminais pela rede interna, em modo aplicativo (PWA). Não depende de internet para operar; a
      manutenção remota e o backup offsite são as únicas conexões externas.</p>
    </section>
    """

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
  @page {{
    size: A4; margin: 16mm 14mm 18mm 14mm;
    @bottom-center {{ content: "Estrela Gestão · Sistema de Estoque e Pedidos"; font-size: 8pt; color: #9a8e72; }}
    @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 8pt; color: #9a8e72; }}
  }}
  @page capa {{ margin: 0; @bottom-center {{ content: none; }} @bottom-right {{ content: none; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #2b2410; margin: 0; }}
  .capa {{
    page: capa; height: 297mm; background: {PALETA["sidebar"]}; color: {PALETA["creme"]};
    display: flex; flex-direction: column; justify-content: center; padding: 40mm;
  }}
  .capa .estrela {{ color: {PALETA["dourado"]}; font-size: 52pt; }}
  .capa h1 {{ font-size: 34pt; margin: 6mm 0 2mm; }}
  .capa h2 {{ font-size: 15pt; font-weight: 400; color: #cdbf9d; margin: 0 0 14mm; }}
  .capa .meta {{ font-size: 10pt; color: #cdbf9d; border-top: 1px solid #4a3f28; padding-top: 6mm; }}
  .intro {{ padding: 6mm 0; page-break-after: always; }}
  .intro h1 {{ color: {PALETA["dourado_esc"]}; font-size: 20pt; }}
  .intro p {{ font-size: 10.5pt; line-height: 1.55; }}
  .perfis {{ display: flex; gap: 4mm; margin-top: 6mm; flex-wrap: wrap; }}
  .perfil-card {{ flex: 1; min-width: 38mm; border: 1px solid {PALETA["borda"]}; border-radius: 3mm;
    padding: 4mm; background: {PALETA["creme"]}; }}
  .perfil-card b {{ color: {PALETA["dourado_esc"]}; }}
  section.tela {{ page-break-before: always; }}
  .tela-cab {{ display: flex; align-items: center; gap: 4mm; border-bottom: 2px solid {PALETA["dourado"]};
    padding-bottom: 3mm; margin-bottom: 4mm; }}
  .tela-cab .num {{ background: {PALETA["dourado"]}; color: white; font-weight: 700; font-size: 13pt;
    width: 11mm; height: 11mm; border-radius: 50%; display: flex; align-items: center; justify-content: center; }}
  .tela-cab h2 {{ font-size: 16pt; margin: 0; color: {PALETA["sidebar"]}; }}
  .badges {{ margin-top: 1.5mm; }}
  .badge {{ display: inline-block; background: {PALETA["creme"]}; border: 1px solid {PALETA["borda"]};
    color: {PALETA["dourado_esc"]}; font-size: 7.5pt; padding: 0.6mm 2.2mm; border-radius: 8px; margin-right: 1.5mm; }}
  section.tela img {{ max-width: 100%; max-height: 185mm; width: auto; display: block;
    border: 1px solid {PALETA["borda"]}; border-radius: 2mm; }}
  .como {{ font-size: 10pt; line-height: 1.5; margin-top: 4mm; color: #43381c;
    background: {PALETA["creme"]}; border-left: 3px solid {PALETA["dourado"]}; padding: 3mm 4mm; border-radius: 0 2mm 2mm 0; }}
  .fluxo {{ display: flex; align-items: center; gap: 2mm; flex-wrap: wrap; margin: 8mm 0; }}
  .passo {{ background: {PALETA["dourado"]}; color: white; padding: 4mm; border-radius: 2mm; font-weight: 600;
    font-size: 10pt; text-align: center; }}
  .passo small {{ font-weight: 400; font-size: 8pt; opacity: .9; }}
  .passo.cancel {{ background: #B3261E; }}
  .seta {{ color: {PALETA["dourado_esc"]}; font-size: 16pt; }}
  .legenda {{ font-size: 9.5pt; line-height: 1.6; color: #43381c; margin: 0 0 8mm; padding-left: 5mm; }}
</style></head>
<body>
  <div class="capa">
    <div class="estrela">★</div>
    <h1>Estrela Gestão</h1>
    <h2>Sistema de Estoque e Pedidos — demonstração do fluxo real (Fase 1)</h2>
    <div class="meta">
      Estrela América do Sul &nbsp;·&nbsp; 100% local e offline &nbsp;·&nbsp; FastAPI + HTMX + PostgreSQL<br>
      Documento gerado a partir das telas reais do sistema em {hoje}
    </div>
  </div>

  <div class="intro">
    <h1>Visão geral</h1>
    <p>O Estrela Gestão é um sistema <b>100% local e offline</b> de controle de estoque e pedidos, instalado
    em um servidor na empresa e acessado por até 10 terminais pela rede interna, em <b>modo aplicativo</b>
    (PWA). Cobre o ciclo completo: catálogo e estoque (com saldo por cor e localização física), pedidos com
    reserva e baixa de estoque, separação, faturamento, contas a receber e relatórios — além da importação da
    planilha atual da empresa.</p>
    <p>Todas as imagens deste documento são <b>capturas reais</b> do sistema em funcionamento, com dados de
    demonstração. Cada tela respeita o perfil de quem acessa:</p>
    <div class="perfis">
      <div class="perfil-card"><b>Admin</b><br>acesso total: cadastros, importação, usuários, relatórios.</div>
      <div class="perfil-card"><b>Vendedor</b><br>pedidos próprios, clientes e consulta de estoque.</div>
      <div class="perfil-card"><b>Financeiro</b><br>contas a receber, baixas e relatórios financeiros.</div>
      <div class="perfil-card"><b>Funcionário</b><br>entradas, separação e consulta de localização.</div>
    </div>
  </div>

  {secoes}
  {diagrama}
</body></html>"""


def main() -> None:
    from weasyprint import HTML

    html = construir_html()
    HTML(string=html, base_url=str(DOCS)).write_pdf(str(SAIDA))
    print(f"PDF gerado: {SAIDA} ({SAIDA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
