#!/usr/bin/env python3
"""
CAPTURADOR DE BANCOS - Sistema ACADE One
Extrai dados completos dos bancos cadastrados no sistema.
"""

import os
import sys
import time
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, 
    StaleElementReferenceException, ElementClickInterceptedException,
    NoAlertPresentException
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains


# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('capturador_bancos.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class CapturadorBancos:
    """Captura dados de bancos do sistema ACADE One"""
    
    def __init__(self, headless: bool = True):
        """
        Inicializa o capturador
        
        Args:
            headless: Se True, executa Chrome em modo oculto
        """
        load_dotenv()
        
        self.base_url = os.getenv('ACADE_BASE_URL', 'https://martins.acadeone.com.br')
        self.usuario = os.getenv('ACADE_USUARIO')
        self.senha = os.getenv('ACADE_SENHA')
        self.timeout = int(os.getenv('TIMEOUT_DEFAULT', '30'))
        self.output_dir = os.getenv('OUTPUT_DIR', './arquivos_auxiliares')
        
        if not self.usuario or not self.senha:
            raise ValueError("Credenciais não encontradas no arquivo .env")
        
        self.headless = headless
        self.driver = None
        self.wait = None
        
        # Dados coletados
        self.bancos: List[Dict] = []
        
        logger.info(f"Capturador inicializado (modo: {'oculto' if headless else 'visível'})")
    
    def iniciar_driver(self):
        """Inicializa o Chrome WebDriver usando Selenium Manager integrado"""
        logger.info("Inicializando Chrome WebDriver...")
        
        try:
            chrome_options = Options()
            if self.headless:
                chrome_options.add_argument("--headless=new")  # Versão moderna do headless
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-save-password-bubble")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-features=PasswordLeakDetection,PasswordDomainLeakDetection,AutofillServerCommunication,PasswordCheck,PasswordManagerOnboarding,PasswordCard,PasswordChange")
            chrome_options.add_experimental_option("prefs", {
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "profile.password_manager_leak_detection": False,
                "profile.password_manager_enable_auto_signin": False,
                "profile.default_content_setting_values.notifications": 2
            })
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            
            # Usando Selenium Manager integrado (Selenium 4.6.0+)
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, self.timeout)
            
            logger.info("Chrome WebDriver inicializado com sucesso usando Selenium Manager")
            
        except Exception as e:
            logger.error(f"Erro ao configurar driver: {e}")
            raise
    
    def tratar_alerta_senha_comprometida(self):
        """Fecha alertas de senha do navegador"""
        fechado = False

        # 1. Verificar alerta JavaScript
        try:
            alerta = self.driver.switch_to.alert
            alerta.accept()
            fechado = True
        except NoAlertPresentException:
            pass
        except Exception:
            pass

        # 2. Enviar ESC
        if not fechado:
            try:
                ActionChains(self.driver).send_keys(Keys.ESCAPE).pause(0.2).send_keys(Keys.ESCAPE).perform()
                fechado = True
            except Exception:
                pass

        # 3. Clicar botões OK/Fechar
        if not fechado:
            try:
                fechado = self.driver.execute_script("""
                    let fechadoLocal = false;
                    const textosOk = ['ok', 'entendi', 'fechar'];
                    const botoes = Array.from(document.querySelectorAll('button'));
                    botoes.forEach(btn => {
                        const texto = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                        if (textosOk.includes(texto)) {
                            btn.click();
                            fechadoLocal = true;
                        }
                    });
                    return fechadoLocal;
                """) or False
            except Exception:
                pass

        if fechado:
            logger.debug("Alerta de senha fechado")

        return fechado

    def _pos_login_tratar_alertas(self):
        """Múltiplas tentativas para fechar alertas"""
        for _ in range(4):
            if self.tratar_alerta_senha_comprometida():
                return
            try:
                self.driver.execute_cdp_cmd("Browser.dismissBrowserPrompt", {"accept": True})
                return
            except Exception:
                pass
            time.sleep(0.25)
    
    def fazer_login(self):
        """Realiza login no sistema ACADE One"""
        logger.info("Acessando página de login...")
        
        login_url = f"{self.base_url}/acadelotear"
        self.driver.get(login_url)
        time.sleep(5)
        
        try:
            # Aguarda campo de usuário (ID correto: txt_login)
            logger.info("Aguardando campo de usuário...")
            campo_usuario = self.wait.until(
                EC.presence_of_element_located((By.ID, "txt_login"))
            )
            logger.info(f"Campo usuário encontrado. Preenchendo com: {self.usuario}")
            campo_usuario.clear()
            campo_usuario.send_keys(self.usuario)
            time.sleep(1)
            logger.info(f"✓ Usuário preenchido: {campo_usuario.get_attribute('value')}")
            
            # Campo de senha (ID correto: txt_senha)
            logger.info("Aguardando campo de senha...")
            campo_senha = self.driver.find_element(By.ID, "txt_senha")
            logger.info("Campo senha encontrado. Preenchendo...")
            campo_senha.clear()
            campo_senha.send_keys(self.senha)
            time.sleep(1)
            logger.info(f"✓ Senha preenchida (tamanho: {len(campo_senha.get_attribute('value'))} caracteres)")
            
            # Botão de login - tentar múltiplas estratégias
            logger.info("Procurando botão de login...")
            botao_login = None
            
            # Estratégia 1: Por tipo submit
            try:
                botao_login = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                logger.info("Botão encontrado por type=submit")
            except:
                pass
            
            # Estratégia 2: Por texto do botão
            if not botao_login:
                try:
                    botoes = self.driver.find_elements(By.TAG_NAME, "button")
                    for btn in botoes:
                        texto = btn.text.strip().lower()
                        if 'acessar' in texto or 'entrar' in texto or 'login' in texto:
                            botao_login = btn
                            logger.info(f"Botão encontrado por texto: '{btn.text}'")
                            break
                except:
                    pass
            
            # Estratégia 3: Qualquer botão visível
            if not botao_login:
                try:
                    botao_login = self.driver.find_element(By.TAG_NAME, "button")
                    logger.info("Usando primeiro botão encontrado")
                except:
                    pass
            
            if not botao_login:
                raise Exception("Nenhum botão de login encontrado")
            
            logger.info("Clicando no botão...")
            botao_login.click()
            logger.info("✓ Botão clicado")
            
            # Aguarda redirecionamento
            time.sleep(10)
            
            # Verifica se login foi bem-sucedido
            # Se redirecionar para admin/index ou sair da página de login, é sucesso
            url_atual = self.driver.current_url
            if "login" in url_atual.lower() and "admin" not in url_atual:
                raise Exception(f"Falha no login - ainda na página de login: {url_atual}")
            
            logger.info(f"Login realizado com sucesso! URL: {url_atual}")
            
            # Trata alertas pós-login
            self._pos_login_tratar_alertas()
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"Erro ao fazer login: {e}")
            raise
    
    def navegar_para_bancos(self):
        """Navega até a página de cadastro de bancos"""
        logger.info("Navegando para página de Bancos...")
        
        try:
            # Garante que alertas estejam fechados
            self._pos_login_tratar_alertas()
            time.sleep(2)
            
            # Clica no menu Cadastro - tentar múltiplas estratégias
            logger.info("Procurando menu Cadastro...")
            menu_cadastro = None
            
            # Estratégia 1: Por seletor CSS especificado
            try:
                menu_cadastro = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "#sidebar-menu > ul > li:nth-child(5) > a"))
                )
                logger.info("Menu Cadastro encontrado por CSS selector")
            except Exception as e:
                logger.warning(f"Estratégia 1 falhou: {e}")
            
            # Estratégia 2: Por texto "Cadastro"
            if not menu_cadastro:
                try:
                    menu_cadastro = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Cadastro')]/.."))
                    )
                    logger.info("Menu Cadastro encontrado por XPath (texto)")
                except Exception as e:
                    logger.warning(f"Estratégia 2 falhou: {e}")
            
            # Estratégia 3: Por classe ou ID do sidebar
            if not menu_cadastro:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, "#sidebar-menu a")
                    for link in links:
                        if "cadastro" in link.text.lower():
                            menu_cadastro = link
                            logger.info(f"Menu Cadastro encontrado por busca em links: '{link.text}'")
                            break
                except Exception as e:
                    logger.warning(f"Estratégia 3 falhou: {e}")
            
            if not menu_cadastro:
                raise Exception("Menu Cadastro não encontrado")
            
            logger.info("Clicando no menu Cadastro...")
            menu_cadastro.click()
            time.sleep(2)
            logger.info("✓ Menu Cadastro clicado")
            
            # Clica em Banco - tentar múltiplas estratégias
            logger.info("Procurando submenu Banco...")
            menu_banco = None
            
            # Estratégia 1: Por seletor CSS especificado
            try:
                menu_banco = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "#sidebar-menu > ul > li:nth-child(5) > ul > li.banco > a"))
                )
                logger.info("Menu Banco encontrado por CSS selector")
            except Exception as e:
                logger.warning(f"Estratégia 1 falhou: {e}")
            
            # Estratégia 2: Por texto "Banco"
            if not menu_banco:
                try:
                    menu_banco = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Banco') and not(contains(text(), 'Banco de Horas'))]"))
                    )
                    logger.info("Menu Banco encontrado por XPath (texto)")
                except Exception as e:
                    logger.warning(f"Estratégia 2 falhou: {e}")
            
            # Estratégia 3: Procurar em submenus visíveis
            if not menu_banco:
                try:
                    submenus = self.driver.find_elements(By.CSS_SELECTOR, "#sidebar-menu ul li ul li a")
                    for submenu in submenus:
                        texto = submenu.text.strip().lower()
                        if texto == "banco" or texto == "bancos":
                            menu_banco = submenu
                            logger.info(f"Menu Banco encontrado por busca: '{submenu.text}'")
                            break
                except Exception as e:
                    logger.warning(f"Estratégia 3 falhou: {e}")
            
            if not menu_banco:
                raise Exception("Menu Banco não encontrado")
            
            logger.info("Clicando no menu Banco...")
            menu_banco.click()
            time.sleep(3)
            logger.info("✓ Menu Banco clicado")
            
            logger.info("Página de Bancos carregada")
            
        except Exception as e:
            logger.error(f"Erro ao navegar para Bancos: {e}")
            raise
    
    def configurar_quantidade_registros(self, quantidade: int = 50):
        """
        Configura quantidade de registros por página
        
        Args:
            quantidade: Número de registros a exibir (10, 25, 50, 100)
        """
        logger.info(f"Configurando exibição para {quantidade} registros por página...")
        
        try:
            # Localiza o select de quantidade
            select_quantidade = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#TabelaListar_length > label > select"))
            )
            
            # Clica no select
            select_quantidade.click()
            time.sleep(0.5)
            
            # Seleciona a opção desejada
            opcao = self.driver.find_element(By.CSS_SELECTOR, f"#TabelaListar_length > label > select > option[value='{quantidade}']")
            opcao.click()
            
            # Aguarda tabela recarregar
            time.sleep(2)
            
            logger.info(f"Quantidade configurada para {quantidade} registros")
            
        except Exception as e:
            logger.error(f"Erro ao configurar quantidade de registros: {e}")
            raise
    
    def obter_info_paginacao(self) -> Dict:
        """
        Obtém informações de paginação da tabela
        
        Returns:
            Dict com informações: start, end, total, page, pages
        """
        try:
            # Executa JavaScript para obter info do DataTable
            info_script = """
            var tabela = $('#TabelaListar').DataTable();
            var info = tabela.page.info();
            return {
                page: info.page,
                pages: info.pages,
                start: info.start,
                end: info.end,
                recordsDisplay: info.recordsDisplay,
                recordsTotal: info.recordsTotal
            };
            """
            
            info = self.driver.execute_script(info_script)
            
            logger.debug(f"Info paginação: Página {info['page'] + 1}/{info['pages']}, "
                        f"Registros {info['start'] + 1}-{info['end']}/{info['recordsTotal']}")
            
            return info
            
        except Exception as e:
            logger.warning(f"Não foi possível obter info de paginação: {e}")
            return None
    
    def extrair_dados_linha_tabela(self, linha) -> Dict:
        """
        Extrai dados de uma linha da tabela de bancos
        
        Args:
            linha: Elemento WebElement da linha <tr>
        
        Returns:
            Dicionário com dados da linha
        """
        try:
            colunas = linha.find_elements(By.TAG_NAME, "td")
            
            if len(colunas) < 8:
                logger.warning(f"Linha com menos de 8 colunas: {len(colunas)}")
                return None
            
            dados = {
                'codigo': colunas[0].text.strip(),
                'empreendimentos': colunas[1].text.strip(),
                'banco': colunas[2].text.strip(),
                'nome_cedente': colunas[3].text.strip(),
                'agencia': colunas[4].text.strip(),
                'conta': colunas[5].text.strip(),
                'status': colunas[6].text.strip(),
                'cnpj_cedente': None  # Será preenchido do pop-up
            }
            
            return dados
            
        except Exception as e:
            logger.error(f"Erro ao extrair dados da linha: {e}")
            return None
    
    def abrir_popup_banco(self, linha, index: int) -> bool:
        """
        Abre o pop-up de detalhes do banco
        
        Args:
            linha: Elemento WebElement da linha <tr>
            index: Índice da linha (para logging)
        
        Returns:
            True se pop-up foi aberto com sucesso
        """
        tentativas = 3
        
        for tentativa in range(tentativas):
            try:
                # Localiza o botão com o ícone fa-eye na coluna de Ações
                botao_visualizar = linha.find_element(By.CSS_SELECTOR, "td:nth-child(8) i.fa.fa-eye")
                
                # Scroll até o elemento
                self.driver.execute_script("arguments[0].scrollIntoView(true);", botao_visualizar)
                time.sleep(0.5)
                
                # Clica no botão
                botao_visualizar.click()
                
                # Aguarda pop-up aparecer
                self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".modal.fade.in"))
                )
                time.sleep(1)
                
                logger.debug(f"Pop-up do banco {index + 1} aberto com sucesso")
                return True
                
            except (StaleElementReferenceException, ElementClickInterceptedException) as e:
                logger.warning(f"Tentativa {tentativa + 1}/{tentativas} falhou para banco {index + 1}: {e}")
                if tentativa < tentativas - 1:
                    time.sleep(2)
                    # Re-localiza a linha
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "#TabelaListar > tbody > tr")
                    if index < len(linhas):
                        linha = linhas[index]
                    else:
                        logger.error(f"Não foi possível re-localizar linha {index + 1}")
                        return False
                else:
                    logger.error(f"Falha ao abrir pop-up do banco {index + 1} após {tentativas} tentativas")
                    return False
            
            except Exception as e:
                logger.error(f"Erro inesperado ao abrir pop-up do banco {index + 1}: {e}")
                return False
        
        return False
    
    def extrair_dados_popup(self) -> Optional[str]:
        """
        Extrai dados do pop-up de detalhes do banco
        
        Returns:
            CNPJ do cedente ou None se não encontrado
        """
        try:
            # Aguarda modal estar visível
            modal = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".modal.fade.in"))
            )
            
            # Busca campo CNPJ Cedente
            cnpj_cedente = None
            
            # Estratégia 1: Por label "CNPJ Cedente" e elemento <b> próximo
            try:
                labels = modal.find_elements(By.TAG_NAME, "label")
                for label in labels:
                    texto_label = label.text.strip()
                    if "CNPJ" in texto_label.upper() and "CEDENTE" in texto_label.upper():
                        # Procura elemento <b> no mesmo form-group
                        parent = label.find_element(By.XPATH, "..")
                        elementos_b = parent.find_elements(By.TAG_NAME, "b")
                        if elementos_b:
                            cnpj_cedente = elementos_b[0].text.strip()
                            logger.debug(f"CNPJ encontrado via label: {cnpj_cedente}")
                            break
            except Exception as e:
                logger.debug(f"Estratégia 1 falhou: {e}")
            
            # Estratégia 2: Buscar por ID do label (for="cnpjCedente")
            if not cnpj_cedente:
                try:
                    label = modal.find_element(By.CSS_SELECTOR, "label[for='cnpjCedente']")
                    parent = label.find_element(By.XPATH, "..")
                    elementos_b = parent.find_elements(By.TAG_NAME, "b")
                    if elementos_b:
                        cnpj_cedente = elementos_b[0].text.strip()
                        logger.debug(f"CNPJ encontrado via for='cnpjCedente': {cnpj_cedente}")
                except Exception as e:
                    logger.debug(f"Estratégia 2 falhou: {e}")
            
            # Estratégia 3: Buscar todos os <b> e verificar se parece com CNPJ
            if not cnpj_cedente:
                try:
                    elementos_b = modal.find_elements(By.TAG_NAME, "b")
                    for elem_b in elementos_b:
                        texto = elem_b.text.strip()
                        # CNPJ tem formato XX.XXX.XXX/XXXX-XX ou apenas dígitos
                        # Verifica se tem caracteres típicos de CNPJ
                        if '/' in texto and '-' in texto:
                            # Remove formatação e verifica se tem 14 dígitos
                            apenas_digitos = ''.join(c for c in texto if c.isdigit())
                            if len(apenas_digitos) == 14:
                                cnpj_cedente = texto
                                logger.debug(f"CNPJ encontrado por padrão: {cnpj_cedente}")
                                break
                except Exception as e:
                    logger.debug(f"Estratégia 3 falhou: {e}")
            
            if cnpj_cedente:
                logger.debug(f"CNPJ Cedente extraído: {cnpj_cedente}")
            else:
                logger.warning("CNPJ Cedente não encontrado no pop-up")
            
            return cnpj_cedente
            
        except Exception as e:
            logger.error(f"Erro ao extrair dados do pop-up: {e}")
            return None
    
    def fechar_popup(self):
        """Fecha o pop-up de detalhes"""
        try:
            # Localiza botão Fechar
            botao_fechar = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-dismiss='modal']#cancelModal"))
            )
            botao_fechar.click()
            
            # Aguarda modal desaparecer
            self.wait.until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, ".modal.fade.in"))
            )
            time.sleep(0.5)
            
            logger.debug("Pop-up fechado com sucesso")
            
        except Exception as e:
            logger.warning(f"Erro ao fechar pop-up (tentando ESC): {e}")
            # Tenta fechar com ESC
            try:
                from selenium.webdriver.common.keys import Keys
                self.driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                time.sleep(1)
            except:
                logger.error("Não foi possível fechar pop-up")
    
    def processar_pagina_atual(self):
        """Processa todos os bancos da página atual"""
        logger.info("Processando bancos da página atual...")
        
        try:
            # Aguarda tabela carregar
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#TabelaListar > tbody"))
            )
            time.sleep(2)
            
            # Obtém todas as linhas
            linhas = self.driver.find_elements(By.CSS_SELECTOR, "#TabelaListar > tbody > tr")
            
            # Filtra linhas válidas (exclui "No data available")
            linhas_validas = []
            for linha in linhas:
                if "dataTables_empty" not in linha.get_attribute("class"):
                    linhas_validas.append(linha)
            
            total_linhas = len(linhas_validas)
            logger.info(f"Encontradas {total_linhas} linha(s) de bancos na página")
            
            if total_linhas == 0:
                logger.warning("Nenhum banco encontrado na página")
                return
            
            # Processa cada linha
            for i, linha in enumerate(linhas_validas):
                try:
                    logger.info(f"Processando banco {i + 1}/{total_linhas}...")
                    
                    # Extrai dados da tabela
                    dados_banco = self.extrair_dados_linha_tabela(linha)
                    
                    if not dados_banco:
                        logger.warning(f"Não foi possível extrair dados do banco {i + 1}")
                        continue
                    
                    logger.info(f"  Banco: {dados_banco['banco']} - {dados_banco['nome_cedente']}")
                    
                    # Abre pop-up para obter CNPJ Cedente
                    if self.abrir_popup_banco(linha, i):
                        # Extrai CNPJ do pop-up
                        cnpj_cedente = self.extrair_dados_popup()
                        dados_banco['cnpj_cedente'] = cnpj_cedente
                        
                        # Fecha pop-up
                        self.fechar_popup()
                    else:
                        logger.warning(f"  Não foi possível abrir pop-up do banco {i + 1}")
                    
                    # Adiciona à lista
                    self.bancos.append(dados_banco)
                    logger.info(f"  ✓ Banco {i + 1} processado com sucesso")
                    
                    # Re-localiza linhas após fechar modal (DOM pode ter mudado)
                    if i < total_linhas - 1:
                        time.sleep(1)
                        linhas_validas = self.driver.find_elements(By.CSS_SELECTOR, "#TabelaListar > tbody > tr")
                        linhas_validas = [l for l in linhas_validas if "dataTables_empty" not in l.get_attribute("class")]
                    
                except Exception as e:
                    logger.error(f"Erro ao processar banco {i + 1}: {e}")
                    # Tenta fechar pop-up se estiver aberto
                    try:
                        self.fechar_popup()
                    except:
                        pass
                    continue
            
            logger.info(f"Página processada: {len(self.bancos)} banco(s) coletado(s) até agora")
            
        except Exception as e:
            logger.error(f"Erro ao processar página: {e}")
            raise
    
    def verificar_proxima_pagina(self) -> bool:
        """
        Verifica se há próxima página disponível
        
        Returns:
            True se há próxima página
        """
        try:
            info = self.obter_info_paginacao()
            
            if not info:
                return False
            
            # Verifica se estamos na última página
            pagina_atual = info['page']
            total_paginas = info['pages']
            
            if pagina_atual >= total_paginas - 1:
                logger.info(f"Última página alcançada ({pagina_atual + 1}/{total_paginas})")
                return False
            
            return True
            
        except Exception as e:
            logger.warning(f"Erro ao verificar próxima página: {e}")
            return False
    
    def avancar_pagina(self) -> bool:
        """
        Avança para a próxima página
        
        Returns:
            True se conseguiu avançar
        """
        try:
            # Localiza botão "Next"
            botao_next = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#TabelaListar_next"))
            )
            
            # Verifica se não está desabilitado
            if "disabled" in botao_next.get_attribute("class"):
                logger.info("Botão 'Próxima' desabilitado - última página alcançada")
                return False
            
            # Clica no botão
            botao_next.click()
            
            # Aguarda nova página carregar
            time.sleep(3)
            
            logger.info("Avançado para próxima página")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao avançar página: {e}")
            return False
    
    def capturar_todos_bancos(self):
        """Captura dados de todos os bancos (todas as páginas)"""
        logger.info("Iniciando captura de bancos...")
        
        try:
            # Configura quantidade de registros
            self.configurar_quantidade_registros(50)
            
            # Obtém info inicial
            info = self.obter_info_paginacao()
            if info:
                total_registros = info['recordsTotal']
                logger.info(f"Total de bancos no sistema: {total_registros}")
            
            pagina = 1
            
            while True:
                logger.info(f"\n{'='*80}")
                logger.info(f"PROCESSANDO PÁGINA {pagina}")
                logger.info(f"{'='*80}\n")
                
                # Processa página atual
                self.processar_pagina_atual()
                
                # Verifica se há próxima página
                if not self.verificar_proxima_pagina():
                    logger.info("Todas as páginas foram processadas")
                    break
                
                # Avança para próxima página
                if not self.avancar_pagina():
                    logger.warning("Não foi possível avançar para próxima página")
                    break
                
                pagina += 1
            
            logger.info(f"\n{'='*80}")
            logger.info(f"CAPTURA CONCLUÍDA: {len(self.bancos)} banco(s) coletado(s)")
            logger.info(f"{'='*80}\n")
            
        except Exception as e:
            logger.error(f"Erro ao capturar bancos: {e}")
            raise
    
    def salvar_csv(self):
        """Salva dados coletados em arquivo CSV"""
        try:
            # Cria diretório se não existir
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            
            # Monta DataFrame
            df = pd.DataFrame(self.bancos)
            
            # Define caminho do arquivo
            arquivo_csv = Path(self.output_dir) / 'bancos.csv'
            
            # Salva CSV
            df.to_csv(arquivo_csv, index=False, encoding='utf-8')
            
            logger.info(f"Arquivo CSV salvo: {arquivo_csv}")
            logger.info(f"Total de registros: {len(df)}")
            
            # Exibe resumo
            print("\n" + "="*80)
            print("RESUMO DOS DADOS COLETADOS")
            print("="*80)
            print(f"Total de bancos: {len(df)}")
            print(f"Arquivo salvo: {arquivo_csv}")
            print("\nPrimeiros registros:")
            print(df.head().to_string())
            print("="*80 + "\n")
            
        except Exception as e:
            logger.error(f"Erro ao salvar CSV: {e}")
            raise
    
    def executar(self):
        """Executa fluxo completo de captura"""
        try:
            self.iniciar_driver()
            self.fazer_login()
            self.navegar_para_bancos()
            self.capturar_todos_bancos()
            self.salvar_csv()
            
            logger.info("Captura finalizada com sucesso!")
            
        except Exception as e:
            logger.error(f"Erro na execução: {e}")
            raise
        
        finally:
            if self.driver:
                logger.info("Fechando navegador...")
                self.driver.quit()


def main():
    """Função principal"""
    print("="*80)
    print("CAPTURADOR DE BANCOS - Sistema ACADE One")
    print("="*80)
    print()
    
    # Pergunta modo de execução
    modo = input("Executar em modo oculto? (S/n): ").strip().lower()
    headless = modo != 'n'
    
    print()
    print(f"Modo de execução: {'Oculto' if headless else 'Visível'}")
    print()
    
    # Cria e executa capturador
    capturador = CapturadorBancos(headless=headless)
    
    try:
        capturador.executar()
        print("\n✅ Captura concluída com sucesso!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Captura interrompida pelo usuário")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n❌ Erro na captura: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
