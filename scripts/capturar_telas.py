"""Captura screenshots reais do sistema rodando, com Playwright + Chromium.

Pré-requisito: o app precisa estar no ar (uvicorn) em BASE_URL (default http://127.0.0.1:8099)
e o banco populado (seed + scripts/demo_dados.py).

Uso:
    BASE_URL=http://127.0.0.1:8099 uv run python scripts/capturar_telas.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.pedido import Pedido  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
OUT = Path(__file__).resolve().parent.parent / "docs" / "capturas"
OUT.mkdir(parents=True, exist_ok=True)

DESKTOP = {"width": 1440, "height": 900}
TABLET = {"width": 1024, "height": 1366}


def _ids():
    from app.models.produto import Produto, ProdutoVariacao

    db = SessionLocal()
    try:
        fat = db.scalar(select(Pedido).where(Pedido.observacao == "DEMO faturado"))
        sep = db.scalar(select(Pedido).where(Pedido.observacao == "DEMO separacao"))
        # produto (caneta) com variações que já têm imagem, para a tela de "Imagens por cor"
        prod = db.scalar(
            select(Produto.id)
            .join(Produto.variacoes)
            .where(Produto.descricao.ilike("%CANETA%"), ProdutoVariacao.imagem_url.isnot(None))
            .limit(1)
        )
        return (fat.id if fat else None), (sep.id if sep else None), prod
    finally:
        db.close()


def login(page, email: str, senha: str) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("input[name=email]", email)
    page.fill("input[name=senha]", senha)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def shot(page, slug: str, url: str, full: bool = True) -> None:
    page.goto(f"{BASE}{url}", wait_until="networkidle")
    page.wait_for_timeout(350)  # deixa HTMX/Alpine assentarem
    page.screenshot(path=str(OUT / f"{slug}.png"), full_page=full)
    print(f"  capturado {slug}.png")


def main() -> None:
    fat_id, sep_id, prod_id = _ids()
    with sync_playwright() as p:
        navegador = p.chromium.launch()

        # ---- Tela de login (sem sessão) ----
        ctx = navegador.new_context(viewport=DESKTOP, device_scale_factor=2)
        page = ctx.new_page()
        shot(page, "01-login", "/login", full=True)
        ctx.close()

        # ---- Admin (enxerga tudo) ----
        ctx = navegador.new_context(viewport=DESKTOP, device_scale_factor=2)
        page = ctx.new_page()
        login(page, "admin@estrela.local", "estrela123")

        # (slug, url, full_page): listas usam viewport; formulários/relatórios, página inteira
        capturas = [
            ("02-painel", "/", False),
            ("03-estoque", "/estoque", False),
            ("05-produtos", "/produtos", False),
            ("06-produto-novo", "/produtos/novo", True),
            ("07-clientes", "/clientes", False),
            ("08-usuarios", "/usuarios", False),
            ("09-pedidos", "/pedidos", False),
            ("10-pedido-novo", "/pedidos/novo", True),
            ("13-financeiro", "/financeiro", False),
            ("14-relatorios", "/relatorios", True),
            ("15-relatorio-vendas", "/relatorios/vendas", True),
            ("16-relatorio-abc", "/relatorios/abc", True),
            ("17-relatorio-valorizacao", "/relatorios/valorizacao", True),
            ("18-importacao", "/importacao", True),
        ]
        for slug, url, full in capturas:
            shot(page, slug, url, full)

        if prod_id:
            shot(page, "06b-produto-imagens", f"/produtos/{prod_id}/editar", True)
        if fat_id:
            shot(page, "11-pedido-detalhe", f"/pedidos/{fat_id}", True)
        if sep_id:
            shot(page, "12-separacao-conferencia", f"/separacao/{sep_id}", True)
        shot(page, "12b-separacao-fila", "/separacao", False)

        # Localização em viewport de tablet, com busca preenchida (resultados reais)
        page.set_viewport_size(TABLET)
        shot(page, "04-localizacao-tablet", "/estoque/localizacao?q=caneta", True)
        ctx.close()

        # ---- Vendedor (menu lateral reduzido pelo RBAC) ----
        ctx = navegador.new_context(viewport=DESKTOP, device_scale_factor=2)
        page = ctx.new_page()
        login(page, "vendedor@estrela.local", "estrela123")
        shot(page, "19-menu-vendedor", "/", False)
        ctx.close()

        navegador.close()
    print(f"\nCapturas salvas em {OUT}")


if __name__ == "__main__":
    main()
