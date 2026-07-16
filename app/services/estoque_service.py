from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import RegraNegocioError
from app.core.estoque_alertas import abaixo_minimo
from app.models.enums import EstoqueModo, OrigemMov, RotuloAprox, TipoMov
from app.models.movimentacao import MovimentacaoEstoque
from app.models.produto import ProdutoVariacao


def _rotulo_variacao(variacao: ProdutoVariacao) -> str:
    """Texto curto para mensagens de erro: cor (ou código do produto)."""
    cor = (variacao.cor or "").strip()
    codigo = variacao.produto.codigo if variacao.produto else f"#{variacao.produto_id}"
    if cor:
        return f"{codigo} ({cor})"
    return codigo


class EstoqueService:
    """Regras de estoque. NUNCA altera saldo sem registrar movimentação append-only.

    Os métodos não fazem commit — quem fecha a transação é o get_db ao fim do request.
    Em fluxos com vários itens (pedido), a atomicidade vem do commit único do request:
    se qualquer item levantar RegraNegocioError, nada é commitado.
    """

    def _registrar(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        tipo: TipoMov,
        qtd: int,
        usuario_id: int,
        origem: OrigemMov,
        ref_id: int | None = None,
        motivo: str | None = None,
    ) -> MovimentacaoEstoque:
        mov = MovimentacaoEstoque(
            produto_variacao_id=variacao.id,
            tipo=tipo,
            qtd=qtd,
            origem=origem,
            ref_id=ref_id,
            usuario_id=usuario_id,
            saldo_apos=variacao.estoque_fisico,
            motivo=motivo,
        )
        db.add(mov)
        db.flush()
        self._emitir_movimentacao(db, variacao, mov)
        return mov

    def _emitir_movimentacao(
        self, db: Session, variacao: ProdutoVariacao, mov: MovimentacaoEstoque
    ) -> None:
        """Avisa as telas de estoque. Emitir aqui cobre os seis métodos de uma vez.

        A importação escreve movimentação direto (sem passar por aqui), mas o
        `adicionar_variacao` chama a entrada com origem=IMPORTACAO — nesses casos o evento por
        linha inundaria os terminais, então a carga emite só um resumo no fim.
        """
        if mov.origem == OrigemMov.IMPORTACAO:
            return
        eventos.emitir(
            db,
            "estoque.movimentado",
            {
                "mov_id": mov.id,
                "variacao_id": variacao.id,
                "produto_id": variacao.produto_id,
                "codigo": variacao.produto.codigo if variacao.produto else None,
                "cor": variacao.cor,
                "tipo": str(mov.tipo),
                "qtd": mov.qtd,
                "origem": str(mov.origem),
                "estoque_fisico": variacao.estoque_fisico,
                "estoque_reservado": variacao.estoque_reservado,
                "disponivel": variacao.disponivel,
                "estoque_modo": str(variacao.estoque_modo),
                "rotulo_aprox": str(variacao.rotulo_aprox) if variacao.rotulo_aprox else None,
            },
            audiencia=eventos.TODOS,
            # Sempre silencioso: a linha mudando na tela já é o aviso, e quem lançou já vê a
            # confirmação no #estoque-msg. Com toast, uma conferência de 40 itens encheria os
            # 10 terminais de avisos. Quem precisa de atenção é o alerta de mínimo, abaixo.
            silencioso=True,
        )

    def _alertar_se_cruzou_minimo(
        self, db: Session, variacao: ProdutoVariacao, antes: bool
    ) -> None:
        """Avisa só na *transição* para abaixo do mínimo — não a cada movimentação."""
        if antes or not abaixo_minimo(variacao):
            return
        eventos.emitir(
            db,
            "estoque.alerta_minimo",
            {
                "variacao_id": variacao.id,
                "produto_id": variacao.produto_id,
                "codigo": variacao.produto.codigo if variacao.produto else None,
                "cor": variacao.cor,
                "estoque_fisico": variacao.estoque_fisico,
                "estoque_minimo": variacao.estoque_minimo,
                "rotulo_aprox": str(variacao.rotulo_aprox) if variacao.rotulo_aprox else None,
            },
            audiencia=eventos.SEP_AUD,
        )

    # ---------------------------------------------------------------- entrada
    def entrada(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        qtd: int,
        usuario_id: int,
        origem: OrigemMov = OrigemMov.MANUAL,
        ref_id: int | None = None,
    ) -> MovimentacaoEstoque:
        """Entrada de mercadoria: soma ao físico e torna a variação EXATA."""
        if qtd <= 0:
            raise RegraNegocioError("A quantidade de entrada deve ser maior que zero.")
        antes = abaixo_minimo(variacao)
        variacao.estoque_fisico += qtd
        variacao.estoque_modo = EstoqueModo.EXATO
        variacao.rotulo_aprox = None
        mov = self._registrar(db, variacao, TipoMov.ENTRADA, qtd, usuario_id, origem, ref_id=ref_id)
        # Entrada normalmente tira do mínimo, mas ela também converte APROXIMADO em EXATO:
        # a variação pode acabar exata e ainda abaixo do mínimo.
        self._alertar_se_cruzou_minimo(db, variacao, antes)
        return mov

    # ---------------------------------------------------------------- reserva
    def reservar(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        qtd: int,
        usuario_id: int,
        pedido_id: int,
    ) -> MovimentacaoEstoque:
        """Reserva para um pedido. Em EXATO bloqueia se não houver disponível.

        Em APROXIMADO não bloqueia (não há número confiável), mas registra a reserva.
        """
        if qtd <= 0:
            raise RegraNegocioError("A quantidade reservada deve ser maior que zero.")
        if variacao.estoque_modo == EstoqueModo.EXATO and qtd > variacao.disponivel:
            raise RegraNegocioError(
                f"Estoque insuficiente para {_rotulo_variacao(variacao)}: "
                f"disponível {variacao.disponivel}, pedido {qtd}."
            )
        variacao.estoque_reservado += qtd
        return self._registrar(
            db, variacao, TipoMov.RESERVA, qtd, usuario_id, OrigemMov.PEDIDO, ref_id=pedido_id
        )

    # ----------------------------------------------------------------- baixa
    def baixar(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        qtd: int,
        usuario_id: int,
        pedido_id: int,
    ) -> MovimentacaoEstoque:
        """Baixa definitiva (faturamento): reduz físico e reservado."""
        if qtd <= 0:
            raise RegraNegocioError("A quantidade de baixa deve ser maior que zero.")
        if variacao.estoque_modo == EstoqueModo.EXATO and qtd > variacao.estoque_fisico:
            raise RegraNegocioError(
                f"Baixa maior que o físico para {_rotulo_variacao(variacao)}: "
                f"físico {variacao.estoque_fisico}, baixa {qtd}."
            )
        antes = abaixo_minimo(variacao)
        variacao.estoque_fisico -= qtd
        variacao.estoque_reservado = max(0, variacao.estoque_reservado - qtd)
        mov = self._registrar(
            db, variacao, TipoMov.SAIDA, qtd, usuario_id, OrigemMov.PEDIDO, ref_id=pedido_id
        )
        self._alertar_se_cruzou_minimo(db, variacao, antes)
        return mov

    # --------------------------------------------------------------- estorno
    def estornar(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        qtd: int,
        usuario_id: int,
        pedido_id: int,
    ) -> MovimentacaoEstoque:
        """Estorno de reserva (cancelamento de pedido): devolve o reservado."""
        if qtd <= 0:
            raise RegraNegocioError("A quantidade de estorno deve ser maior que zero.")
        variacao.estoque_reservado = max(0, variacao.estoque_reservado - qtd)
        return self._registrar(
            db, variacao, TipoMov.ESTORNO, qtd, usuario_id, OrigemMov.PEDIDO, ref_id=pedido_id
        )

    # ----------------------------------------------------------------- ajuste
    def ajustar(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        novo_saldo: int,
        usuario_id: int,
        motivo: str,
        origem: OrigemMov = OrigemMov.MANUAL,
        ref_id: int | None = None,
    ) -> MovimentacaoEstoque:
        """Ajuste manual / inventário. Motivo OBRIGATÓRIO. Torna a variação EXATA."""
        if not motivo or not motivo.strip():
            raise RegraNegocioError("O motivo do ajuste é obrigatório.")
        if novo_saldo < 0:
            raise RegraNegocioError("O novo saldo não pode ser negativo.")
        antes = abaixo_minimo(variacao)
        diferenca = novo_saldo - variacao.estoque_fisico
        variacao.estoque_fisico = novo_saldo
        variacao.estoque_modo = EstoqueModo.EXATO
        variacao.rotulo_aprox = None
        mov = self._registrar(
            db,
            variacao,
            TipoMov.AJUSTE,
            abs(diferenca),
            usuario_id,
            origem,
            ref_id=ref_id,
            motivo=motivo.strip(),
        )
        self._alertar_se_cruzou_minimo(db, variacao, antes)
        return mov

    # ----------------------------------------------------------- aproximado
    def definir_aproximado(
        self,
        db: Session,
        variacao: ProdutoVariacao,
        rotulo: RotuloAprox,
        usuario_id: int,
        motivo: str = "rótulo aproximado",
    ) -> MovimentacaoEstoque:
        """Marca a variação como APROXIMADO com um rótulo (MUITO/POUCO/TEM/ACABOU)."""
        antes = abaixo_minimo(variacao)
        variacao.estoque_modo = EstoqueModo.APROXIMADO
        variacao.rotulo_aprox = rotulo
        if rotulo == RotuloAprox.ACABOU:
            variacao.estoque_fisico = 0
        mov = self._registrar(
            db, variacao, TipoMov.AJUSTE, 0, usuario_id, OrigemMov.MANUAL, motivo=motivo
        )
        self._alertar_se_cruzou_minimo(db, variacao, antes)
        return mov


estoque_service = EstoqueService()
