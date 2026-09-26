"""Tarefas que acessam o ACADE (sempre em modo oculto) com as credenciais de quem as iniciou."""
import re

from app.bancos import RepositorioBancos
from app.jobs import ContextoJob
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
