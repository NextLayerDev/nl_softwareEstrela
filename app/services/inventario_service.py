from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.enums import EstoqueModo, OrigemMov, StatusInventario
from app.models.inventario import Inventario, InventarioItem
from app.models.produto import ProdutoVariacao
from app.repositories.estoque_repo import estoque_repo, inventario_repo
from app.services.estoque_service import estoque_service


class InventarioService:
    """Inventário: funcionário abre/conta, admin aplica (gera ajustes auditáveis)."""

    def abrir(
        self,
        db: Session,
        usuario_id: int,
        descricao: str | None = None,
        variacao_ids: list[int] | None = None,
    ) -> Inventario:
        """Abre um inventário. Sem escopo explícito, inclui todas as variações ativas."""
        inv = Inventario(descricao=(descricao or None), criado_por=usuario_id)
        db.add(inv)
        db.flush()

        if variacao_ids:
            variacoes = [
                v for vid in variacao_ids if (v := db.get(ProdutoVariacao, vid)) is not None
            ]
        else:
            variacoes = estoque_repo.listar_variacoes_ativas(db)

        for v in variacoes:
            db.add(
                InventarioItem(
                    inventario_id=inv.id,
                    produto_variacao_id=v.id,
                    qtd_sistema=v.estoque_fisico,
                    qtd_contada=None,
                )
            )
        db.flush()
        eventos.emitir(
            db,
            "inventario.aberto",
            {"inventario_id": inv.id, "descricao": inv.descricao, "itens": len(variacoes)},
            audiencia=eventos.SEP_AUD,
        )
        return inv

    def registrar_contagem(
        self, db: Session, inventario_id: int, item_id: int, qtd_contada: int
    ) -> InventarioItem:
        """Registra a contagem física de um item do inventário."""
        inv = inventario_repo.get(db, inventario_id)
        if inv is None:
            raise NaoEncontradoError("Inventário não encontrado.")
        if inv.status != StatusInventario.ABERTO:
            raise RegraNegocioError("Este inventário já foi aplicado e não aceita contagens.")
        item = db.get(InventarioItem, item_id)
        if item is None or item.inventario_id != inventario_id:
            raise NaoEncontradoError("Item de inventário não encontrado.")
        if qtd_contada < 0:
            raise RegraNegocioError("A quantidade contada não pode ser negativa.")
        item.qtd_contada = qtd_contada
        db.flush()
        # Contagem em vários tablets ao mesmo tempo: cada um vê o avanço do outro.
        eventos.emitir(
            db,
            "inventario.contagem_registrada",
            {
                "inventario_id": inv.id,
                "item_id": item.id,
                "variacao_id": item.produto_variacao_id,
                "qtd_contada": item.qtd_contada,
            },
            audiencia=eventos.SEP_AUD,
            silencioso=True,
        )
        return item

    def aplicar(self, db: Session, inventario_id: int, usuario_id: int) -> Inventario:
        """Aplica o inventário (admin): para cada item contado, gera ajuste -> EXATO."""
        inv = inventario_repo.get(db, inventario_id)
        if inv is None:
            raise NaoEncontradoError("Inventário não encontrado.")
        if inv.status != StatusInventario.ABERTO:
            raise RegraNegocioError("Este inventário já foi aplicado.")

        itens_contados = [i for i in inv.itens if i.qtd_contada is not None]
        if not itens_contados:
            raise RegraNegocioError("Nenhum item foi contado neste inventário.")

        for item in itens_contados:
            variacao = db.get(ProdutoVariacao, item.produto_variacao_id)
            if variacao is None:
                continue
            estoque_service.ajustar(
                db,
                variacao,
                novo_saldo=item.qtd_contada,
                usuario_id=usuario_id,
                motivo=f"inventário #{inv.id}",
                origem=OrigemMov.INVENTARIO,
                ref_id=inv.id,
            )
            variacao.estoque_modo = EstoqueModo.EXATO

        inv.status = StatusInventario.APLICADO
        inv.aplicado_por = usuario_id
        inv.aplicado_em = datetime.now(UTC)
        db.flush()
        # Cada ajustar() acima já emitiu estoque.movimentado; aqui é o fecho do inventário.
        eventos.emitir(
            db,
            "inventario.aplicado",
            {"inventario_id": inv.id, "itens_ajustados": len(itens_contados)},
            audiencia=eventos.SEP_AUD,
        )
        return inv


inventario_service = InventarioService()
