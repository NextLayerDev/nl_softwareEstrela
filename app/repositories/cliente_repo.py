from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.pedido import Pedido


def _so_digitos(valor: str | None) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


def _digitos_col(coluna):
    """Versão só-dígitos da coluna, no banco (`regexp_replace`).

    Sem índice: a base de clientes é de centenas de linhas e a busca já vem com debounce
    do HTMX. Se um dia virar gargalo, o caminho é um índice funcional sobre esta mesma
    expressão — não trocar a regra.
    """
    return func.regexp_replace(func.coalesce(coluna, ""), r"\D", "", "g")


class ClienteRepository:
    def get(self, db: Session, cliente_id: int) -> Cliente | None:
        return db.get(Cliente, cliente_id)

    def contar_pedidos(self, db: Session, cliente_id: int) -> int:
        return db.scalar(select(func.count(Pedido.id)).where(Pedido.cliente_id == cliente_id)) or 0

    def listar(
        self, db: Session, incluir_inativos: bool = False, limit: int = 100
    ) -> list[Cliente]:
        stmt = select(Cliente).order_by(Cliente.nome).limit(limit)
        if not incluir_inativos:
            stmt = stmt.where(Cliente.ativo.is_(True))
        return list(db.scalars(stmt))

    def busca_rapida(self, db: Session, termo: str, limit: int = 30) -> list[Cliente]:
        termo = termo.strip()
        condicoes = [
            Cliente.nome.ilike(f"%{termo}%"),
            Cliente.cnpj_cpf.ilike(f"%{termo}%"),
            Cliente.telefone.ilike(f"%{termo}%"),
            Cliente.telefone2.ilike(f"%{termo}%"),
        ]
        # Telefone é digitado de todo jeito: "11999998888", "(11) 99999-8888", "11 99999
        # 8888". O ILIKE cru só acha quem digitou exatamente como está gravado, então
        # quando o termo tem dígitos suficientes comparamos só os dígitos dos dois lados.
        digitos = _so_digitos(termo)
        if len(digitos) >= 4:
            condicoes.append(_digitos_col(Cliente.telefone).like(f"%{digitos}%"))
            condicoes.append(_digitos_col(Cliente.telefone2).like(f"%{digitos}%"))
            condicoes.append(_digitos_col(Cliente.cnpj_cpf).like(f"%{digitos}%"))
        stmt = (
            select(Cliente)
            .where(Cliente.ativo.is_(True), or_(*condicoes))
            .order_by(Cliente.nome)
            .limit(limit)
        )
        return list(db.scalars(stmt))

    def por_telefone(self, db: Session, telefone: str) -> Cliente | None:
        """Cliente ativo cujo telefone tem exatamente os mesmos dígitos.

        Base do vínculo automático do pedido: o vendedor digita o telefone e o pedido
        se amarra ao cadastro que já existe, sem ele precisar procurar. Exige 8 dígitos
        (um fixo sem DDD) — menos que isso casaria com meia agenda.
        """
        digitos = _so_digitos(telefone)
        if len(digitos) < 8:
            return None
        stmt = (
            select(Cliente)
            .where(
                Cliente.ativo.is_(True),
                or_(
                    _digitos_col(Cliente.telefone) == digitos,
                    _digitos_col(Cliente.telefone2) == digitos,
                ),
            )
            .order_by(Cliente.id)
            .limit(1)
        )
        return db.scalar(stmt)

    def add(self, db: Session, cliente: Cliente) -> Cliente:
        db.add(cliente)
        db.flush()
        return cliente


cliente_repo = ClienteRepository()
