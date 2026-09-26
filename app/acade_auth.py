"""
Validação de acesso: o usuário pode usar o app se conseguir logar no ACADE.

Reusa AutomatizadorAcadeOneFINAL.fazer_login (src/automatizador_final.py), o
mesmo login usado por contas a pagar, pessoas e baixa, sempre em modo oculto.
"""
import logging
import threading

logger = logging.getLogger(__name__)

# Cada validação abre um Chromium; limita o consumo de memória no contêiner.
_SLOTS = threading.BoundedSemaphore(2)


def validar_no_acade(usuario: str, senha: str) -> bool:
    from automatizador_final import AutomatizadorAcadeOneFINAL

    with _SLOTS:
        automatizador = None
        try:
            automatizador = AutomatizadorAcadeOneFINAL(headless=True)
            return bool(automatizador.fazer_login(usuario, senha))
        except Exception:
            logger.exception("Falha ao validar credenciais no ACADE")
            return False
        finally:
            if automatizador is not None:
                automatizador.fechar()
