from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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
