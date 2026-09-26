from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse

from app.bancos import chave_agencia, chave_conta
from app.jobs import EM_ANDAMENTO, JobEmAndamento
from app.retornos import (MODOS, MODOS_REAIS, TAMANHO_MAXIMO, ArquivoRepetido, ErroRetorno, RepositorioRetornos,
                          item_exibicao, termo_conta_movimento)
from app.security import Credenciais
from app.web import pagina, usuario_atual

router = APIRouter(prefix="/retornos")

TIPO_JOB = "baixar_retorno"


def _repo(request: Request) -> RepositorioRetornos:
    return request.app.state.retornos


def situacao(request: Request, retorno: dict, com_itens: bool = False) -> dict:
    """Estado de um retorno: prévia do arquivo, execuções e o que ainda está pendente."""
    repo, jobs = _repo(request), request.app.state.jobs
    rid = retorno["id"]
    previa = repo.ler(rid)
    execucoes = [{**e, "titulo": MODOS[e["modo"]], "job": jobs.obter(e["job_id"])} for e in repo.execucoes(rid)]
    reais, simulados = repo.ultimo_estado(rid, reais=True), repo.ultimo_estado(rid, reais=False)
    resolvidos = repo.resolvidos(rid)
    confirmados = previa["confirmados"]
    teve_baixa = any(e["modo"] in MODOS_REAIS for e in execucoes)
    em_andamento = next((e for e in execucoes if e["job"] and e["job"]["status"] in EM_ANDAMENTO), None)
    contagem = {"baixados": 0, "desconhecidos": 0}
    for p in confirmados:
        st = reais.get(p["chave"], {}).get("status")
        if st == "sucesso":
            contagem["baixados"] += 1
        elif st == "desconhecido":
            contagem["desconhecidos"] += 1
    pendentes = [p for p in confirmados if p["chave"] not in resolvidos]

    if em_andamento:
        rotulo, classe = "Em andamento", "badge--warn"
    elif not teve_baixa:
        rotulo, classe = ("Simulado" if simulados else "Aguardando baixa"), ""
    elif pendentes or contagem["desconhecidos"]:
        rotulo, classe = "Com pendências", "badge--warn"
    else:
        rotulo, classe = "Baixado", "badge--ok"

    out = {
        **retorno,
        "previa": previa,
        "execucoes": execucoes,
        "teve_baixa": teve_baixa,
        "simulado": bool(simulados),
        "em_andamento": em_andamento,
        "pendentes": len(pendentes),
        "baixados": contagem["baixados"],
        "desconhecidos": contagem["desconhecidos"],
        "anormais": [e for e in execucoes if e["modo"] in MODOS_REAIS and e["job"]
                     and e["job"]["status"] in ("erro", "interrompido")],
        "rotulo": rotulo,
        "classe": classe,
    }
    if com_itens:
        out["confirmados"] = [{**item_exibicao(p), "real": reais.get(p["chave"]), "simulado": simulados.get(p["chave"])}
                              for p in confirmados]
        out["nao_confirmados"] = [item_exibicao(p) for p in previa["nao_confirmados"]]
    return out


def _lista(request: Request, cred: Credenciais, erro: str | None = None, status_code: int = 200,
           anterior: str | None = None):
    repo = _repo(request)
    retornos = [situacao(request, r) for r in repo.listar(30)]
    return pagina(request, "retornos.html", cred, status_code=status_code, ativo="retornos", erro=erro,
                  anterior=anterior, retornos=retornos, ultimo_job=request.app.state.jobs.ultimo(TIPO_JOB))


@router.get("")
def tela(request: Request, cred: Credenciais = Depends(usuario_atual)):
    return _lista(request, cred)


@router.post("/enviar")
async def enviar(request: Request, arquivos: list[UploadFile] = File(...), cred: Credenciais = Depends(usuario_atual)):
    lidos = []
    for a in arquivos:
        lidos.append((a.filename or "retorno.RET", await a.read(TAMANHO_MAXIMO + 1)))
    try:
        retorno_id = await run_in_threadpool(_repo(request).registrar, cred.usuario, lidos)
    except ArquivoRepetido as exc:
        return _lista(request, cred, str(exc), 409, anterior=exc.retorno_id)
    except ErroRetorno as exc:
        return _lista(request, cred, str(exc), 400)
    return RedirectResponse(f"/retornos/{retorno_id}", status_code=303)


def _detalhe(request: Request, cred: Credenciais, retorno: dict, erro: str | None = None, status_code: int = 200):
    r = situacao(request, retorno, com_itens=True)
    contas = {(c["banco_codigo"], c["agencia_chave"], c["conta_chave"]): c for c in request.app.state.repo_bancos.listar_contas()}
    lotes = []
    for a in r["previa"]["arquivos"]:
        for l in a["lotes"]:
            chave = (l["codigo_banco"], chave_agencia(l["agencia"]), chave_conta(l["conta"] + l["conta_dv"]))
            lotes.append({**l, "arquivo": a["arquivo"], "cadastro": contas.get(chave),
                          "conta_movimento": termo_conta_movimento(l["agencia"], l["conta"])})
    return pagina(request, "retorno.html", cred, status_code=status_code, ativo="retornos", erro=erro, r=r,
                  lotes=lotes, workers=request.app.state.settings.baixa_workers)


@router.get("/{retorno_id}")
def detalhe(request: Request, retorno_id: str, cred: Credenciais = Depends(usuario_atual)):
    retorno = _repo(request).obter(retorno_id)
    if not retorno:
        return RedirectResponse("/retornos", status_code=303)
    return _detalhe(request, cred, retorno)


@router.post("/{retorno_id}/executar")
def executar(request: Request, retorno_id: str, modo: str = Form(...), confirmo: str = Form(""),
             cred: Credenciais = Depends(usuario_atual)):
    st, repo = request.app.state, _repo(request)
    retorno = repo.obter(retorno_id)
    if not retorno:
        return RedirectResponse("/retornos", status_code=303)
    if modo not in MODOS:
        return _detalhe(request, cred, retorno, "Opção inválida.", 400)
    s = situacao(request, retorno)
    if modo in MODOS_REAIS and confirmo != "sim":
        return _detalhe(request, cred, retorno, "Marque a confirmação para dar baixa no ACADE.", 400)
    if modo in ("simulacao", "baixa") and s["teve_baixa"]:
        return _detalhe(request, cred, retorno, "Este retorno já teve baixa; só é possível reprocessar as pendências.", 409)
    if modo == "reprocessar" and (not s["teve_baixa"] or not s["pendentes"]):
        return _detalhe(request, cred, retorno, "Não há pendências para reprocessar.", 409)
    if not s["previa"]["confirmados"]:
        return _detalhe(request, cred, retorno, "O arquivo não tem pagamentos confirmados para baixar.", 409)

    tarefa, workers = st.tarefa_baixar_retorno, st.settings.baixa_workers
    try:
        job_id = st.jobs.iniciar(TIPO_JOB, cred.usuario,
                                 lambda ctx: tarefa(ctx, cred, repo, retorno_id, modo, workers=workers),
                                 ao_criar=lambda j: repo.iniciar_execucao(j, retorno_id, modo))
    except JobEmAndamento as exc:
        return _detalhe(request, cred, retorno, f"Já há uma baixa ou simulação em andamento (tarefa {exc.job_id}). "
                                                "Aguarde terminar.", 409)
    except ErroRetorno as exc:
        return _detalhe(request, cred, retorno, str(exc), 409)
    return RedirectResponse(f"/tarefas/{job_id}", status_code=303)


@router.get("/{retorno_id}/entradas/{nome}")
def baixar_entrada(request: Request, retorno_id: str, nome: str, cred: Credenciais = Depends(usuario_atual)):
    caminho = _repo(request).arquivo_entrada(retorno_id, nome)
    if not caminho:
        return RedirectResponse("/retornos", status_code=303)
    return FileResponse(caminho, media_type="application/octet-stream", filename=caminho.name)


@router.get("/{retorno_id}/execucoes/{job_id}/{nome}")
def baixar_relatorio(request: Request, retorno_id: str, job_id: str, nome: str,
                     cred: Credenciais = Depends(usuario_atual)):
    caminho = _repo(request).arquivo_relatorio(retorno_id, job_id, nome)
    if not caminho:
        return RedirectResponse("/retornos", status_code=303)
    return FileResponse(caminho, media_type="text/plain; charset=utf-8", filename=caminho.name)
