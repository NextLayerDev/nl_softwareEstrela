from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.imagens import remover_imagem
from app.models.enums import EstoqueModo, OrigemMov
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao
from app.repositories.produto_repo import produto_repo
from app.schemas.produto import ProdutoCreate, ProdutoUpdate, VariacaoCreate
from app.services.estoque_service import estoque_service


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
        if not dados.variacoes:
            # Produto "sem cor" ganha uma variação padrão: imagem e saldo sempre
            # vivem em ProdutoVariacao, então todo produto precisa de ao menos uma.
            produto.variacoes.append(ProdutoVariacao(cor="", estoque_modo=EstoqueModo.APROXIMADO))
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

    def adicionar_variacao(
        self, db: Session, produto_id: int, dados: VariacaoCreate, usuario_id: int
    ) -> ProdutoVariacao:
        """Adiciona uma cor nova a um produto já salvo (resolve "cor nova do mesmo produto").

        O saldo nunca é setado direto: se vier com estoque inicial, vira uma
        movimentação de ENTRADA (append-only) via estoque_service.
        """
        produto = self.obter(db, produto_id)
        if not produto.ativo:
            raise RegraNegocioError("Não é possível adicionar cores a um produto inativo.")
        cor = (dados.cor or "").strip()
        if produto_repo.variacao_por_cor(db, produto_id, cor) is not None:
            rotulo = f" ({cor})" if cor else " padrão"
            raise RegraNegocioError(f"Já existe a cor{rotulo} neste produto.")
        variacao = ProdutoVariacao(
            cor=cor,
            estoque_modo=dados.estoque_modo,
            estoque_fisico=0,  # saldo entra via movimentação, nunca direto
            estoque_reservado=0,
            rotulo_aprox=dados.rotulo_aprox,
            estoque_minimo=dados.estoque_minimo,
            ativo=True,
        )
        produto.variacoes.append(variacao)
        db.flush()
        if dados.estoque_fisico and dados.estoque_fisico > 0:
            estoque_service.entrada(
                db, variacao, dados.estoque_fisico, usuario_id, origem=OrigemMov.MANUAL
            )
        return variacao

    def remover_variacao(self, db: Session, variacao_id: int) -> tuple[ProdutoVariacao, str]:
        """Remove (ou inativa) uma cor de um produto.

        Regras:
        - saldo != 0 -> bloqueia (zerar no módulo de Estoque antes).
        - com histórico (movimentações/pedidos/inventário) -> inativa (preserva append-only).
        - limpa -> hard-delete + remove a foto do MinIO.
        Retorna (variacao, acao) onde acao é "inativada" ou "deletada".
        """
        variacao = self.obter_variacao(db, variacao_id)
        if variacao.estoque_fisico != 0 or variacao.estoque_reservado != 0:
            raise RegraNegocioError(
                "Não é possível remover uma cor com saldo em estoque. "
                "Zere o saldo no módulo de Estoque antes de remover."
            )
        if produto_repo.variacao_tem_historico(db, variacao_id):
            variacao.ativo = False
            db.flush()
            return variacao, "inativada"
        if variacao.imagem_url:
            remover_imagem(variacao.imagem_url)
        db.delete(variacao)
        db.flush()
        return variacao, "deletada"


produto_service = ProdutoService()
