"""Helper de feedback de sucesso via redirect (padrão PRG — Post/Redirect/Get).

Depois de salvar, redireciona para a listagem com `?ok=<mensagem>`; a rota de listagem
lê o parâmetro e passa `mensagem_ok` ao contexto, exibido pelo `_flash.html` (já incluído
no base.html). Evita reenvio de formulário no F5 e dá confirmação visual ao usuário.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import RedirectResponse


def redirect_ok(url: str, msg: str) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url=f"{url}{sep}ok={quote(msg)}", status_code=303)
