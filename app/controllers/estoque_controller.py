from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError
from app.models.enums import OrigemMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import ProdutoVariacao
from app.repositories.estoque_repo import estoque_repo, movimentacao_repo
from app.schemas.estoque import AjusteCreate, EntradaCreate
from app.services.estoque_service import estoque_service


class EstoqueController:
    def _get_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        variacao = estoque_repo.get_variacao(db, variacao_id)
        if variacao is None:
            raise NaoEncontradoError("Variação de produto não encontrada.")
        return variacao

    def registrar_entrada(
        self, db: Session, dados: EntradaCreate, usuario_id: int
    ) -> ProdutoVariacao:
        variacao = self._get_variacao(db, dados.variacao_id)
        estoque_service.entrada(db, variacao, dados.qtd, usuario_id, origem=OrigemMov.MANUAL)
        return variacao

    def registrar_ajuste(
        self, db: Session, dados: AjusteCreate, usuario_id: int
    ) -> ProdutoVariacao:
        variacao = self._get_variacao(db, dados.variacao_id)
        estoque_service.ajustar(db, variacao, dados.novo_saldo, usuario_id, motivo=dados.motivo)
        return variacao

    def historico(self, db: Session, variacao_id: int) -> list[MovimentacaoEstoque]:
        self._get_variacao(db, variacao_id)
        return movimentacao_repo.historico(db, variacao_id)


estoque_controller = EstoqueController()
