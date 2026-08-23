"""Testes de regressão das melhorias de UI/acessibilidade/usabilidade."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _admin() -> TestClient:
    c = TestClient(app)
    r = c.post(
        "/login",
        data={"email": "admin@estrela.local", "senha": "estrela123"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return c


def test_login_tem_aviso_caps_lock_e_meta_ios() -> None:
    t = TestClient(app).get("/login").text
    assert "caps-hint" in t
    assert "apple-mobile-web-app-capable" in t


def test_tabelas_tem_scope_col() -> None:
    """Acessibilidade: cabeçalhos de tabela com scope."""
    for url in ("/clientes", "/produtos", "/", "/usuarios"):
        t = _admin().get(url).text
        assert 'scope="col"' in t, url


def test_lista_clientes_usa_modal_de_confirmacao() -> None:
    t = _admin().get("/clientes").text
    # macro confirmar_botao (data-action) + modal (dialog acessível)
    assert "data-action=" in t
    assert 'role="dialog"' in t
    # não deve mais usar o confirm() nativo nas linhas
    assert "return confirm(" not in t


def test_flash_de_sucesso_aparece_via_query_ok() -> None:
    t = _admin().get("/clientes?ok=Cliente+salvo+com+sucesso.").text
    assert "alerta-ok" in t
    assert "Cliente salvo com sucesso." in t


def test_categoria_cliente_tem_aria_label(db) -> None:
    """Cor não é o único indicador da categoria (WCAG 1.4.1)."""
    from app.core.templates import templates
    from app.models.enums import CATEGORIA_CLIENTE_INFO

    class _Cli:
        nome = "X"
        cnpj_cpf = telefone = vendedor = condicao_pagto_padrao = None
        categoria = "ruim"

    html = templates.get_template("clientes/_linhas.html").render(
        clientes=[_Cli()], categorias=CATEGORIA_CLIENTE_INFO, pode_editar=False
    )
    assert 'role="img"' in html
    assert "Categoria: Ruim" in html


# ------------------------------------------------- padrão único das telas de lista
def test_fila_de_separacao_usa_o_selo_por_etapa() -> None:
    """Regressão: a fila pintava TODA linha de azul, com o enum cru dentro.

    `_fila.html` tinha `<span class="selo-info">{{ p.status }}</span>` fixo, então um
    pedido já conferido aparecia igualzinho a um que ninguém tinha tocado — na tela
    cujo trabalho é justamente distinguir os dois.
    """
    from datetime import datetime
    from types import SimpleNamespace

    from app.core.templates import templates
    from app.models.enums import StatusPedido

    pedido = SimpleNamespace(
        id=1,
        numero=42,
        nome_cliente="MARIA",
        itens=[1, 2, 3],
        status=StatusPedido.SEPARADO,
        criado_em=datetime(2026, 8, 21, 14, 30),
    )
    html = templates.env.get_template("separacao/_fila.html").render(pedidos=[pedido])

    assert "selo-separado" in html
    assert "Separado" in html
    assert "selo-info" not in html


def test_listas_poem_os_filtros_fora_do_card() -> None:
    """Mesmo desenho de /pedidos: lede, filtros soltos e a tabela dentro do card.

    Antes cada tela punha a busca num lugar diferente — dentro do card em umas, fora
    em outras — e era isso que fazia as telas parecerem de sistemas diferentes ENTRE SI.
    """
    for url, marcador in (
        ("/produtos", "Buscar por código ou descrição"),
        ("/estoque", "Buscar por código, descrição, cor ou localização"),
        ("/clientes", "Buscar por nome, CNPJ/CPF ou telefone"),
    ):
        t = _admin().get(url).text
        corpo = t.split("<main", 1)[1]
        assert marcador in corpo, url
        # a busca vem ANTES do primeiro card da tela
        assert corpo.index(marcador) < corpo.index('class="card"'), url


def test_migalhas_ficam_no_bloco_breadcrumbs() -> None:
    """Dentro do `content` elas saíam ABAIXO do título, invertendo a hierarquia."""
    for url in ("/financeiro", "/relatorios", "/relatorios/vendas"):
        t = _admin().get(url).text
        assert 'class="trilha"' in t, url
        # a trilha pertence ao cabeçalho: vem antes do <main>
        assert t.index('class="trilha"') < t.index("<main"), url


def test_estados_vazios_usam_o_componente() -> None:
    """`.vazio` em todo lugar — três telas improvisavam o seu."""
    from app.core.templates import templates

    for nome, ctx in (
        ("financeiro/_linhas.html", {"contas": []}),
        ("estoque/_cartoes_local.html", {"variacoes": [], "q": ""}),
        ("separacao/_fila.html", {"pedidos": []}),
    ):
        html = templates.env.get_template(nome).render(**ctx)
        assert 'class="vazio' in html, nome
        assert "vazio-icone" in html, nome


def test_compre_junto_usa_o_mesmo_contrato_da_busca() -> None:
    """É essa igualdade que faz o `selecionar($el)` das duas telas funcionar sem JS novo.

    Se um dos fragmentos ganhar um `data-*` que o outro não tem, clicar num acessório
    passa a lançar um item com menos informação que clicar num resultado da busca — e o
    preço da prévia sai errado sem nada quebrar.
    """
    import re

    from app.core.templates import templates

    fontes = [
        templates.env.loader.get_source(templates.env, nome)[0]
        for nome in ("pedidos/_busca_resultado.html", "pedidos/_compre_junto.html")
    ]
    # Os dois têm que emitir pela MESMA macro, não por listas copiadas.
    for fonte in fontes:
        assert "dados_variacao(v, user.perfil)" in fonte
        assert not re.search(r"data-preco-pouca=", fonte), "atributo copiado fora da macro"

    macro = templates.env.loader.get_source(templates.env, "pedidos/_macro_dados_variacao.html")[0]
    esperados = {
        "data-id",
        "data-codigo",
        "data-descricao",
        "data-cor",
        "data-img",
        "data-preco-pouca",
        "data-preco-muita",
        "data-faixas",
        "data-qtd-corte",
        "data-unidades-caixa",
        "data-preco-minimo",
        "data-disponivel",
    }
    assert esperados <= set(re.findall(r"(data-[a-z-]+)=", macro))


# ------------------------------------------------- confirmação: um mecanismo só
def test_nenhum_template_usa_confirmacao_crua() -> None:
    """Conviviam TRÊS mecanismos: a macro, `hx-confirm` e `window.confirm`.

    Os dois crus não passam pelo modal do tema — saem com a cara do navegador, sem
    título, sem detalhe e sem foco preso. Este teste é o que impede a volta deles.
    """
    from pathlib import Path

    raiz = Path("app/web/templates")
    ofensores = [
        str(caminho.relative_to(raiz))
        for caminho in raiz.rglob("*.html")
        # O _ui.html é quem DEFINE o modal; a palavra aparece nos nomes das macros.
        if caminho.name != "_ui.html"
        and ("hx-confirm" in caminho.read_text() or "confirm(" in caminho.read_text())
    ]
    assert ofensores == [], f"confirmação crua em: {ofensores}"


def test_macro_de_confirmacao_suporta_o_caminho_htmx() -> None:
    """Sem `alvo` a macro segue no <form> POST; com `alvo`, vira htmx.

    É o que permite confirmar um DELETE (que não existe em <form>) sem navegar a página
    inteira e jogar o fragmento fora.
    """
    from pathlib import Path

    from app.core.templates import templates

    macro = templates.env.loader.get_source(templates.env, "_ui.html")[0]
    assert "data-metodo" in macro and "data-alvo" in macro and "data-swap" in macro
    # o formulário do modal decide entre os dois caminhos
    assert "if (!confirmar())" in macro

    ui_js = Path("app/static/js/ui.js").read_text()
    assert "confirmar()" in ui_js
    assert "window.htmx.ajax" in ui_js


def test_remover_item_do_pedido_passa_pelo_modal() -> None:
    """A remoção é um DELETE — o caso que obrigou a macro a aprender htmx."""
    from app.core.templates import templates

    fonte = templates.env.loader.get_source(templates.env, "pedidos/_itens.html")[0]
    assert 'metodo="delete"' in fonte
    assert 'alvo="#bloco-itens"' in fonte
    assert "hx-delete" not in fonte
