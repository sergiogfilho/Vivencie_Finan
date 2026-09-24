"""
Worker Thread para processamento paralelo de baixas no ACADE
"""

import logging
import time
import threading
from queue import Queue, Empty
from typing import Dict, List
from datetime import datetime

from automatizador_final import AutomatizadorAcadeOneFINAL
from exceptions import (
    AcadeLoginException,
    AcadeSessionExpiredException,
    AcadeBrowserCrashException,
    AcadeTituloNotFoundException,
    AcadePaymentException
)


class WorkerThread:
    """
    Worker thread que processa pagamentos da fila.
    Cada worker tem seu próprio browser e sessão.
    """
    
    def __init__(self, worker_id: int, pagamentos_queue: Queue, resultados: Dict,
                 usuario: str, senha: str, headless: bool = True, max_retries: int = 3):
        """
        Inicializa o worker
        
        Args:
            worker_id: ID único do worker (1, 2, 3...)
            pagamentos_queue: Fila compartilhada com pagamentos
            resultados: Dict thread-safe para agregar resultados
            usuario: Usuário para login no ACADE
            senha: Senha para login no ACADE
            headless: Se deve rodar browser invisível
            max_retries: Tentativas máximas por pagamento
        """
        self.worker_id = worker_id
        self.name = f"Worker-{worker_id}"
        self.queue = pagamentos_queue
        self.resultados = resultados
        self.usuario = usuario
        self.senha = senha
        self.headless = headless
        self.max_retries = max_retries
        
        # Lock para escrever nos resultados
        self.results_lock = threading.Lock()
        
        # Logger específico do worker
        self.logger = logging.getLogger(f"worker_{worker_id}")
        
        # Instância própria do automatizador
        self.automatizador = None
        
        # Estatísticas
        self.processados = 0
        self.sucessos = 0
        self.erros = 0
        
    def inicializar_browser(self) -> bool:
        """
        Inicializa o browser e faz login
        
        Returns:
            True se sucesso, False se falhou
        """
        try:
            self.logger.info(f"🚀 [{self.name}] Inicializando browser...")
            
            # Criar instância própria do automatizador
            self.automatizador = AutomatizadorAcadeOneFINAL(headless=self.headless)
            
            # Fazer login
            if not self.automatizador.fazer_login(self.usuario, self.senha):
                raise AcadeLoginException("Falha ao fazer login")
            
            # Navegar para Contas a Pagar
            if not self.automatizador.navegar_para_contas_pagar():
                raise Exception("Falha ao navegar para Contas a Pagar")
            
            self.logger.info(f"✅ [{self.name}] Browser iniciado e login realizado")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ [{self.name}] Erro ao inicializar: {str(e)}")
            if self.automatizador:
                try:
                    self.automatizador.fechar()
                except:
                    pass
            return False
    
    def finalizar_browser(self):
        """Fecha o browser e limpa recursos"""
        if self.automatizador:
            try:
                self.logger.info(f"🔒 [{self.name}] Fechando browser...")
                self.automatizador.fechar()
            except:
                pass
            finally:
                self.automatizador = None
    
    def processar_pagamento(self, pagamento: Dict) -> Dict:
        """
        Processa um único pagamento
        
        Args:
            pagamento: Dict com dados do pagamento
            
        Returns:
            Dict com resultado do processamento
        """
        seu_numero = pagamento.get('seu_numero', '').strip()
        nome = pagamento.get('nome_favorecido', '')
        
        self.logger.info(f"🔄 [{self.name}] Processando: {seu_numero} - {nome}")
        
        tentativas = 0
        ultimo_erro = None
        
        while tentativas < self.max_retries:
            tentativas += 1
            
            try:
                # Tentar baixar o título
                resultado = self.automatizador.baixar_titulo(pagamento)
                
                if resultado.get('sucesso'):
                    self.logger.info(f"✅ [{self.name}] Sucesso: {seu_numero}")
                    return {
                        'status': 'sucesso',
                        'documento': seu_numero,
                        'nome': nome,
                        'tentativas': tentativas,
                        'worker': self.name,
                        'agencia': resultado.get('agencia', ''),
                        'conta': resultado.get('conta', ''),
                        'data_pagamento': resultado.get('data_pagamento', ''),
                        'motivo': resultado.get('motivo', '')
                    }
                else:
                    ultimo_erro = resultado.get('motivo', 'Erro desconhecido')
                    self.logger.warning(f"⚠️ [{self.name}] Tentativa {tentativas}/{self.max_retries} falhou: {ultimo_erro}")
                    
                    # Se for erro de título não encontrado, não vale a pena tentar de novo
                    if 'não encontrado' in ultimo_erro.lower():
                        break
                    
                    # Aguardar antes de tentar novamente
                    if tentativas < self.max_retries:
                        time.sleep(2)
                        
            except AcadeSessionExpiredException as e:
                self.logger.warning(f"🔄 [{self.name}] Sessão expirada, reiniciando browser...")
                self.finalizar_browser()
                if not self.inicializar_browser():
                    raise AcadeBrowserCrashException("Falha ao reiniciar browser")
                    
            except AcadeBrowserCrashException as e:
                self.logger.error(f"💥 [{self.name}] Browser crashou, reiniciando...")
                self.finalizar_browser()
                if not self.inicializar_browser():
                    ultimo_erro = str(e)
                    break
                    
            except Exception as e:
                ultimo_erro = str(e)
                self.logger.error(f"❌ [{self.name}] Erro inesperado: {ultimo_erro}")
                break
        
        # Se chegou aqui, falhou
        self.logger.error(f"❌ [{self.name}] Erro: {seu_numero} - {ultimo_erro}")
        return {
            'status': 'erro',
            'documento': seu_numero,
            'nome': nome,
            'erro': ultimo_erro,
            'tentativas': tentativas,
            'worker': self.name
        }
    
    def registrar_resultado(self, resultado: Dict):
        """Registra resultado de forma thread-safe"""
        with self.results_lock:
            if resultado['status'] == 'sucesso':
                self.resultados['sucessos'].append(resultado)
                self.sucessos += 1
            else:
                self.resultados['erros'].append(resultado)
                self.erros += 1
            
            self.processados += 1
    
    def run(self):
        """
        Loop principal do worker.
        Processa pagamentos da fila até ela esvaziar.
        """
        self.logger.info(f"🏁 [{self.name}] Iniciando worker...")
        
        # Inicializar browser
        if not self.inicializar_browser():
            self.logger.error(f"❌ [{self.name}] Falha crítica ao inicializar, worker encerrando")
            return
        
        try:
            while True:
                try:
                    # Tentar pegar um pagamento da fila (timeout de 1 segundo)
                    pagamento = self.queue.get(timeout=1)
                    
                    # Processar o pagamento
                    resultado = self.processar_pagamento(pagamento)
                    
                    # Registrar resultado
                    self.registrar_resultado(resultado)
                    
                    # Marcar como concluído na fila
                    self.queue.task_done()
                    
                except Empty:
                    # Fila vazia, trabalho concluído
                    self.logger.info(f"🏁 [{self.name}] Fila vazia, finalizando...")
                    break
                    
        except Exception as e:
            self.logger.error(f"❌ [{self.name}] Erro crítico no worker: {str(e)}")
            
        finally:
            # Sempre fechar o browser ao sair
            self.finalizar_browser()
            
            self.logger.info(
                f"📊 [{self.name}] Estatísticas finais: "
                f"{self.processados} processados, "
                f"{self.sucessos} sucessos, "
                f"{self.erros} erros"
            )
