"""
Exceções customizadas para o sistema de automação ACADE
"""

class AcadeAutomationException(Exception):
    """Exceção base para erros de automação"""
    pass

class AcadeLoginException(AcadeAutomationException):
    """Falha ao fazer login no sistema ACADE"""
    pass

class AcadeSessionExpiredException(AcadeAutomationException):
    """Sessão expirada, necessário novo login"""
    pass

class AcadeNavigationException(AcadeAutomationException):
    """Erro ao navegar entre páginas do sistema"""
    pass

class AcadeTituloNotFoundException(AcadeAutomationException):
    """Título não encontrado no sistema"""
    pass

class AcadePaymentException(AcadeAutomationException):
    """Erro ao processar pagamento"""
    pass

class AcadeBrowserCrashException(AcadeAutomationException):
    """Browser travou ou crashou, necessário reiniciar"""
    pass
