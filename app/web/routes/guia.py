# ruff: noqa: E501  (módulo de conteúdo: prosa de treinamento com linhas longas intencionais)
"""Guia interativo de treinamento do sistema (conteúdo estático + página).

Página única acessível a todos os perfis. O conteúdo fica aqui (estruturado) e o
template renderiza de forma genérica + interativa (Alpine: busca, navegação por
tópico e progresso salvo no navegador).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates
from app.deps.auth import get_current_user
from app.models.usuario import Usuario

router = APIRouter()

_TODOS = ["admin", "vendedor", "financeiro", "funcionario"]

# Cada tópico: id, grupo, titulo, icone, perfis (quem usa), resumo, passos, dicas.
# As strings de passos/dicas aceitam HTML simples (negrito, selos) — conteúdo confiável.
TOPICOS: list[dict] = [
    # ---------------------------------------------------------------- COMEÇANDO
    {
        "id": "visao-geral",
        "grupo": "Começando",
        "titulo": "Visão geral e login",
        "icone": "painel",
        "perfis": _TODOS,
        "resumo": "O que é o sistema e como entrar.",
        "passos": [
            "O <strong>Estrela Gestão</strong> controla <strong>estoque</strong>, <strong>pedidos</strong>, "
            "<strong>separação</strong> e <strong>financeiro</strong> da empresa, tudo em um só lugar.",
            "Para entrar, informe seu <strong>e-mail</strong> e <strong>senha</strong> na tela de login e clique em "
            "<strong>Entrar</strong>.",
            "Cada pessoa tem um <strong>perfil</strong> (Admin, Vendedor, Financeiro ou Funcionário) que define o que ela vê e pode fazer.",
            "Para sair com segurança, use o botão <strong>Sair</strong> no canto superior direito.",
        ],
        "dicas": [
            "Esqueceu a senha? Peça a um <strong>Admin</strong> para redefinir em <em>Usuários → Resetar senha</em>.",
            "O sistema funciona <strong>offline</strong> na rede da loja — não depende de internet para operar.",
        ],
    },
    {
        "id": "navegacao",
        "grupo": "Começando",
        "titulo": "Como navegar",
        "icone": "menu",
        "perfis": _TODOS,
        "resumo": "Menu lateral, busca, abas e atalhos.",
        "passos": [
            "O <strong>menu lateral</strong> (à esquerda) agrupa as telas em <strong>Estoque</strong>, "
            "<strong>Vendas</strong> e <strong>Gestão</strong>. O item atual fica destacado em dourado.",
            "Em telas pequenas (celular/tablet), toque no <strong>☰ (menu)</strong> no topo para abrir a navegação; toque fora ou em <strong>Esc</strong> para fechar.",
            "Os <strong>caminhos</strong> no topo (ex.: <em>Painel / Pedidos</em>) mostram onde você está e permitem voltar com um clique.",
            "Telas com muitos itens têm uma <strong>busca instantânea</strong> — comece a digitar e a lista filtra sozinha.",
            "Alguns módulos têm <strong>abas</strong> no topo (ex.: Estoque / Inventário / Localização) para alternar rapidamente.",
        ],
        "dicas": [
            "O sistema é <strong>100% navegável pelo teclado</strong>: use <strong>Tab</strong> para mover e <strong>Enter</strong> para acionar.",
        ],
    },
    {
        "id": "perfis",
        "grupo": "Começando",
        "titulo": "Perfis e permissões",
        "icone": "usuarios",
        "perfis": _TODOS,
        "resumo": "O que cada perfil pode fazer.",
        "passos": [
            "<strong>Admin</strong>: acesso total — produtos, estoque, pedidos, financeiro, relatórios, importação e usuários.",
            "<strong>Vendedor</strong>: cria e gerencia <strong>pedidos</strong> e <strong>clientes</strong>; vê estoque e produtos (sem o preço de custo).",
            "<strong>Financeiro</strong>: cuida das <strong>contas a receber</strong> e dos <strong>relatórios</strong> (incl. valorização).",
            "<strong>Funcionário</strong>: opera o <strong>estoque</strong> (entradas), o <strong>inventário</strong> e a <strong>separação</strong> de pedidos.",
        ],
        "dicas": [
            "O <strong>preço de custo</strong> fica oculto para Vendedor e Funcionário.",
            "Se aparecer a tela <strong>403</strong>, é porque seu perfil não tem acesso àquele recurso.",
        ],
    },
    # ------------------------------------------------------------ OPERAÇÃO DIÁRIA
    {
        "id": "estoque-consultar",
        "grupo": "Operação diária",
        "titulo": "Estoque: consultar e buscar",
        "icone": "caixa",
        "perfis": _TODOS,
        "resumo": "Ver saldos, status e histórico de um produto.",
        "passos": [
            "Abra <strong>Estoque</strong> no menu. Use a <strong>busca</strong> por código, descrição, cor ou localização.",
            "A coluna <strong>Saldo</strong> mostra a quantidade. Em produtos de <strong>controle exato</strong> aparece o número; "
            "quando há reserva, mostra também <em>(-X)</em> de itens já comprometidos em pedidos.",
            "Produtos de <strong>controle aproximado</strong> mostram um rótulo: "
            "<span class='selo-muito'>muito</span> <span class='selo-tem'>tem</span> "
            "<span class='selo-pouco'>pouco</span> <span class='selo-acabou'>acabou</span>.",
            "Clique em <strong>Histórico</strong> de uma linha para ver todas as movimentações (entradas, ajustes, vendas) com data, usuário e saldo após.",
        ],
        "dicas": [
            "O estoque <strong>nunca é editado direto</strong>: toda mudança vira uma movimentação registrada e rastreável.",
        ],
    },
    {
        "id": "estoque-entrada",
        "grupo": "Operação diária",
        "titulo": "Estoque: entrada de mercadoria",
        "icone": "upload",
        "perfis": ["admin", "funcionario"],
        "resumo": "Registrar a chegada de produtos.",
        "passos": [
            "Em <strong>Estoque</strong>, encontre a variação (cor) que chegou e clique em <strong>Entrada</strong>.",
            "Informe a <strong>Quantidade</strong> recebida e clique em <strong>Registrar entrada</strong>.",
            "O saldo da linha é <strong>somado</strong> na hora e aparece a confirmação <em>“Entrada de X registrada”</em>.",
        ],
        "dicas": [
            "Registrar entrada muda o produto para <strong>controle exato</strong> automaticamente.",
        ],
    },
    {
        "id": "estoque-ajuste",
        "grupo": "Operação diária",
        "titulo": "Estoque: ajuste manual",
        "icone": "editar",
        "perfis": ["admin"],
        "resumo": "Corrigir o saldo (avaria, recontagem).",
        "passos": [
            "Em <strong>Estoque</strong>, clique em <strong>Ajustar</strong> na variação.",
            "Informe o <strong>novo saldo</strong> correto e um <strong>motivo</strong> (obrigatório — ex.: avaria, recontagem).",
            "Clique em <strong>Ajustar</strong>. A diferença vira uma movimentação de ajuste registrada com seu nome.",
        ],
        "dicas": [
            "Use o ajuste só para <strong>correções</strong>. Recebimento de mercadoria é <em>Entrada</em>; conferência geral é <em>Inventário</em>.",
        ],
    },
    {
        "id": "localizacao",
        "grupo": "Operação diária",
        "titulo": "Localização (modo tablet)",
        "icone": "mapa",
        "perfis": _TODOS,
        "resumo": "Achar rápido onde o produto está guardado.",
        "passos": [
            "Abra <strong>Localização</strong> (aba do Estoque). A busca é grande, pensada para o tablet do estoque.",
            "Digite o código ou nome e veja <strong>cartões grandes</strong> com a foto e a <strong>localização em destaque</strong>.",
            "Use no balcão/depósito para localizar a prateleira sem precisar abrir a ficha completa do produto.",
        ],
        "dicas": [],
    },
    {
        "id": "pedido-criar",
        "grupo": "Operação diária",
        "titulo": "Pedidos: criar e adicionar itens",
        "icone": "carrinho",
        "perfis": ["admin", "vendedor"],
        "resumo": "Montar um pedido do zero.",
        "passos": [
            "Em <strong>Pedidos</strong>, clique em <strong>Novo pedido</strong>, escolha o <strong>cliente</strong> e clique em <strong>Criar rascunho</strong>.",
            "No campo <strong>Buscar produto/variação</strong>, digite o código/cor e clique em <strong>Selecionar</strong> no resultado desejado.",
            "Confira o <strong>saldo</strong> e a <strong>sugestão de preço</strong> que aparecem; informe a <strong>quantidade</strong>.",
            "Para vender em caixas, marque <strong>Vender por caixa</strong> e informe a quantidade de caixas (o sistema converte em unidades).",
            "O <strong>preço</strong> vem automático pela faixa (varejo/atacado); você pode sobrescrever e aplicar <strong>desconto</strong> por item.",
            "Clique em <strong>Adicionar</strong>. O item entra na lista e o total é recalculado.",
        ],
        "dicas": [
            "O preço sugere <strong>atacado</strong> quando a quantidade atinge o corte configurado no produto.",
            "Vendedor tem <strong>limite de desconto</strong>; acima disso o sistema bloqueia (Admin não tem limite).",
        ],
    },
    {
        "id": "pedido-ciclo",
        "grupo": "Operação diária",
        "titulo": "Pedidos: confirmar, faturar e status",
        "icone": "lista",
        "perfis": ["admin", "vendedor", "financeiro"],
        "resumo": "O ciclo de vida de um pedido.",
        "passos": [
            "<strong>Rascunho</strong> → adicione os itens e clique em <strong>Confirmar</strong>. Isso <strong>reserva o estoque</strong> e gera o número do pedido.",
            "<strong>Confirmado</strong> → o pedido entra na <strong>fila de separação</strong>.",
            "<strong>Separado</strong> → após o funcionário conferir os itens, o pedido fica pronto para faturar.",
            "<strong>Faturar</strong> (Admin/Financeiro) → dá <strong>baixa definitiva</strong> no estoque e gera as <strong>contas a receber</strong>.",
            "<strong>Marcar entregue</strong> → registra a entrega ao cliente. Em qualquer ponto antes de faturar, dá para <strong>Cancelar</strong> (estorna as reservas).",
            "Use <strong>Imprimir</strong> para gerar o pedido em A4.",
        ],
        "dicas": [
            "Numeração de pedido é <strong>sequencial e sem buracos</strong>.",
            "Veja o resumo visual em <em>Referência → Ciclo de vida do pedido</em>.",
        ],
    },
    {
        "id": "separacao",
        "grupo": "Operação diária",
        "titulo": "Separação de pedidos",
        "icone": "lista",
        "perfis": ["admin", "funcionario"],
        "resumo": "Conferir e preparar os pedidos confirmados.",
        "passos": [
            "Abra <strong>Separação</strong> para ver a <strong>fila</strong> de pedidos confirmados, em ordem de chegada.",
            "Clique em <strong>Separar</strong> para abrir a conferência. Cada item mostra a <strong>foto</strong> e a <strong>localização</strong>.",
            "Pegue os produtos e <strong>marque o item</strong> conferido — a barra de progresso atualiza sozinha.",
            "Com tudo conferido (100%), clique em <strong>Concluir separação</strong>: o pedido sai da fila (fica <strong>separado</strong>, pronto para faturar).",
            "Use <strong>Imprimir lista</strong> para uma folha de separação física.",
        ],
        "dicas": [],
    },
    # --------------------------------------------------------------------- GESTÃO
    {
        "id": "produtos",
        "grupo": "Gestão",
        "titulo": "Produtos: cadastro e fotos",
        "icone": "etiqueta",
        "perfis": ["admin"],
        "resumo": "Cadastrar produtos, cores e imagens.",
        "passos": [
            "Em <strong>Produtos</strong>, clique em <strong>Novo produto</strong> e preencha código, descrição, categoria e preços "
            "(<strong>varejo</strong>, <strong>atacado</strong>, <strong>custo</strong> e o <strong>corte de atacado</strong>).",
            "Em <strong>Variações (cores)</strong>, adicione cada cor com seu modo (exato/aproximado), estoque inicial e mínimo.",
            "Em <strong>Códigos alternativos</strong>, cadastre outros códigos pelos quais o produto também é encontrado.",
            "Após salvar, <strong>edite</strong> o produto para enviar uma <strong>foto por cor</strong> (aparece no estoque, pedido e separação).",
            "Para tirar um produto de circulação, use <strong>Inativar</strong> (não apaga o histórico).",
        ],
        "dicas": [
            "<strong>Unidades por caixa</strong> habilita a venda por caixa nos pedidos.",
        ],
    },
    {
        "id": "inventario",
        "grupo": "Gestão",
        "titulo": "Inventário (contagem)",
        "icone": "caixa",
        "perfis": ["admin", "funcionario"],
        "resumo": "Conferir o estoque físico e corrigir saldos.",
        "passos": [
            "Em <strong>Estoque → Inventário</strong>, dê uma descrição e clique em <strong>Abrir inventário</strong> "
            "(inclui todas as variações ativas).",
            "Conte fisicamente e preencha a <strong>quantidade contada</strong> de cada item, clicando em <strong>Salvar</strong> por linha.",
            "O topo mostra <strong>“Contados: X de Y”</strong> e quantos ainda estão <strong>sem contagem</strong>.",
            "Quando terminar, o <strong>Admin</strong> clica em <strong>Aplicar inventário</strong>: os saldos contados são ajustados "
            "e cada diferença vira uma movimentação.",
        ],
        "dicas": [
            "Itens <strong>não contados</strong> ficam <strong>inalterados</strong> — o sistema avisa quantos são antes de aplicar.",
            "Depois de aplicado, o inventário fica <strong>somente leitura</strong>.",
        ],
    },
    {
        "id": "financeiro",
        "grupo": "Gestão",
        "titulo": "Financeiro (contas a receber)",
        "icone": "dinheiro",
        "perfis": ["admin", "financeiro"],
        "resumo": "Receber, dar baixa e controlar atrasos.",
        "passos": [
            "Ao <strong>faturar</strong> um pedido, o sistema cria as <strong>contas a receber</strong> conforme a condição de pagamento do cliente "
            "(à vista, 30 dias, 2x…), com as datas de vencimento.",
            "Em <strong>Financeiro</strong>, use os <strong>filtros</strong> (status, cliente, vencimento) para encontrar as contas.",
            "Para registrar um recebimento, escolha a <strong>forma</strong> (Pix, boleto, dinheiro) e clique em <strong>Baixar</strong>.",
            "Clique em <strong>Marcar vencidas como atrasadas</strong> para atualizar o status das contas que passaram do vencimento.",
        ],
        "dicas": [
            "O card <strong>Recebimentos de hoje</strong> soma o que já foi baixado no dia.",
        ],
    },
    {
        "id": "relatorios",
        "grupo": "Gestão",
        "titulo": "Relatórios e exportação",
        "icone": "grafico",
        "perfis": ["admin", "vendedor", "financeiro"],
        "resumo": "Vendas, Curva ABC e Valorização.",
        "passos": [
            "<strong>Vendas</strong>: total e lista de pedidos faturados por período (Vendedor vê só os seus).",
            "<strong>Curva ABC</strong>: ranqueia os produtos por valor vendido e classifica em A, B e C.",
            "<strong>Valorização de estoque</strong>: soma <em>quantidade × custo</em> de cada produto (Admin/Financeiro).",
            "Use os <strong>filtros de data</strong> e o botão <strong>Exportar XLSX</strong> para baixar a planilha.",
        ],
        "dicas": [],
    },
    {
        "id": "importacao",
        "grupo": "Gestão",
        "titulo": "Importação de planilha",
        "icone": "upload",
        "perfis": ["admin"],
        "resumo": "Carregar/atualizar produtos em massa.",
        "passos": [
            "Em <strong>Importação</strong>, envie a planilha de catálogo (.xlsx).",
            "Clique em <strong>Validar e pré-visualizar</strong>: o sistema mostra quantos produtos/variações serão criados ou atualizados "
            "e lista as <strong>inconsistências</strong> encontradas — <strong>sem gravar nada</strong> ainda.",
            "Revise; se estiver tudo certo, clique em <strong>Confirmar carga</strong> para gravar.",
        ],
        "dicas": [
            "A importação é <strong>idempotente</strong>: rodar a mesma planilha de novo <strong>não duplica</strong> produtos.",
        ],
    },
    {
        "id": "usuarios",
        "grupo": "Gestão",
        "titulo": "Usuários",
        "icone": "engrenagem",
        "perfis": ["admin"],
        "resumo": "Criar acessos e redefinir senhas.",
        "passos": [
            "Em <strong>Usuários</strong>, clique em <strong>Novo usuário</strong> e informe nome, e-mail, <strong>perfil</strong> e senha inicial.",
            "Use <strong>Editar</strong> para mudar dados/perfil ou ativar/inativar o acesso.",
            "Use <strong>Resetar senha</strong> para definir uma nova senha quando alguém esquecer.",
        ],
        "dicas": [
            "Dê a cada pessoa o <strong>perfil mínimo</strong> necessário para o trabalho dela.",
        ],
    },
    # ----------------------------------------------------------------- REFERÊNCIA
    {
        "id": "ciclo-pedido",
        "grupo": "Referência",
        "titulo": "Ciclo de vida do pedido",
        "icone": "carrinho",
        "perfis": _TODOS,
        "resumo": "Os estados de um pedido, do início ao fim.",
        "passos": [
            "<strong>Rascunho</strong> — sendo montado; ainda não afeta o estoque.",
            "<strong>Confirmado</strong> — itens definidos; <strong>estoque reservado</strong>; entra na fila de separação.",
            "<strong>Separação</strong> — funcionário conferindo os itens.",
            "<strong>Separado</strong> — conferência concluída; aguardando faturamento.",
            "<strong>Faturado</strong> — <strong>baixa no estoque</strong> + <strong>contas a receber</strong> geradas.",
            "<strong>Entregue</strong> — entregue ao cliente (fim do fluxo).",
            "<strong>Cancelado</strong> — encerrado antes de faturar; reservas são <strong>estornadas</strong>.",
        ],
        "dicas": [],
    },
    {
        "id": "glossario",
        "grupo": "Referência",
        "titulo": "Glossário",
        "icone": "lista",
        "perfis": _TODOS,
        "resumo": "Termos usados no sistema.",
        "passos": [
            "<strong>Variação</strong>: uma cor/versão específica de um produto (cada cor tem seu próprio estoque).",
            "<strong>Reserva</strong>: estoque comprometido por um pedido confirmado, ainda não baixado.",
            "<strong>Baixa</strong>: saída definitiva do estoque, feita ao faturar.",
            "<strong>Estorno</strong>: devolução da reserva ao cancelar um pedido.",
            "<strong>Controle exato × aproximado</strong>: exato usa números; aproximado usa rótulos (muito/tem/pouco/acabou).",
            "<strong>Faixa de preço</strong>: varejo (pouca quantidade) × atacado (a partir do corte).",
            "<strong>Movimentação</strong>: cada registro histórico de mudança de estoque (entrada, saída, ajuste, reserva, estorno).",
        ],
        "dicas": [],
    },
]


@router.get("/guia", response_class=HTMLResponse)
def guia(
    request: Request,
    usuario: Usuario = Depends(get_current_user),
):
    # Ordem dos grupos no menu.
    grupos: list[str] = []
    for t in TOPICOS:
        if t["grupo"] not in grupos:
            grupos.append(t["grupo"])
    contexto = {
        "request": request,
        "user": usuario,
        "titulo": "Guia",
        "topicos": TOPICOS,
        "grupos": grupos,
    }
    return templates.TemplateResponse(request, "guia/index.html", contexto)
