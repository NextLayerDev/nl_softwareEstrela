from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.enums import EstoqueModo, RotuloAprox
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.produto import (
    CodigoAltCreate,
    ProdutoCreate,
    ProdutoUpdate,
    VariacaoCorUpdate,
    VariacaoCreate,
)
from app.services.produto_service import produto_service


def _dec(valor: str | None) -> Decimal:
    if valor is None or str(valor).strip() == "":
        return Decimal("0")
    bruto = str(valor)
    bruto = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
    try:
        return Decimal(bruto)
    except InvalidOperation as exc:
        raise RegraNegocioError(f"Valor numérico inválido: {valor}") from exc


def _dec_opt(valor: str | None) -> Decimal | None:
    if valor is None or str(valor).strip() == "":
        return None
    return _dec(valor)


def _int_opt(valor: str | None) -> int | None:
    if valor is None or str(valor).strip() == "":
        return None
    return int(valor)


class ProdutoController:
    def listar(self, db: Session, termo: str | None) -> list[Produto]:
        return produto_service.listar(db, termo)

    def obter(self, db: Session, produto_id: int) -> Produto:
        return produto_service.obter(db, produto_id)

    def criar(self, db: Session, form: dict) -> Produto:
        dados = ProdutoCreate(
            codigo=form.get("codigo", ""),
            descricao=form.get("descricao", ""),
            categoria_id=_int_opt(form.get("categoria_id")),
            unidades_por_caixa=_int_opt(form.get("unidades_por_caixa")),
            localizacao=(form.get("localizacao") or None),
            preco_pouca_qtd=_dec(form.get("preco_pouca_qtd")),
            preco_muita_qtd=_dec(form.get("preco_muita_qtd")),
            preco_promocional=_dec_opt(form.get("preco_promocional")),
            qtd_corte_atacado=_int_opt(form.get("qtd_corte_atacado")),
            preco_custo=_dec(form.get("preco_custo")),
            observacao=(form.get("observacao") or None),
            ativo=form.get("ativo") in ("on", "true", "1", True),
            publicar_catalogo=form.get("publicar_catalogo") in ("on", "true", "1", True),
            variacoes=self._parse_variacoes(form),
            codigos_alt=self._parse_codigos(form),
        )
        return produto_service.criar(db, dados)

    def atualizar(self, db: Session, produto_id: int, form: dict) -> Produto:
        dados = ProdutoUpdate(
            descricao=form.get("descricao") or None,
            categoria_id=_int_opt(form.get("categoria_id")),
            unidades_por_caixa=_int_opt(form.get("unidades_por_caixa")),
            localizacao=(form.get("localizacao") or None),
            preco_pouca_qtd=_dec(form.get("preco_pouca_qtd")),
            preco_muita_qtd=_dec(form.get("preco_muita_qtd")),
            preco_promocional=_dec_opt(form.get("preco_promocional")),
            qtd_corte_atacado=_int_opt(form.get("qtd_corte_atacado")),
            preco_custo=_dec(form.get("preco_custo")),
            observacao=(form.get("observacao") or None),
            ativo=form.get("ativo") in ("on", "true", "1", True),
            publicar_catalogo=form.get("publicar_catalogo") in ("on", "true", "1", True),
        )
        return produto_service.atualizar(db, produto_id, dados)

    def inativar(self, db: Session, produto_id: int) -> Produto:
        return produto_service.inativar(db, produto_id)

    def renomear_variacao(self, db: Session, variacao_id: int, form: dict) -> ProdutoVariacao:
        dados = VariacaoCorUpdate(cor=form.get("cor", ""))
        return produto_service.renomear_variacao(db, variacao_id, dados.cor)

    @staticmethod
    def _parse_variacoes(form: dict) -> list[VariacaoCreate]:
        """Lê listas paralelas var_cor[], var_modo[], var_estoque[], var_minimo[], var_rotulo[]."""
        cores = form.get("var_cor") if isinstance(form.get("var_cor"), list) else None
        variacoes: list[VariacaoCreate] = []
        if cores is None:
            return variacoes
        modos = form.get("var_modo") or []
        estoques = form.get("var_estoque") or []
        minimos = form.get("var_minimo") or []
        rotulos = form.get("var_rotulo") or []
        for i, cor in enumerate(cores):
            modo = (modos[i] if i < len(modos) else "APROXIMADO") or "APROXIMADO"
            rotulo = rotulos[i] if i < len(rotulos) else ""
            variacoes.append(
                VariacaoCreate(
                    cor=cor or "",
                    estoque_modo=EstoqueModo(modo),
                    estoque_fisico=int(estoques[i]) if i < len(estoques) and estoques[i] else 0,
                    estoque_minimo=int(minimos[i]) if i < len(minimos) and minimos[i] else 0,
                    rotulo_aprox=RotuloAprox(rotulo) if rotulo else None,
                )
            )
        return variacoes

    @staticmethod
    def _parse_codigos(form: dict) -> list[CodigoAltCreate]:
        codigos = form.get("cod_alt") if isinstance(form.get("cod_alt"), list) else None
        resultado: list[CodigoAltCreate] = []
        if codigos is None:
            return resultado
        for c in codigos:
            if c and str(c).strip():
                resultado.append(CodigoAltCreate(codigo_alt=str(c).strip()))
        return resultado


produto_controller = ProdutoController()
