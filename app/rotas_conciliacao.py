import io
import zipfile

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.conciliacao import TAMANHO_MAXIMO, ErroConciliacao, checar_pessoas, validar_envio
from app.jobs import JobEmAndamento
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/conciliacao")

TIPO_JOB = "conciliar"


def _tela(request: Request, cred: Credenciais, erro: str | None = None, status_code: int = 200):
    st = request.app.state
    execucoes = st.conciliacoes.listar(15)
    for e in execucoes:
        e["job"] = st.jobs.obter(e.get("job_id") or e["nome"][-12:])
    return pagina(request, "conciliacao.html", cred, status_code=status_code, ativo="conciliacao", erro=erro,
                  pessoas=st.arquivo_pessoas.resumo(), bancos=st.repo_bancos.resumo(),
                  ultimo_job=st.jobs.ultimo(TIPO_JOB), execucoes=execucoes)


@router.get("")
def tela(request: Request, cred: Credenciais = Depends(usuario_atual)):
    return _tela(request, cred)


@router.post("")
async def enviar(request: Request, arquivos: list[UploadFile] = File(...), cred: Credenciais = Depends(usuario_atual)):
    st = request.app.state
    lidos = [(a.filename or "", await a.read(TAMANHO_MAXIMO + 1)) for a in arquivos if a.filename]
    try:
        lidos = validar_envio(lidos)
        checar_pessoas(st.arquivo_pessoas.caminho)
        if st.repo_bancos.vazio():
            raise ErroConciliacao("Cadastro de bancos vazio. Faça a carga inicial em Bancos e convênios.")
    except ErroConciliacao as exc:
        return _tela(request, cred, str(exc), 400)

    pastas = {}
    tarefa = st.tarefa_conciliar

    def criar(job_id):
        pastas[job_id] = st.conciliacoes.nova(job_id, cred.usuario, lidos)

    try:
        job_id = await run_in_threadpool(
            st.jobs.iniciar, TIPO_JOB, cred.usuario,
            lambda ctx: tarefa(ctx, cred, pastas[ctx.job_id], st.repo_bancos, st.arquivo_pessoas), criar)
    except JobEmAndamento as exc:
        return _tela(request, cred, "Já há uma conciliação em andamento. Aguarde terminar para enviar outra.", 409)
    return RedirectResponse(f"/tarefas/{job_id}", status_code=303)


@router.get("/{execucao}")
def resultado(request: Request, execucao: str, cred: Credenciais = Depends(usuario_atual)):
    st = request.app.state
    r = st.conciliacoes.resultado(execucao)
    if r is None:
        return RedirectResponse("/conciliacao", status_code=303)
    meta = st.conciliacoes.meta(execucao)
    return pagina(request, "conciliacao_resultado.html", cred, ativo="conciliacao", execucao=execucao, r=r, meta=meta,
                  relatorios=[a.name for a in st.conciliacoes.relatorios(execucao)])


@router.get("/{execucao}/relatorios.zip")
def baixar_zip(request: Request, execucao: str, cred: Credenciais = Depends(usuario_atual)):
    arquivos = request.app.state.conciliacoes.relatorios(execucao)
    if not arquivos:
        return RedirectResponse("/conciliacao", status_code=303)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in arquivos:
            z.write(a, a.name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="conciliacao_{execucao[:15]}.zip"'})


@router.get("/{execucao}/{arquivo}")
def baixar_relatorio(request: Request, execucao: str, arquivo: str, cred: Credenciais = Depends(usuario_atual)):
    caminho = request.app.state.conciliacoes.arquivo_relatorio(execucao, arquivo)
    if not caminho:
        return RedirectResponse("/conciliacao", status_code=303)
    return FileResponse(caminho, filename=caminho.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
