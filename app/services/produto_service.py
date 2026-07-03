from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.repositories.produto_repo import produto_repo
from app.schemas.produto import ProdutoCreate, ProdutoUpdate


class ProdutoService:
    def listar(
        self, db: Session, termo: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Produto]:
        # Busca por termo usa pg_trgm (topo dos matches); navegação sem termo é paginada
        # (scroll infinito) via limit/offset.
        if termo:
            return produto_repo.busca_rapida(db, termo)
        return produto_repo.listar(db, limit=limit, offset=offset)

    def obter(self, db: Session, produto_id: int) -> Produto:
        produto = produto_repo.get(db, produto_id)
        if produto is None:
            raise NaoEncontradoError("Produto não encontrado.")
        return produto

    def criar(self, db: Session, dados: ProdutoCreate) -> Produto:
        if produto_repo.get_by_codigo(db, dados.codigo) is not None:
            raise RegraNegocioError(f"Já existe um produto com o código {dados.codigo}.")
        produto = Produto(
            codigo=dados.codigo,
            descricao=dados.descricao,
            categoria_id=dados.categoria_id,
            unidades_por_caixa=dados.unidades_por_caixa,
            localizacao=dados.localizacao,
            preco_pouca_qtd=dados.preco_pouca_qtd,
            preco_muita_qtd=dados.preco_muita_qtd,
            preco_promocional=dados.preco_promocional,
            qtd_corte_atacado=dados.qtd_corte_atacado,
            preco_custo=dados.preco_custo,
            observacao=dados.observacao,
            ativo=dados.ativo,
            publicar_catalogo=dados.publicar_catalogo,
        )
        for v in dados.variacoes:
            produto.variacoes.append(
                ProdutoVariacao(
                    cor=v.cor,
                    estoque_modo=v.estoque_modo,
                    estoque_fisico=v.estoque_fisico,
                    rotulo_aprox=v.rotulo_aprox,
                    estoque_minimo=v.estoque_minimo,
                    ativo=v.ativo,
                )
            )
        for c in dados.codigos_alt:
            produto.codigos_alt.append(
                ProdutoCodigoAlt(codigo_alt=c.codigo_alt, fornecedor_id=c.fornecedor_id)
            )
        return produto_repo.add(db, produto)

    def atualizar(self, db: Session, produto_id: int, dados: ProdutoUpdate) -> Produto:
        produto = self.obter(db, produto_id)
        for campo, valor in dados.model_dump(exclude_unset=True).items():
            setattr(produto, campo, valor)
        db.flush()
        return produto

    def inativar(self, db: Session, produto_id: int) -> Produto:
        """Soft-delete: produtos nunca são removidos fisicamente (histórico/estoque)."""
        produto = self.obter(db, produto_id)
        produto.ativo = False
        db.flush()
        return produto

    def obter_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        variacao = produto_repo.get_variacao(db, variacao_id)
        if variacao is None:
            raise NaoEncontradoError("Variação não encontrada.")
        return variacao

    def renomear_variacao(self, db: Session, variacao_id: int, cor: str) -> ProdutoVariacao:
        variacao = self.obter_variacao(db, variacao_id)
        variacao.cor = cor
        db.flush()
        return variacao


produto_service = ProdutoService()
