"""Colagem de planilha em pedido: texto colado -> itens do rascunho.

O vendedor copia a tabela do Excel e cola. Aqui o texto vira linhas (`core/colagem.py`),
cada linha é casada com um produto do catálogo e vira item do pedido. O que não casa não
trava o resto: sai como pendência, com o motivo em português e — quando dá — os
candidatos para resolver num clique.

Duas coisas guiam o desenho:

1. **Uma linha ruim não pode derrubar as outras.** A classificação é feita ANTES de
   gravar, sobre o catálogo já carregado em memória, e cada gravação ainda vai dentro de
   um SAVEPOINT — de modo que uma regra futura acrescentada ao `adicionar_item` não
   corrompa o lote em silêncio.
2. **O número de queries não cresce com o número de linhas.** Uma colagem de 200 linhas
   faz as mesmas 3 consultas de leitura que uma de 2.

NÃO faz commit — o `get_db` fecha a transação.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.colagem import (
    QTD_MAXIMA,
    LinhaColada,
    consolidar,
    normalizar_codigo,
    normalizar_texto,
    parse_colagem,
)
from app.core.errors import DominioError
from app.models.auditoria import Auditoria
from app.models.pedido import Pedido
from app.models.produto import Produto, ProdutoVariacao
from app.repositories.produto_repo import produto_repo
from app.schemas.colagem import (
    ItemAplicado,
    LinhaIgnoradaOut,
    Pendencia,
    ResultadoColagem,
    SugestaoProduto,
)
from app.schemas.pedido import ItemAdicionar, PedidoCreate
from app.services.pedido_service import pedido_service

logger = logging.getLogger(__name__)

# Limiares do match por descrição, sobre a `similarity` do pg_trgm (0..1).
# Sendo "cria direto, sem tela de prévia", errar para o lado da dúvida é barato — o
# vendedor vê a pendência e resolve num clique. Errar para o lado do match põe produto
# errado num pedido de verdade. Daí o piso alto e a exigência de folga sobre o segundo
# colocado: "CANETA AZUL" contra "CANETA AZUL METALICA" fica perto de 0,60.
SIM_CASA = 0.62
SIM_DUVIDA = 0.34
SIM_FOLGA = 0.12

_CANDIDATOS_POR_LINHA = 3

# Teto de botões de sugestão por pendência: um produto com 20 cores viraria uma parede.
_LIMITE_SUGESTOES = 8


@dataclass
class _Caso:
    """Uma linha colada depois de passar pelo matching."""

    linha: LinhaColada
    dados: ItemAdicionar | None = None
    variacao: ProdutoVariacao | None = None
    tipo_match: str = ""
    pendencia: Pendencia | None = None


@dataclass
class _Indice:
    """Índices em memória do catálogo trazido em lote."""

    proprios: dict[str, list[Produto]] = field(default_factory=dict)
    alternativos: dict[str, list[Produto]] = field(default_factory=dict)
    normalizados: dict[str, list[Produto]] = field(default_factory=dict)


def _unicos(produtos: list[Produto]) -> list[Produto]:
    vistos: dict[int, Produto] = {}
    for produto in produtos:
        vistos.setdefault(produto.id, produto)
    return list(vistos.values())


def _contem_sequencia(tokens: list[str], alvo: list[str]) -> bool:
    """True se `alvo` aparece como sequência de tokens INTEIROS dentro de `tokens`.

    Comparar por substring faria "AZUL" casar dentro de "AZULADO" e, pior, cor de uma
    letra casar com qualquer coisa.
    """
    if not alvo or len(alvo) > len(tokens):
        return False
    return any(tokens[i : i + len(alvo)] == alvo for i in range(len(tokens) - len(alvo) + 1))


class ColagemService:
    """Motor da colagem. NÃO faz commit (o get_db fecha a transação)."""

    # ------------------------------------------------------------- entradas
    def criar_com_colagem(
        self, db: Session, dados: PedidoCreate, texto: str, usuario_id: int, perfil: str
    ) -> tuple[Pedido, ResultadoColagem]:
        """Abre o rascunho e cola em seguida — a porta de `/pedidos/novo`."""
        pedido = pedido_service.criar(
            db,
            dados.cliente_id,
            usuario_id,
            dados.observacao,
            cliente_nome=dados.cliente_nome,
            cliente_telefone=dados.cliente_telefone,
        )
        # O flush aqui é obrigatório por dois motivos: materializa o `pedido.id` e deixa
        # a Session LIMPA antes do primeiro SAVEPOINT. Com `autoflush=False`, sujeira
        # pendente seria flushada dentro do savepoint e revertida junto com ele — e o
        # pedido recém-criado voltaria a transient, sem id.
        db.flush()
        return pedido, self.aplicar(db, pedido.id, texto, perfil, usuario_id)

    def aplicar(
        self, db: Session, pedido_id: int, texto: str, perfil: str, usuario_id: int
    ) -> ResultadoColagem:
        """Lê o texto colado e grava no rascunho o que casou."""
        pedido = pedido_service.carregar_editavel(db, pedido_id)
        itens_antes = len(pedido.itens)
        total_antes = pedido.total

        lidas, ignoradas = parse_colagem(texto)
        lidas = consolidar(lidas)

        resultado = ResultadoColagem(
            pedido_id=pedido_id,
            linhas_lidas=len(lidas),
            ignoradas=[
                LinhaIgnoradaOut(linha=i.numero, bruto=i.bruto, motivo=i.motivo) for i in ignoradas
            ],
        )
        if not lidas:
            return resultado

        for caso in self._casar(db, lidas, perfil):
            if caso.pendencia is not None:
                resultado.pendencias.append(caso.pendencia)
                continue
            self._gravar(db, pedido_id, caso, perfil, resultado)

        self._auditar(db, pedido_id, usuario_id, texto, itens_antes, total_antes, resultado)
        return resultado

    # ------------------------------------------------------------- gravação
    def _gravar(
        self,
        db: Session,
        pedido_id: int,
        caso: _Caso,
        perfil: str,
        resultado: ResultadoColagem,
    ) -> None:
        """Grava uma linha dentro de um SAVEPOINT.

        O `adicionar_item` insere e dá flush ANTES de recalcular o total, e no ramo de
        consolidação muta `qtd`/`desconto` antes de validar o subtotal. Um `try/except`
        puro não desfaz mutação em objeto já persistente — ela iria embora no commit do
        `get_db`. O savepoint desfaz.

        O `with` fica DENTRO do `try` de propósito: exceção que escapasse sem passar pelo
        `__exit__` deixaria a SessionTransaction desativada e envenenaria a request
        inteira com `PendingRollbackError`.
        """
        if caso.dados is None or caso.variacao is None:  # pragma: no cover - guarda de tipo
            return
        try:
            with db.begin_nested():
                item = pedido_service.adicionar_item(db, pedido_id, caso.dados, perfil)
        except DominioError as exc:
            resultado.pendencias.append(self._pendencia(caso.linha, exc.mensagem))
            return
        except SQLAlchemyError:
            # A saída limpa do `with` faz flush, então um erro de banco nasce aqui.
            logger.exception("colagem: falha ao gravar a linha %s", caso.linha.numero)
            resultado.pendencias.append(
                self._pendencia(caso.linha, "Falha ao gravar esta linha no pedido.")
            )
            return

        produto = caso.variacao.produto
        resultado.aplicados.append(
            ItemAplicado(
                linha=caso.linha.numero,
                variacao_id=caso.variacao.id,
                codigo=produto.codigo,
                descricao=produto.descricao,
                cor=caso.variacao.cor,
                qtd=item.qtd,
                preco_unit=item.preco_unit,
                tipo_match=caso.tipo_match,
            )
        )

    # ------------------------------------------------------------- matching
    def _casar(self, db: Session, linhas: list[LinhaColada], perfil: str) -> list[_Caso]:
        """Resolve todas as linhas contra o catálogo. 3 queries, independente do volume."""
        codigos = [linha.codigo for linha in linhas]
        indice = self._indexar(produto_repo.catalogo_por_codigos(db, codigos))

        casos: list[_Caso] = []
        sem_codigo: list[tuple[int, LinhaColada, str]] = []  # (posição em casos, linha, termo)

        for linha in linhas:
            produto, tipo, candidatos = self._por_codigo(indice, linha.codigo)
            if produto is not None:
                casos.append(self._montar(linha, produto, tipo, perfil))
                continue
            if candidatos:
                casos.append(
                    self._duvida(
                        linha,
                        self._sugerir(candidatos),
                        "mais de um produto usa esse código",
                    )
                )
                continue

            termo = normalizar_texto(linha.descricao)
            if not termo:
                casos.append(
                    _Caso(linha, pendencia=self._pendencia(linha, "código não encontrado"))
                )
                continue
            sem_codigo.append((len(casos), linha, termo))
            casos.append(_Caso(linha))  # lugar reservado, preenchido logo abaixo

        for posicao, caso in self._por_descricao(db, sem_codigo, perfil):
            casos[posicao] = caso

        return casos

    def _indexar(self, produtos: list[Produto]) -> _Indice:
        indice = _Indice()
        for produto in produtos:
            proprio = (produto.codigo or "").strip().upper()
            if proprio:
                indice.proprios.setdefault(proprio, []).append(produto)
            for alt in produto.codigos_alt:
                chave = (alt.codigo_alt or "").strip().upper()
                if chave:
                    indice.alternativos.setdefault(chave, []).append(produto)
            for codigo in [produto.codigo, *(a.codigo_alt for a in produto.codigos_alt)]:
                normal = normalizar_codigo(codigo)
                if normal:
                    indice.normalizados.setdefault(normal, []).append(produto)
        return indice

    def _por_codigo(
        self, indice: _Indice, codigo: str
    ) -> tuple[Produto | None, str, list[Produto]]:
        """Escada do código. Devolve (produto, tipo_match, candidatos ambíguos)."""
        chave = (codigo or "").strip().upper()
        if not chave:
            return None, "", []

        for mapa, tipo in ((indice.proprios, "codigo_exato"), (indice.alternativos, "codigo_alt")):
            achados = _unicos(mapa.get(chave, []))
            if len(achados) == 1:
                return achados[0], tipo, []
            if achados:
                return None, "", achados

        # Só letras e dígitos: é o que faz "K708" achar o "K-708" do cadastro.
        achados = _unicos(indice.normalizados.get(normalizar_codigo(chave), []))
        if len(achados) == 1:
            return achados[0], "codigo_normalizado", []
        return None, "", achados

    def _por_descricao(
        self, db: Session, pendentes: list[tuple[int, LinhaColada, str]], perfil: str
    ) -> list[tuple[int, _Caso]]:
        """Fallback por similaridade de descrição, em lote para todos os que sobraram."""
        if not pendentes:
            return []

        ranking = produto_repo.melhores_por_descricao(
            db, [termo for _, _, termo in pendentes], por_termo=_CANDIDATOS_POR_LINHA
        )
        ids = {pid for lista in ranking.values() for pid, _ in lista}
        produtos: dict[int, Produto] = (
            {
                p.id: p
                for p in db.scalars(
                    select(Produto)
                    .options(selectinload(Produto.variacoes))
                    .where(Produto.id.in_(ids))
                )
            }
            if ids
            else {}
        )

        saida: list[tuple[int, _Caso]] = []
        for posicao, linha, termo in pendentes:
            achados = [
                (produtos[pid], sim) for pid, sim in ranking.get(termo, []) if pid in produtos
            ]
            if not achados or achados[0][1] < SIM_DUVIDA:
                saida.append(
                    (
                        posicao,
                        _Caso(linha, pendencia=self._pendencia(linha, "produto não encontrado")),
                    )
                )
                continue

            melhor, sim = achados[0]
            segundo = achados[1][1] if len(achados) > 1 else 0.0
            if sim >= SIM_CASA and (sim - segundo) >= SIM_FOLGA:
                saida.append((posicao, self._montar(linha, melhor, f"descricao:{sim:.2f}", perfil)))
                continue

            saida.append(
                (
                    posicao,
                    self._duvida(
                        linha,
                        self._sugerir([p for p, _ in achados]),
                        "a descrição não bateu com um produto só",
                    ),
                )
            )
        return saida

    # ------------------------------------------------------------- variação + validação
    def _escolher_variacao(
        self, produto: Produto, descricao_colada: str
    ) -> tuple[ProdutoVariacao | None, str]:
        """Qual cor do produto a linha está pedindo.

        O item do pedido pendura na VARIAÇÃO, não no produto — sem escolher a cor não há
        o que gravar (`produto_variacao_id` é NOT NULL).
        """
        ativas = [v for v in produto.variacoes if v.ativo]
        if not ativas:
            return None, f"{produto.codigo} não tem nenhuma cor ativa cadastrada"
        if len(ativas) == 1:
            return ativas[0], ""

        tokens = normalizar_texto(descricao_colada).split()
        casadas = [v for v in ativas if _contem_sequencia(tokens, normalizar_texto(v.cor).split())]
        if len(casadas) == 1:
            return casadas[0], ""
        if not casadas and all(not normalizar_texto(v.cor) for v in ativas):
            # Todas sem cor preenchida: mesma regra do `estoque_repo.por_codigo_exato`.
            return min(ativas, key=lambda v: v.id), ""

        cores = ", ".join(sorted({v.cor for v in ativas if v.cor}))
        return None, f"{produto.codigo} tem mais de uma cor ({cores}) e a linha não diz qual"

    def _montar(self, linha: LinhaColada, produto: Produto, tipo: str, perfil: str) -> _Caso:
        """Valida a linha contra o produto achado e monta o item — ou a pendência.

        Tudo aqui é conta em memória, sobre o catálogo já carregado: a maioria esmagadora
        das linhas problemáticas nunca chega a tocar o banco.
        """
        if not produto.ativo:
            return _Caso(linha, pendencia=self._pendencia(linha, f"{produto.codigo} está inativo"))

        if linha.qtd is None:
            return _Caso(linha, pendencia=self._pendencia(linha, "quantidade ilegível"))
        if linha.qtd <= 0:
            return _Caso(linha, pendencia=self._pendencia(linha, "quantidade zerada ou negativa"))
        if linha.qtd > QTD_MAXIMA:
            return _Caso(
                linha,
                pendencia=self._pendencia(linha, f"quantidade fora do razoável ({linha.qtd})"),
            )

        variacao, erro = self._escolher_variacao(produto, linha.descricao)
        if variacao is None:
            return self._duvida(linha, self._sugerir([produto]), erro)

        preco = linha.preco_unit
        if preco is None:
            preco = pedido_service.sugerir_preco(produto, linha.qtd).preco_sugerido
        if preco is None or preco <= 0:
            # Produto nunca precificado: `preco_minimo` zerado significa "sem piso", então
            # a validação do pedido deixaria passar um item a R$ 0,00 em silêncio. Criando
            # direto, sem tela de prévia, isso é dinheiro saindo pela porta.
            return _Caso(
                linha,
                pendencia=self._pendencia(
                    linha, f"{produto.codigo} está sem preço — informe o valor"
                ),
            )

        erro_preco = pedido_service.erro_de_preco(perfil, produto, linha.qtd, preco)
        if erro_preco:
            return _Caso(linha, pendencia=self._pendencia(linha, erro_preco))

        return _Caso(
            linha,
            dados=ItemAdicionar(variacao_id=variacao.id, qtd=linha.qtd, preco_unit=preco),
            variacao=variacao,
            tipo_match=tipo,
        )

    # ------------------------------------------------------------- pendências
    def _pendencia(self, linha: LinhaColada, motivo: str) -> Pendencia:
        return Pendencia(
            linha=linha.numero,
            bruto=linha.bruto,
            codigo=linha.codigo,
            descricao=linha.descricao,
            qtd=linha.qtd,
            preco_unit=linha.preco_unit,
            motivo=motivo,
        )

    def _sugerir(self, produtos: list[Produto]) -> list[SugestaoProduto]:
        """Variações ativas dos candidatos, viradas em botão de um clique.

        O código e a descrição saem do `Produto` que já está em mãos, nunca de
        `variacao.produto` — esse atalho dispararia um SELECT por variação listada.
        """
        sugestoes: list[SugestaoProduto] = []
        for produto in produtos:
            ativas = [v for v in produto.variacoes if v.ativo]
            sugestoes.extend(
                SugestaoProduto(
                    variacao_id=v.id,
                    codigo=produto.codigo,
                    descricao=produto.descricao,
                    cor=v.cor,
                )
                for v in ativas[:_LIMITE_SUGESTOES]
            )
        return sugestoes

    def _duvida(self, linha: LinhaColada, sugestoes: list[SugestaoProduto], motivo: str) -> _Caso:
        """Pendência que já vem com os candidatos — resolver vira um clique na tela."""
        pendencia = self._pendencia(linha, motivo)
        pendencia.sugestoes = sugestoes
        return _Caso(linha, pendencia=pendencia)

    # ------------------------------------------------------------- auditoria
    def _auditar(
        self,
        db: Session,
        pedido_id: int,
        usuario_id: int,
        texto: str,
        itens_antes: int,
        total_antes: Decimal,
        resultado: ResultadoColagem,
    ) -> None:
        """Uma linha por colagem, fora dos savepoints.

        Dentro de um savepoint, o rollback de uma linha levaria a auditoria junto. O
        texto colado não é guardado — hash, contagens e o resumo por linha bastam e não
        crescem sem limite. O `tipo_match` é o que permite calibrar os limiares de
        similaridade com dados reais depois, em vez de chute.
        """
        db.add(
            Auditoria(
                usuario_id=usuario_id,
                entidade="pedidos",
                entidade_id=pedido_id,
                acao="colar_itens",
                antes={"itens": itens_antes, "total": str(total_antes)},
                depois={
                    "texto_sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                    "linhas_lidas": resultado.linhas_lidas,
                    "aplicados": [
                        {
                            "linha": a.linha,
                            "variacao_id": a.variacao_id,
                            "qtd": a.qtd,
                            "preco_unit": str(a.preco_unit),
                            "match": a.tipo_match,
                        }
                        for a in resultado.aplicados
                    ],
                    "pendencias": [
                        {"linha": p.linha, "codigo": p.codigo, "motivo": p.motivo}
                        for p in resultado.pendencias
                    ],
                },
            )
        )
        db.flush()


colagem_service = ColagemService()
