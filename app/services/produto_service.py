from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import eventos
from app.core.errors import NaoEncontradoError, RegraNegocioError
from app.core.precos import normalizar_faixas, validar_faixas
from app.models.enums import EstoqueModo, OrigemMov
from app.models.produto import (
    Produto,
    ProdutoCodigoAlt,
    ProdutoEspecificacao,
    ProdutoFaixaPreco,
    ProdutoRelacionado,
    ProdutoVariacao,
)
from app.repositories.produto_repo import produto_repo
from app.schemas.produto import ProdutoCreate, ProdutoUpdate, VariacaoCreate
from app.services.estoque_service import estoque_service


def _dados_produto(produto: Produto) -> dict:
    """Payload dos eventos de produto. Jamais inclui preco_custo nem margem."""
    return {
        "produto_id": produto.id,
        "codigo": produto.codigo,
        "descricao": produto.descricao,
        "ativo": produto.ativo,
    }


def _dados_variacao(variacao: ProdutoVariacao) -> dict:
    """Payload dos eventos de variação. Jamais inclui preco_custo nem margem."""
    return {
        "variacao_id": variacao.id,
        "produto_id": variacao.produto_id,
        "codigo": variacao.produto.codigo if variacao.produto else None,
        "cor": variacao.cor,
        "ativo": variacao.ativo,
    }


MAX_ESPECIFICACOES = 20
MAX_RELACIONADOS = 8


class ProdutoService:
    def listar(
        self,
        db: Session,
        termo: str | None = None,
        limit: int = 50,
        offset: int = 0,
        categoria_id: int | None = None,
    ) -> list[Produto]:
        # Busca por termo usa pg_trgm (topo dos matches); navegação sem termo é paginada
        # (scroll infinito) via limit/offset. `categoria_id` combina com ambos os caminhos.
        if termo:
            return produto_repo.busca_rapida(db, termo, categoria_id=categoria_id)
        return produto_repo.listar(db, limit=limit, offset=offset, categoria_id=categoria_id)

    def obter(self, db: Session, produto_id: int) -> Produto:
        produto = produto_repo.get(db, produto_id)
        if produto is None:
            raise NaoEncontradoError("Produto não encontrado.")
        return produto

    def criar(self, db: Session, dados: ProdutoCreate) -> Produto:
        # A checagem ignora pontuação e caixa, igual à busca: "K-708" e "K708" seriam
        # o mesmo produto para quem procura, então não podem coexistir no cadastro.
        existente = produto_repo.get_by_codigo(db, dados.codigo)
        if existente is not None:
            raise RegraNegocioError(f"Já existe um produto com o código {existente.codigo}.")
        produto = Produto(
            codigo=dados.codigo,
            descricao=dados.descricao,
            categoria_id=dados.categoria_id,
            unidades_por_caixa=dados.unidades_por_caixa,
            localizacao=dados.localizacao,
            preco_pouca_qtd=dados.preco_pouca_qtd,
            preco_muita_qtd=dados.preco_muita_qtd,
            preco_promocional=dados.preco_promocional,
            qtd_corte_atacado=dados.qtd_corte_atacado,
            preco_custo=dados.preco_custo,
            preco_minimo=dados.preco_minimo,
            observacao=dados.observacao,
            ativo=dados.ativo,
            publicar_catalogo=dados.publicar_catalogo,
        )
        for v in dados.variacoes:
            produto.variacoes.append(
                ProdutoVariacao(
                    cor=v.cor,
                    estoque_modo=v.estoque_modo,
                    estoque_fisico=v.estoque_fisico,
                    rotulo_aprox=v.rotulo_aprox,
                    estoque_minimo=v.estoque_minimo,
                    ativo=v.ativo,
                )
            )
        if not dados.variacoes:
            # Produto "sem cor" ganha uma variação padrão: imagem e saldo sempre
            # vivem em ProdutoVariacao, então todo produto precisa de ao menos uma.
            produto.variacoes.append(ProdutoVariacao(cor="", estoque_modo=EstoqueModo.APROXIMADO))
        for c in dados.codigos_alt:
            produto.codigos_alt.append(
                ProdutoCodigoAlt(codigo_alt=c.codigo_alt, fornecedor_id=c.fornecedor_id)
            )
        self._aplicar_faixas(produto, dados.faixas)
        self._aplicar_especificacoes(produto, dados.especificacoes)
        self._aplicar_relacionados(db, produto, dados.relacionados)
        produto_repo.add(db, produto)
        eventos.emitir(db, "produto.criado", _dados_produto(produto), audiencia=eventos.TODOS)
        return produto

    def atualizar(self, db: Session, produto_id: int, dados: ProdutoUpdate) -> Produto:
        produto = self.obter(db, produto_id)
        # Código: valida unicidade excluindo o próprio produto (evita erro
        # genérico do Postgres e dá mensagem amigável, como na criação).
        if dados.codigo is not None:
            novo = dados.codigo.strip()
            outro = produto_repo.get_by_codigo(db, novo)
            if outro is not None and outro.id != produto_id:
                raise RegraNegocioError(f"Já existe um produto com o código {outro.codigo}.")
            produto.codigo = novo
        # Campos simples (codigo e codigos_alt são tratados à parte: o código
        # passa por validação de unicidade e codigos_alt é relationship de lista).
        for campo, valor in dados.model_dump(
            exclude_unset=True,
            exclude={
                "codigo",
                "codigos_alt",
                "faixas",
                "especificacoes",
                "relacionados",
            },
        ).items():
            setattr(produto, campo, valor)
        # Códigos alternativos: reconcilia add/remove por string (preserva os
        # que permanecem, adiciona os novos, remove os que saíram).
        desejados = {c.codigo_alt.strip() for c in dados.codigos_alt if c.codigo_alt.strip()}
        existentes = {c.codigo_alt: c for c in produto.codigos_alt}
        for codigo_alt, obj in existentes.items():
            if codigo_alt not in desejados:
                # remove da coleção -> delete-orphan apaga a linha no flush
                # (e mantém a lista em memória sincronizada).
                produto.codigos_alt.remove(obj)
        for codigo_alt in desejados:
            if codigo_alt not in existentes:
                produto.codigos_alt.append(ProdutoCodigoAlt(codigo_alt=codigo_alt))
        # `None` é "o formulário não trouxe o editor" e lista vazia é "apague a tabela".
        # Sem essa distinção, salvar de qualquer tela sem o editor zeraria as faixas em
        # silêncio — o mesmo buraco que `preco_custo`/`preco_minimo` têm no controller.
        if dados.faixas is not None:
            self._aplicar_faixas(produto, dados.faixas)
        if dados.especificacoes is not None:
            self._aplicar_especificacoes(produto, dados.especificacoes)
        if dados.relacionados is not None:
            self._aplicar_relacionados(db, produto, dados.relacionados)
        db.flush()
        eventos.emitir(db, "produto.atualizado", _dados_produto(produto), audiencia=eventos.TODOS)
        return produto

    def _aplicar_relacionados(self, db: Session, produto: Produto, ids) -> None:
        """Reescreve a lista do "Compre Junto", preservando a ordem escolhida.

        Auto-referência é DESCARTADA, não recusada: o produto aparece na própria lista de
        candidatos e clicar nele é engano de dedo, não pedido — recusar o save inteiro
        por isso seria punir o engano em vez de corrigi-lo. Id que não existe mais some
        pela mesma razão.
        """
        vistos: set[int] = set()
        limpos: list[int] = []
        for bruto in ids or []:
            try:
                alvo_id = int(bruto)
            except (TypeError, ValueError):
                continue
            if alvo_id == produto.id or alvo_id in vistos:
                continue
            vistos.add(alvo_id)
            limpos.append(alvo_id)
        limpos = limpos[:MAX_RELACIONADOS]

        existentes = (
            {p.id for p in db.query(Produto.id).filter(Produto.id.in_(limpos)).all()}
            if limpos
            else set()
        )

        produto.relacionados.clear()
        for ordem, alvo_id in enumerate(i for i in limpos if i in existentes):
            produto.relacionados.append(ProdutoRelacionado(relacionado_id=alvo_id, ordem=ordem))

    def _aplicar_especificacoes(self, produto: Produto, itens) -> None:
        """Reescreve a ficha técnica inteira, renumerando a ordem.

        Reescrever em vez de reconciliar porque a ORDEM é o dado: as setas ↑↓ da tela
        só continuam triviais se a posição sair de `enumerate` a cada save. São no
        máximo 20 linhas — a rotatividade é grátis.
        """
        limpas = [
            (str(getattr(i, "rotulo", "")).strip(), str(getattr(i, "valor", "")).strip())
            for i in itens or []
        ]
        limpas = [(r, v) for r, v in limpas if r and v][:MAX_ESPECIFICACOES]
        produto.especificacoes.clear()
        for ordem, (rotulo, valor) in enumerate(limpas):
            produto.especificacoes.append(
                ProdutoEspecificacao(ordem=ordem, rotulo=rotulo[:40], valor=valor[:120])
            )

    def _aplicar_faixas(self, produto: Produto, faixas) -> None:
        """Reescreve a tabela de preço do produto por inteiro.

        Reescrever em vez de reconciliar linha a linha porque são no máximo 10 linhas e
        a ordem importa: trocar duas de lugar viraria uma dança de updates só para
        respeitar a unicidade de (produto, min_qtd).
        """
        limpas = normalizar_faixas(faixas)
        validar_faixas(limpas)
        produto.faixas.clear()
        for faixa in limpas:
            produto.faixas.append(ProdutoFaixaPreco(min_qtd=faixa.min_qtd, preco=faixa.preco))

    def inativar(self, db: Session, produto_id: int) -> Produto:
        """Soft-delete: produtos nunca são removidos fisicamente (histórico/estoque)."""
        produto = self.obter(db, produto_id)
        produto.ativo = False
        db.flush()
        eventos.emitir(db, "produto.inativado", _dados_produto(produto), audiencia=eventos.TODOS)
        return produto

    def reativar(self, db: Session, produto_id: int) -> Produto:
        """Reverte o soft-delete: o produto volta a aparecer em novos pedidos."""
        produto = self.obter(db, produto_id)
        produto.ativo = True
        db.flush()
        eventos.emitir(db, "produto.reativado", _dados_produto(produto), audiencia=eventos.TODOS)
        return produto

    def obter_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        variacao = produto_repo.get_variacao(db, variacao_id)
        if variacao is None:
            raise NaoEncontradoError("Variação não encontrada.")
        return variacao

    def renomear_variacao(self, db: Session, variacao_id: int, cor: str) -> ProdutoVariacao:
        variacao = self.obter_variacao(db, variacao_id)
        variacao.cor = cor
        db.flush()
        eventos.emitir(db, "variacao.renomeada", _dados_variacao(variacao), audiencia=eventos.TODOS)
        return variacao

    def adicionar_variacao(
        self, db: Session, produto_id: int, dados: VariacaoCreate, usuario_id: int
    ) -> ProdutoVariacao:
        """Adiciona uma cor nova a um produto já salvo (resolve "cor nova do mesmo produto").

        O saldo nunca é setado direto: se vier com estoque inicial, vira uma
        movimentação de ENTRADA (append-only) via estoque_service.
        """
        produto = self.obter(db, produto_id)
        if not produto.ativo:
            raise RegraNegocioError("Não é possível adicionar cores a um produto inativo.")
        cor = (dados.cor or "").strip()
        if produto_repo.variacao_por_cor(db, produto_id, cor) is not None:
            rotulo = f" ({cor})" if cor else " padrão"
            raise RegraNegocioError(f"Já existe a cor{rotulo} neste produto.")
        variacao = ProdutoVariacao(
            cor=cor,
            estoque_modo=dados.estoque_modo,
            estoque_fisico=0,  # saldo entra via movimentação, nunca direto
            estoque_reservado=0,
            rotulo_aprox=dados.rotulo_aprox,
            estoque_minimo=dados.estoque_minimo,
            ativo=True,
        )
        produto.variacoes.append(variacao)
        db.flush()
        eventos.emitir(
            db, "variacao.adicionada", _dados_variacao(variacao), audiencia=eventos.TODOS
        )
        if dados.estoque_fisico and dados.estoque_fisico > 0:
            # entrada() emite estoque.movimentado por conta própria — não duplicar aqui.
            estoque_service.entrada(
                db, variacao, dados.estoque_fisico, usuario_id, origem=OrigemMov.MANUAL
            )
        return variacao

    def remover_variacao(self, db: Session, variacao_id: int) -> tuple[ProdutoVariacao, str]:
        """Remove (ou inativa) uma cor de um produto.

        Regras:
        - saldo != 0 -> bloqueia (zerar no módulo de Estoque antes).
        - com histórico (movimentações/pedidos/inventário) -> inativa (preserva append-only).
        - limpa -> hard-delete + remove a foto do MinIO.
        Retorna (variacao, acao) onde acao é "inativada" ou "deletada".
        """
        variacao = self.obter_variacao(db, variacao_id)
        if variacao.estoque_fisico != 0 or variacao.estoque_reservado != 0:
            raise RegraNegocioError(
                "Não é possível remover uma cor com saldo em estoque. "
                "Zere o saldo no módulo de Estoque antes de remover."
            )
        if produto_repo.variacao_tem_historico(db, variacao_id):
            variacao.ativo = False
            db.flush()
            eventos.emitir(
                db,
                "variacao.removida",
                {**_dados_variacao(variacao), "acao": "inativada"},
                audiencia=eventos.TODOS,
            )
            return variacao, "inativada"
        # Limpa: hard-delete. Os bytes da foto vão junto com a linha (bytea no Postgres).
        # O payload é montado antes do delete: depois do flush a instância está expirada
        # e ler os atributos dispararia um SELECT da linha que já não existe.
        dados_evento = {**_dados_variacao(variacao), "acao": "deletada"}
        db.delete(variacao)
        db.flush()
        eventos.emitir(db, "variacao.removida", dados_evento, audiencia=eventos.TODOS)
        return variacao, "deletada"

    def reativar_variacao(self, db: Session, variacao_id: int) -> ProdutoVariacao:
        """Reverte a inativação de uma cor (não do hard-delete, que remove a linha).

        Bloqueia se já existir outra cor ativa com o mesmo nome no mesmo produto
        (variacao_por_cor só enxerga ativas, então qualquer hit aqui é conflito).
        """
        variacao = self.obter_variacao(db, variacao_id)
        cor = variacao.cor or ""
        conflito = produto_repo.variacao_por_cor(db, variacao.produto_id, cor)
        if conflito is not None and conflito.id != variacao_id:
            rotulo = f' "{cor}"' if cor else " padrão"
            raise RegraNegocioError(f"Já existe uma cor ativa{rotulo} neste produto.")
        variacao.ativo = True
        db.flush()
        eventos.emitir(db, "variacao.reativada", _dados_variacao(variacao), audiencia=eventos.TODOS)
        return variacao


produto_service = ProdutoService()
