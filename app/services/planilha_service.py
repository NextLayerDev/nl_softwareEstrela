"""Pedido montado na planilha editável — o mesmo desenho amarelo do resumo, só que cada
célula é um campo.

Duas portas de escrita, e as duas reusam o que o pedido já tem:

1. **Criar** (`/pedidos/planilha`): a planilha inteira vira pedido pelo mesmo
   `criar_completo` da tela nova e já é CONFIRMADA — planilha é venda fechada, como a
   colagem da planilha do dia. Se a confirmação esbarra numa regra (estoque exato
   insuficiente, por exemplo), o pedido fica como rascunho e a tela avisa: melhor o
   trabalho digitado salvo do que perdido.
2. **Salvar rascunho** (planilha aberta na lista): as linhas que só tiveram quantidade ou
   valor mexidos são atualizadas no lugar; as que mudaram de produto, as novas e as
   apagadas são trocadas. Rascunho não mexe em estoque (a reserva nasce no `confirmar`),
   então trocar linha é seguro.

O preço é sempre o que está na célula: é a planilha que o cliente recebe, e o valor ali
já é o negociado. O aviso de piso continua valendo onde já valia (tela do pedido).

NÃO faz commit — o `get_db` fecha a transação.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import (
    DominioError,
    NaoEncontradoError,
    PermissaoNegadaError,
    RegraNegocioError,
)
from app.models.cliente import Cliente
from app.models.enums import OrigemPedido, Perfil, StatusPedido, e_admin
from app.models.pedido import Pedido, PedidoItem
from app.models.produto import ProdutoVariacao
from app.models.usuario import Usuario
from app.repositories.pedido_repo import pedido_repo
from app.schemas.pedido import (
    ConsultaPlanilha,
    ItemAvulsoAdicionar,
    ItemCarrinhoAvulso,
    ItemCarrinhoCatalogo,
    ItemPlanilhaOut,
    LinhaPlanilha,
    PedidoCompletoCreate,
    PedidoPlanilhaSalvar,
    PlanilhaPedidoOut,
    ResolucaoPlanilha,
)
from app.services.colagem_service import colagem_service
from app.services.pedido_service import CENT, pedido_service


def _centavos(valor: Decimal | None) -> int:
    return int((valor or Decimal("0")).quantize(CENT) * 100)


class PlanilhaService:
    # ------------------------------------------------------------- consulta
    def resolver(
        self, db: Session, consultas: list[ConsultaPlanilha], perfil: str
    ) -> list[ResolucaoPlanilha]:
        """Responde as células: código digitado, ou variação já escolhida + quantidade.

        Com `variacao_id` a pergunta é só "quanto custa nesta quantidade" (a faixa de
        atacado muda com a quantidade). Sem ele, casa o código pela escada da colagem.
        """
        saida: list[ResolucaoPlanilha | None] = [None] * len(consultas)
        por_codigo: list[tuple[int, ConsultaPlanilha]] = []
        for i, c in enumerate(consultas):
            if c.variacao_id is not None:
                variacao = db.get(ProdutoVariacao, c.variacao_id)
                if variacao is None or not variacao.ativo or not variacao.produto.ativo:
                    saida[i] = ResolucaoPlanilha(situacao="nada", motivo="produto inativo")
                    continue
                produto = variacao.produto
                preco = pedido_service.sugerir_preco(produto, c.qtd).preco_sugerido
                saida[i] = ResolucaoPlanilha(
                    situacao="ok",
                    variacao_id=variacao.id,
                    codigo=produto.codigo,
                    descricao=produto.descricao,
                    cor=variacao.cor,
                    preco_centavos=_centavos(preco),
                )
            elif c.codigo.strip():
                por_codigo.append((i, c))
            else:
                saida[i] = ResolucaoPlanilha(situacao="nada", motivo="sem código")

        if por_codigo:
            achados = colagem_service.resolver_codigos(db, [c for _, c in por_codigo], perfil)
            for (i, _), achado in zip(por_codigo, achados, strict=True):
                saida[i] = achado
        return [s for s in saida if s is not None]

    # ------------------------------------------------------------- linhas -> itens
    def _variacao_da_linha(
        self, db: Session, linha: LinhaPlanilha, perfil: str, casados: dict[int, int]
    ) -> ProdutoVariacao | None:
        """A variação de catálogo da linha, ou None para item avulso."""
        variacao_id = linha.variacao_id
        if variacao_id is None:
            variacao_id = casados.get(id(linha))
        if variacao_id is None:
            return None
        variacao = db.get(ProdutoVariacao, variacao_id)
        if variacao is None:
            raise RegraNegocioError("Um dos produtos da planilha não existe mais.")
        if not variacao.ativo or not variacao.produto.ativo:
            raise RegraNegocioError(
                f"{variacao.produto.codigo} está inativo e não pode ser vendido."
            )
        return variacao

    def _casar_sem_variacao(
        self, db: Session, linhas: list[LinhaPlanilha], perfil: str
    ) -> dict[int, int]:
        """Última tentativa antes de virar avulso: linha com código mas sem variação.

        Só aceita casamento sem ambiguidade — código de produto com várias cores continua
        avulso em vez de o servidor chutar uma cor.
        """
        soltas = [ln for ln in linhas if ln.variacao_id is None and ln.codigo.strip()]
        if not soltas:
            return {}
        achados = colagem_service.resolver_codigos(
            db,
            [
                ConsultaPlanilha(codigo=ln.codigo, descricao=ln.descricao, qtd=ln.qtd)
                for ln in soltas
            ],
            perfil,
        )
        return {
            id(ln): a.variacao_id
            for ln, a in zip(soltas, achados, strict=True)
            if a.situacao == "ok" and a.variacao_id is not None
        }

    @staticmethod
    def _nome_avulso(linha: LinhaPlanilha) -> str:
        nome = linha.descricao.strip() or linha.codigo.strip()
        if not nome:
            raise RegraNegocioError("Toda linha da planilha precisa de código ou descrição.")
        return nome

    # ------------------------------------------------------------- criar
    def criar(
        self, db: Session, dados: PedidoPlanilhaSalvar, vendedor_id: int, perfil: str
    ) -> tuple[Pedido, str | None]:
        """Cria o pedido da planilha e já confirma. Devolve (pedido, aviso)."""
        casados = self._casar_sem_variacao(db, dados.itens, perfil)
        itens: list[ItemCarrinhoCatalogo | ItemCarrinhoAvulso] = []
        for linha in dados.itens:
            variacao = self._variacao_da_linha(db, linha, perfil, casados)
            if variacao is not None:
                itens.append(
                    ItemCarrinhoCatalogo(
                        tipo="catalogo",
                        variacao_id=variacao.id,
                        qtd=linha.qtd,
                        preco_unit=linha.preco_unit,
                    )
                )
            else:
                itens.append(
                    ItemCarrinhoAvulso(
                        tipo="avulso",
                        nome=self._nome_avulso(linha),
                        codigo=linha.codigo.strip(),
                        qtd=linha.qtd,
                        preco_unit=linha.preco_unit,
                    )
                )

        pedido = pedido_service.criar_completo(
            db,
            PedidoCompletoCreate(
                cliente_id=dados.cliente_id,
                cliente_nome=(dados.cliente_nome or "").strip() or None,
                itens=itens,
            ),
            vendedor_id,
            perfil,
        )
        return pedido, self._confirmar_ou_avisar(db, pedido, vendedor_id)

    def _confirmar_ou_avisar(self, db: Session, pedido: Pedido, usuario_id: int) -> str | None:
        """Confirma dentro de um SAVEPOINT: se uma regra barrar (estoque exato sem saldo,
        por exemplo), a reserva feita pela metade volta e o pedido segue como rascunho."""
        try:
            with db.begin_nested():
                pedido_service.confirmar(db, pedido.id, usuario_id)
        except DominioError as exc:
            db.refresh(pedido)
            return f"Salvo como rascunho — não deu para confirmar: {exc.mensagem}"
        return None

    # ------------------------------------------------------------- abrir
    def montar(self, pedido: Pedido) -> PlanilhaPedidoOut:
        """Estado inicial do editor para um pedido que já existe."""
        return PlanilhaPedidoOut(
            pedido_id=pedido.id,
            numero=f"#{pedido.numero}" if pedido.numero else "RASCUNHO",
            status=pedido.status.value,
            editavel=pedido.status == StatusPedido.RASCUNHO,
            data=pedido.criado_em.strftime("%d/%m/%Y") if pedido.criado_em else "",
            # Sem cadastro e sem nome digitado a célula fica vazia (e não "CONSUMIDOR"),
            # para a pessoa digitar por cima.
            cliente=pedido.nome_cliente if (pedido.cliente_id or pedido.cliente_nome) else "",
            cliente_vinculado=pedido.cliente_id is not None,
            desconto_centavos=_centavos(pedido.desconto_total),
            itens=[
                ItemPlanilhaOut(
                    item_id=item.id,
                    variacao_id=item.produto_variacao_id,
                    codigo=item.codigo_exibido or "",
                    descricao=item.descricao_exibida,
                    cor=item.cor_exibida,
                    qtd=item.qtd,
                    preco_centavos=_centavos(item.preco_unit),
                    desconto_centavos=_centavos(item.desconto),
                )
                for item in pedido.itens
            ],
        )

    # ------------------------------------------------------------- salvar rascunho
    def salvar_rascunho(
        self,
        db: Session,
        pedido_id: int,
        dados: PedidoPlanilhaSalvar,
        usuario_id: int,
        perfil: str,
    ) -> tuple[Pedido, str | None]:
        """Grava a planilha de volta no rascunho e, se pedido, confirma."""
        pedido = pedido_service.carregar_editavel(db, pedido_id)
        existentes: dict[int, PedidoItem] = {item.id: item for item in pedido.itens}

        manter = [
            ln
            for ln in dados.itens
            if ln.item_id is not None and ln.item_id in existentes and not ln.identidade_mudou
        ]
        # Por identidade, não por igualdade: duas linhas iguais digitadas são duas linhas.
        mantidas = {id(ln) for ln in manter}
        novas = [ln for ln in dados.itens if id(ln) not in mantidas]
        ids_mantidos = {ln.item_id for ln in manter}

        # Tudo o que pode dar erro é resolvido ANTES de mexer no pedido.
        casados = self._casar_sem_variacao(db, novas, perfil)
        montados: list[PedidoItem] = []
        for linha in novas:
            variacao = self._variacao_da_linha(db, linha, perfil, casados)
            preco = Decimal(linha.preco_unit).quantize(CENT)
            if variacao is not None:
                montados.append(
                    pedido_service._montar_item(
                        pedido.id, variacao, linha.qtd, None, preco, Decimal("0")
                    )
                )
            else:
                montados.append(
                    pedido_service._montar_item_avulso(
                        pedido.id,
                        ItemAvulsoAdicionar(
                            nome=self._nome_avulso(linha),
                            codigo=linha.codigo.strip(),
                            qtd=linha.qtd,
                            preco_unit=preco,
                        ),
                    )
                )

        for linha in manter:
            item = existentes[linha.item_id]
            preco = Decimal(linha.preco_unit).quantize(CENT)
            if linha.qtd != item.qtd:
                # Quantidade digitada é em unidades: a contagem em caixas deixa de valer.
                item.qtd_caixas = None
            item.qtd = linha.qtd
            item.preco_unit = preco
            item.subtotal = pedido_service._calcular_subtotal(linha.qtd, preco, item.desconto)

        for item_id, item in existentes.items():
            if item_id not in ids_mantidos:
                pedido_repo.remover_item(db, item)
        for item in montados:
            pedido_repo.add_item(db, item)

        # Cliente: com cadastro vinculado o nome é o do cadastro; sem, é o texto da célula.
        if dados.cliente_id is not None and dados.cliente_id != pedido.cliente_id:
            if db.get(Cliente, dados.cliente_id) is None:
                raise NaoEncontradoError("Cliente não encontrado.")
            pedido.cliente_id = dados.cliente_id
            pedido.cliente_nome = None
            pedido.cliente_telefone = None
        elif pedido.cliente_id is None:
            pedido.cliente_nome = (dados.cliente_nome or "").strip()[:160] or None

        db.flush()
        db.refresh(pedido)
        pedido_service._recalcular_total(pedido)
        db.flush()

        aviso = self._confirmar_ou_avisar(db, pedido, usuario_id) if dados.confirmar else None
        return pedido, aviso

    # ------------------------------------------------------------- célula da lista
    # Colunas da lista em planilha que viram campo no clique. Nº, VENDEDOR e STATUS
    # ficam com o admin: numeração, comissão e estoque.
    CAMPOS_ADMIN = frozenset({"numero", "vendedor", "status"})

    def editar_celula(
        self, db: Session, pedido_id: int, campo: str, valor: str, usuario_id: int, perfil: str
    ) -> Pedido:
        if campo in self.CAMPOS_ADMIN and not e_admin(perfil):
            raise PermissaoNegadaError("Só o administrador altera número, vendedor e status.")
        valor = (valor or "").strip()

        if campo == "status":
            try:
                novo = StatusPedido(valor)
            except ValueError as exc:
                raise RegraNegocioError("Status inválido.") from exc
            return pedido_service.alterar_status_livre(db, pedido_id, novo, usuario_id)

        pedido = pedido_repo.get(db, pedido_id)
        if pedido is None:
            raise NaoEncontradoError("Pedido não encontrado.")

        if campo == "numero":
            if pedido.numero is None:
                raise RegraNegocioError("Rascunho ganha número ao ser confirmado.")
            if not valor.isdigit() or not 1 <= int(valor) <= 9_999_999:
                raise RegraNegocioError("Número inválido.")
            numero = int(valor)
            if pedido_repo.numero_em_uso(db, numero, pedido.id):
                raise RegraNegocioError(f"Já existe o pedido #{numero}.")
            pedido.numero = numero
            pedido_repo.acompanhar_sequencia(db, numero)
        elif campo == "data":
            try:
                dia = date.fromisoformat(valor)
            except ValueError as exc:
                raise RegraNegocioError("Data inválida.") from exc
            # Meio-dia no fuso do servidor: o dia não "escorrega" na conversão.
            pedido.criado_em = datetime.combine(dia, time(12, 0)).astimezone()
        elif campo == "cliente":
            # Com cadastro vinculado, o nome digitado vale por cima dele (nome_cliente).
            pedido.cliente_nome = valor[:160] or None
        elif campo == "origem":
            try:
                pedido.origem = OrigemPedido(valor)
            except ValueError as exc:
                raise RegraNegocioError("Origem inválida.") from exc
        elif campo == "vendedor":
            vendedor = db.get(Usuario, int(valor)) if valor.isdigit() else None
            if vendedor is None or not vendedor.ativo or vendedor.perfil == Perfil.DEV:
                raise RegraNegocioError("Vendedor inválido.")
            pedido.vendedor_id = vendedor.id
        else:
            raise RegraNegocioError("Esta coluna não é editável.")
        db.flush()
        db.refresh(pedido)
        return pedido


planilha_service = PlanilhaService()
