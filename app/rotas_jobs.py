from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/tarefas")

TITULOS = {
    "atualizar_bancos": ("Atualizar bancos do ACADE", "/bancos"),
    "atualizar_pessoas": ("Atualizar pessoas do ACADE", "/pessoas"),
}


@router.get("/{job_id}")
def ver(request: Request, job_id: str, cred: Credenciais = Depends(usuario_atual)):
    job = request.app.state.jobs.obter(job_id)
    if not job:
        return RedirectResponse("/", status_code=303)
    titulo, voltar = TITULOS.get(job["tipo"], (job["tipo"], "/"))
    return pagina(request, "tarefa.html", cred, ativo=job["tipo"], job=job, titulo=titulo, voltar=voltar)


@router.get("/{job_id}/estado")
def estado(request: Request, job_id: str, depois_de: int = 0, depois_ponto: int = 0, cred: Credenciais = Depends(usuario_atual)):
    jobs = request.app.state.jobs
    job = jobs.obter(job_id)
    if not job:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    return {"job": job, "logs": jobs.logs(job_id, depois_de), "pontos": jobs.pontos(job_id, depois_ponto)}
