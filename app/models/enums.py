from __future__ import annotations

import enum


class Perfil(enum.StrEnum):
    ADMIN = "admin"
    VENDEDOR = "vendedor"
    FINANCEIRO = "financeiro"
    FUNCIONARIO = "funcionario"


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
