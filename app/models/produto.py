from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EstoqueModo, RotuloAprox

if TYPE_CHECKING:
    from app.models.categoria import Categoria
    from app.models.fornecedor import Fornecedor


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


# Espelho da expressão gerada por `app/core/codigos.coluna_normalizada`. Declarada aqui
# para o modelo bater com o banco (o `alembic check` da CI compara os dois); mudar a
# fórmula lá obriga a mudar aqui e a refazer os índices por migration.
_CODIGO_NORM = "upper(regexp_replace({coluna}, '[^A-Za-z0-9]', '', 'g'))"


class Produto(Base):
    __tablename__ = "produtos"
    __table_args__ = (
        Index(
            "ix_produtos_descricao_trgm",
            "descricao",
            postgresql_using="gin",
            postgresql_ops={"descricao": "gin_trgm_ops"},
        ),
        Index(
            "ix_produtos_localizacao_trgm",
            "localizacao",
            postgresql_using="gin",
            postgresql_ops={"localizacao": "gin_trgm_ops"},
        ),
        # Busca por código sem traço e sem caixa ("ch1086" acha "CH-1086"). A expressão
        # tem que ser IDÊNTICA à do `app/core/codigos.coluna_normalizada` — é ela que a
        # consulta gera, e qualquer diferença faz o Postgres ignorar o índice.
        Index(
            "ix_produtos_codigo_norm",
            text(_CODIGO_NORM.format(coluna="codigo")),
            postgresql_using="gin",
            postgresql_ops={_CODIGO_NORM.format(coluna="codigo"): "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    # descricao e localizacao recebem índice GIN trigram na migration (busca do tablet).
    descricao: Mapped[str] = mapped_column(Text)
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"))

    unidades_por_caixa: Mapped[int | None] = mapped_column(Integer)
    localizacao: Mapped[str | None] = mapped_column(String(255))

    preco_pouca_qtd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    preco_muita_qtd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    preco_promocional: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    qtd_corte_atacado: Mapped[int | None] = mapped_column(Integer)
    preco_custo: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Piso de venda por unidade, definido pelo admin. ZERO significa "sem piso" — é o
    # estado de todo produto que ainda não foi revisado, e um piso implícito faria o
    # sistema recusar venda de produto recém-cadastrado. Quem valida é o pedido_service,
    # sobre o preço JÁ com desconto; o admin passa por cima (é ele quem define o número).
    preco_minimo: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    observacao: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    publicar_catalogo: Mapped[bool] = mapped_column(Boolean, default=False)

    categoria: Mapped[Categoria | None] = relationship(back_populates="produtos")
    variacoes: Mapped[list[ProdutoVariacao]] = relationship(
        back_populates="produto", cascade="all, delete-orphan"
    )
    codigos_alt: Mapped[list[ProdutoCodigoAlt]] = relationship(
        back_populates="produto", cascade="all, delete-orphan"
    )
    # `selectin` e não o lazy padrão: o `criar_completo` percorre até 100 itens e a
    # colagem grava linha a linha DENTRO de um savepoint. Com carga preguiçosa por
    # linha, cada item viraria um SELECT extra e a garantia de "3 consultas
    # independente do volume" que a colagem documenta iria embora em silêncio.
    faixas: Mapped[list[ProdutoFaixaPreco]] = relationship(
        back_populates="produto",
        cascade="all, delete-orphan",
        order_by="ProdutoFaixaPreco.min_qtd",
        lazy="selectin",
    )
    especificacoes: Mapped[list[ProdutoEspecificacao]] = relationship(
        back_populates="produto",
        cascade="all, delete-orphan",
        order_by="ProdutoEspecificacao.ordem",
        lazy="selectin",
    )
    # SEMPRE de mão única: `foreign_keys` amarra a coleção ao lado "quem sugere".
    # Sem isso o SQLAlchemy não sabe qual das duas FKs usar — e criar a volta
    # automaticamente encheria o cartão de todo acessório com o produto principal.
    relacionados: Mapped[list[ProdutoRelacionado]] = relationship(
        back_populates="produto",
        cascade="all, delete-orphan",
        order_by="ProdutoRelacionado.ordem",
        foreign_keys="ProdutoRelacionado.produto_id",
        lazy="selectin",
    )


class ProdutoVariacao(Base):
    """O saldo de estoque mora aqui (uma linha por cor)."""

    __tablename__ = "produto_variacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    cor: Mapped[str] = mapped_column(String(80), default="", index=True)

    estoque_modo: Mapped[EstoqueModo] = mapped_column(
        _enum(EstoqueModo, "estoque_modo"), default=EstoqueModo.APROXIMADO
    )
    estoque_fisico: Mapped[int] = mapped_column(Integer, default=0)
    estoque_reservado: Mapped[int] = mapped_column(Integer, default=0)
    rotulo_aprox: Mapped[RotuloAprox | None] = mapped_column(_enum(RotuloAprox, "rotulo_aprox"))
    estoque_minimo: Mapped[int] = mapped_column(Integer, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Foto da variação (cor): o funcionário identifica o modelo visualmente. Upload em /produtos.
    # imagem_dados guarda os bytes do JPEG (redimensionado) no próprio Postgres — offline-first,
    # sem depender de MinIO/S3. imagem_url guarda o caminho da rota que serve a foto
    # ("/produtos/variacao/{id}/foto?v=…"), usado pelo filtro foto_url() nos templates.
    imagem_dados: Mapped[bytes | None] = mapped_column(LargeBinary)
    imagem_url: Mapped[str | None] = mapped_column(String(500))

    produto: Mapped[Produto] = relationship(back_populates="variacoes")

    @property
    def disponivel(self) -> int:
        """Saldo disponível em modo EXATO (físico - reservado)."""
        return self.estoque_fisico - self.estoque_reservado


class ProdutoCodigoAlt(Base):
    __tablename__ = "produto_codigos_alt"
    __table_args__ = (
        Index(
            "ix_produto_codigos_alt_norm",
            text(_CODIGO_NORM.format(coluna="codigo_alt")),
            postgresql_using="gin",
            postgresql_ops={_CODIGO_NORM.format(coluna="codigo_alt"): "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    codigo_alt: Mapped[str] = mapped_column(String(60), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"))

    produto: Mapped[Produto] = relationship(back_populates="codigos_alt")
    fornecedor: Mapped[Fornecedor | None] = relationship()


class ProdutoFaixaPreco(Base):
    """Uma linha da tabela de atacado: a partir de `min_qtd` un, cada uma sai por `preco`.

    Tabela filha e não JSONB de propósito. O psycopg desserializa número JSON como
    `float`, e `Decimal(8.0)` vira 8.000000000000000444… — dinheiro em ponto flutuante é
    exatamente o que o CLAUDE.md §5 proíbe. De quebra, a unicidade de `min_qtd` passa a
    valer no banco, e não só na validação do formulário.

    A regra que usa isto mora em `app/core/precos.py`.
    """

    __tablename__ = "produto_faixas_preco"
    __table_args__ = (UniqueConstraint("produto_id", "min_qtd", name="uq_faixa_produto_min_qtd"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produtos.id", ondelete="CASCADE"), index=True
    )
    min_qtd: Mapped[int] = mapped_column(Integer)
    preco: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    produto: Mapped[Produto] = relationship(back_populates="faixas")


class ProdutoEspecificacao(Base):
    """Uma linha da ficha técnica: "Altura" / "50 cm".

    O índice em `ordem` é NÃO único de propósito: único transformaria trocar duas linhas
    de lugar numa dança de três updates só para não colidir no meio do caminho. São no
    máximo 20 linhas — o service reescreve a lista inteira a cada save, e a ordem sai
    certa por construção.
    """

    __tablename__ = "produto_especificacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produtos.id", ondelete="CASCADE"), index=True
    )
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    rotulo: Mapped[str] = mapped_column(String(40))
    valor: Mapped[str] = mapped_column(String(120))

    produto: Mapped[Produto] = relationship(back_populates="especificacoes")


class ProdutoRelacionado(Base):
    """ "Compre Junto": `produto_id` sugere `relacionado_id`, nessa ordem.

    A chave primária composta é o que impede duplicata no banco, e não só no formulário.

    De MÃO ÚNICA de propósito: capa é acessório de celular, o contrário não. Criar a
    volta automaticamente encheria o cartão de toda capa com o celular — e quem cadastra
    perderia o controle de uma lista que ele nunca escreveu.
    """

    __tablename__ = "produto_relacionados"

    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True
    )
    relacionado_id: Mapped[int] = mapped_column(
        ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True
    )
    ordem: Mapped[int] = mapped_column(Integer, default=0)

    produto: Mapped[Produto] = relationship(
        back_populates="relacionados", foreign_keys=[produto_id]
    )
    # Sem `back_populates`: o produto sugerido não sabe (nem precisa saber) quem o sugere.
    alvo: Mapped[Produto] = relationship(foreign_keys=[relacionado_id], lazy="selectin")
