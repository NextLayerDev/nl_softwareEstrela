from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import ARRAY, Text, cast, func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.models.inventario import InventarioItem
from app.models.movimentacao import MovimentacaoEstoque
from app.models.pedido import PedidoItem
from app.models.produto import Produto, ProdutoCodigoAlt, ProdutoVariacao

_SEM_ALFANUM = re.compile(r"[^A-Z0-9]")


def _codigo_normalizado(coluna):  # noqa: ANN001, ANN202 - expressão SQL
    """Espelho SQL do `normalizar_codigo` do `app/core/colagem.py`."""
    return func.regexp_replace(func.upper(coluna), "[^A-Z0-9]", "", "g")


class ProdutoRepository:
    def get(self, db: Session, produto_id: int) -> Produto | None:
        return db.get(Produto, produto_id)

    def get_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao | None:
        return db.get(ProdutoVariacao, variacao_id)

    def get_by_codigo(self, db: Session, codigo: str) -> Produto | None:
        return db.scalar(select(Produto).where(Produto.codigo == codigo.strip()))

    def listar(
        self,
        db: Session,
        incluir_inativos: bool = False,
        limit: int = 100,
        offset: int = 0,
        categoria_id: int | None = None,
    ) -> list[Produto]:
        stmt = (
            select(Produto)
            .options(
                selectinload(Produto.variacoes),
                selectinload(Produto.codigos_alt),
                selectinload(Produto.categoria),
            )
            .order_by(Produto.descricao)
            .limit(limit)
            .offset(offset)
        )
        if not incluir_inativos:
            stmt = stmt.where(Produto.ativo.is_(True))
        if categoria_id is not None:
            stmt = stmt.where(Produto.categoria_id == categoria_id)
        return list(db.scalars(stmt))

    def busca_rapida(
        self, db: Session, termo: str, limit: int = 20, categoria_id: int | None = None
    ) -> list[Produto]:
        """Busca por pg_trgm na descrição + match de substring no código.

        - Código: `ilike('%termo%')` — basta um pedaço do código (ex: `708`
          casa com `K-708`), não precisa digitar desde o início.
        - Descrição: trigram (ranking) + fallback `ilike('%termo%')` para garantir
          que qualquer pedaço case, inclusive curto, mesmo quando a similaridade por
          trigramas fica abaixo do limiar padrão (0.3) do Postgres.
        - `categoria_id` (opcional): restringe o resultado a uma categoria, combinando
          com a busca por texto.
        """
        termo = termo.strip()
        stmt = (
            select(Produto)
            .options(
                selectinload(Produto.variacoes),
                selectinload(Produto.codigos_alt),
                selectinload(Produto.categoria),
            )
            .where(
                or_(
                    Produto.codigo.ilike(f"%{termo}%"),
                    Produto.descricao.op("%")(termo),
                    Produto.descricao.ilike(f"%{termo}%"),
                )
            )
            .order_by(func.similarity(Produto.descricao, termo).desc())
            .limit(limit)
        )
        if categoria_id is not None:
            stmt = stmt.where(Produto.categoria_id == categoria_id)
        return list(db.scalars(stmt))

    # ------------------------------------------------------------- colagem de pedido
    def catalogo_por_codigos(self, db: Session, codigos: Sequence[str]) -> list[Produto]:
        """Produtos cujo código — ou código alternativo — casa com algum dos colados.

        Casa por igualdade exata (upper) e por igualdade NORMALIZADA (só letras e
        dígitos), que é o que faz "K708" achar o "K-708" do cadastro.

        Uma query para a colagem inteira: 200 linhas coladas não podem virar 200 idas ao
        banco. Traz variações e códigos alternativos carregados junto, porque quem
        escolhe a variação é o service, em memória, sem uma segunda rodada de queries.
        """
        alvos = {c.strip().upper() for c in codigos if c and c.strip()}
        if not alvos:
            return []
        normalizados = {n for n in (_SEM_ALFANUM.sub("", c) for c in alvos) if n}

        sub_alt = select(ProdutoCodigoAlt.produto_id).where(
            or_(
                func.upper(ProdutoCodigoAlt.codigo_alt).in_(alvos),
                _codigo_normalizado(ProdutoCodigoAlt.codigo_alt).in_(normalizados),
            )
        )
        stmt = (
            select(Produto)
            .options(selectinload(Produto.variacoes), selectinload(Produto.codigos_alt))
            .where(
                or_(
                    func.upper(Produto.codigo).in_(alvos),
                    _codigo_normalizado(Produto.codigo).in_(normalizados),
                    Produto.id.in_(sub_alt),
                )
            )
        )
        return list(db.scalars(stmt))

    def melhores_por_descricao(
        self, db: Session, termos: Sequence[str], por_termo: int = 3
    ) -> dict[str, list[tuple[int, float]]]:
        """Para cada termo, os produtos ativos mais parecidos + a similaridade (0..1).

        Um round-trip para TODOS os termos, via `unnest(...) WITH ORDINALITY` + `LATERAL`
        — é o fallback de quem não casou por código, e uma query por linha colada
        derrubaria a ideia. O `%` é o operador do pg_trgm, que usa o índice GIN
        `ix_produtos_descricao_trgm`; o ranking sai do `similarity()` e o corte entre
        "casou" e "dúvida" é decisão do service, não daqui.
        """
        limpos = [t.strip() for t in termos if t and t.strip()]
        if not limpos:
            return {}

        # `render_derived` é o que emite o "AS t(termo, ord)" — sem a lista de colunas o
        # Postgres não enxerga `t.termo` de dentro do LATERAL.
        tabela_termos = (
            func.unnest(cast(limpos, ARRAY(Text)))
            .table_valued("termo", with_ordinality="ord")
            .render_derived(name="termos_colados")
        )
        similaridade = func.similarity(Produto.descricao, tabela_termos.c.termo)
        melhores = (
            select(Produto.id.label("produto_id"), similaridade.label("sim"))
            .where(
                Produto.ativo.is_(True),
                Produto.descricao.op("%")(tabela_termos.c.termo),
            )
            .order_by(similaridade.desc(), Produto.id)
            .limit(por_termo)
            .lateral()
        )
        stmt = select(tabela_termos.c.termo, melhores.c.produto_id, melhores.c.sim).select_from(
            tabela_termos.join(melhores, true())
        )

        saida: dict[str, list[tuple[int, float]]] = {}
        for termo, produto_id, sim in db.execute(stmt):
            saida.setdefault(termo, []).append((produto_id, float(sim)))
        return saida

    def add(self, db: Session, produto: Produto) -> Produto:
        db.add(produto)
        db.flush()
        return produto

    def variacao_por_cor(self, db: Session, produto_id: int, cor: str) -> ProdutoVariacao | None:
        """Busca a variação ativa de um produto pelo nome da cor (idempotência ao adicionar)."""
        return db.scalar(
            select(ProdutoVariacao).where(
                ProdutoVariacao.produto_id == produto_id,
                ProdutoVariacao.cor == cor,
                ProdutoVariacao.ativo.is_(True),
            )
        )

    def variacao_tem_historico(self, db: Session, variacao_id: int) -> bool:
        """True se a variação já foi usada em movimentações, pedidos ou inventário.

        As FKs de `movimentacoes_estoque`, `pedido_itens` e `inventario_itens` para
        `produto_variacoes` são RESTRICT (sem ondelete), então um hard-delete de uma
        variação com histórico seria barrado pelo Postgres. Esta checagem permite
        dar uma mensagem amigável e, em vez de deletar, inativar a variação.
        """
        existe_mov = db.scalar(
            select(func.count())
            .select_from(MovimentacaoEstoque)
            .where(MovimentacaoEstoque.produto_variacao_id == variacao_id)
        )
        if existe_mov:
            return True
        existe_pedido = db.scalar(
            select(func.count())
            .select_from(PedidoItem)
            .where(PedidoItem.produto_variacao_id == variacao_id)
        )
        if existe_pedido:
            return True
        existe_inv = db.scalar(
            select(func.count())
            .select_from(InventarioItem)
            .where(InventarioItem.produto_variacao_id == variacao_id)
        )
        return bool(existe_inv)


produto_repo = ProdutoRepository()
