"""Aplicação web Vivencie Finan."""
import logging
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import acade_auth, config, rotas_bancos, rotas_jobs, rotas_pessoas, rotas_remessas, tarefas
from app.bancos import RepositorioBancos
from app.db import Banco
from app.jobs import GerenciadorJobs
from app.pessoas import ArquivoPessoas
from app.remessas import ArquivoContasPagar, Execucoes
from app.security import COOKIE_NOME, CofreCredenciais, Credenciais
from app.web import NaoAutenticado, pagina, templates, usuario_atual

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vivencie.web")

BASE = Path(__file__).parent

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
    tarefa_atualizar_bancos: Callable = tarefas.atualizar_bancos,
    tarefa_atualizar_pessoas: Callable = tarefas.atualizar_pessoas,
    tarefa_gerar_remessas: Callable = tarefas.gerar_remessas,
) -> FastAPI:
    settings = settings or config.carregar()
    cofre = CofreCredenciais(settings.secret_key, settings.cred_ttl_dias * 86400)
    limitador = LimitadorTentativas()

    app = FastAPI(title="Vivencie Finan", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.state.settings = settings
    app.state.cofre = cofre
    db = Banco(settings.data_dir / "vivencie.db")
    app.state.db = db
    app.state.repo_bancos = RepositorioBancos(db)
    app.state.jobs = GerenciadorJobs(db)
    app.state.tarefa_atualizar_bancos = tarefa_atualizar_bancos
    app.state.arquivo_pessoas = ArquivoPessoas(settings.data_dir / "arquivos_auxiliares")
    app.state.tarefa_atualizar_pessoas = tarefa_atualizar_pessoas
    app.state.arquivo_contas = ArquivoContasPagar(settings.data_dir / "arquivos_auxiliares")
    app.state.execucoes = Execucoes(settings.data_dir / "remessas")
    app.state.tarefa_gerar_remessas = tarefa_gerar_remessas
    app.include_router(rotas_bancos.router)
    app.include_router(rotas_pessoas.router)
    app.include_router(rotas_remessas.router)
    app.include_router(rotas_jobs.router)

    @app.middleware("http")
    async def cabecalhos_seguranca(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(NaoAutenticado)
    async def _redirecionar_login(request: Request, exc: NaoAutenticado):
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NOME, path="/")
        return resp

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
        return pagina(request, "painel.html", cred, ativo="painel",
                      resumo_bancos=app.state.repo_bancos.resumo(),
                      resumo_pessoas=app.state.arquivo_pessoas.resumo(),
                      job_pessoas=app.state.jobs.ultimo(rotas_pessoas.TIPO_JOB),
                      job_remessas=app.state.jobs.ultimo(rotas_remessas.TIPO_JOB))

    return app

