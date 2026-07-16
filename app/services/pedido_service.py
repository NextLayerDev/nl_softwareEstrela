from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, PermissaoNegadaError, RegraNegocioError
from app.models.cliente import Cliente
from app.models.conta_receber import ContaReceber
from app.models.enums import Perfil, StatusConta, StatusPedido
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import Produto, ProdutoVariacao
from app.repositories.pedido_repo import pedido_repo
from app.schemas.pedido import ItemAdicionar, SugestaoPreco
from app.services.estoque_service import estoque_service

# Limite máximo de desconto (em %) que um vendedor pode aplicar sem aprovação de admin.
LIMITE_DESCONTO_VENDEDOR_PCT = Decimal("10")

CENT = Decimal("0.01")


class PedidoService:
    """Regras de negócio de pedidos. NÃO faz commit (o get_db fecha a transação)."""

    # ------------------------------------------------------------- precificação
    def sugerir_preco(self, produto: Produto, qtd: int) -> SugestaoPreco:
        """Sugere preço por faixa: atacado quando qtd >= corte, senão varejo."""
        corte = produto.qtd_corte_atacado
        if corte is not None and qtd >= corte:
            preco = produto.preco_muita_qtd
            faixa = "atacado"
        else:
            preco = produto.preco_pouca_qtd
            faixa = "varejo"
        return SugestaoPreco(
            preco_sugerido=preco,
            faixa=faixa,
            preco_pouca_qtd=produto.preco_pouca_qtd,
            preco_muita_qtd=produto.preco_muita_qtd,
            preco_promocional=produto.preco_promocional,
            qtd_corte_atacado=corte,
        )

    # ------------------------------------------------------------- caixa -> un
    def _resolver_qtd(
        self, produto: Produto, qtd: int | None, qtd_caixas: int | None
    ) -> tuple[int, int | None]:
        """Converte caixas em unidades. Retorna (qtd_unidades, qtd_caixas)."""
        if qtd_caixas is not None:
            unidades_por_caixa = produto.unidades_por_caixa
            if not unidades_por_caixa or unidades_por_caixa <= 0:
                raise RegraNegocioError(
                    f"O produto {produto.codigo} não tem unidades por caixa definidas."
                )
            return qtd_caixas * unidades_por_caixa, qtd_caixas
        if qtd is None or qtd <= 0:
            raise RegraNegocioError("Informe a quantidade (em unidades ou caixas).")
        return qtd, None

    # ------------------------------------------------------------- totais
    def _calcular_subtotal(self, qtd: int, preco_unit: Decimal, desconto: Decimal) -> Decimal:
        bruto = (Decimal(qtd) * preco_unit) - desconto
        if bruto < 0:
            raise RegraNegocioError("O desconto do item não pode ser maior que o valor do item.")
        return bruto.quantize(CENT)

    def _recalcular_total(self, pedido: Pedido) -> None:
        soma = sum((item.subtotal for item in pedido.itens), Decimal("0"))
        total = (soma - (pedido.desconto_total or Decimal("0"))).quantize(CENT)
        if total < 0:
            raise RegraNegocioError("O desconto total não pode ser maior que a soma dos itens.")
        pedido.total = total

    def _validar_limite_desconto(self, perfil: str, bruto_item: Decimal, desconto: Decimal) -> None:
        """Vendedor só aplica desconto até o limite (%). Acima, exige admin."""
        if desconto <= 0 or perfil == "admin":
            return
        if bruto_item <= 0:
            return
        pct = (desconto / bruto_item) * Decimal("100")
        if pct > LIMITE_DESCONTO_VENDEDOR_PCT:
            raise PermissaoNegadaError(
                f"Desconto de {pct.quantize(CENT)}% acima do limite do vendedor "
                f"({LIMITE_DESCONTO_VENDEDOR_PCT}%). Requer aprovação do administrador."
            )

    # ------------------------------------------------------------- criação
    def criar(
        self, db: Session, cliente_id: int, vendedor_id: int, observacao: str | None = None
    ) -> Pedido:
        cliente = db.get(Cliente, cliente_id)
        if cliente is None:
            raise NaoEncontradoError("Cliente não encontrado.")
        pedido = Pedido(
            cliente_id=cliente_id,
            vendedor_id=vendedor_id,
            status=StatusPedido.RASCUNHO,
            observacao=observacao,
            total=Decimal("0"),
            desconto_total=Decimal("0"),
        )
        return pedido_repo.add(db, pedido)

    def _carregar_editavel(self, db: Session, pedido_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status != StatusPedido.RASCUNHO:
            raise RegraNegocioError("Só é possível editar itens de um pedido em rascunho.")
        return pedido

    def _get_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        variacao = db.get(ProdutoVariacao, variacao_id)
        if variacao is None:
            raise NaoEncontradoError("Variação de produto não encontrada.")
        return variacao

    # ------------------------------------------------------------- itens
    def adicionar_item(
        self, db: Session, pedido_id: int, dados: ItemAdicionar, perfil: str
    ) -> PedidoItem:
        pedido = self._carregar_editavel(db, pedido_id)
        variacao = self._get_variacao(db, dados.variacao_id)
        produto = variacao.produto

        qtd, qtd_caixas = self._resolver_qtd(produto, dados.qtd, dados.qtd_caixas)

        if dados.preco_unit is not None:
            preco_unit = Decimal(dados.preco_unit).quantize(CENT)
        else:
            preco_unit = self.sugerir_preco(produto, qtd).preco_sugerido

        desconto = Decimal(dados.desconto).quantize(CENT)
        bruto = (Decimal(qtd) * preco_unit).quantize(CENT)
        self._validar_limite_desconto(perfil, bruto, desconto)

        subtotal = self._calcular_subtotal(qtd, preco_unit, desconto)
        item = PedidoItem(
            pedido_id=pedido.id,
            produto_variacao_id=variacao.id,
            qtd=qtd,
            qtd_caixas=qtd_caixas,
            preco_unit=preco_unit,
            desconto=desconto,
            subtotal=subtotal,
        )
        pedido_repo.add_item(db, item)
        db.refresh(pedido)
        self._recalcular_total(pedido)
        db.flush()
        return item

    def remover_item(self, db: Session, pedido_id: int, item_id: int) -> Pedido:
        pedido = self._carregar_editavel(db, pedido_id)
        item = pedido_repo.get_item(db, item_id)
        if item is None or item.pedido_id != pedido.id:
            raise NaoEncontradoError("Item do pedido não encontrado.")
        pedido_repo.remover_item(db, item)
        db.refresh(pedido)
        self._recalcular_total(pedido)
        db.flush()
        return pedido

    def aplicar_desconto_total(
        self, db: Session, pedido_id: int, desconto: Decimal, perfil: str
    ) -> Pedido:
        pedido = self._carregar_editavel(db, pedido_id)
        desconto = Decimal(desconto).quantize(CENT)
        if desconto < 0:
            raise RegraNegocioError("O desconto não pode ser negativo.")
        soma = sum((item.subtotal for item in pedido.itens), Decimal("0"))
        if perfil != "admin" and soma > 0:
            pct = (desconto / soma) * Decimal("100")
            if pct > LIMITE_DESCONTO_VENDEDOR_PCT:
                raise PermissaoNegadaError(
                    f"Desconto total de {pct.quantize(CENT)}% acima do limite do vendedor "
                    f"({LIMITE_DESCONTO_VENDEDOR_PCT}%). Requer aprovação do administrador."
                )
        pedido.desconto_total = desconto
        self._recalcular_total(pedido)
        db.flush()
        return pedido

    # ------------------------------------------------------------- ciclo
    def confirmar(self, db: Session, pedido_id: int, usuario_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status != StatusPedido.RASCUNHO:
            raise RegraNegocioError("Apenas pedidos em rascunho podem ser confirmados.")
        if not pedido.itens:
            raise RegraNegocioError("Não é possível confirmar um pedido sem itens.")

        for item in pedido.itens:
            variacao = self._get_variacao(db, item.produto_variacao_id)
            estoque_service.reservar(db, variacao, item.qtd, usuario_id, pedido.id)

        pedido.numero = pedido_repo.proximo_numero(db)
        pedido.status = StatusPedido.CONFIRMADO
        db.flush()
        # O funcionário parado na fila de separação vê o pedido entrar na hora.
        eventos.emitir(
            db,
            "pedido.confirmado",
            self._dados_pedido(pedido),
            audiencia=eventos.SEP_AUD,
            vendedor_id=pedido.vendedor_id,
        )
        return pedido

    def iniciar_separacao(self, db: Session, pedido_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status not in (StatusPedido.CONFIRMADO, StatusPedido.SEPARACAO):
            raise RegraNegocioError("Pedido não está disponível para separação.")
        if pedido.status == StatusPedido.CONFIRMADO:
            pedido.status = StatusPedido.SEPARACAO
            db.flush()
            eventos.emitir(
                db,
                "pedido.status_alterado",
                self._dados_pedido(pedido),
                audiencia=eventos.SEP_AUD,
                vendedor_id=pedido.vendedor_id,
                silencioso=True,
            )
        return pedido

    def marcar_item_separado(
        self, db: Session, pedido_id: int, item_id: int, separado: bool
    ) -> PedidoItem:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status not in (StatusPedido.CONFIRMADO, StatusPedido.SEPARACAO):
            raise RegraNegocioError("Pedido não está em separação.")
        if pedido.status == StatusPedido.CONFIRMADO:
            pedido.status = StatusPedido.SEPARACAO
        item = pedido_repo.get_item(db, item_id)
        if item is None or item.pedido_id != pedido.id:
            raise NaoEncontradoError("Item do pedido não encontrado.")
        item.separado = separado
        db.flush()
        # Dois tablets conferindo o mesmo pedido enxergam o tique um do outro.
        feitos = sum(1 for i in pedido.itens if i.separado)
        eventos.emitir(
            db,
            "separacao.item_conferido",
            {
                **self._dados_pedido(pedido),
                "item_id": item.id,
                "separado": item.separado,
                "feitos": feitos,
                "itens": len(pedido.itens),
            },
            audiencia=eventos.SEP_AUD,
            silencioso=True,
        )
        return item

    def concluir_separacao(self, db: Session, pedido_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status not in (StatusPedido.CONFIRMADO, StatusPedido.SEPARACAO):
            raise RegraNegocioError("Pedido não está em separação.")
        if not all(item.separado for item in pedido.itens):
            raise RegraNegocioError("Há itens ainda não conferidos na separação.")
        pedido.status = StatusPedido.SEPARADO
        db.flush()
        # Sai da fila do funcionário e fica pronto para o financeiro faturar.
        eventos.emitir(
            db,
            "separacao.concluida",
            self._dados_pedido(pedido),
            audiencia=eventos.SEP_AUD + (Perfil.FINANCEIRO.value,),
            vendedor_id=pedido.vendedor_id,
        )
        return pedido

    def faturar(self, db: Session, pedido_id: int, usuario_id: int) -> Pedido:
        pedido = pedido_repo.get_completo(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status not in (
            StatusPedido.CONFIRMADO,
            StatusPedido.SEPARACAO,
            StatusPedido.SEPARADO,
        ):
            raise RegraNegocioError(
                "Apenas pedidos confirmados ou em separação podem ser faturados."
            )

        for item in pedido.itens:
            variacao = self._get_variacao(db, item.produto_variacao_id)
            estoque_service.baixar(db, variacao, item.qtd, usuario_id, pedido.id)

        pedido.status = StatusPedido.FATURADO
        pedido.faturado_em = datetime.now(UTC)
        contas = self._gerar_contas_receber(db, pedido)
        db.flush()
        # Cada baixar() acima já emitiu estoque.movimentado; aqui é o fato do faturamento.
        eventos.emitir(
            db,
            "pedido.faturado",
            {**self._dados_pedido(pedido), "contas_geradas": len(contas or [])},
            audiencia=eventos.FIN_AUD + (Perfil.FUNCIONARIO.value,),
            vendedor_id=pedido.vendedor_id,
        )
        return pedido

    def cancelar(self, db: Session, pedido_id: int, usuario_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status in (StatusPedido.FATURADO, StatusPedido.ENTREGUE):
            raise RegraNegocioError("Não é possível cancelar um pedido já faturado/entregue.")
        if pedido.status == StatusPedido.CANCELADO:
            raise RegraNegocioError("Pedido já está cancelado.")
        # Estorna reservas se havia (confirmado/separação/separado).
        if pedido.status in (
            StatusPedido.CONFIRMADO,
            StatusPedido.SEPARACAO,
            StatusPedido.SEPARADO,
        ):
            for item in pedido.itens:
                variacao = self._get_variacao(db, item.produto_variacao_id)
                estoque_service.estornar(db, variacao, item.qtd, usuario_id, pedido.id)
        pedido.status = StatusPedido.CANCELADO
        db.flush()
        # Sai da fila de separação em todos os terminais.
        eventos.emitir(
            db,
            "pedido.cancelado",
            self._dados_pedido(pedido),
            audiencia=eventos.SEP_AUD + (Perfil.FINANCEIRO.value,),
            vendedor_id=pedido.vendedor_id,
        )
        return pedido

    def entregar(self, db: Session, pedido_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status != StatusPedido.FATURADO:
            raise RegraNegocioError("Apenas pedidos faturados podem ser marcados como entregues.")
        pedido.status = StatusPedido.ENTREGUE
        db.flush()
        eventos.emitir(
            db,
            "pedido.status_alterado",
            self._dados_pedido(pedido),
            audiencia=eventos.SEP_AUD + (Perfil.FINANCEIRO.value,),
            vendedor_id=pedido.vendedor_id,
        )
        return pedido

    # ------------------------------------------------------- eventos
    def _dados_pedido(self, pedido: Pedido) -> dict:
        """Payload comum dos eventos de pedido: só ids/primitivos, nada de custo."""
        return {
            "pedido_id": pedido.id,
            "numero": pedido.numero,
            "status": str(pedido.status),
            "cliente": pedido.cliente.nome if pedido.cliente else None,
            "vendedor_id": pedido.vendedor_id,
            "total": str(pedido.total or Decimal("0")),
        }

    # ------------------------------------------------------- contas a receber
    def _parse_parcelas(self, condicao: str | None) -> list[int]:
        """Interpreta a condição de pagamento (texto livre) em dias de vencimento.

        Regras:
          - vazio / "à vista" / "a vista" / "dinheiro" / "pix" -> [0] (1 título hoje)
          - "30 dias", "60 dias", "45d" -> [N] (1 título em hoje+N)
          - "2x", "3x", "4 x" -> N parcelas mensais (0, 30, 60, ...)
        Retorna a lista de offsets em dias (uma posição por parcela).
        """
        if not condicao or not condicao.strip():
            return [0]
        texto = condicao.strip().lower()

        # Parcelado: "Nx"
        m = re.search(r"(\d+)\s*x", texto)
        if m:
            n = int(m.group(1))
            if n <= 0:
                return [0]
            return [30 * i for i in range(n)]

        if "vista" in texto or "pix" in texto or "dinheiro" in texto or "boleto" in texto:
            # boleto sem prazo explícito cai aqui como à vista; prazo é tratado abaixo
            m_dias = re.search(r"(\d+)\s*d", texto)
            if m_dias:
                return [int(m_dias.group(1))]
            return [0]

        # "N dias" / "Nd"
        m = re.search(r"(\d+)\s*d", texto)
        if m:
            return [int(m.group(1))]

        return [0]

    def _gerar_contas_receber(self, db: Session, pedido: Pedido) -> list[ContaReceber]:
        """Cria os títulos a receber conforme a condição de pagamento do cliente."""
        offsets = self._parse_parcelas(pedido.cliente.condicao_pagto_padrao)
        n = len(offsets)
        total = (pedido.total or Decimal("0")).quantize(CENT)
        base = (total / n).quantize(CENT)
        hoje = date.today()
        contas: list[ContaReceber] = []
        acumulado = Decimal("0")
        for idx, offset in enumerate(offsets):
            if idx == n - 1:
                valor = (total - acumulado).quantize(CENT)  # ajusta centavos na última
            else:
                valor = base
                acumulado += base
            conta = ContaReceber(
                pedido_id=pedido.id,
                parcela=idx + 1,
                valor=valor,
                vencimento=hoje + timedelta(days=offset),
                status=StatusConta.PENDENTE,
                forma_pagamento=None,
            )
            pedido_repo.add_conta(db, conta)
            contas.append(conta)
        return contas


pedido_service = PedidoService()
