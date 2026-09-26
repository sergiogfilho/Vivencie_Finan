"""Tarefas que acessam o ACADE (sempre em modo oculto) com as credenciais de quem as iniciou."""
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from app.bancos import RepositorioBancos
from app.jobs import ContextoJob
from app.pessoas import TIPOS, ArquivoPessoas, comparar, consolidar, rotulo_tipo
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
