from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_ASSETS = (
    _STATIC_DIR / "css" / "output.css",
    _STATIC_DIR / "js" / "ui.js",
    _STATIC_DIR / "js" / "realtime.js",
    _STATIC_DIR / "js" / "pedido_novo.js",
    _STATIC_DIR / "js" / "resumo_pedido.js",
)

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


def _faixas_json(faixas: object) -> str:
    """Faixas para o `data-faixas` do fragmento de busca: [[1,"10.00"],[10,"8.00"]].

    Pares e não objetos porque o fragmento renderiza uma dezena de linhas a cada tecla
    digitada — `[[1,"10.00"]]` é metade dos bytes de `[{"min_qtd":1,"preco":"10.00"}]`.

    Preço como STRING: quem converte no navegador é o `parseMoedaBR`, que devolve
    centavos inteiros. Number() em dinheiro é como se perde centavo.
    """
    pares = []
    for faixa in faixas or []:
        min_qtd = getattr(faixa, "min_qtd", None)
        preco = getattr(faixa, "preco", None)
        if min_qtd is None or preco is None:
            continue
        pares.append([int(min_qtd), f"{preco:.2f}"])
    return json.dumps(pares, separators=(",", ":"))


templates.env.filters["faixas_json"] = _faixas_json


def _desde(valor: object) -> str:
    """Tempo relativo curto em pt-BR: 'há 8 min'. Acima de 30 dias, vira data.

    Datas ingênuas são tratadas como UTC: todo timestamp do banco é timestamptz, mas um
    naive vindo de outro caminho não pode explodir a tela de manutenção — é justamente a
    tela que precisa continuar de pé quando o resto quebrou.
    """
    if not isinstance(valor, datetime):
        return "—"
    quando = valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    segundos = (datetime.now(UTC) - quando).total_seconds()
    if segundos < 0:
        return "agora mesmo"
    if segundos < 60:
        return "agora mesmo"
    if segundos < 3600:
        return f"há {int(segundos // 60)} min"
    if segundos < 86400:
        return f"há {int(segundos // 3600)} h"
    dias = int(segundos // 86400)
    if dias == 1:
        return "ontem"
    if dias <= 30:
        return f"há {dias} dias"
    return quando.strftime("%d/%m/%Y")


templates.env.filters["desde"] = _desde


def _foto_url(chave: object) -> str:
    """Devolve o caminho da rota de foto (mesma origem) guardado em ``imagem_url``.

    Import tardio para evitar carregar PIL quando o módulo de templates sobe.
    """
    from app.core.imagens import url_para_exibicao

    return url_para_exibicao(chave if isinstance(chave, str) else None)


# Disponível em todos os templates como {{ foto_url(variacao.imagem_url) }}.
templates.env.globals["foto_url"] = _foto_url


def _e_admin(perfil: object) -> bool:
    """`{% if e_admin(user.perfil) %}` — poder de admin, com o dev incluído.

    Existe para tirar dos templates listas literais tipo `["admin", "dev"]`, que
    silenciosamente deixam de bater quando um perfil muda de nome ou some.
    """
    from app.models.enums import e_admin

    return e_admin(perfil) if isinstance(perfil, str) else False


templates.env.globals["e_admin"] = _e_admin
