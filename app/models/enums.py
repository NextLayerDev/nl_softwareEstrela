from __future__ import annotations

import enum


class Perfil(enum.StrEnum):
    ADMIN = "admin"
    VENDEDOR = "vendedor"
    FINANCEIRO = "financeiro"
    FUNCIONARIO = "funcionario"
    # Perfil de manutenção (quem cuida do sistema, não quem opera a empresa).
    # Passa em qualquer require_role e é o ÚNICO que enxerga /deploy — nem o admin vê.
    # Não é atribuível pela tela de usuários: ver PERFIS_ATRIBUIVEIS.
    DEV = "dev"


class EstoqueModo(enum.StrEnum):
    EXATO = "EXATO"
    APROXIMADO = "APROXIMADO"


class RotuloAprox(enum.StrEnum):
    MUITO = "MUITO"
    POUCO = "POUCO"
    TEM = "TEM"
    ACABOU = "ACABOU"


class TipoMov(enum.StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"
    AJUSTE = "ajuste"
    RESERVA = "reserva"
    ESTORNO = "estorno"


class OrigemMov(enum.StrEnum):
    PEDIDO = "pedido"
    INVENTARIO = "inventario"
    IMPORTACAO = "importacao"
    MANUAL = "manual"


class StatusPedido(enum.StrEnum):
    RASCUNHO = "rascunho"
    CONFIRMADO = "confirmado"
    SEPARACAO = "separacao"
    SEPARADO = "separado"  # separação concluída, aguardando faturamento
    FATURADO = "faturado"
    ENTREGUE = "entregue"
    CANCELADO = "cancelado"


class OrigemPedido(enum.StrEnum):
    LOCAL = "local"
    CATALOGO = "catalogo"
    WHATSAPP = "whatsapp"


class StatusConta(enum.StrEnum):
    PENDENTE = "pendente"
    PAGO = "pago"
    ATRASADO = "atrasado"


class StatusInventario(enum.StrEnum):
    ABERTO = "aberto"
    APLICADO = "aplicado"


class CategoriaCliente(enum.StrEnum):
    """Categoria de risco/qualidade do cliente, exibida como uma cor no cadastro."""

    MUITO_BOM = "muito_bom"
    BOM = "bom"
    RUIM = "ruim"


# Rótulo + cor (hex) de cada categoria, para o template renderizar a bolinha via style inline.
CATEGORIA_CLIENTE_INFO: dict[str, dict[str, str]] = {
    CategoriaCliente.MUITO_BOM.value: {"rotulo": "Muito bom", "cor": "#16a34a"},  # verde
    CategoriaCliente.BOM.value: {"rotulo": "Bom", "cor": "#2563eb"},  # azul
    CategoriaCliente.RUIM.value: {"rotulo": "Ruim", "cor": "#dc2626"},  # vermelho
}


# Perfis que NÃO podem ver preço de custo / margem (doc 01 §7).
PERFIS_SEM_CUSTO = {Perfil.VENDEDOR.value, Perfil.FUNCIONARIO.value}

# Perfis oferecidos na tela de usuários. `dev` fica DE FORA de propósito: se ele
# aparecesse no select, o admin da empresa criaria um usuário dev e se auto-promoveria
# para a tela de manutenção — que reinicia o sistema. Um dev só nasce pelo seed ou por
# outro dev.
PERFIS_ATRIBUIVEIS = [p.value for p in Perfil if p is not Perfil.DEV]


def e_dev(perfil: str) -> bool:
    return perfil == Perfil.DEV.value


def e_admin(perfil: str) -> bool:
    """Poder de admin. O dev tem tudo que o admin tem, e mais.

    Use isto no lugar de `perfil == "admin"`: os flags de UI (pode_editar, pode_ajuste…)
    decidem se o botão aparece, e sem isto o dev abriria as telas sem botão nenhum.
    """
    return perfil in (Perfil.ADMIN.value, Perfil.DEV.value)


def tem_perfil(perfil: str, *perfis: str) -> bool:
    """Como `perfil in perfis`, mas o dev passa em qualquer conjunto."""
    return e_dev(perfil) or perfil in perfis
