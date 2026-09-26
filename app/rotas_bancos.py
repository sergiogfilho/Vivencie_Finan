import io

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.bancos import BANCOS_COM_CNAB, CAMPOS_EDITAVEIS, ErroBancos, RepositorioBancos
from app.jobs import JobEmAndamento
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/bancos")

TIPO_JOB = "atualizar_bancos"


def _repo(request: Request) -> RepositorioBancos:
    return request.app.state.repo_bancos


def _ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _lista(request: Request, cred: Credenciais, erro: str | None = None, status_code: int = 200):
    repo = _repo(request)
    return pagina(request, "bancos.html", cred, status_code=status_code, ativo="bancos",
                  contas=repo.listar_contas(), resumo=repo.resumo(), vazio=repo.vazio(), erro=erro,
                  ultimo_job=request.app.state.jobs.ultimo(TIPO_JOB), bancos_cnab=BANCOS_COM_CNAB)


@router.get("")
def listar(request: Request, cred: Credenciais = Depends(usuario_atual)):
    return _lista(request, cred)


@router.post("/importar")
async def importar(request: Request, arquivo: UploadFile = File(...), cred: Credenciais = Depends(usuario_atual)):
    try:
        conteudo = await arquivo.read()
        df = pd.read_csv(io.BytesIO(conteudo), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        _repo(request).importar_csv(df, cred.usuario)
    except (ErroBancos, ValueError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        return _lista(request, cred, erro=f"Não foi possível importar: {exc}", status_code=400)
    return _ir("/bancos?msg=importado")


@router.get("/exportar.csv")
def exportar(request: Request, cred: Credenciais = Depends(usuario_atual)):
    csv = _repo(request).exportar_df().to_csv(index=False, encoding="utf-8")
    return Response(csv.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="bancos.csv"'})


@router.post("/atualizar-acade")
def atualizar_acade(request: Request, cred: Credenciais = Depends(usuario_atual)):
    repo, tarefa = _repo(request), request.app.state.tarefa_atualizar_bancos
    try:
        job_id = request.app.state.jobs.iniciar(TIPO_JOB, cred.usuario, lambda ctx: tarefa(ctx, cred, repo))
    except JobEmAndamento as exc:
        job_id = exc.job_id
    return _ir(f"/tarefas/{job_id}")


@router.post("/{conta_id}/convenio")
def salvar_convenio(request: Request, conta_id: int, convenio: str = Form(""), cred: Credenciais = Depends(usuario_atual)):
    try:
        _repo(request).atualizar_conta(conta_id, {"convenio": convenio}, cred.usuario)
    except ErroBancos as exc:
        return _lista(request, cred, erro=str(exc), status_code=400)
    return _ir(f"/bancos?msg=convenio_salvo#conta-{conta_id}")


@router.get("/nova")
def nova(request: Request, cred: Credenciais = Depends(usuario_atual)):
    return pagina(request, "conta_form.html", cred, ativo="bancos", conta=None, centros=[], erro=None)


@router.post("/nova")
async def criar(request: Request, cred: Credenciais = Depends(usuario_atual)):
    form = await request.form()
    campos = {k: form.get(k, "") for k in CAMPOS_EDITAVEIS}
    try:
        conta_id = _repo(request).criar_conta(campos, cred.usuario)
    except ErroBancos as exc:
        return pagina(request, "conta_form.html", cred, status_code=400, ativo="bancos",
                      conta=campos, centros=[], erro=str(exc))
    return _ir(f"/bancos/{conta_id}?msg=conta_criada")


@router.get("/{conta_id}")
def detalhe(request: Request, conta_id: int, cred: Credenciais = Depends(usuario_atual)):
    repo = _repo(request)
    conta = repo.obter_conta(conta_id)
    if not conta:
        return _ir("/bancos")
    return pagina(request, "conta_form.html", cred, ativo="bancos", conta=conta,
                  centros=repo.listar_centros(conta_id), erro=None)


@router.post("/{conta_id}")
async def salvar(request: Request, conta_id: int, cred: Credenciais = Depends(usuario_atual)):
    form = await request.form()
    repo = _repo(request)
    try:
        repo.atualizar_conta(conta_id, {k: form.get(k, "") for k in CAMPOS_EDITAVEIS if k in form}, cred.usuario)
    except ErroBancos as exc:
        conta = {**(repo.obter_conta(conta_id) or {}), **dict(form)}
        return pagina(request, "conta_form.html", cred, status_code=400, ativo="bancos", conta=conta,
                      centros=repo.listar_centros(conta_id), erro=str(exc))
    return _ir(f"/bancos/{conta_id}?msg=conta_salva")


@router.post("/{conta_id}/centros")
def vincular_centro(request: Request, conta_id: int, nome: str = Form(""), cred: Credenciais = Depends(usuario_atual)):
    repo = _repo(request)
    try:
        repo.salvar_centro(nome, conta_id, cred.usuario)
    except ErroBancos as exc:
        return pagina(request, "conta_form.html", cred, status_code=400, ativo="bancos",
                      conta=repo.obter_conta(conta_id), centros=repo.listar_centros(conta_id), erro=str(exc))
    return _ir(f"/bancos/{conta_id}?msg=centro_salvo#centros")


@router.post("/{conta_id}/centros/{centro_id}/remover")
def remover_centro(request: Request, conta_id: int, centro_id: int, cred: Credenciais = Depends(usuario_atual)):
    repo = _repo(request)
    try:
        repo.remover_centro(centro_id, cred.usuario)
    except ErroBancos as exc:
        return pagina(request, "conta_form.html", cred, status_code=400, ativo="bancos",
                      conta=repo.obter_conta(conta_id), centros=repo.listar_centros(conta_id), erro=str(exc))
    return _ir(f"/bancos/{conta_id}?msg=centro_removido#centros")
