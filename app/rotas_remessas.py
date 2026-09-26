import io
import zipfile
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.jobs import JobEmAndamento
from app.remessas import NOME_RELATORIO, diagnosticar, ler_bancos_csv
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/remessas")

TIPO_JOB = "gerar_remessas"


def _diagnostico_atual(request: Request):
    """Validação do relatório atual contra o cadastro de bancos, pelo mesmo caminho da tarefa."""
    relatorio, repo = request.app.state.arquivo_contas, request.app.state.repo_bancos
    df = relatorio.ler()
    if df is None or repo.vazio():
        return None
    df_bancos = ler_bancos_csv(io.StringIO(repo.exportar_df().to_csv(index=False)))
    try:
        return diagnosticar(df, df_bancos)
    except (KeyError, ValueError) as exc:
        return {"erro": str(exc)}


def _tela(request: Request, cred: Credenciais, erro: str | None = None, status_code: int = 200,
          data_inicial: str | None = None, data_final: str | None = None):
    st = request.app.state
    hoje = date.today().isoformat()
    return pagina(request, "remessas.html", cred, status_code=status_code, ativo="remessas", erro=erro,
                  relatorio=st.arquivo_contas.resumo(), diagnostico=_diagnostico_atual(request),
                  pessoas=st.arquivo_pessoas.resumo(), bancos=st.repo_bancos.resumo(),
                  ultimo_job=st.jobs.ultimo(TIPO_JOB), historico=st.jobs.listar(TIPO_JOB, 15),
                  data_inicial=data_inicial or hoje, data_final=data_final or hoje)


@router.get("")
def tela(request: Request, cred: Credenciais = Depends(usuario_atual)):
    return _tela(request, cred)


def _br(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")


@router.post("/gerar")
def gerar(request: Request, modo: str = Form(...), data_inicial: str = Form(""), data_final: str = Form(""),
          cred: Credenciais = Depends(usuario_atual)):
    st = request.app.state
    periodo = None
    if modo == "capturar":
        try:
            ini, fim = _br(data_inicial), _br(data_final)
        except ValueError:
            return _tela(request, cred, "Informe as datas inicial e final.", 400, data_inicial, data_final)
        if data_inicial > data_final:
            return _tela(request, cred, "A data inicial não pode ser posterior à final.", 400, data_inicial, data_final)
        periodo = (ini, fim)
    elif modo != "atual":
        return _tela(request, cred, "Opção inválida.", 400)
    elif not st.arquivo_contas.existe():
        return _tela(request, cred, "Ainda não há relatório capturado; informe o período.", 400)

    tarefa = st.tarefa_gerar_remessas
    try:
        job_id = st.jobs.iniciar(TIPO_JOB, cred.usuario, lambda ctx: tarefa(
            ctx, cred, st.arquivo_contas, st.repo_bancos, st.arquivo_pessoas, st.execucoes, periodo=periodo))
    except JobEmAndamento as exc:
        job_id = exc.job_id
    return RedirectResponse(f"/tarefas/{job_id}", status_code=303)


@router.get(f"/{NOME_RELATORIO}")
def baixar_relatorio(request: Request, cred: Credenciais = Depends(usuario_atual)):
    rel = request.app.state.arquivo_contas
    if not rel.existe():
        return RedirectResponse("/remessas", status_code=303)
    return FileResponse(rel.caminho, media_type="text/csv; charset=utf-8", filename=NOME_RELATORIO)


@router.get("/{execucao}/remessas.zip")
def baixar_zip(request: Request, execucao: str, cred: Credenciais = Depends(usuario_atual)):
    arquivos = request.app.state.execucoes.remessas(execucao)
    if not arquivos:
        return RedirectResponse("/remessas", status_code=303)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in arquivos:
            z.write(a, a.name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="remessas_{execucao[:15]}.zip"'})


@router.get("/{execucao}/{arquivo}")
def baixar_remessa(request: Request, execucao: str, arquivo: str, cred: Credenciais = Depends(usuario_atual)):
    caminho = request.app.state.execucoes.arquivo_remessa(execucao, arquivo)
    if not caminho:
        return RedirectResponse("/remessas", status_code=303)
    return FileResponse(caminho, media_type="application/octet-stream", filename=caminho.name)
