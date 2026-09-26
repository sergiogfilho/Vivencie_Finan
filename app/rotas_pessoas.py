from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.jobs import JobEmAndamento
from app.pessoas import NOME_ARQUIVO, TIPOS, ArquivoPessoas
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/pessoas")

TIPO_JOB = "atualizar_pessoas"
POR_PAGINA = 50


def _arquivo(request: Request) -> ArquivoPessoas:
    return request.app.state.arquivo_pessoas


@router.get("")
def listar(request: Request, q: str = "", p: int = 1, cred: Credenciais = Depends(usuario_atual)):
    arquivo = _arquivo(request)
    lista = arquivo.listar(q, p, POR_PAGINA)
    ultimo = request.app.state.jobs.ultimo(TIPO_JOB)
    return pagina(request, "pessoas.html", cred, ativo="pessoas", resumo=arquivo.resumo(), tipos=TIPOS,
                  q=q.strip(), lista=lista, janela=_janela(lista["pagina"], lista["paginas"]), ultimo_job=ultimo)


def _janela(atual: int, total: int, raio: int = 2) -> list:
    """Números de página a exibir, com None onde há salto: 1 … 4 5 6 7 8 … 48."""
    nums = sorted({1, total, *range(max(1, atual - raio), min(total, atual + raio) + 1)})
    out = []
    for n in nums:
        if out and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


@router.get(f"/{NOME_ARQUIVO}")
def baixar(request: Request, cred: Credenciais = Depends(usuario_atual)):
    arquivo = _arquivo(request)
    if not arquivo.existe():
        return RedirectResponse("/pessoas", status_code=303)
    return FileResponse(arquivo.caminho, media_type="text/csv; charset=utf-8", filename=NOME_ARQUIVO)


@router.post("/atualizar-acade")
def atualizar_acade(request: Request, cred: Credenciais = Depends(usuario_atual)):
    arquivo, tarefa = _arquivo(request), request.app.state.tarefa_atualizar_pessoas
    paralelo = request.app.state.settings.pessoas_paralelo
    try:
        job_id = request.app.state.jobs.iniciar(TIPO_JOB, cred.usuario,
                                                lambda ctx: tarefa(ctx, cred, arquivo, paralelo=paralelo))
    except JobEmAndamento as exc:
        job_id = exc.job_id
    return RedirectResponse(f"/tarefas/{job_id}", status_code=303)
