from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.controllers.pedido_controller import pedido_controller
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.numeros_br import parse_decimal_br
from app.core.templates import templates
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.enums import StatusPedido
from app.models.pedido import Pedido
from app.models.usuario import Usuario
from app.repositories.cliente_repo import cliente_repo
from app.repositories.estoque_repo import estoque_repo
from app.schemas.pedido import (
    ItemAdicionar,
    ItemAvulsoAdicionar,
    PedidoCompletoCreate,
    PedidoCreate,
)
from app.services.empresa_service import empresa_service
from app.services.pedido_service import pedido_service

router = APIRouter()

_CRIA = ("admin", "vendedor")


def _to_decimal(valor: str | None) -> Decimal:
    """Campo de dinheiro do formulário. Ilegível vale zero — o service valida o resto."""
    lido = parse_decimal_br(valor)
    return lido if lido is not None else Decimal("0")


# ===================================================================== LISTAR
@router.get("/pedidos", response_class=HTMLResponse)
def index_pedidos(
    request: Request,
    status: str = "",
    origem: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Lista de pedidos, com os filtros de status e origem na query string.

    Filtrar no servidor (e não escondendo linha no navegador) é o que faz o realtime
    continuar honesto: quando um pedido muda de status em outro terminal, a lista é
    re-buscada JÁ com o filtro aplicado, em vez de reaparecer uma linha que o filtro
    tinha tirado.
    """
    pedidos = pedido_controller.listar(db, usuario, status, origem)
    contexto = {
        "user": usuario,
        "titulo": "Pedidos",
        "pedidos": pedidos,
        "filtro_status": status,
        "filtro_origem": origem,
    }
    return templates.TemplateResponse(request, "pedidos/index.html", contexto)


# Fragmento da lista, re-buscado pelo realtime a cada mudança de status.
# Antes de /pedidos/{pedido_id}: aquela rota casa por ordem e tentaria ler "lista" como int.
# Reusa o controller da listagem, então o vendedor continua vendo só os pedidos dele.
@router.get("/pedidos/lista", response_class=HTMLResponse)
def fragmento_lista(
    request: Request,
    status: str = "",
    origem: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Só as linhas. Serve os dois gatilhos: os filtros da tela e o realtime."""
    pedidos = pedido_controller.listar(db, usuario, status, origem)
    return templates.TemplateResponse(
        request, "pedidos/_linhas.html", {"user": usuario, "pedidos": pedidos}
    )


# ===================================================================== NOVO
@router.get("/pedidos/novo", response_class=HTMLResponse)
def novo_pedido(
    request: Request,
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    contexto = {"user": usuario, "titulo": "Novo pedido"}
    return templates.TemplateResponse(request, "pedidos/novo.html", contexto)


@router.post("/pedidos")
def criar_pedido(
    cliente_id: str = Form(""),
    cliente_nome: str = Form(""),
    cliente_telefone: str = Form(""),
    observacao: str = Form(""),
    desconto_total: str = Form("0"),
    itens_json: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Cria o pedido inteiro: cliente + itens do carrinho, numa tacada só.

    Os três campos de cliente são opcionais (venda de balcão). `cliente_id` chega como
    string porque é um input hidden preenchido por JS: vazio quando o vendedor só
    digitou o nome, e um id quando ele clicou numa sugestão.

    `itens_json` é o carrinho montado no navegador. Vem como JSON num hidden e não como
    campos repetidos do formulário porque cada linha tem forma diferente (catálogo x
    avulso) — desempacotar isso de `item[0][tipo]` seria reinventar um parser pior.
    O preço de cada linha ainda é resolvido no servidor; o navegador só manda o que o
    vendedor digitou por cima.
    """
    try:
        itens = json.loads(itens_json) if itens_json.strip() else []
    except ValueError as exc:
        raise RegraNegocioError("Não consegui ler os itens do pedido.") from exc
    if not isinstance(itens, list) or not itens:
        raise RegraNegocioError("Adicione ao menos um item ao pedido.")

    try:
        dados = PedidoCompletoCreate(
            cliente_id=int(cliente_id) if cliente_id.strip().isdigit() else None,
            cliente_nome=cliente_nome or None,
            cliente_telefone=cliente_telefone or None,
            observacao=observacao or None,
            desconto_total=_to_decimal(desconto_total),
            itens=itens,
        )
    except ValidationError as exc:
        raise RegraNegocioError("Há itens inválidos no pedido.") from exc

    pedido = pedido_controller.criar_completo(db, dados, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido.id}", status_code=303)


@router.post("/pedidos/resolver-colagem")
def resolver_colagem(
    texto: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Casa o texto colado com o catálogo e devolve as linhas — SEM gravar nada.

    Serve a aba "Colar itens" da tela de novo pedido: as linhas caem no carrinho do
    navegador e só viram pedido no submit. Responde JSON porque o destino é estado de
    cliente (o carrinho do Alpine); devolver fragmento HTML só para o JS reparsear
    custaria mais. É a única rota JSON daqui — `app/routers/` segue reservado para a
    API da Fase 2.
    """
    linhas = pedido_controller.resolver_colagem(db, texto, usuario)
    return JSONResponse({"linhas": [linha.model_dump(mode="json") for linha in linhas]})


# ===================================================================== COLAR PLANILHA
# Antes de /pedidos/{pedido_id} pelo mesmo motivo da rota "lista": o path casa por ordem.
#
# As duas rotas abaixo respondem SEMPRE 200, mesmo quando nada casou. O htmx descarta o
# corpo de respostas 4xx (`responseHandling` manda `swap:false` para "[45].."), então
# mandar as pendências como erro seria mandá-las para o lixo — o vendedor veria o botão
# não fazer nada. Pendência é conteúdo, não falha.
@router.post("/pedidos/colar", response_class=HTMLResponse)
def colar_pedido_novo(
    request: Request,
    texto: str = Form(""),
    cliente_id: str = Form(""),
    cliente_nome: str = Form(""),
    cliente_telefone: str = Form(""),
    observacao: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Cola a planilha e já abre os pedidos (painel da tela `/pedidos/novo`).

    Uma planilha do dia traz vários blocos empilhados e vira UM PEDIDO POR BLOCO. Um
    bloco só continua caindo no caminho de sempre: casando tudo, redireciona direto
    para o pedido pronto.

    Os campos de cliente vêm por `hx-include` do formulário ao lado, então quem o
    vendedor digitou (ou vinculou) vale como padrão — o código no topo de cada bloco
    tem precedência sobre ele.
    """
    dados = PedidoCreate(
        cliente_id=int(cliente_id) if cliente_id.strip().isdigit() else None,
        cliente_nome=cliente_nome or None,
        cliente_telefone=cliente_telefone or None,
        observacao=observacao or None,
    )
    lote = pedido_controller.criar_colando(db, dados, texto, usuario)

    if len(lote.pedidos) == 1:
        unico = lote.pedidos[0]
        # Casou tudo: não há o que conferir, manda direto para o pedido pronto.
        if unico.resultado.tudo_casou:
            return HTMLResponse("", headers={"HX-Redirect": f"/pedidos/{unico.pedido_id}"})
        contexto = {
            "user": usuario,
            "pedido": db.get(Pedido, unico.pedido_id),
            "resultado": unico.resultado,
        }
        return templates.TemplateResponse(request, "pedidos/_colagem_resultado.html", contexto)

    contexto = {"user": usuario, "lote": lote}
    return templates.TemplateResponse(request, "pedidos/_colagem_lote.html", contexto)


@router.get("/pedidos/{pedido_id}/colagem", response_class=HTMLResponse)
def painel_colagem(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Devolve o painel de colagem vazio — o "colar outra planilha" do resultado."""
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido}
    return templates.TemplateResponse(request, "pedidos/_colagem_painel.html", contexto)


@router.post("/pedidos/{pedido_id}/colar", response_class=HTMLResponse)
def colar_itens(
    request: Request,
    pedido_id: int,
    texto: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Acrescenta em bloco os itens colados a um rascunho já aberto.

    A resposta é toda OOB (o form usa `hx-swap="none"`), mesmo padrão do estoque/_oob:
    atualiza o painel de resultado, a tabela de itens e os botões do pedido de uma vez.
    """
    resultado = pedido_controller.colar_itens(db, pedido_id, texto, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {
        "user": usuario,
        "pedido": pedido,
        "resultado": resultado,
        "editavel": True,
        "oob": True,
        "oob_bloco": True,
    }
    return templates.TemplateResponse(request, "pedidos/_colagem_resultado.html", contexto)


@router.get("/pedidos/busca-cliente", response_class=HTMLResponse)
def busca_cliente(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Sugestões de cliente por nome, telefone ou documento (fragmento HTMX).

    Um caractere só devolveria a base inteira a cada tecla; a partir de dois já vale a
    consulta. A mesma rota serve os dois campos da tela — quem chama manda o que o
    vendedor está digitando, seja nome ou telefone.
    """
    termo = q.strip()
    clientes = cliente_repo.busca_rapida(db, termo, limit=8) if len(termo) >= 2 else []
    contexto = {"user": usuario, "clientes": clientes}
    return templates.TemplateResponse(request, "pedidos/_busca_cliente.html", contexto)


# ===================================================================== BUSCA ITEM (HTMX)
@router.get("/pedidos/busca-item", response_class=HTMLResponse)
def busca_item(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Busca de produto no contexto do pedido: cada resultado é um botão 'Selecionar'."""
    variacoes = estoque_repo.busca_localizacao(db, q) if q else []
    contexto = {"user": usuario, "variacoes": variacoes}
    return templates.TemplateResponse(request, "pedidos/_busca_resultado.html", contexto)


# ===================================================================== DETALHE
@router.get("/pedidos/{pedido_id}", response_class=HTMLResponse)
def detalhe_pedido(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {
        "user": usuario,
        "titulo": f"Pedido #{pedido.numero or pedido.id}",
        "pedido": pedido,
        "editavel": pedido.status == StatusPedido.RASCUNHO,
    }
    return templates.TemplateResponse(request, "pedidos/detalhe.html", contexto)


@router.get("/pedidos/{pedido_id}/estado", response_class=HTMLResponse)
def fragmento_estado(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Selo de status + botões do pedido, re-buscados quando ele muda em outro terminal.

    Passa pelo mesmo `pedido_controller.get`, então o vendedor segue sem enxergar pedido
    de outro vendedor.
    """
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {
        "user": usuario,
        "pedido": pedido,
        "editavel": pedido.status == StatusPedido.RASCUNHO,
    }
    return templates.TemplateResponse(request, "pedidos/_estado_oob.html", contexto)


# ===================================================================== SALDO (HTMX)
@router.get("/pedidos/saldo/{variacao_id}", response_class=HTMLResponse)
def saldo_variacao(
    request: Request,
    variacao_id: int,
    qtd: int = 1,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Mostra disponível/selo + sugestão de preço por faixa ao escolher variação."""
    variacao = estoque_repo.get_variacao(db, variacao_id)
    if variacao is None:
        raise NaoEncontradoError("Variação não encontrada.")
    sugestao = pedido_service.sugerir_preco(variacao.produto, max(qtd, 1))
    contexto = {
        "user": usuario,
        "variacao": variacao,
        "produto": variacao.produto,
        "sugestao": sugestao,
        "qtd": qtd,
    }
    return templates.TemplateResponse(request, "pedidos/_saldo.html", contexto)


# ===================================================================== ITENS
@router.post("/pedidos/{pedido_id}/itens", response_class=HTMLResponse)
def adicionar_item(
    request: Request,
    pedido_id: int,
    variacao_id: int = Form(...),
    qtd: int | None = Form(None),
    qtd_caixas: int | None = Form(None),
    preco_unit: str | None = Form(None),
    desconto: str | None = Form(None),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    dados = ItemAdicionar(
        variacao_id=variacao_id,
        qtd=qtd or None,
        qtd_caixas=qtd_caixas or None,
        preco_unit=_to_decimal(preco_unit) if preco_unit else None,
        desconto=_to_decimal(desconto),
    )
    pedido_controller.adicionar_item(db, pedido_id, dados, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True, "oob": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


@router.post("/pedidos/{pedido_id}/itens-avulsos", response_class=HTMLResponse)
def adicionar_item_avulso(
    request: Request,
    pedido_id: int,
    nome: str = Form(...),
    codigo: str = Form(""),
    detalhe: str = Form(""),
    qtd: int = Form(1),
    preco_unit: str = Form("0"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Lança no rascunho um item que não está no catálogo (produto acabou, item novo)."""
    dados = ItemAvulsoAdicionar(
        nome=nome,
        codigo=codigo,
        detalhe=detalhe,
        qtd=max(qtd, 1),
        preco_unit=_to_decimal(preco_unit),
    )
    pedido_controller.adicionar_item_avulso(db, pedido_id, dados, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True, "oob": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


@router.delete("/pedidos/{pedido_id}/itens/{item_id}", response_class=HTMLResponse)
def remover_item(
    request: Request,
    pedido_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.remover_item(db, pedido_id, item_id, usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True, "oob": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


@router.post("/pedidos/{pedido_id}/desconto", response_class=HTMLResponse)
def aplicar_desconto(
    request: Request,
    pedido_id: int,
    desconto: str = Form("0"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.aplicar_desconto_total(db, pedido_id, _to_decimal(desconto), usuario)
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "editavel": True, "oob": True}
    return templates.TemplateResponse(request, "pedidos/_itens.html", contexto)


# ===================================================================== CICLO
@router.post("/pedidos/{pedido_id}/confirmar")
def confirmar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.confirmar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.post("/pedidos/{pedido_id}/cancelar")
def cancelar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.cancelar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.post("/pedidos/{pedido_id}/faturar")
def faturar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role("admin")),
):
    pedido_controller.faturar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


@router.post("/pedidos/{pedido_id}/entregar")
def entregar_pedido(
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido_controller.entregar(db, pedido_id, usuario)
    return RedirectResponse(url=f"/pedidos/{pedido_id}", status_code=303)


# ===================================================================== IMPRESSÃO
@router.get("/pedidos/{pedido_id}/imprimir", response_class=HTMLResponse)
def imprimir_pedido(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido}
    return templates.TemplateResponse(request, "pedidos/impressao_pedido.html", contexto)


@router.get("/pedidos/{pedido_id}/cupom", response_class=HTMLResponse)
def cupom_pedido(
    request: Request,
    pedido_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_role(*_CRIA)),
):
    """Comprovante não fiscal em formato cupom (bobina 80mm)."""
    pedido = pedido_controller.get(db, pedido_id, usuario)
    contexto = {"user": usuario, "pedido": pedido, "empresa": empresa_service.obter(db)}
    return templates.TemplateResponse(request, "pedidos/cupom.html", contexto)
