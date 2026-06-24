"""Gera imagens de DEMONSTRAÇÃO (placeholders tingidos pela cor) para variações.

Apenas para a apresentação: mostra a feature de foto por cor funcionando, offline. O cliente
substitui pelas fotos reais via a tela de Produtos. Idempotente (pula variação que já tem imagem).

Uso: uv run python scripts/gerar_imagens_demo.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from sqlalchemy import or_, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.imagens import salvar_imagem_variacao  # noqa: E402
from app.models.pedido import Pedido, PedidoItem  # noqa: E402
from app.models.produto import Produto, ProdutoVariacao  # noqa: E402

# Nome de cor (substring, minúsculas) → RGB de fundo.
CORES = {
    "branco": (238, 238, 238),
    "preto": (45, 45, 45),
    "prata": (188, 192, 196),
    "inox": (188, 192, 196),
    "cinza": (150, 150, 150),
    "azul marinho": (28, 42, 92),
    "azul escuro": (30, 58, 138),
    "azul royal": (37, 99, 235),
    "azul claro": (125, 185, 232),
    "azul": (45, 100, 210),
    "verde escuro": (22, 101, 52),
    "verde claro": (134, 201, 120),
    "verde": (40, 150, 75),
    "vermelho": (200, 45, 45),
    "laranja": (230, 130, 30),
    "rosa claro": (244, 175, 200),
    "rosa escuro": (200, 70, 120),
    "rosa": (232, 120, 165),
    "roxo": (128, 70, 170),
    "amarelo": (235, 200, 45),
    "dourado": (185, 138, 25),
}
PADRAO = (170, 150, 110)


def _rgb(cor: str) -> tuple[int, int, int]:
    c = (cor or "").strip().lower()
    for nome, rgb in CORES.items():
        if nome in c:
            return rgb
    return PADRAO


def _fonte(tam: int):
    for caminho in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(caminho, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def _imagem(codigo: str, descricao: str, cor: str) -> bytes:
    fundo = _rgb(cor)
    lum = 0.299 * fundo[0] + 0.587 * fundo[1] + 0.114 * fundo[2]
    tinta = (30, 30, 30) if lum > 150 else (245, 245, 245)
    img = Image.new("RGB", (600, 600), fundo)
    d = ImageDraw.Draw(img)
    # moldura sutil
    d.rectangle([12, 12, 588, 588], outline=tinta, width=3)
    d.text((300, 250), codigo, fill=tinta, font=_fonte(64), anchor="mm")
    d.text((300, 330), (cor or "padrão").upper(), fill=tinta, font=_fonte(40), anchor="mm")
    d.text((300, 540), descricao[:38], fill=tinta, font=_fonte(22), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main() -> None:
    db = SessionLocal()
    try:
        # Variações alvo: canetas (exemplo do cliente) + produtos de seed + itens dos pedidos DEMO.
        ids_demo = set(
            db.scalars(
                select(PedidoItem.produto_variacao_id)
                .join(Pedido, Pedido.id == PedidoItem.pedido_id)
                .where(Pedido.observacao.like("DEMO%"))
            )
        )
        codigos_seed = ["K708", "KD33", "JSC1140", "A9003", "FA12"]
        stmt = (
            select(ProdutoVariacao)
            .join(ProdutoVariacao.produto)
            .where(
                or_(
                    Produto.descricao.ilike("%CANETA%"),
                    Produto.codigo.in_(codigos_seed),
                    ProdutoVariacao.id.in_(ids_demo),
                )
            )
        )
        variacoes = list(db.scalars(stmt))

        gerados = 0
        for v in variacoes:
            if v.imagem_filename:
                continue
            conteudo = _imagem(v.produto.codigo, v.produto.descricao, v.cor)
            v.imagem_filename = salvar_imagem_variacao(v.id, conteudo)
            gerados += 1
        db.commit()
        print(f"Imagens de demonstração geradas: {gerados} (de {len(variacoes)} variações alvo)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
