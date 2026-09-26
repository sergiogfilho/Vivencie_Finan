"""Peças compartilhadas pelas rotas: templates, autenticação e contexto de página."""
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.security import COOKIE_NOME, Credenciais

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["milhar"] = lambda n: f"{n:,}".replace(",", ".") if isinstance(n, int) else n

MENSAGENS = {
    "convenio_salvo": ("ok", "Convênio salvo."),
    "conta_salva": ("ok", "Conta bancária salva."),
    "conta_criada": ("ok", "Conta bancária criada."),
    "centro_salvo": ("ok", "Centro de custo salvo."),
    "centro_removido": ("ok", "Vínculo do centro de custo removido."),
    "importado": ("ok", "Cadastro de bancos importado."),
}


class NaoAutenticado(Exception):
    pass


def usuario_atual(request: Request) -> Credenciais:
    cred = request.app.state.cofre.decifrar(request.cookies.get(COOKIE_NOME))
    if cred is None:
        raise NaoAutenticado()
    return cred


def pagina(request: Request, template: str, cred: Credenciais, status_code: int = 200, **extra):
    cofre = request.app.state.cofre
    msg = MENSAGENS.get(request.query_params.get("msg", ""))
    ctx = {
        "usuario": cred.usuario,
        "cache_expira": datetime.fromtimestamp(cred.expira_em(cofre.ttl_segundos)),
        "mensagem": msg,
        **extra,
    }
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
