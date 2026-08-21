from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.errors import RegraNegocioError
from app.models.enums import EstoqueModo, RotuloAprox
from app.models.produto import Produto, ProdutoVariacao
from app.schemas.produto import (
    CodigoAltCreate,
    EspecificacaoCreate,
    FaixaPrecoCreate,
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
    def listar(
        self,
        db: Session,
        termo: str | None,
        limit: int = 50,
        offset: int = 0,
        categoria_id: int | None = None,
    ) -> list[Produto]:
        return produto_service.listar(
            db, termo, limit=limit, offset=offset, categoria_id=categoria_id
        )

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
            preco_minimo=_dec(form.get("preco_minimo")),
            observacao=(form.get("observacao") or None),
            ativo=form.get("ativo") in ("on", "true", "1", True),
            publicar_catalogo=form.get("publicar_catalogo") in ("on", "true", "1", True),
            variacoes=self._parse_variacoes(form),
            codigos_alt=self._parse_codigos(form),
            faixas=self._parse_faixas(form) or [],
            especificacoes=self._parse_especificacoes(form) or [],
        )
        return produto_service.criar(db, dados)

    def atualizar(self, db: Session, produto_id: int, form: dict) -> Produto:
        campos: dict = {
            "codigo": form.get("codigo") or None,
            "descricao": form.get("descricao") or None,
            "categoria_id": _int_opt(form.get("categoria_id")),
            "unidades_por_caixa": _int_opt(form.get("unidades_por_caixa")),
            "localizacao": (form.get("localizacao") or None),
            "preco_pouca_qtd": _dec(form.get("preco_pouca_qtd")),
            "preco_muita_qtd": _dec(form.get("preco_muita_qtd")),
            "preco_promocional": _dec_opt(form.get("preco_promocional")),
            "qtd_corte_atacado": _int_opt(form.get("qtd_corte_atacado")),
            "observacao": (form.get("observacao") or None),
            "ativo": form.get("ativo") in ("on", "true", "1", True),
            "publicar_catalogo": form.get("publicar_catalogo") in ("on", "true", "1", True),
            "codigos_alt": self._parse_codigos(form),
        }
        # `preco_custo` e `preco_minimo` só aparecem no formulário do admin. Passá-los
        # sempre significaria zerar os dois toda vez que um vendedor salvasse o produto
        # — `_dec` devolve 0 para campo ausente, e o service usa exclude_unset, então
        # a única forma de dizer "não mexe" é não mandar a chave.
        for campo in ("preco_custo", "preco_minimo"):
            if campo in form:
                campos[campo] = _dec(form.get(campo))
        # Mesma armadilha das duas linhas acima, e pior: lista VAZIA é um pedido
        # legítimo ("apague a tabela"), então não dá para inferir pela ausência das
        # linhas. Sem o sentinela do formulário, salvar de qualquer tela sem o editor
        # apagaria a tabela de preço inteira — em silêncio, e com os testes passando.
        if form.get("tem_editor_faixas"):
            campos["faixas"] = self._parse_faixas(form) or []
        if form.get("tem_editor_especificacoes"):
            campos["especificacoes"] = self._parse_especificacoes(form) or []
        return produto_service.atualizar(db, produto_id, ProdutoUpdate(**campos))

    def inativar(self, db: Session, produto_id: int) -> Produto:
        return produto_service.inativar(db, produto_id)

    def reativar(self, db: Session, produto_id: int) -> Produto:
        return produto_service.reativar(db, produto_id)

    def renomear_variacao(self, db: Session, variacao_id: int, form: dict) -> ProdutoVariacao:
        dados = VariacaoCorUpdate(cor=form.get("cor", ""))
        return produto_service.renomear_variacao(db, variacao_id, dados.cor)

    def criar_variacao(
        self, db: Session, produto_id: int, form: dict, usuario_id: int
    ) -> ProdutoVariacao:
        """Adiciona uma cor nova a um produto existente, a partir do form HTMX."""
        dados = VariacaoCreate(
            cor=(form.get("cor") or "").strip(),
            estoque_modo=EstoqueModo(form.get("modo") or "APROXIMADO"),
            estoque_fisico=int(form.get("estoque")) if form.get("estoque") else 0,
            estoque_minimo=int(form.get("minimo")) if form.get("minimo") else 0,
            rotulo_aprox=RotuloAprox(form.get("rotulo")) if form.get("rotulo") else None,
        )
        return produto_service.adicionar_variacao(db, produto_id, dados, usuario_id)

    def remover_variacao(self, db: Session, variacao_id: int) -> tuple[ProdutoVariacao, str]:
        return produto_service.remover_variacao(db, variacao_id)

    def reativar_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        return produto_service.reativar_variacao(db, variacao_id)

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
    def _parse_especificacoes(form: dict) -> list[EspecificacaoCreate] | None:
        """Lê `espec_rotulo[]` / `espec_valor[]`. `None` = o formulário não trouxe o editor.

        A ORDEM das listas paralelas é a ordem da ficha — as setas ↑↓ da tela só
        reordenam o array do Alpine, e o que chega aqui já vem na posição final.
        """
        rotulos = form.get("espec_rotulo")
        valores = form.get("espec_valor")
        if not isinstance(rotulos, list) or not isinstance(valores, list):
            return None
        saida: list[EspecificacaoCreate] = []
        for rotulo, valor in zip(rotulos, valores, strict=False):
            rotulo, valor = str(rotulo).strip(), str(valor).strip()
            if not rotulo or not valor:
                continue  # linha que a pessoa abriu e desistiu
            saida.append(EspecificacaoCreate(rotulo=rotulo[:40], valor=valor[:120]))
        return saida

    @staticmethod
    def _parse_faixas(form: dict) -> list[FaixaPrecoCreate] | None:
        """Lê as listas paralelas `faixa_min_qtd[]` / `faixa_preco[]` do formulário.

        Devolve `None` quando o formulário não trouxe o editor — quem chama usa isso
        para distinguir "não mexe" de "apague a tabela". Linha em branco é descartada:
        o editor deixa o vendedor adicionar uma linha e desistir dela.
        """
        qtds = form.get("faixa_min_qtd")
        precos = form.get("faixa_preco")
        if not isinstance(qtds, list) or not isinstance(precos, list):
            return None
        saida: list[FaixaPrecoCreate] = []
        for qtd_bruta, preco_bruto in zip(qtds, precos, strict=False):
            if not str(qtd_bruta).strip():
                continue
            min_qtd = _int_opt(qtd_bruta)
            if min_qtd is None or min_qtd < 1:
                continue
            saida.append(FaixaPrecoCreate(min_qtd=min_qtd, preco=_dec(preco_bruto)))
        return saida

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
