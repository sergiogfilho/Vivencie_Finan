"""Aplicação web Vivencie Finan."""
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import acade_auth, config
from app.security import COOKIE_NOME, CofreCredenciais, Credenciais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vivencie.web")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


class _NaoAutenticado(Exception):
    pass


class LimitadorTentativas:
    """Bloqueia um IP após N falhas numa janela, para não travar a conta no ACADE."""

    def __init__(self, maximo: int = 5, janela_s: int = 900):
        self.maximo, self.janela_s = maximo, janela_s
        self._falhas: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _limpar(self, ip: str, agora: float) -> deque:
        fila = self._falhas[ip]
        while fila and agora - fila[0] > self.janela_s:
            fila.popleft()
        return fila

    def bloqueado(self, ip: str) -> bool:
        with self._lock:
            return len(self._limpar(ip, time.time())) >= self.maximo

    def registrar_falha(self, ip: str) -> None:
        with self._lock:
            self._limpar(ip, time.time()).append(time.time())

    def limpar(self, ip: str) -> None:
        with self._lock:
            self._falhas.pop(ip, None)


def criar_app(
    settings: config.Settings | None = None,
    validador: Callable[[str, str], bool] = acade_auth.validar_no_acade,
) -> FastAPI:
    settings = settings or config.carregar()
    cofre = CofreCredenciais(settings.secret_key, settings.cred_ttl_dias * 86400)
    limitador = LimitadorTentativas()

    app = FastAPI(title="Vivencie Finan", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.state.settings = settings
    app.state.cofre = cofre

    @app.middleware("http")
    async def cabecalhos_seguranca(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(_NaoAutenticado)
    async def _redirecionar_login(request: Request, exc: _NaoAutenticado):
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NOME, path="/")
        return resp

    def usuario_atual(request: Request) -> Credenciais:
        cred = cofre.decifrar(request.cookies.get(COOKIE_NOME))
        if cred is None:
            raise _NaoAutenticado()
        return cred

    def contexto(request: Request, cred: Credenciais, **extra):
        expira = datetime.fromtimestamp(cred.expira_em(cofre.ttl_segundos))
        return {"request": request, "usuario": cred.usuario, "cache_expira": expira, **extra}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if cofre.decifrar(request.cookies.get(COOKIE_NOME)):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"erro": None, "usuario": ""})

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request, usuario: str = Form(...), senha: str = Form(...)):
        ip = request.client.host if request.client else "?"
        usuario = usuario.strip()

        def erro(msg: str, status: int):
            return templates.TemplateResponse(
                request, "login.html", {"erro": msg, "usuario": usuario}, status_code=status
            )

        if limitador.bloqueado(ip):
            return erro("Muitas tentativas sem sucesso. Aguarde 15 minutos e tente novamente.", 429)
        if not usuario or not senha:
            return erro("Informe usuário e senha do ACADE.", 400)

        ok = await run_in_threadpool(validador, usuario, senha)
        if not ok:
            limitador.registrar_falha(ip)
            logger.info("Login recusado para usuário=%s ip=%s", usuario, ip)
            return erro("Não foi possível entrar no ACADE com essas credenciais "
                        "(usuário/senha incorretos ou ACADE indisponível).", 401)

        limitador.limpar(ip)
        logger.info("Login aceito para usuário=%s ip=%s", usuario, ip)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE_NOME, cofre.cifrar(usuario, senha),
            max_age=cofre.ttl_segundos, httponly=True, secure=settings.cookie_secure,
            samesite="strict", path="/",
        )
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NOME, path="/")
        return resp

    @app.get("/", response_class=HTMLResponse)
    def painel(request: Request, cred: Credenciais = Depends(usuario_atual)):
        return templates.TemplateResponse(request, "painel.html", contexto(request, cred, ativo="painel"))

    app.state.usuario_atual = usuario_atual
    return app

