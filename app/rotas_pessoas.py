from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.jobs import JobEmAndamento
from app.pessoas import NOME_ARQUIVO, TIPOS, ArquivoPessoas
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/pessoas")

TIPO_JOB = "atualizar_pessoas"


def _arquivo(request: Request) -> ArquivoPessoas:
    return request.app.state.arquivo_pessoas


@router.get("")
def listar(request: Request, q: str = "", cred: Credenciais = Depends(usuario_atual)):
    arquivo = _arquivo(request)
    achados, qtd = arquivo.buscar(q)
    jobs = request.app.state.jobs
    ultimo = jobs.ultimo(TIPO_JOB)
    return pagina(request, "pessoas.html", cred, ativo="pessoas", resumo=arquivo.resumo(), tipos=TIPOS,
                  q=q, achados=achados, qtd_achados=qtd, ultimo_job=ultimo)


@router.get(f"/{NOME_ARQUIVO}")
def baixar(request: Request, cred: Credenciais = Depends(usuario_atual)):
    arquivo = _arquivo(request)
    if not arquivo.existe():
        return RedirectResponse("/pessoas", status_code=303)
    return FileResponse(arquivo.caminho, media_type="text/csv; charset=utf-8", filename=NOME_ARQUIVO)


@router.post("/atualizar-acade")
def atualizar_acade(request: Request, cred: Credenciais = Depends(usuario_atual)):
    arquivo, tarefa = _arquivo(request), request.app.state.tarefa_atualizar_pessoas
    try:
        job_id = request.app.state.jobs.iniciar(TIPO_JOB, cred.usuario, lambda ctx: tarefa(ctx, cred, arquivo))
    except JobEmAndamento as exc:
        job_id = exc.job_id
    return RedirectResponse(f"/tarefas/{job_id}", status_code=303)
