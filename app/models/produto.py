from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EstoqueModo, RotuloAprox

if TYPE_CHECKING:
    from app.models.categoria import Categoria
    from app.models.fornecedor import Fornecedor


def _enum(py_enum, nome: str) -> SAEnum:
    return SAEnum(py_enum, name=nome, values_callable=lambda e: [m.value for m in e])


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
    imagem_filename: Mapped[str | None] = mapped_column(String(255))

    produto: Mapped[Produto] = relationship(back_populates="variacoes")

    @property
    def disponivel(self) -> int:
        """Saldo disponível em modo EXATO (físico - reservado)."""
        return self.estoque_fisico - self.estoque_reservado

    @property
    def imagem_url(self) -> str | None:
        """URL local da imagem (servida por /uploads), ou None se não houver foto."""
        if self.imagem_filename:
            return f"/uploads/variacoes/{self.imagem_filename}"
        return None


class ProdutoCodigoAlt(Base):
    __tablename__ = "produto_codigos_alt"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    codigo_alt: Mapped[str] = mapped_column(String(60), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"))

    produto: Mapped[Produto] = relationship(back_populates="codigos_alt")
    fornecedor: Mapped[Fornecedor | None] = relationship()
