"""Tarefas que acessam o ACADE (sempre em modo oculto) com as credenciais de quem as iniciou."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

import pandas as pd

from app.bancos import RepositorioBancos
from app.jobs import ContextoJob
from app.pessoas import TIPOS, ArquivoPessoas, comparar, consolidar, rotulo_tipo
from app.remessas import (NOME_RELATORIO, ArquivoContasPagar, Execucoes, analisar_remessa, bancos_para_gerador,
                          diagnosticar, ler_bancos_csv)
from app.retornos import RepositorioRetornos, item_exibicao
from app.security import Credenciais

_TOTAL_BANCOS = re.compile(r"Total de bancos no sistema:\s*(\d+)")


def atualizar_bancos(ctx: ContextoJob, cred: Credenciais, repo: RepositorioBancos) -> dict:
    """
    Executa as mesmas etapas de CapturadorBancos.executar() (src/capturador_bancos.py),
    exceto salvar_csv: os dados capturados vão para a sincronização, que preserva
    convênios e vínculos locais.
    """
    from capturador_bancos import CapturadorBancos

    cap = CapturadorBancos(headless=True, usuario=cred.usuario, senha=cred.senha)
    estado = {"total": None}

    def ao_registrar(record):
        m = _TOTAL_BANCOS.search(record.getMessage())
        if m:
            estado["total"] = int(m.group(1))
        if estado["total"]:
            ctx.progresso(len(cap.bancos), estado["total"])

    with ctx.capturar_logs(["capturador_bancos"], ao_registrar):
        try:
            ctx.progresso(0, etapa="Abrindo navegador")
            cap.iniciar_driver()
            ctx.progresso(etapa="Entrando no ACADE")
            cap.fazer_login()
            ctx.progresso(etapa="Abrindo cadastro de bancos")
            cap.navegar_para_bancos()
            ctx.progresso(etapa="Capturando contas bancárias")
            cap.capturar_todos_bancos()
        finally:
            if cap.driver:
                cap.driver.quit()

    ctx.progresso(len(cap.bancos), len(cap.bancos), etapa="Aplicando alterações")
    resumo = repo.sincronizar_acade(cap.bancos, cred.usuario)
    ctx.log(f"{resumo.lidas} contas lidas do ACADE: {resumo.existentes} já cadastradas, "
            f"{len(resumo.novas)} novas, {len(resumo.ausentes)} ausentes no ACADE, "
            f"{len(resumo.pendentes_convenio)} com convênio pendente.")
    ctx.progresso(etapa="Concluído")
    return resumo.como_dict()


# Mensagem de capturador_pessoas.py:1071, emitida a cada página aceita.
_PAGINA_PESSOAS = re.compile(r"Página (\d+): \d+ registros capturados \(Total: (\d+)\)")


class ErroCapturaPessoas(RuntimeError):
    pass


def atualizar_pessoas(ctx: ContextoJob, cred: Credenciais, arquivo: ArquivoPessoas,
                      capturador_cls=None, paralelo: bool = True) -> dict:
    """
    Executa, para Física e Jurídica, as mesmas etapas de
    CapturadorPessoasAcadeOne.executar_captura_completa() (src/capturador_pessoas.py:1103).
    Por padrão os dois tipos rodam ao mesmo tempo, um navegador cada, como no modo
    paralelo do CLI (src/capturador_pessoas.py:1255). Só grava se os dois trouxerem
    dados; se um falhar, o navegador do outro é fechado para encerrar logo.
    """
    if capturador_cls is None:
        from capturador_pessoas import CapturadorPessoasAcadeOne as capturador_cls

    antes = arquivo.ler()
    anterior = {t: int((antes["Tipo"] == rotulo_tipo(t)).sum()) if antes is not None else None for t in TIPOS}
    alvo = dict(anterior)          # estimativa até o ACADE informar o total real
    no_acade = {t: None for t in TIPOS}
    capturados = {t: 0 for t in TIPOS}
    paginas = {t: 0 for t in TIPOS}
    etapas = {t: "aguardando" for t in TIPOS}
    capturadores = {}
    trava = threading.Lock()
    abortar = threading.Event()

    def publicar_progresso():
        with trava:
            if all(alvo[t] for t in TIPOS):
                ctx.progresso(sum(capturados.values()), sum(max(alvo[t], capturados[t]) for t in TIPOS))
            else:
                ctx.progresso(sum(capturados.values()))

    def etapa(tipo, texto):
        with trava:
            etapas[tipo] = texto
            ctx.progresso(etapa=" · ".join(f"Pessoa {t}: {etapas[t]}" for t in TIPOS))

    def capturar(tipo) -> pd.DataFrame:
        def ao_registrar(record):
            m = _PAGINA_PESSOAS.search(record.getMessage())
            if m:
                paginas[tipo], capturados[tipo] = int(m.group(1)), int(m.group(2))
                ctx.ponto(tipo, capturados[tipo], alvo[tipo])
                publicar_progresso()

        with ctx.capturar_logs(["capturador_pessoas"], ao_registrar, prefixo=f"[{tipo}] "):
            etapa(tipo, "abrindo navegador")
            cap = capturador_cls(headless=True, tipo_pessoa=tipo, output_dir=str(arquivo.pasta))
            capturadores[tipo] = cap
            try:
                if abortar.is_set():
                    raise ErroCapturaPessoas(f"Pessoa {tipo}: cancelada")
                etapa(tipo, "entrando no ACADE")
                if not cap.fazer_login(cred.usuario, cred.senha):
                    raise ErroCapturaPessoas(f"Pessoa {tipo}: falha no login do ACADE")
                etapa(tipo, "abrindo o cadastro")
                if not cap.navegar_menu_cadastro():
                    raise ErroCapturaPessoas(f"Pessoa {tipo}: falha ao abrir o menu Cadastro")
                if not cap.clicar_tipo_pessoa():
                    raise ErroCapturaPessoas(f"Falha ao abrir Pessoa {tipo}")
                if not cap.configurar_50_registros():
                    raise ErroCapturaPessoas(f"Pessoa {tipo}: falha ao configurar 50 registros por página")
                info = cap.obter_info_datatable() or {}
                if info.get("status") == "ok" and info.get("recordsTotal") is not None:
                    no_acade[tipo] = alvo[tipo] = int(info["recordsTotal"])
                    ctx.log(f"Pessoa {tipo}: o ACADE informa {no_acade[tipo]} registros")
                ctx.ponto(tipo, 0, alvo[tipo])
                publicar_progresso()
                etapa(tipo, "capturando páginas")
                df = cap.capturar_todas_paginas()
            finally:
                cap.fechar()
        if abortar.is_set():
            raise ErroCapturaPessoas(f"Pessoa {tipo}: cancelada")
        if df.empty:
            raise ErroCapturaPessoas(f"Pessoa {tipo}: nenhum registro capturado")
        capturados[tipo] = len(df)
        ctx.ponto(tipo, capturados[tipo], alvo[tipo], fim=True)
        etapa(tipo, "concluída")
        return df

    erros = []

    def capturar_ou_abortar(tipo):
        try:
            return capturar(tipo)
        except Exception as exc:
            with trava:
                primeiro = not abortar.is_set()
                abortar.set()
                erros.append(exc)
            etapa(tipo, "falhou")
            if primeiro:
                for outro, cap in list(capturadores.items()):
                    if outro != tipo:
                        cap.fechar()
            raise

    frames = {}
    if paralelo:
        with ThreadPoolExecutor(max_workers=len(TIPOS), thread_name_prefix="pessoas") as pool:
            futuros = {t: pool.submit(capturar_ou_abortar, t) for t in TIPOS}
            for t, f in futuros.items():
                try:
                    frames[t] = f.result()
                except Exception:
                    pass
    else:
        for t in TIPOS:
            try:
                frames[t] = capturar_ou_abortar(t)
            except Exception:
                break
    if erros:
        raise ErroCapturaPessoas(f"{erros[0]}. O arquivo anterior foi mantido.") from erros[0]

    ctx.progresso(etapa="Gravando pessoas_cadastradas.csv")
    novo = consolidar([frames[t] for t in TIPOS])
    diferencas = comparar(antes, novo)
    arquivo.gravar(novo)

    incompletos = [t for t in TIPOS if no_acade[t] is not None and capturados[t] < no_acade[t]]
    for t in incompletos:
        ctx.log(f"Pessoa {t}: capturados {capturados[t]} de {no_acade[t]} informados pelo ACADE", "WARNING")
    ctx.log(f"Gravados {len(novo)} registros ({', '.join(f'{t}: {capturados[t]}' for t in TIPOS)}).")
    ctx.progresso(sum(capturados.values()), sum(capturados.values()), etapa="Concluído")
    return {
        "paralelo": paralelo,
        "tipos": {t: {"capturados": capturados[t], "acade": no_acade[t], "anterior": anterior[t],
                      "paginas": paginas[t]} for t in TIPOS},
        "total": len(novo),
        "total_anterior": len(antes) if antes is not None else None,
        "incompletos": incompletos,
        "diferencas": diferencas,
    }


# ------------------------------------------------------------------ remessas
class ErroRemessas(RuntimeError):
    pass


RAIZ = Path(__file__).resolve().parent.parent
# Formato do logger do gerador (gerador_cnab240.py:15-22), que escreve em stderr.
_LOG_GERADOR = re.compile(r"^\d{4}-\d{2}-\d{2} [\d:,]+ - (\w+) - (.*)$")
_PROCESSANDO_CONTA = re.compile(r"Processando Conta: (.+?) \| Agência: (.+?) \(")


def _float(v) -> float | None:
    """Valor já convertido pelo gerador (float gravado como texto em pagamentos_nao_incluidos)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def capturar_contas_pagar(ctx: ContextoJob, cred: Credenciais, arquivo: ArquivoContasPagar,
                          data_inicial: str, data_final: str, automatizador_cls=None) -> int:
    """
    Mesmas etapas de AutomatizadorAcadeOneFINAL.executar_automacao_completa
    (automatizador_final.py:1270) para contas em aberto ('A'), exceto
    salvar_relatorio, que grava relativo ao diretório corrente: aqui a gravação
    é feita por ArquivoContasPagar com os mesmos parâmetros. Datas em DD/MM/AAAA.
    """
    if automatizador_cls is None:
        from automatizador_final import AutomatizadorAcadeOneFINAL as automatizador_cls

    etapas = [
        ("Entrando no ACADE", lambda a: a.fazer_login(cred.usuario, cred.senha), "Falha no login do ACADE"),
        ("Abrindo o menu Relatório", lambda a: a.navegar_para_menu_relatorio(), "Falha ao abrir o menu Relatório"),
        ("Abrindo Contas à Pagar", lambda a: a.clicar_contas_a_pagar(), "Falha ao abrir Contas à Pagar"),
        ("Preenchendo o período", lambda a: a.configurar_formulario(data_inicial, data_final, "A"),
         "Falha ao configurar o formulário do relatório"),
        ("Gerando o relatório", lambda a: a.gerar_relatorio(), "Falha ao solicitar o relatório"),
    ]
    with ctx.capturar_logs(["automatizador_final"]):
        ctx.progresso(etapa="Abrindo navegador")
        aut = automatizador_cls(headless=True)
        try:
            for texto, passo, erro in etapas:
                ctx.progresso(etapa=texto)
                if not passo(aut):
                    raise ErroRemessas(erro)
            ctx.progresso(etapa="Lendo a tabela do relatório")
            df = aut.extrair_dados_tabela()
        finally:
            aut.fechar()
    if df.empty:
        raise ErroRemessas(f"O ACADE não retornou contas a pagar em aberto de {data_inicial} a {data_final}. "
                           "O relatório anterior foi mantido e nenhuma remessa foi gerada.")
    arquivo.gravar(df, {"data_inicial": data_inicial, "data_final": data_final, "usuario": cred.usuario,
                        "capturado_em": datetime.now().isoformat(timespec="seconds")})
    ctx.log(f"Relatório de contas a pagar gravado: {len(df)} títulos ({data_inicial} a {data_final}).")
    return len(df)


def executar_gerador(pasta: Path, contas: Path, bancos: Path, pessoas: Path, ao_linha) -> int:
    """Roda o CLI em processo próprio (diretório corrente = pasta da execução); stdout vai para resultado.txt."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(RAIZ / "src"), str(RAIZ), env.get("PYTHONPATH")) if p)
    env["PYTHONUTF8"] = "1"           # como run_processar_cnab.ps1
    with open(pasta / "resultado.txt", "w", encoding="utf-8") as saida:
        with subprocess.Popen(
                [sys.executable, "-m", "app.cnab_execucao", str(pasta / "nao_incluidos.json"),
                 str(contas), str(bancos), str(pessoas)],
                cwd=pasta, env=env, stdout=saida, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace") as proc:
            for linha in proc.stderr:
                ao_linha(linha.rstrip("\n"))
        return proc.returncode


def gerar_remessas(ctx: ContextoJob, cred: Credenciais, relatorio: ArquivoContasPagar, repo: RepositorioBancos,
                   pessoas: ArquivoPessoas, execucoes: Execucoes, periodo: tuple[str, str] | None = None,
                   automatizador_cls=None, gerador=executar_gerador) -> dict:
    if not pessoas.existe():
        raise ErroRemessas("Cadastro de pessoas ainda não capturado. Atualize as pessoas antes de gerar remessas.")
    if repo.vazio():
        raise ErroRemessas("Cadastro de bancos vazio. Faça a carga inicial em Bancos e convênios.")

    if periodo:
        capturar_contas_pagar(ctx, cred, relatorio, periodo[0], periodo[1], automatizador_cls)
    elif not relatorio.existe():
        raise ErroRemessas("Nenhum relatório de contas a pagar capturado. Informe o período e capture do ACADE.")

    ctx.progresso(etapa="Validando títulos contra o cadastro de bancos")
    pasta = execucoes.nova(ctx.job_id)
    entradas = pasta / "entradas"
    contas_csv, pessoas_csv = entradas / NOME_RELATORIO, entradas / "pessoas_cadastradas.csv"
    cadastro_csv, bancos_csv = entradas / "bancos_cadastro.csv", entradas / "bancos.csv"
    shutil.copy2(relatorio.caminho, contas_csv)
    shutil.copy2(pessoas.caminho, pessoas_csv)
    repo.exportar_df().to_csv(cadastro_csv, index=False, encoding="utf-8")
    df_bancos = ler_bancos_csv(cadastro_csv)
    bancos_para_gerador(df_bancos).to_csv(bancos_csv, index=False, encoding="utf-8")

    df_contas = pd.read_csv(contas_csv, encoding="utf-8", dtype=str)
    diag = diagnosticar(df_contas, df_bancos)
    for motivo, p in diag["por_motivo"].items():
        ctx.log(f"{p['titulos']} título(s) fora da remessa — {motivo}: {', '.join(p['centros'])}", "WARNING")
    ctx.log(f"{diag['titulos']} títulos no relatório; {diag['aptos']} aptos em {len(diag['contas'])} conta(s).")

    codigo, nao_incluidos, resultados = None, [], {}
    if diag["aptos"]:
        total_contas, feitas, nivel = len(diag["contas"]), [0], ["INFO"]
        ctx.progresso(0, total_contas, etapa="Gerando remessas")

        def ao_linha(linha):
            m = _LOG_GERADOR.match(linha)
            if m:
                nivel[0], msg = m.group(1), m.group(2)
            else:
                msg = linha           # continuação de mensagem ou traceback
            if not msg.strip() or set(msg.strip()) == {"="}:
                return
            ctx.log(msg, nivel[0] if nivel[0] in ("WARNING", "ERROR", "CRITICAL") else "INFO")
            c = _PROCESSANDO_CONTA.search(msg)
            if c:
                feitas[0] += 1
                ctx.progresso(feitas[0], total_contas, etapa=f"Gerando remessa da conta {c.group(1)} · agência {c.group(2)}")

        codigo = gerador(pasta, contas_csv, bancos_csv, pessoas_csv, ao_linha)
        if codigo != 0:
            raise ErroRemessas(f"O gerador CNAB terminou com erro (código {codigo}). Veja o registro da execução.")
        saida = json.loads((pasta / "nao_incluidos.json").read_text(encoding="utf-8"))
        nao_incluidos, resultados = saida["nao_incluidos"], saida["resultados"]

    ctx.progresso(etapa="Conferindo arquivos gerados")
    centro_para_conta = {cc: f"{c['conta']}_{c['agencia']}" for c in diag["contas"] for cc in c["centros"]}
    nao_por_conta = {}
    for n in nao_incluidos:
        chave = centro_para_conta.get(n.get("Centro Custo"))
        nao_por_conta[chave] = nao_por_conta.get(chave, 0) + 1

    arquivos, sem_remessa, divergencias = [], [], []
    for c in diag["contas"]:
        chave = f"{c['conta']}_{c['agencia']}"
        esperados = c["titulos"] - nao_por_conta.get(chave, 0)
        gerados = [Path(a).name for a in resultados.get(chave, [])]
        if not gerados:
            if esperados:
                sem_remessa.append({**c, "esperados": esperados})
            continue
        for nome in gerados:
            info = analisar_remessa(pasta / "remessas" / nome)
            arquivos.append({"arquivo": nome, "conta": c["conta"], "agencia": c["agencia"], "centros": c["centros"],
                             "tamanho": (pasta / "remessas" / nome).stat().st_size, **info})
            if info["pagamentos"] != esperados:
                divergencias.append({"arquivo": nome, "esperados": esperados, "no_arquivo": info["pagamentos"]})
    for s in sem_remessa:
        ctx.log(f"Conta {s['conta']} · agência {s['agencia']}: {s['esperados']} título(s) aptos e nenhuma remessa "
                "gerada (veja os erros acima).", "ERROR")
    for d in divergencias:
        ctx.log(f"{d['arquivo']}: {d['no_arquivo']} pagamento(s) no arquivo, {d['esperados']} esperado(s).", "WARNING")

    incluidos = sum(a["pagamentos"] for a in arquivos)
    ctx.log(f"{len(arquivos)} remessa(s) gerada(s) com {incluidos} pagamento(s).")
    ctx.progresso(len(diag["contas"]), len(diag["contas"]) or None, etapa="Concluído")
    meta = relatorio.meta()
    return {
        "execucao": pasta.name,
        "relatorio": {"titulos": diag["titulos"], "valor": diag["valor"], "data_inicial": meta.get("data_inicial"),
                      "data_final": meta.get("data_final"), "capturado_em": meta.get("capturado_em"),
                      "capturado_agora": bool(periodo)},
        "aptos": diag["aptos"],
        "incluidos": incluidos,
        "valor_incluido": sum(a["valor"] for a in arquivos),
        "arquivos": arquivos,
        "excluidos": diag["excluidos"],
        "por_motivo": diag["por_motivo"],
        "nao_incluidos": [{"centro_custo": n.get("Centro Custo", ""), "vencto": n.get("Vencto", ""),
                           "lancto": n.get("Lancto", ""), "doc": n.get("Doc", ""), "beneficiado": n.get("Beneficiado", ""),
                           "parc": n.get("Parc", ""), "valor": _float(n.get("Valor")), "motivo": n.get("Motivo Exclusao", "")}
                          for n in nao_incluidos],
        "sem_remessa": sem_remessa,
        "divergencias": divergencias,
    }


# ------------------------------------------------------------ retornos / baixas
DETALHE_SIMULACAO = "Simulação: título e parcela localizados, modal de pagamento aberto; nada foi salvo"
DETALHE_SIMULACAO_SEM_MODAL = "Simulação: o modal de pagamento não abriu"
DETALHE_NAO_INICIADO = "Não processado: nenhum navegador conseguiu entrar no ACADE"
DETALHE_DESCONHECIDO = ("Resultado desconhecido: o navegador parou durante este pagamento. "
                        "Confira no ACADE se a baixa foi salva antes de tentar de novo")


def _modal_de_pagamento_visivel(driver, espera: int = 5) -> bool:
    """Mesmos seletores de preencher_modal_pagamento (automatizador_final.py:1960-1965), exigindo visibilidade."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    for seletor in ((By.ID, "mPagar"), (By.CSS_SELECTOR, ".modal.show"), (By.CSS_SELECTOR, "div[role='dialog']")):
        try:
            WebDriverWait(driver, espera).until(EC.visibility_of_element_located(seletor))
            return True
        except Exception:
            continue
    return False


def classe_simulacao(base):
    """
    Automatizador cuja baixa_titulo faz busca, PAGAR e seleção da parcela reais, mas
    no lugar de preencher_modal_pagamento (que escolhe conta, datas e clica em Salvar,
    automatizador_final.py:1996-2104) só confere que o modal abriu.
    """
    class AutomatizadorSimulacao(base):
        def preencher_modal_pagamento(self, agencia, conta, data_pagamento):
            if _modal_de_pagamento_visivel(self.driver):
                self.logger.info("Simulação: modal de pagamento aberto; nada foi preenchido nem salvo")
                return True
            self.logger.error("Simulação: o modal de pagamento não abriu")
            return False

    return AutomatizadorSimulacao


def _classe_worker(automatizador_cls, ao_resultado):
    from exceptions import AcadeLoginException
    from worker_thread import WorkerThread

    class WorkerWeb(WorkerThread):
        def inicializar_browser(self) -> bool:
            # Mesmo corpo de WorkerThread.inicializar_browser (worker_thread.py:65-96), com a classe
            # do automatizador recebida por parâmetro; a sequência de chamadas é comparada em teste.
            try:
                self.logger.info(f"🚀 [{self.name}] Inicializando browser...")
                self.automatizador = automatizador_cls(headless=self.headless)
                if not self.automatizador.fazer_login(self.usuario, self.senha):
                    raise AcadeLoginException("Falha ao fazer login")
                if not self.automatizador.navegar_para_contas_pagar():
                    raise Exception("Falha ao navegar para Contas a Pagar")
                self.logger.info(f"✅ [{self.name}] Browser iniciado e login realizado")
                return True
            except Exception as e:
                self.logger.error(f"❌ [{self.name}] Erro ao inicializar: {str(e)}")
                if self.automatizador:
                    try:
                        self.automatizador.fechar()
                    except Exception:
                        pass
                return False

        def processar_pagamento(self, pagamento):
            resultado = super().processar_pagamento(pagamento)
            resultado["chave"] = pagamento["chave"]
            return resultado

        def registrar_resultado(self, resultado):
            super().registrar_resultado(resultado)
            ao_resultado(resultado)

    return WorkerWeb


def baixar_retorno(ctx: ContextoJob, cred: Credenciais, retornos: RepositorioRetornos, retorno_id: str, modo: str,
                   workers: int = 3, max_retries: int = 3, automatizador_cls=None) -> dict:
    """
    Mesmas fases de baixar_contas_pagas.main() (src/baixar_contas_pagas.py:242-376): leitura
    pelo parser do CLI, fila com WorkerThread em paralelo (um navegador cada) e relatórios
    TXT gravados pelas funções do CLI. Diferenças: credenciais de quem iniciou; o arquivo
    fica guardado em vez de ir para processados/bak; cada resultado é gravado na hora; e
    não se espera a fila esvaziar (queue.join() travaria se nenhum navegador entrasse no
    ACADE) — o que sobrar na fila é informado como não processado.
    """
    if automatizador_cls is None:
        from automatizador_final import AutomatizadorAcadeOneFINAL as automatizador_cls
    from baixar_contas_pagas import salvar_nao_processados, salvar_relatorio_consolidado

    retornos.iniciar_execucao(ctx.job_id, retorno_id, modo)
    simulacao = modo == "simulacao"
    ctx.progresso(etapa="Lendo o arquivo de retorno")
    previa = retornos.ler(retorno_id)
    confirmados = previa["confirmados"]
    if modo == "reprocessar":
        resolvidos = retornos.resolvidos(retorno_id)
        fila = [p for p in confirmados if p["chave"] not in resolvidos]
    else:
        fila = list(confirmados)
    pasta = retornos.pasta_execucao(retorno_id, ctx.job_id)
    ctx.log(f"Retorno com {len(confirmados)} pagamento(s) confirmado(s) (ocorrência 00) e "
            f"{len(previa['nao_confirmados'])} não confirmado(s).")
    if modo == "reprocessar":
        ctx.log(f"{len(fila)} pendente(s) para reprocessar; {len(confirmados) - len(fila)} já resolvido(s) antes.")
    if modo == "baixa" and previa["nao_confirmados"]:
        salvar_nao_processados(previa["nao_confirmados"], str(pasta))

    total = len(fila)
    contagem = {"sucesso": 0, "erro": 0}
    trava = threading.Lock()
    rotulo = "Simulando" if simulacao else "Baixando"

    def ao_resultado(r):
        status = "sucesso" if r.get("status") == "sucesso" else "erro"
        detalhe = r.get("motivo") if status == "sucesso" else r.get("erro")
        if simulacao:
            detalhe = DETALHE_SIMULACAO if status == "sucesso" else (
                DETALHE_SIMULACAO_SEM_MODAL if detalhe == "Erro ao preencher modal" else detalhe)
        retornos.registrar_resultado(ctx.job_id, r["chave"], status, detalhe or "")
        with trava:
            contagem[status] += 1
            feitos = contagem["sucesso"] + contagem["erro"]
            ctx.progresso(feitos, total, etapa=f"{rotulo}: {feitos} de {total} · {contagem['sucesso']} ok · "
                                               f"{contagem['erro']} com erro")

    resultados = {"sucessos": [], "erros": [], "inicio": datetime.now().isoformat(), "fim": None,
                  "total_processado": 0, "num_workers": 0}
    n = max(1, min(workers, total)) if total else 0
    if total:
        resultados["num_workers"] = n
        fila_q = Queue()
        for p in fila:
            fila_q.put(p)
        worker_cls = _classe_worker(classe_simulacao(automatizador_cls) if simulacao else automatizador_cls,
                                    ao_resultado)
        ws = [worker_cls(worker_id=i, pagamentos_queue=fila_q, resultados=resultados, usuario=cred.usuario,
                         senha=cred.senha, headless=True, max_retries=max_retries) for i in range(1, n + 1)]
        prefixo = f"baixa-{ctx.job_id}"
        ctx.progresso(0, total, etapa=f"Abrindo {n} navegador(es) no ACADE")
        loggers = ["automatizador_final"] + [f"worker_{i}" for i in range(1, n + 1)]
        with ctx.capturar_logs(loggers, prefixo_threads=prefixo):
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix=prefixo) as pool:
                for futuro in as_completed([pool.submit(w.run) for w in ws]):
                    try:
                        futuro.result()
                    except Exception as exc:
                        ctx.log(f"Erro em worker: {exc}", "ERROR")

        nunca_iniciados = set()
        while True:
            try:
                nunca_iniciados.add(fila_q.get_nowait()["chave"])
            except Empty:
                break
        # Conciliação pelo SQLite: o que o worker concluiu mas não chegou a gravar é gravado agora;
        # o que não tem resultado nem em memória fica como não iniciado ou desconhecido.
        gravados = retornos.resultados(ctx.job_id)
        em_memoria = {r.get("chave"): r for r in resultados["sucessos"] + resultados["erros"]}
        for p in fila:
            if p["chave"] in gravados:
                continue
            if p["chave"] in em_memoria:
                ao_resultado(em_memoria[p["chave"]])
                continue
            status, detalhe = (("nao_iniciado", DETALHE_NAO_INICIADO) if p["chave"] in nunca_iniciados
                               else ("desconhecido", DETALHE_DESCONHECIDO))
            retornos.registrar_resultado(ctx.job_id, p["chave"], status, detalhe)
            documento, nome = (p.get("seu_numero") or "").strip(), p.get("nome_favorecido", "")
            resultados["erros"].append({"status": "erro", "documento": documento, "nome": nome, "erro": detalhe,
                                        "tentativas": 0, "worker": "-", "chave": p["chave"]})
            ctx.log(f"{documento} - {nome}: {detalhe}", "ERROR")

        resultados["fim"] = datetime.now().isoformat()
        resultados["total_processado"] = len(resultados["sucessos"]) + len(resultados["erros"])
        if not simulacao:
            salvar_relatorio_consolidado(resultados, str(pasta))

    detalhes = retornos.resultados(ctx.job_id)
    itens = [{**item_exibicao(p), "status": detalhes.get(p["chave"], {}).get("status", "desconhecido"),
              "detalhe": detalhes.get(p["chave"], {}).get("detalhe", "")} for p in fila]
    por_status = {}
    for i in itens:
        por_status[i["status"]] = por_status.get(i["status"], 0) + 1
    ctx.log(f"{'Simulação' if simulacao else 'Baixa'} concluída: "
            + ", ".join(f"{v} {k}" for k, v in sorted(por_status.items())) if itens else "Nada a processar.")
    ctx.progresso(total, total or None, etapa="Concluído")
    return {
        "retorno": retorno_id,
        "modo": modo,
        "simulacao": simulacao,
        "workers": n,
        "total": total,
        "confirmados": len(confirmados),
        "nao_confirmados": len(previa["nao_confirmados"]),
        "ja_resolvidos": len(confirmados) - total,
        "por_status": por_status,
        "valor_sucesso": sum(i["valor"] for i in itens if i["status"] == "sucesso"),
        "itens": itens,
        "relatorios": [a.name for a in retornos.relatorios(retorno_id, ctx.job_id)],
    }
