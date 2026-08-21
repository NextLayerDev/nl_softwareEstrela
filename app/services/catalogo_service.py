"""Monta o Catálogo Inteligente — o documento A4 que a loja imprime ou manda em PDF.

NÃO faz commit: só lê. O preço de cada cartão sai do `pedido_service.sugerir_preco` para
uma unidade, então tabela de faixas e varejo/atacado são respeitados por construção — e
continuarão sendo quando a regra de preço mudar de novo.
"""

from __future__ import annotations

import base64
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.imagens import miniatura_catalogo
from app.models.produto import Produto, ProdutoVariacao
from app.repositories.produto_repo import produto_repo
from app.schemas.catalogo import CatalogoCartao, CatalogoDoc, CatalogoSecao
from app.services.pedido_service import pedido_service

# Um catálogo de 900 produtos não é documento que alguém imprime. O teto protege a
# memória (as fotos vão embutidas) e a paciência de quem recebe o PDF.
MAX_PRODUTOS = 400

# Teto de cores mostradas por cartão; o resto vira "+N".
MAX_CORES = 6

_SEM_CATEGORIA = "Outros produtos"


class CatalogoService:
    def montar(self, db: Session, categoria_id: int | None = None) -> CatalogoDoc:
        produtos = produto_repo.catalogo_publicado(
            db, categoria_id=categoria_id, limit=MAX_PRODUTOS + 1
        )
        truncado = len(produtos) > MAX_PRODUTOS
        produtos = produtos[:MAX_PRODUTOS]

        por_categoria: dict[str, list[CatalogoCartao]] = {}
        total = 0
        for produto in produtos:
            cartao = self._cartao(produto)
            if cartao is None:
                continue
            nome = produto.categoria.nome if produto.categoria else _SEM_CATEGORIA
            por_categoria.setdefault(nome, []).append(cartao)
            total += 1

        # "Outros produtos" sempre por último: é o balaio, não uma categoria de verdade.
        nomes = sorted(n for n in por_categoria if n != _SEM_CATEGORIA)
        if _SEM_CATEGORIA in por_categoria:
            nomes.append(_SEM_CATEGORIA)

        return CatalogoDoc(
            secoes=[CatalogoSecao(nome=n, cartoes=por_categoria[n]) for n in nomes],
            total=total,
            truncado=truncado,
        )

    def _cartao(self, produto: Produto) -> CatalogoCartao | None:
        ativas = [v for v in produto.variacoes if v.ativo]
        if not ativas:
            return None  # sem cor ativa não há o que oferecer

        cores = [v.cor for v in ativas if v.cor]
        return CatalogoCartao(
            codigo=produto.codigo,
            descricao=produto.descricao,
            descricao_curta=descricao_curta(produto.observacao or produto.descricao),
            foto=self._foto(ativas),
            cores=cores[:MAX_CORES],
            cores_ocultas=max(0, len(cores) - MAX_CORES),
            # Uma unidade: é o preço que a pessoa vê ao folhear. Passando pelo service,
            # a tabela de faixas manda aqui igual manda no pedido.
            preco=pedido_service.sugerir_preco(produto, 1).preco_sugerido or Decimal("0"),
        )

    def _foto(self, variacoes: list[ProdutoVariacao]) -> str | None:
        """A primeira foto disponível, embutida como `data:` URI e já reduzida."""
        for v in variacoes:
            if not v.imagem_dados:
                continue
            try:
                miniatura = miniatura_catalogo(v.imagem_dados)
            except Exception:  # noqa: BLE001 - foto quebrada não derruba o catálogo
                continue
            return "data:image/jpeg;base64," + base64.b64encode(miniatura).decode()
        return None


_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s")


def descricao_curta(texto: str, limite: int = 120) -> str:
    """Primeira frase, ou o texto cortado no limite sem partir palavra."""
    limpo = " ".join((texto or "").split())
    if not limpo:
        return ""
    primeira = _FIM_DE_FRASE.split(limpo, maxsplit=1)[0]
    # Frase muito curta não descreve nada: nesse caso vale mais o texto inteiro.
    escolhido = limpo if len(primeira) < 40 else primeira
    if len(escolhido) <= limite:
        return escolhido
    cortado = escolhido[:limite].rsplit(" ", 1)[0]
    return f"{cortado}…"


catalogo_service = CatalogoService()
