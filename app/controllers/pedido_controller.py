from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import NaoEncontradoError
from app.models.enums import OrigemPedido, StatusPedido
from app.models.pedido import Pedido, PedidoItem
from app.models.usuario import Usuario
from app.repositories.pedido_repo import pedido_repo
from app.schemas.colagem import LinhaResolvida, ResultadoColagem, ResultadoLote
from app.schemas.pedido import (
    ItemAdicionar,
    ItemAvulsoAdicionar,
    PedidoCompletoCreate,
    PedidoCreate,
)
from app.services.colagem_service import colagem_service
from app.services.pedido_service import pedido_service


def _enum_ou_nada(enum_cls, valor: str):
    """Converte a string da query em enum. Vazio ou inválido -> None (sem filtro)."""
    try:
        return enum_cls(valor) if valor else None
    except ValueError:
        return None


class PedidoController:
    """Valida entrada e chama o service."""

    # ----------------------------------------------------------- carregamento
    def _carregar_para_usuario(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        """Carrega o pedido para quem está logado.

        Não há mais filtro de propriedade: com os perfis reduzidos a admin e vendedor,
        a loja tem um balcão só. O cliente volta e é atendido por quem estiver livre, e
        a fila de separação virou trabalho de vendedor — travar cada pedido no seu autor
        deixaria metade da tela inútil. Quem faz o quê continua registrado em
        `pedido.vendedor_id` e na auditoria.
        """
        pedido = pedido_repo.get_completo(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        return pedido

    # ----------------------------------------------------------- listagem
    def listar(
        self,
        db: Session,
        usuario: Usuario,
        status: str = "",
        origem: str = "",
    ) -> list[Pedido]:
        """Lista com os filtros da tela. Valor irreconhecível vale "sem filtro".

        Os filtros chegam da query string (é o que deixa o realtime re-buscar a lista
        JÁ filtrada); um valor inválido não é erro de operação, é link velho ou dedo no
        endereço — mostrar tudo é melhor que estourar uma tela de erro.
        """
        return pedido_repo.listar(
            db,
            status=_enum_ou_nada(StatusPedido, status),
            origem=_enum_ou_nada(OrigemPedido, origem),
        )

    def get(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        return self._carregar_para_usuario(db, pedido_id, usuario)

    # ----------------------------------------------------------- criação
    def criar(self, db: Session, dados: PedidoCreate, usuario: Usuario) -> Pedido:
        return pedido_service.criar(
            db,
            dados.cliente_id,
            usuario.id,
            dados.observacao,
            cliente_nome=dados.cliente_nome,
            cliente_telefone=dados.cliente_telefone,
        )

    def criar_completo(self, db: Session, dados: PedidoCompletoCreate, usuario: Usuario) -> Pedido:
        """Cria o pedido inteiro de uma vez — o `POST /pedidos` da tela nova."""
        return pedido_service.criar_completo(db, dados, usuario.id, usuario.perfil)

    # ----------------------------------------------------------- colagem de planilha
    def resolver_colagem(self, db: Session, texto: str, usuario: Usuario) -> list[LinhaResolvida]:
        """Casa o texto colado com o catálogo sem gravar nada (carrinho da tela nova)."""
        return colagem_service.resolver_para_carrinho(db, texto, usuario.perfil)

    def criar_colando(
        self, db: Session, dados: PedidoCreate, texto: str, usuario: Usuario
    ) -> ResultadoLote:
        """Abre um pedido por bloco da planilha colada (tela `/pedidos/novo`)."""
        return colagem_service.criar_lote(db, dados, texto, usuario.id, usuario.perfil)

    def colar_itens(
        self, db: Session, pedido_id: int, texto: str, usuario: Usuario
    ) -> ResultadoColagem:
        """Acrescenta em bloco os itens da planilha colada a um rascunho aberto."""
        self._carregar_para_usuario(db, pedido_id, usuario)
        return colagem_service.aplicar(db, pedido_id, texto, usuario.perfil, usuario.id)

    def adicionar_item(
        self, db: Session, pedido_id: int, dados: ItemAdicionar, usuario: Usuario
    ) -> PedidoItem:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.adicionar_item(db, pedido_id, dados, usuario.perfil)

    def adicionar_item_avulso(
        self, db: Session, pedido_id: int, dados: ItemAvulsoAdicionar, usuario: Usuario
    ) -> PedidoItem:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.adicionar_item_avulso(db, pedido_id, dados)

    def remover_item(self, db: Session, pedido_id: int, item_id: int, usuario: Usuario) -> Pedido:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.remover_item(db, pedido_id, item_id)

    def aplicar_desconto_total(
        self, db: Session, pedido_id: int, desconto: Decimal, usuario: Usuario
    ) -> Pedido:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.aplicar_desconto_total(db, pedido_id, desconto, usuario.perfil)

    # ----------------------------------------------------------- ciclo
    def confirmar(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.confirmar(db, pedido_id, usuario.id)

    def cancelar(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.cancelar(db, pedido_id, usuario.id)

    def faturar(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        # Faturar é exclusivo do admin (ver require_role da rota).
        return pedido_service.faturar(db, pedido_id, usuario.id)

    def entregar(self, db: Session, pedido_id: int, usuario: Usuario) -> Pedido:
        self._carregar_para_usuario(db, pedido_id, usuario)
        return pedido_service.entregar(db, pedido_id)

    # ----------------------------------------------------------- separação
    def fila_separacao(self, db: Session) -> list[Pedido]:
        return pedido_repo.fila_separacao(db)

    def get_separacao(self, db: Session, pedido_id: int) -> Pedido:
        pedido = pedido_repo.get_completo(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        return pedido

    def marcar_item_separado(
        self, db: Session, pedido_id: int, item_id: int, separado: bool
    ) -> PedidoItem:
        return pedido_service.marcar_item_separado(db, pedido_id, item_id, separado)

    def concluir_separacao(self, db: Session, pedido_id: int) -> Pedido:
        return pedido_service.concluir_separacao(db, pedido_id)


pedido_controller = PedidoController()


def progresso_separacao(pedido: Pedido) -> tuple[int, int]:
    """(itens separados, total de itens) — útil para a barra de progresso."""
    total = len(pedido.itens)
    feitos = sum(1 for item in pedido.itens if item.separado)
    return feitos, total
