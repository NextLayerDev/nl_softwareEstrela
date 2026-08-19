from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.models.cliente import Cliente
from app.models.conta_receber import ContaReceber
from app.models.enums import StatusConta, StatusPedido, e_admin
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import Produto, ProdutoVariacao
from app.repositories.cliente_repo import cliente_repo
from app.repositories.pedido_repo import pedido_repo
from app.schemas.pedido import (
    ItemAdicionar,
    ItemAvulsoAdicionar,
    ItemCarrinho,
    PedidoCompletoCreate,
    SugestaoPreco,
)
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

    def _preco_efetivo(self, qtd: int, preco_unit: Decimal, desconto: Decimal) -> Decimal:
        """Quanto de fato sai cada unidade, já descontado o desconto do item."""
        if qtd <= 0:
            return preco_unit
        return ((Decimal(qtd) * preco_unit - desconto) / Decimal(qtd)).quantize(CENT)

    def aviso_de_preco(
        self,
        perfil: str,
        produto: Produto,
        qtd: int,
        preco_unit: Decimal,
        desconto: Decimal = Decimal("0"),
    ) -> str | None:
        """O que há de errado com este preço — em texto, sem barrar a venda.

        O piso do produto e o limite de desconto do vendedor deixaram de BLOQUEAR o
        lançamento: quem está no balcão fecha negócio difícil na frente do cliente, e
        travar a gravação só empurrava a venda para fora do sistema. O número continua
        valendo como informação — a tela mostra este aviso em amarelo ao lado do preço,
        e a colagem o carrega junto da linha.

        As duas contas são sobre o preço EFETIVO (já com o desconto do item), não sobre
        o `preco_unit` digitado: olhar só o preço deixaria o aviso mudo justamente
        quando o desconto é que derrubou a margem.

        `preco_minimo == 0` é "sem piso" (produto ainda não revisado). Admin não recebe
        aviso: é ele quem define o número.
        """
        if e_admin(perfil):
            return None

        piso = produto.preco_minimo or Decimal("0")
        if piso > 0:
            efetivo = self._preco_efetivo(qtd, preco_unit, desconto)
            if efetivo < piso:
                return (
                    f"{produto.codigo}: R$ {efetivo} por unidade fica abaixo do preço "
                    f"mínimo de R$ {piso.quantize(CENT)}."
                )

        bruto = (Decimal(qtd) * preco_unit).quantize(CENT)
        if desconto > 0 and bruto > 0:
            pct = (desconto / bruto) * Decimal("100")
            if pct > LIMITE_DESCONTO_VENDEDOR_PCT:
                return (
                    f"Desconto de {pct.quantize(CENT)}% acima do limite usual do vendedor "
                    f"({LIMITE_DESCONTO_VENDEDOR_PCT}%)."
                )
        return None

    # ------------------------------------------------------------- criação
    def criar(
        self,
        db: Session,
        cliente_id: int | None,
        vendedor_id: int,
        observacao: str | None = None,
        cliente_nome: str | None = None,
        cliente_telefone: str | None = None,
    ) -> Pedido:
        """Abre um rascunho, com ou sem cliente cadastrado.

        Se o vendedor escolheu uma sugestão da busca, vem `cliente_id` e o resto é
        ignorado. Se ele só digitou o telefone, tentamos amarrar sozinhos ao cadastro
        que já existe — é o que faz a segunda compra do mesmo cliente cair na ficha
        certa sem ninguém procurar. Não achando, guardamos o texto livre.
        """
        nome = (cliente_nome or "").strip() or None
        telefone = (cliente_telefone or "").strip() or None

        cliente: Cliente | None = None
        if cliente_id is not None:
            cliente = db.get(Cliente, cliente_id)
            if cliente is None:
                raise NaoEncontradoError("Cliente não encontrado.")
        elif telefone:
            cliente = cliente_repo.por_telefone(db, telefone)

        pedido = Pedido(
            cliente_id=cliente.id if cliente is not None else None,
            # O texto livre só sobra quando não há cadastro: com cliente vinculado, o
            # nome verdadeiro é o do cadastro, e duplicá-lo aqui só criaria duas versões
            # do mesmo dado esperando divergir.
            cliente_nome=None if cliente is not None else nome,
            cliente_telefone=None if cliente is not None else telefone,
            vendedor_id=vendedor_id,
            status=StatusPedido.RASCUNHO,
            observacao=observacao,
            total=Decimal("0"),
            desconto_total=Decimal("0"),
        )
        return pedido_repo.add(db, pedido)

    def criar_completo(
        self, db: Session, dados: PedidoCompletoCreate, vendedor_id: int, perfil: str
    ) -> Pedido:
        """Abre o pedido JÁ com todos os itens (tela `/pedidos/novo`).

        O carrinho é montado no navegador e chega inteiro aqui, em vez de um rascunho
        vazio seguido de um POST por item. O preço é sempre resolvido NO SERVIDOR a
        partir do catálogo — o navegador só manda o que o vendedor digitou por cima
        (`preco_unit`), nunca dita o preço sozinho. Item avulso é a exceção de sempre.

        Todos os itens são resolvidos ANTES de qualquer escrita: um item inválido no
        meio da lista levanta a exceção antes de existir pedido, e como o `get_db` só
        commita no fim da requisição, nada fica gravado pela metade — não é preciso
        apagar rascunho órfão depois.
        """
        pendentes: list[tuple[ProdutoVariacao | None, ItemCarrinho]] = []
        for entrada in dados.itens:
            if entrada.tipo == "avulso":
                pendentes.append((None, entrada))
                continue
            variacao = self._get_variacao(db, entrada.variacao_id)
            if not variacao.ativo or not variacao.produto.ativo:
                raise RegraNegocioError(
                    f"{variacao.produto.codigo} está inativo e não pode ser vendido."
                )
            pendentes.append((variacao, entrada))

        pedido = self.criar(
            db,
            dados.cliente_id,
            vendedor_id,
            dados.observacao,
            cliente_nome=dados.cliente_nome,
            cliente_telefone=dados.cliente_telefone,
        )
        db.flush()  # precisa do id para pendurar os itens

        for variacao, entrada in pendentes:
            if variacao is None:
                item = self._montar_item_avulso(
                    pedido.id,
                    ItemAvulsoAdicionar(
                        nome=entrada.nome,
                        codigo=entrada.codigo,
                        detalhe=entrada.detalhe,
                        qtd=entrada.qtd,
                        preco_unit=entrada.preco_unit,
                        desconto=entrada.desconto,
                    ),
                )
            else:
                produto = variacao.produto
                qtd, qtd_caixas = self._resolver_qtd(produto, entrada.qtd, entrada.qtd_caixas)
                if entrada.preco_unit is not None:
                    preco_unit = Decimal(entrada.preco_unit).quantize(CENT)
                else:
                    preco_unit = self.sugerir_preco(produto, qtd).preco_sugerido
                item = self._montar_item(
                    pedido.id,
                    variacao,
                    qtd,
                    qtd_caixas,
                    preco_unit,
                    Decimal(entrada.desconto).quantize(CENT),
                )
            pedido_repo.add_item(db, item)

        db.refresh(pedido)
        pedido.desconto_total = Decimal(dados.desconto_total).quantize(CENT)
        self._recalcular_total(pedido)
        db.flush()
        return pedido

    def carregar_editavel(self, db: Session, pedido_id: int) -> Pedido:
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
        pedido = self.carregar_editavel(db, pedido_id)
        variacao = self._get_variacao(db, dados.variacao_id)
        produto = variacao.produto

        qtd, qtd_caixas = self._resolver_qtd(produto, dados.qtd, dados.qtd_caixas)

        if dados.preco_unit is not None:
            preco_unit = Decimal(dados.preco_unit).quantize(CENT)
        else:
            preco_unit = self.sugerir_preco(produto, qtd).preco_sugerido

        desconto = Decimal(dados.desconto).quantize(CENT)

        # Mesma variação pelo mesmo preço vira UMA linha, somando a quantidade — é o
        # que mantém o pedido e o cupom legíveis quando o vendedor lança o produto três
        # vezes seguidas. Preço diferente abre linha nova de propósito: são negociações
        # distintas do mesmo item, e juntá-las esconderia isso do faturamento.
        existente = next(
            (
                i
                for i in pedido.itens
                if i.produto_variacao_id == variacao.id and i.preco_unit == preco_unit
            ),
            None,
        )
        if existente is not None:
            qtd_total = existente.qtd + qtd
            desconto_total = (existente.desconto + desconto).quantize(CENT)
            existente.qtd = qtd_total
            existente.desconto = desconto_total
            if qtd_caixas is not None:
                existente.qtd_caixas = (existente.qtd_caixas or 0) + qtd_caixas
            existente.subtotal = self._calcular_subtotal(qtd_total, preco_unit, desconto_total)
            self._recalcular_total(pedido)
            db.flush()
            return existente

        item = self._montar_item(pedido.id, variacao, qtd, qtd_caixas, preco_unit, desconto)
        pedido_repo.add_item(db, item)
        db.refresh(pedido)
        self._recalcular_total(pedido)
        db.flush()
        return item

    def _montar_item(
        self,
        pedido_id: int,
        variacao: ProdutoVariacao,
        qtd: int,
        qtd_caixas: int | None,
        preco_unit: Decimal,
        desconto: Decimal,
    ) -> PedidoItem:
        """Item de catálogo, com o snapshot do que está sendo vendido AGORA."""
        return PedidoItem(
            pedido_id=pedido_id,
            produto_variacao_id=variacao.id,
            descricao=variacao.produto.descricao[:200],
            codigo=(variacao.produto.codigo or "")[:60] or None,
            qtd=qtd,
            qtd_caixas=qtd_caixas,
            preco_unit=preco_unit,
            desconto=desconto,
            subtotal=self._calcular_subtotal(qtd, preco_unit, desconto),
        )

    def _montar_item_avulso(self, pedido_id: int, dados: ItemAvulsoAdicionar) -> PedidoItem:
        """Item sem catálogo: nome e preço são o que o vendedor digitou.

        Nunca funde com outra linha — sem chave de catálogo, duas linhas com o mesmo
        nome podem ser negociações diferentes, e juntá-las escondia isso do faturamento.
        """
        preco_unit = Decimal(dados.preco_unit).quantize(CENT)
        desconto = Decimal(dados.desconto).quantize(CENT)
        return PedidoItem(
            pedido_id=pedido_id,
            produto_variacao_id=None,
            descricao=dados.nome.strip()[:200],
            codigo=(dados.codigo or "").strip()[:60] or None,
            detalhe=(dados.detalhe or "").strip()[:500] or None,
            qtd=dados.qtd,
            qtd_caixas=None,
            preco_unit=preco_unit,
            desconto=desconto,
            subtotal=self._calcular_subtotal(dados.qtd, preco_unit, desconto),
        )

    def adicionar_item_avulso(
        self, db: Session, pedido_id: int, dados: ItemAvulsoAdicionar
    ) -> PedidoItem:
        """Lança no rascunho um item que não está no catálogo."""
        pedido = self.carregar_editavel(db, pedido_id)
        item = self._montar_item_avulso(pedido.id, dados)
        pedido_repo.add_item(db, item)
        db.refresh(pedido)
        self._recalcular_total(pedido)
        db.flush()
        return item

    def remover_item(self, db: Session, pedido_id: int, item_id: int) -> Pedido:
        pedido = self.carregar_editavel(db, pedido_id)
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
        """Desconto do rodapé, em R$. Não barra por percentual — ver `aviso_de_preco`.

        O `_recalcular_total` continua recusando desconto maior que a soma dos itens:
        isso não é política comercial, é conta que não fecha.
        """
        pedido = self.carregar_editavel(db, pedido_id)
        desconto = Decimal(desconto).quantize(CENT)
        if desconto < 0:
            raise RegraNegocioError("O desconto não pode ser negativo.")
        pedido.desconto_total = desconto
        self._recalcular_total(pedido)
        db.flush()
        return pedido

    # ------------------------------------------------------------- ciclo
    def _itens_com_estoque(self, db: Session, pedido: Pedido) -> list[tuple[ProdutoVariacao, int]]:
        """Só os itens que movem saldo.

        Item avulso não está no catálogo, então não tem estoque a reservar, baixar ou
        estornar. Ele segue no pedido, na impressão e na conferência da separação — o
        que não existe é a movimentação.
        """
        pares: list[tuple[ProdutoVariacao, int]] = []
        for item in pedido.itens:
            if item.produto_variacao_id is None:
                continue
            pares.append((self._get_variacao(db, item.produto_variacao_id), item.qtd))
        return pares

    def confirmar(self, db: Session, pedido_id: int, usuario_id: int) -> Pedido:
        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")
        if pedido.status != StatusPedido.RASCUNHO:
            raise RegraNegocioError("Apenas pedidos em rascunho podem ser confirmados.")
        if not pedido.itens:
            raise RegraNegocioError("Não é possível confirmar um pedido sem itens.")

        for variacao, qtd in self._itens_com_estoque(db, pedido):
            estoque_service.reservar(db, variacao, qtd, usuario_id, pedido.id)

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
            audiencia=eventos.TODOS,
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

        for variacao, qtd in self._itens_com_estoque(db, pedido):
            estoque_service.baixar(db, variacao, qtd, usuario_id, pedido.id)

        pedido.status = StatusPedido.FATURADO
        pedido.faturado_em = datetime.now(UTC)
        contas = self._gerar_contas_receber(db, pedido)
        db.flush()
        # Cada baixar() acima já emitiu estoque.movimentado; aqui é o fato do faturamento.
        eventos.emitir(
            db,
            "pedido.faturado",
            {**self._dados_pedido(pedido), "contas_geradas": len(contas or [])},
            audiencia=eventos.TODOS,
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
            for variacao, qtd in self._itens_com_estoque(db, pedido):
                estoque_service.estornar(db, variacao, qtd, usuario_id, pedido.id)
        pedido.status = StatusPedido.CANCELADO
        db.flush()
        # Sai da fila de separação em todos os terminais.
        eventos.emitir(
            db,
            "pedido.cancelado",
            self._dados_pedido(pedido),
            audiencia=eventos.TODOS,
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
            audiencia=eventos.TODOS,
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
            "cliente": pedido.nome_cliente,
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
        offsets = self._parse_parcelas(pedido.condicao_pagto)
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
