from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_ASSETS = (_STATIC_DIR / "css" / "output.css", _STATIC_DIR / "js" / "ui.js")

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _asset_version() -> str:
    """Versão dos assets (maior mtime de CSS/JS) para cache-busting.

    Garante que terminais peguem o CSS/JS novo após cada build, sem ficar presos
    em uma versão antiga do navegador.
    """
    mtimes = []
    for caminho in _ASSETS:
        try:
            mtimes.append(int(caminho.stat().st_mtime))
        except OSError:
            continue
    return str(max(mtimes)) if mtimes else "0"


# Disponível em todos os templates como {{ asset_version }}.
templates.env.globals["asset_version"] = _asset_version()


def _moeda(valor: object) -> str:
    """Formata Decimal/float como moeda pt-BR: 1234.5 -> 'R$ 1.234,50'."""
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return "R$ 0,00"
    inteiro = f"{num:,.2f}"
    inteiro = inteiro.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {inteiro}"


templates.env.filters["moeda"] = _moeda
