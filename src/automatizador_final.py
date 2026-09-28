#!/usr/bin/env python3
"""
AUTOMATIZADOR FINAL - ACADE ONE RELATÓRIOS E BAIXA DE PAGAMENTOS
Baseado na análise completa do HTML da página
"""

import time
import logging
import pandas as pd
import sys
import os
import glob
import json
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException, NoAlertPresentException
import os
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()


def formatar_data_cnab(data_str):
    """
    Formata data do formato CNAB (DDMMAAAA) para DD/MM/AAAA
    Se a data for inválida ou vazia, retorna '--/--/----'
    
    Args:
        data_str: String com data no formato DDMMAAAA ou DD/MM/AAAA
    
    Returns:
        str: Data formatada DD/MM/AAAA ou '--/--/----'
    """
    if not data_str or not isinstance(data_str, str):
        return '--/--/----'
    
    data_str = data_str.strip()
    
    # Se já está no formato DD/MM/AAAA
    if '/' in data_str and len(data_str) == 10:
        return data_str
    
    # Se está no formato DDMMAAAA
    if len(data_str) == 8 and data_str.isdigit():
        return f"{data_str[0:2]}/{data_str[2:4]}/{data_str[4:8]}"
    
    # Caso contrário, retorna placeholder
    return '--/--/----'


class ParserCNAB240Retorno:
    """Parser de arquivos de retorno CNAB 240 Sicoob"""
    
    def __init__(self, arquivo_path):
        self.arquivo_path = arquivo_path
        self.logger = logging.getLogger(__name__)
        
    def ler_arquivo(self):
        """Lê o arquivo de retorno"""
        try:
            with open(self.arquivo_path, 'r', encoding='latin-1') as f:
                linhas = f.readlines()
            return linhas
        except Exception as e:
            self.logger.error(f"Erro ao ler arquivo {self.arquivo_path}: {str(e)}")
            return []
    
    def parse_header_arquivo(self, linha):
        """Parse do Header de Arquivo (tipo 0)"""
        if linha[7:8] != '0':
            return None
        return {
            'tipo_registro': '0',
            'codigo_banco': linha[0:3],
            'nome_banco': linha[72:102].strip(),
        }
    
    def parse_header_lote(self, linha):
        """Parse do Header de Lote (tipo 1) - contém agência e conta"""
        if linha[7:8] != '1':
            return None
        return {
            'tipo_registro': '1',
            'lote': linha[3:7].strip(),
            'codigo_banco': linha[0:3],
            'agencia': linha[52:57].strip(),
            'agencia_dv': linha[57:58].strip(),
            'conta': linha[58:70].strip(),
            'conta_dv': linha[70:71].strip(),
        }
    
    def parse_segmento_a(self, linha):
        """Parse do Segmento A (tipo 3, segmento A) - dados do pagamento"""
        if linha[7:8] != '3' or linha[13:14] != 'A':
            return None
        
        # Extrair código de ocorrência (posições 231-240, 10 caracteres divididos em 5 códigos de 2 dígitos)
        ocorrencias_raw = linha[230:240]
        ocorrencias = [ocorrencias_raw[i:i+2].strip() for i in range(0, 10, 2) if ocorrencias_raw[i:i+2].strip()]
        
        return {
            'tipo_registro': '3A',
            'lote': linha[3:7].strip(),
            'sequencial': linha[8:13].strip(),
            'segmento': 'A',
            'codigo_movimento': linha[15:17].strip(),
            'nome_favorecido': linha[43:73].strip(),
            'seu_numero': linha[73:93].strip(),  # Posição 74-93 (índice 73:93)
            'data_pagamento': linha[93:101].strip(),  # Posição 94-101 - usar como vencimento
            'valor_pagamento': linha[119:134].strip(),  # Posição 120-134
            'data_real': linha[154:162].strip(),  # Posição 155-162 - data efetivação
            'valor_real': linha[162:177].strip(),  # Posição 163-177
            'ocorrencias': ocorrencias,
        }
    
    def parse_segmento_b(self, linha):
        """Parse do Segmento B (tipo 3, segmento B) - dados complementares"""
        if linha[7:8] != '3' or linha[13:14] != 'B':
            return None
        return {
            'tipo_registro': '3B',
            'lote': linha[3:7].strip(),
            'sequencial': linha[8:13].strip(),
            'segmento': 'B',
        }
    
    def parse_segmento_j(self, linha):
        """Parse do Segmento J (tipo 3, segmento J) - pagamento de títulos/boletos.

        Ignora Segmento J-52 (complemento), identificado por '52' em [17:19].
        """
        if linha[7:8] != '3' or linha[13:14] != 'J':
            return None
        if linha[17:19] == '52':
            return None

        ocorrencias_raw = linha[230:240]
        ocorrencias = [ocorrencias_raw[i:i+2].strip() for i in range(0, 10, 2) if ocorrencias_raw[i:i+2].strip()]

        return {
            'tipo_registro': '3J',
            'lote': linha[3:7].strip(),
            'sequencial': linha[8:13].strip(),
            'segmento': 'J',
            'codigo_movimento': linha[15:17].strip(),
            'codigo_barras': linha[17:61].strip(),
            'nome_favorecido': linha[61:91].strip(),
            'data_pagamento': linha[91:99].strip(),   # Data vencimento (usada para lookup)
            'valor_titulo': linha[99:114].strip(),
            'valor_desconto': linha[114:129].strip(),
            'valor_juros_multa': linha[129:144].strip(),
            'data_real': linha[144:152].strip(),       # Data efetivação do pagamento
            'valor_pagamento': linha[152:167].strip(),
            'valor_real': linha[152:167].strip(),
            'seu_numero': linha[182:202].strip(),      # Nº Documento da empresa (pos 183-202)
            'nosso_numero': linha[202:222].strip(),    # Referência do banco (pos 203-222)
            'ocorrencias': ocorrencias,
        }

    def parse_segmento_o(self, linha):
        """Parse do Segmento O (tipo 3, segmento O) - tributos/concessionárias"""
        if linha[7:8] != '3' or linha[13:14] != 'O':
            return None
        
        # Extrair código de ocorrência
        ocorrencias_raw = linha[230:240]
        ocorrencias = [ocorrencias_raw[i:i+2].strip() for i in range(0, 10, 2) if ocorrencias_raw[i:i+2].strip()]
        
        return {
            'tipo_registro': '3O',
            'lote': linha[3:7].strip(),
            'sequencial': linha[8:13].strip(),
            'segmento': 'O',
            'codigo_movimento': linha[15:17].strip(),
            'codigo_barras': linha[17:61].strip(),
            'nome_concessionaria': linha[61:91].strip(),
            'data_vencimento': linha[91:99].strip(),
            'data_pagamento': linha[99:107].strip(),
            'valor_pagamento': linha[107:122].strip(),
            'seu_numero': linha[122:142].strip(),  # Campo "Seu Número" para tributos
            'ocorrencias': ocorrencias,
        }
    
    def processar_arquivo(self):
        """Processa todo o arquivo de retorno e retorna lista de pagamentos confirmados"""
        linhas = self.ler_arquivo()
        if not linhas:
            return [], []
        
        pagamentos_confirmados = []
        pagamentos_nao_confirmados = []
        
        header_arquivo = None
        header_lote_atual = None
        segmento_a_atual = None
        
        for idx, linha in enumerate(linhas, 1):
            if len(linha) < 8:
                continue
            
            tipo_registro = linha[7:8]
            
            # Header de arquivo
            if tipo_registro == '0':
                header_arquivo = self.parse_header_arquivo(linha)
                self.logger.debug(f"Header arquivo: Banco {header_arquivo.get('codigo_banco')} - {header_arquivo.get('nome_banco')}")
            
            # Header de lote (contém agência e conta)
            elif tipo_registro == '1':
                header_lote_atual = self.parse_header_lote(linha)
                self.logger.debug(f"Header lote: Ag {header_lote_atual.get('agencia')} Conta {header_lote_atual.get('conta')}")
            
            # Segmento A (crédito/débito)
            elif tipo_registro == '3' and len(linha) > 13 and linha[13:14] == 'A':
                segmento_a_atual = self.parse_segmento_a(linha)
                
                if segmento_a_atual and header_lote_atual:
                    # Adicionar dados do lote ao segmento
                    segmento_a_atual['agencia'] = header_lote_atual['agencia']
                    segmento_a_atual['agencia_dv'] = header_lote_atual['agencia_dv']
                    segmento_a_atual['conta'] = header_lote_atual['conta']
                    segmento_a_atual['conta_dv'] = header_lote_atual['conta_dv']
                    
                    # Verificar se tem ocorrência '00' (Crédito/Débito Efetivado)
                    if '00' in segmento_a_atual['ocorrencias']:
                        pagamentos_confirmados.append(segmento_a_atual)
                        self.logger.info(f"✅ Pagamento confirmado: Seu Nº {segmento_a_atual['seu_numero'].strip()} - {segmento_a_atual['nome_favorecido']}")
                    else:
                        pagamentos_nao_confirmados.append({
                            **segmento_a_atual,
                            'motivo': f"Ocorrências: {', '.join(segmento_a_atual['ocorrencias']) if segmento_a_atual['ocorrencias'] else 'Nenhuma'}"
                        })
                        self.logger.warning(f"⚠️  Pagamento não confirmado: Seu Nº {segmento_a_atual['seu_numero'].strip()} - Ocorrências: {segmento_a_atual['ocorrencias']}")
            
            # Segmento J (pagamento de títulos/boletos) — ignora J-52 (complemento)
            elif tipo_registro == '3' and len(linha) > 18 and linha[13:14] == 'J' and linha[17:19] != '52':
                segmento_j = self.parse_segmento_j(linha)

                if segmento_j and header_lote_atual:
                    segmento_j['agencia'] = header_lote_atual['agencia']
                    segmento_j['agencia_dv'] = header_lote_atual['agencia_dv']
                    segmento_j['conta'] = header_lote_atual['conta']
                    segmento_j['conta_dv'] = header_lote_atual['conta_dv']

                    if '00' in segmento_j['ocorrencias']:
                        pagamentos_confirmados.append(segmento_j)
                        self.logger.info(f"✅ Boleto confirmado: Seu Nº {segmento_j['seu_numero'].strip()} - {segmento_j['nome_favorecido']}")
                    else:
                        pagamentos_nao_confirmados.append({
                            **segmento_j,
                            'motivo': f"Ocorrências: {', '.join(segmento_j['ocorrencias']) if segmento_j['ocorrencias'] else 'Nenhuma'}"
                        })
                        self.logger.warning(f"⚠️  Boleto não confirmado: Seu Nº {segmento_j['seu_numero'].strip()} - Ocorrências: {segmento_j['ocorrencias']}")

            # Segmento O (tributos/concessionárias)
            elif tipo_registro == '3' and len(linha) > 13 and linha[13:14] == 'O':
                segmento_o = self.parse_segmento_o(linha)
                
                if segmento_o and header_lote_atual:
                    # Adicionar dados do lote
                    segmento_o['agencia'] = header_lote_atual['agencia']
                    segmento_o['agencia_dv'] = header_lote_atual['agencia_dv']
                    segmento_o['conta'] = header_lote_atual['conta']
                    segmento_o['conta_dv'] = header_lote_atual['conta_dv']
                    segmento_o['nome_favorecido'] = segmento_o['nome_concessionaria']
                    segmento_o['data_real'] = segmento_o['data_pagamento']
                    
                    # Verificar ocorrência '00'
                    if '00' in segmento_o['ocorrencias']:
                        pagamentos_confirmados.append(segmento_o)
                        self.logger.info(f"✅ Tributo confirmado: Seu Nº {segmento_o['seu_numero'].strip()} - {segmento_o['nome_concessionaria']}")
                    else:
                        pagamentos_nao_confirmados.append({
                            **segmento_o,
                            'motivo': f"Ocorrências: {', '.join(segmento_o['ocorrencias']) if segmento_o['ocorrencias'] else 'Nenhuma'}"
                        })
                        self.logger.warning(f"⚠️  Tributo não confirmado: Seu Nº {segmento_o['seu_numero'].strip()} - Ocorrências: {segmento_o['ocorrencias']}")
        
        self.logger.info(f"📊 Total processado: {len(pagamentos_confirmados)} confirmados, {len(pagamentos_nao_confirmados)} não confirmados")
        
        return pagamentos_confirmados, pagamentos_nao_confirmados


class AutomatizadorAcadeOneFINAL:
    def __init__(self, headless=False, timeout=60):
        """
        Automatizador final com elementos corretos descobertos no HTML
        """
        self.timeout = timeout
        self.setup_logging()
        self.setup_driver(headless)
        self.base_url = os.getenv("ACADE_BASE_URL", "https://martins.acadeone.com.br").rstrip('/')
        self.login_url = f"{self.base_url}/acade/"
        self.relatorio_url = f"{self.base_url}/acade/finan/rlContaPagar/"
        
    def setup_logging(self):
        """Configura o sistema de logs"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('automatizador_final.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_driver(self, headless):
        """Configura o driver do Selenium usando o Selenium Manager integrado"""
        try:
            chrome_options = Options()
            if headless:
                chrome_options.add_argument("--headless=new")  # Usa a versão mais nova do headless
            
            # ISOLAMENTO CRÍTICO: Cada instância precisa de seu próprio perfil
            import tempfile
            import uuid
            user_data_dir = os.path.join(tempfile.gettempdir(), f"chrome_profile_{uuid.uuid4().hex}")
            chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
            self.logger.debug(f"User data dir: {user_data_dir}")
            
            # Isolamento de processos
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-shared-workers")  # Evita workers compartilhados
            chrome_options.add_argument("--disable-web-security")  # Permite múltiplas sessões
            chrome_options.add_argument("--disable-site-isolation-trials")  # Desabilita isolamento de sites
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-save-password-bubble")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-features=PasswordLeakDetection,PasswordDomainLeakDetection,AutofillServerCommunication,PasswordCheck,PasswordManagerOnboarding,PasswordCard,PasswordChange")
            
            # Cada sessão com cookies isolados
            chrome_options.add_experimental_option("prefs", {
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "profile.password_manager_leak_detection": False,
                "profile.password_manager_enable_auto_signin": False,
                "profile.default_content_setting_values.notifications": 2
            })
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            
            # Solução moderna: usar Selenium Manager integrado (Selenium 4.6.0+)
            # Não precisa mais do webdriver-manager!
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, self.timeout)
            
            # Armazenar user_data_dir para limpeza posterior
            self.user_data_dir = user_data_dir
            
            self.logger.info("Driver configurado com sucesso usando Selenium Manager")
            
        except Exception as e:
            self.logger.error(f"Erro ao configurar driver: {str(e)}")
            raise
            
    def fazer_login(self, usuario, senha):
        """
        Realiza o login no sistema
        ELEMENTOS DESCOBERTOS:
        - Campo usuário: ID = "login"
        - Campo senha: ID = "senha"
        - Botão acessar: CSS = "button.btn.btn-primary"
        """
        try:
            self.logger.debug("Iniciando processo de login")

            # Navegar para a página de login configurada
            self.driver.get(self.login_url)
            time.sleep(5)

            # Preencher usuário
            input_usuario = self.wait.until(
                EC.presence_of_element_located((By.ID, "login"))
            )
            input_usuario.clear()
            input_usuario.send_keys(usuario)
            self.logger.debug("Usuário preenchido")

            # Preencher senha
            input_senha = self.driver.find_element(By.ID, "senha")
            input_senha.clear()
            input_senha.send_keys(senha)
            self.logger.debug("Senha preenchida")

            # Clicar no botão Acessar
            btn_acessar = self.driver.find_element(By.CSS_SELECTOR, "button.btn.btn-primary")
            btn_acessar.click()
            self.logger.debug("Botão Acessar clicado")

            # Aguardar redirecionamento/validação
            time.sleep(10)

            url_atual = self.driver.current_url
            if "finan" in url_atual:
                self.logger.debug("Login realizado com sucesso")
                self._pos_login_tratar_alertas()
                return True

            self.logger.error(f"Falha no login - URL atual: {url_atual}")
            return False

        except Exception as e:
            self.logger.error(f"Erro durante o login: {str(e)}")
            return False

    def tratar_alerta_senha_comprometida(self):
        """Tenta fechar o alerta de senha comprometida exibido pelo Chrome."""
        fechado = False

        # 1. Verificar se há um alerta JavaScript padrão
        try:
            alerta = self.driver.switch_to.alert
            alerta.accept()
            fechado = True
        except NoAlertPresentException:
            pass
        except Exception as e:
            self.logger.debug(f"Falha ao aceitar alerta padrão: {e}")

        # 2. Enviar ESC para fechar notificações do navegador
        if not fechado:
            try:
                ActionChains(self.driver).send_keys(Keys.ESCAPE).pause(0.2).send_keys(Keys.ESCAPE).perform()
                fechado = True
            except Exception:
                pass

        # 3. Tentativa adicional via script (caso seja composto por elementos DOM)
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
            self.logger.debug("Alerta de senha do navegador fechado automaticamente")

        return fechado

    def _pos_login_tratar_alertas(self):
        """Executa múltiplas tentativas para garantir que o alerta de senha foi fechado."""
        for _ in range(4):
            if self.tratar_alerta_senha_comprometida():
                return
            try:
                # Tentar pelo DevTools Command API (fecha prompts de navegador)
                self.driver.execute_cdp_cmd("Browser.dismissBrowserPrompt", {"accept": True})
                return
            except Exception:
                pass
            time.sleep(0.25)

    def navegar_para_menu_relatorio(self):
        """
        Clica no menu "Relatório" do sidebar
        ELEMENTO DESCOBERTO:
        - Menu Relatório: XPath = "//span[text()='Relatório']/.."
        """
        try:
            self.logger.debug("Navegando para menu Relatório")

            # Garantir que qualquer alerta do navegador esteja fechado antes de interagir
            self._pos_login_tratar_alertas()

            # Aguardar carregamento da página após login
            time.sleep(3)

            menu_relatorio = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Relatório']/.."))
            )

            self.logger.debug("Menu Relatório encontrado")
            menu_relatorio.click()

            time.sleep(3)
            self.logger.debug("Menu Relatório expandido")
            return True

        except TimeoutException:
            self.logger.error("Timeout ao aguardar menu Relatório")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao navegar para menu Relatório: {str(e)}")
            return False

    def clicar_contas_a_pagar(self):
        """
        Clica no item "Contas à Pagar" no submenu de Relatório
        ELEMENTO DESCOBERTO:
        - Item Contas à Pagar: href contém "rlContaPagar"
        """
        try:
            self.logger.debug("Clicando em Contas à Pagar")

            time.sleep(2)
            item_contas_pagar = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='rlContaPagar']"))
            )

            self.logger.debug("Item Contas à Pagar encontrado")
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item_contas_pagar)
            time.sleep(0.5)
            item_contas_pagar.click()

            # Aguardar carregamento da página do relatório
            self.wait.until(lambda d: "rlContaPagar" in d.current_url)
            time.sleep(3)
            self.logger.debug("Navegou para página de Contas à Pagar")
            return True

        except TimeoutException:
            self.logger.error("Timeout ao aguardar item Contas à Pagar")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao clicar em Contas à Pagar: {str(e)}")
            return False
            
    def configurar_formulario(self, data_inicial=None, data_final=None, tipo_pagamento='A'):
        """
        Configura todos os campos do formulário do relatório
        ELEMENTOS DESCOBERTOS:
        - Tipo Relatório: ID = "cdTipoRelatorio" (select2)
        - Empreendimento: name = "cdEmpreendimento[]" (select2)
        - Centro de Custo: name = "cdCentroCusto[]" (select2)
        - Campo Data Inicial: ID = "dtInicial"
        - Campo Data Final: ID = "dtFinal"
        - Radio Visualizar: ID = "tipoRelatorioVisualizar"
        
        Args:
            tipo_pagamento: 'A' para Contas à Pagar (ABERTOS - padrão) ou 'P' para Contas Pagas
        """
        try:
            self.logger.debug("Configurando formulário do relatório")
            
            # Se não fornecidas, usar valores padrão
            hoje = datetime.now()
            
            if not data_inicial:
                # Data inicial: HOJE (não primeiro dia do mês)
                data_inicial = hoje.strftime("%d/%m/%Y")
            
            if not data_final:
                # Data final: HOJE
                data_final = hoje.strftime("%d/%m/%Y")
            
            # Determinar tipo de relatório baseado no parâmetro
            if tipo_pagamento == 'P':
                valor_tipo = 'P'
                texto_tipo = 'Contas Pagas'
            else:
                valor_tipo = 'A'
                texto_tipo = 'Contas à Pagar'
            
            self.logger.debug(f"Período configurado: {data_inicial} a {data_final}")
            self.logger.debug(f"Tipo de relatório: {texto_tipo}")
            
            # Aguardar carregamento da página de relatório
            time.sleep(5)
            
            # 1. Configurar Tipo de Relatório (valor "A" ou "P")
            try:
                self.logger.debug(f"Configurando Tipo de Relatório: {texto_tipo}")
                # SIMULAR INTERAÇÃO REAL: abrir dropdown e selecionar da lista
                self.driver.execute_script("""
                    var $select = $('#cdTipoRelatorio');
                    
                    // Abrir o dropdown do Select2
                    $select.select2('open');
                """)
                time.sleep(0.5)
                
                # Selecionar a opção baseada no tipo_pagamento
                self.driver.execute_script(f"""
                    var $select = $('#cdTipoRelatorio');
                    
                    // Fechar dropdown
                    $select.select2('close');
                    
                    // Definir valor e disparar eventos
                    $select.val('{valor_tipo}').trigger('change');
                    
                    // Disparar eventos select2
                    $select.trigger({{
                        type: 'select2:select',
                        params: {{
                            data: {{id: '{valor_tipo}', text: '{texto_tipo}'}}
                        }}
                    }});
                    
                    $select.blur();
                """)
                time.sleep(1.5)
                self.logger.debug("Tipo de Relatório configurado")
            except Exception as e:
                self.logger.warning(f"Erro ao configurar Tipo de Relatório: {str(e)}")
            
            # 2. Configurar Empreendimento = "Empreendimento Padrão" (temporário)
            # Será alterado para "Todos" DEPOIS de preencher todos os campos
            try:
                self.logger.debug("Configurando Empreendimento temporário")

                # Localizar o container Select2 do Empreendimento
                container = self.wait.until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "select[name='cdEmpreendimento[]'] + span.select2"
                    ))
                )

                # Abrir o dropdown clicando na área de seleção
                selection = container.find_element(By.CSS_SELECTOR, ".select2-selection")
                selection.click()
                time.sleep(0.5)

                # Campo de busca do Select2
                search_input = self.wait.until(
                    EC.visibility_of_element_located((
                        By.CSS_SELECTOR,
                        "span.select2-container--open input.select2-search__field"
                    ))
                )

                # Digitar o texto e selecionar a opção desejada
                termo_busca = "padr"
                search_input.clear()
                search_input.send_keys(termo_busca)
                time.sleep(0.5)
                search_input.send_keys(Keys.ENTER)
                time.sleep(1.5)

                # Verificar se a escolha foi aplicada; caso contrário, selecionar primeira opção não vazia
                escolhas = container.find_elements(By.CSS_SELECTOR, ".select2-selection__choice")
                if not escolhas:
                    self.logger.debug("Empreendimento Padrão não encontrado via busca, selecionando primeira opção disponível")
                    selection.click()
                    search_input = self.wait.until(
                        EC.visibility_of_element_located((
                            By.CSS_SELECTOR,
                            "span.select2-container--open input.select2-search__field"
                        ))
                    )
                    search_input.send_keys(Keys.ARROW_DOWN)
                    time.sleep(0.3)
                    search_input.send_keys(Keys.ENTER)
                    time.sleep(1.5)

                self.logger.debug("Empreendimento temporário configurado")
            except Exception as e:
                self.logger.warning(f"Erro ao configurar Empreendimento: {str(e)}")
            
            # 3. Configurar Centro de Custo = "Todos" (valor vazio "")
            try:
                self.logger.debug("Configurando Centro de Custo")
                # SIMULAR INTERAÇÃO REAL: abrir dropdown e selecionar da lista
                self.driver.execute_script("""
                    var $select = $('select[name="cdCentroCusto[]"]');
                    
                    // Abrir o dropdown do Select2
                    $select.select2('open');
                """)
                time.sleep(0.5)
                
                # Selecionar "Todos" (valor vazio)
                self.driver.execute_script("""
                    var $select = $('select[name="cdCentroCusto[]"]');
                    
                    // Fechar dropdown
                    $select.select2('close');
                    
                    // Definir valor e disparar eventos
                    $select.val('').trigger('change');
                    
                    // Disparar eventos select2
                    $select.trigger({
                        type: 'select2:select',
                        params: {
                            data: {id: '', text: 'Todos'}
                        }
                    });
                    
                    $select.blur();
                """)
                time.sleep(1.5)
                self.logger.debug("Centro de Custo configurado")
            except Exception as e:
                self.logger.warning(f"Erro ao configurar Centro de Custo: {str(e)}")
            
            # 4. Preencher data inicial COM eventos de validação
            campo_data_inicial = self.wait.until(
                EC.presence_of_element_located((By.ID, "dtInicial"))
            )
            # Focar no campo
            campo_data_inicial.click()
            time.sleep(0.3)
            
            # Limpar e preencher
            campo_data_inicial.clear()
            time.sleep(0.2)
            campo_data_inicial.send_keys(data_inicial)
            time.sleep(0.3)
            
            # Disparar eventos de validação via JavaScript
            self.driver.execute_script("""
                var campo = arguments[0];
                campo.dispatchEvent(new Event('input', { bubbles: true }));
                campo.dispatchEvent(new Event('change', { bubbles: true }));
                campo.dispatchEvent(new Event('blur', { bubbles: true }));
            """, campo_data_inicial)
            
            # TAB para próximo campo (fecha calendário se aberto)
            campo_data_inicial.send_keys(Keys.TAB)
            time.sleep(1.5)  # Aumentado para garantir processamento
            self.logger.debug("Data inicial preenchida")
            
            # 5. Preencher data final COM eventos de validação
            campo_data_final = self.driver.find_element(By.ID, "dtFinal")
            # Focar no campo
            campo_data_final.click()
            time.sleep(0.5)
            
            # Limpar e preencher
            campo_data_final.clear()
            time.sleep(0.3)
            campo_data_final.send_keys(data_final)
            time.sleep(0.5)
            
            # Disparar eventos de validação via JavaScript
            self.driver.execute_script("""
                var campo = arguments[0];
                campo.dispatchEvent(new Event('input', { bubbles: true }));
                campo.dispatchEvent(new Event('change', { bubbles: true }));
                campo.dispatchEvent(new Event('blur', { bubbles: true }));
            """, campo_data_final)
            
            # TAB para próximo campo (fecha calendário se aberto)
            campo_data_final.send_keys(Keys.TAB)
            time.sleep(1.5)  # Aumentado para garantir processamento
            self.logger.debug("Data final preenchida")
            
            # Remover foco dos campos de data para fechar qualquer calendário
            self.driver.execute_script("document.activeElement.blur();")
            time.sleep(1.5)
            
            # 6. Selecionar opção "Visualizar" COM validação
            try:
                self.logger.debug("Selecionando opção Visualizar")
                # O sistema usa iCheck, então precisamos usar o método correto
                self.driver.execute_script("""
                    // Desmarcar PDF (padrão)
                    $('#tipoRelatorio').iCheck('uncheck');
                    // Marcar Visualizar
                    $('#tipoRelatorioVisualizar').iCheck('check');
                    
                    // Disparar evento change no input original
                    $('#tipoRelatorioVisualizar').trigger('change').blur();
                """)
                time.sleep(1.5)  # Aumentado para garantir processamento
                self.logger.debug("Opção Visualizar selecionada")
            except Exception as e:
                self.logger.warning(f"Erro ao selecionar Visualizar: {str(e)}")
            
            # 7. Garantir que radio buttons de data estão marcados
            try:
                self.logger.debug("Garantindo seleção dos radio buttons de data")
                self.driver.execute_script("""
                    // tipodata: Pagamento (P)
                    $('#dtPagamento').iCheck('check');
                    $('#dtPagamento').trigger('change').blur();
                    
                    // tpdata: Vencimento (V)  
                    $('#dtVencimento').iCheck('check');
                    $('#dtVencimento').trigger('change').blur();
                """)
                time.sleep(1.5)  # Aumentado para garantir processamento
                self.logger.debug("Radio buttons de data configurados")
            except Exception as e:
                self.logger.warning(f"Erro ao configurar radio buttons de data: {str(e)}")
            
            # Aguardar um pouco para validação processar
            time.sleep(2)
            
            # Fechar qualquer calendário aberto clicando fora
            try:
                self.driver.execute_script("document.body.click();")
                time.sleep(0.5)
            except:
                pass
            
            # VALIDAÇÃO FINAL: Verificar se Bootstrap Validator reconheceu todos os campos
            try:
                self.logger.debug("Verificando validação do formulário")
                validacao = self.driver.execute_script("""
                    var form = $('#FormAdicionar');
                    var bv = form.data('bootstrapValidator');
                    
                    if (!bv) {
                        return { hasValidator: false, valid: 'N/A' };
                    }
                    
                    // Revalidar todos os campos
                    bv.validate();
                    
                    // Verificar validade
                    var isValid = bv.isValid();
                    
                    // Coletar campos inválidos
                    var invalidFields = [];
                    form.find('[data-bv-field]').each(function() {
                        var fieldName = $(this).attr('data-bv-field');
                        var status = bv.getFieldElements(fieldName).data('bv.result');
                        if (status === 'INVALID') {
                            invalidFields.push(fieldName);
                        }
                    });
                    
                    return {
                        hasValidator: true,
                        valid: isValid,
                        invalidFields: invalidFields
                    };
                """)
                
                self.logger.debug(f"Validação: {validacao}")
                
                if validacao.get('hasValidator') and not validacao.get('valid'):
                    self.logger.warning(f"⚠️  Campos inválidos: {validacao.get('invalidFields')}")
                else:
                    self.logger.debug("Formulário válido")
                    
            except Exception as e:
                self.logger.warning(f"Erro ao verificar validação: {str(e)}")
            
            # 🔥 PASSO FINAL: Voltar ao Empreendimento, clicar no × e selecionar "Todos"
            try:
                self.logger.debug("Reaplicando seleção de Empreendimento para 'Todos'")

                container = self.wait.until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "select[name='cdEmpreendimento[]'] + span.select2"
                    ))
                )
                selection = container.find_element(By.CSS_SELECTOR, ".select2-selection")
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", selection)

                # Focar o campo para exibir o input de busca
                selection.click()
                search_input = self.wait.until(
                    EC.visibility_of_element_located((
                        By.CSS_SELECTOR,
                        "span.select2-container--open input.select2-search__field"
                    ))
                )

                # 1. Limpar seleção usando ActionChains (equivalente ao clique no ×)
                self.logger.debug("Removendo seleção atual do Empreendimento")
                clear_button = container.find_elements(By.CSS_SELECTOR, ".select2-selection__clear")
                if clear_button:
                    ActionChains(self.driver).move_to_element(clear_button[0]).pause(0.2).click().perform()
                else:
                    # Fallback: enviar BACK_SPACE para remover o último chip selecionado
                    self.logger.debug("Botão × não encontrado, usando BACK_SPACE para limpar")
                    search_input.send_keys(Keys.BACKSPACE)

                # Aguardar até que nenhum chip permaneça selecionado
                self.wait.until(
                    lambda d: len(d.find_elements(
                        By.CSS_SELECTOR,
                        "select[name='cdEmpreendimento[]'] + span.select2 .select2-selection__choice"
                    )) == 0
                )
                self.logger.debug("Seleção de Empreendimento limpa")

                # 2. Selecionar "Todos" (valor vazio)
                self.logger.debug("Selecionando 'Todos' para Empreendimento")
                # Reabrir dropdown (caso tenha fechado após limpar)
                if "select2-container--open" not in container.get_attribute("class"):
                    selection.click()
                    search_input = self.wait.until(
                        EC.visibility_of_element_located((
                            By.CSS_SELECTOR,
                            "span.select2-container--open input.select2-search__field"
                        ))
                    )

                search_input.clear()
                search_input.send_keys("Todos")
                time.sleep(0.5)
                search_input.send_keys(Keys.ENTER)
                time.sleep(1.5)

                # Garantir que 'Todos' está selecionado (aparece como chip)
                escolhas = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "select[name='cdEmpreendimento[]'] + span.select2 .select2-selection__choice"
                )
                if not escolhas or all("Todos" not in escolha.text for escolha in escolhas):
                    raise RuntimeError("'Todos' não foi selecionado corretamente após limpar")
                self.logger.debug("'Todos' selecionado para Empreendimento")

                # Atualizar o estado de validação do Bootstrap Validator
                self.driver.execute_script("""
                    var form = $('#FormAdicionar');
                    var bv = form.data('bootstrapValidator');
                    if (bv) {
                        bv.updateStatus('cdEmpreendimento[]', 'VALID');
                    }
                """)

            except Exception as e:
                self.logger.warning(f"Erro ao reselecionar Empreendimento: {str(e)}")
            
            self.logger.debug("Formulário configurado com sucesso")
            return True
            
        except Exception as e:
            self.logger.error(f"Erro ao configurar formulário: {str(e)}")
            return False
            
    def gerar_relatorio(self):
        """
        Clica no botão Gerar para processar o relatório
        ELEMENTO DESCOBERTO:
        - Botão Gerar: ID = "btnSalvar" (texto: "Gerar")
        """
        try:
            self.logger.debug("Preparando clique no botão Gerar")
            
            # Aguardar um pouco antes de clicar
            time.sleep(2)
            
            # IMPORTANTE: Fechar TODOS os calendários antes de clicar em Gerar
            try:
                self.logger.debug("Fechando calendários ativos")
                # Método 1: Pressionar ESC para fechar qualquer popup
                actions = ActionChains(self.driver)
                actions.send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
                
                # Método 2: Remover foco via JavaScript
                self.driver.execute_script("""
                    // Fechar todos os datepickers
                    $('.datepicker').hide();
                    $('.datepicker-dropdown').hide();
                    // Remover foco
                    document.activeElement.blur();
                    // Clicar no body para garantir
                    document.body.focus();
                """)
                time.sleep(1)
                self.logger.debug("Calendários fechados")
                
            except Exception as e:
                self.logger.warning(f"Erro ao fechar calendários: {str(e)}")
            
            # Buscar botão Gerar
            btn_gerar = self.wait.until(
                EC.presence_of_element_located((By.ID, "btnSalvar"))
            )
            
            self.logger.debug("Botão Gerar localizado")
            
            # Verificar se o botão está visível e habilitado
            is_displayed = btn_gerar.is_displayed()
            is_enabled = btn_gerar.is_enabled()
            self.logger.debug(f"Botão Gerar - visível: {is_displayed}, habilitado: {is_enabled}")
            
            # Rolar até o botão para garantir que está visível
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_gerar)
            time.sleep(1)
            
            
            # CLICAR NO BOTÃO COM ACTIONCHAINS (clique REAL do mouse)
            self.logger.debug("Clicando no botão Gerar com ActionChains")
            try:
                # Mover para o botão e clicar
                actions = ActionChains(self.driver)
                actions.move_to_element(btn_gerar).pause(0.5).click().perform()
                
                self.logger.debug("Clique no botão Gerar realizado")
                
            except Exception as e:
                self.logger.error(f"Erro ao clicar: {str(e)}")
                raise
            
            # Aguardar a página começar a recarregar
            self.logger.debug("Aguardando processamento após clique em Gerar")
            time.sleep(2)
            # Aguardar readyState === 'complete'
            WebDriverWait(self.driver, 30).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self.logger.debug("ReadyState sinaliza carregamento concluído")
            
            # Aguardar mais um pouco para processamento
            time.sleep(3)
            
            # Aguardar processamento e carregamento da tabela
            self.logger.debug("Aguardando processamento do relatório")
            time.sleep(5)
            self.logger.debug("Processamento do relatório finalizado")
            
            return True
            
        except TimeoutException:
            self.logger.error("Timeout ao aguardar botão Gerar")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao gerar relatório: {str(e)}")
            return False
            
    def extrair_dados_tabela(self):
        """
        Extrai dados da tabela de relatório gerada
        ELEMENTO DESCOBERTO:
        - Tabela de resultados: table.tabela.visualizarInformacoesTabela
        - Container: div.row.visualizarInformacoes.tableFix
        """
        try:
            self.logger.debug("Iniciando extração da tabela")
            
            # Aguardar o container de visualização ficar VISÍVEL (não apenas presente no DOM)
            self.logger.debug("Aguardando tabela de resultados carregar")
            
            # Aguardar que o container fique VISÍVEL (sem atributo hidden)
            # Isso indica que o relatório foi processado e está pronto para visualização
            try:
                self.logger.debug("Aguardando container de resultados ficar visível (até 60s)")
                
                # Condição personalizada: verificar se display: block via CSS
                def elemento_visivel_por_css(driver):
                    try:
                        resultado = driver.execute_script("""
                            var container = document.querySelector('div.visualizarInformacoes');
                            if (!container) return null;
                            
                            // Verificar estilo computado e inline
                            var computedStyle = window.getComputedStyle(container).display;
                            var inlineStyle = container.style.display;
                            
                            // Verificar se tem tabela com linhas
                            var tabela = container.querySelector('table.visualizarInformacoesTabela');
                            var rows = tabela ? tabela.rows.length : 0;
                            
                            return {
                                computedDisplay: computedStyle,
                                inlineDisplay: inlineStyle,
                                rows: rows,
                                visible: (computedStyle === 'block' || inlineStyle === 'block') && rows > 0
                            };
                        """)
                        
                        if not resultado:
                            return False
                        
                        # Log apenas a cada 5 segundos
                        import time
                        if not hasattr(elemento_visivel_por_css, 'last_log'):
                            elemento_visivel_por_css.last_log = 0
                        
                        if time.time() - elemento_visivel_por_css.last_log > 5:
                            self.logger.debug(f"Verificação container: display={resultado.get('computedDisplay')}/{resultado.get('inlineDisplay')}, rows={resultado.get('rows')}")
                            elemento_visivel_por_css.last_log = time.time()
                        
                        # Retorna True se visível E tem linhas
                        return resultado.get('visible', False)
                        
                    except Exception as e:
                        return False
                
                WebDriverWait(self.driver, 60).until(elemento_visivel_por_css)
                self.logger.debug("Container de resultados visível")
                
            except TimeoutException:
                self.logger.error("❌ Timeout: Container não ficou visível após 60s")
                return pd.DataFrame()
            except Exception as e:
                self.logger.error(f"❌ Erro ao aguardar container: {str(e)}")
                return pd.DataFrame()
            
            # Aguardar especificamente a tabela de resultados com dados (pelo menos 1 linha de dados)
            tabela = None
            max_tentativas = 10
            for tentativa in range(max_tentativas):
                try:
                    # Seletor específico da tabela de resultados
                    tabela = self.driver.find_element(By.CSS_SELECTOR, "table.tabela.visualizarInformacoesTabela")
                    
                    # Verificar se tem linhas (thead + tbody com dados)
                    rows = tabela.find_elements(By.TAG_NAME, "tr")
                    
                    if len(rows) > 1:  # Mais de 1 linha = cabeçalho + pelo menos 1 linha de dados
                        self.logger.debug(f"Tabela de resultados encontrada com {len(rows)} linhas")
                        break
                    else:
                        self.logger.debug(f"Tentativa {tentativa + 1}/{max_tentativas}: tabela vazia, aguardando")
                        time.sleep(2)
                        tabela = None
                        
                except NoSuchElementException:
                    self.logger.debug(f"Tentativa {tentativa + 1}/{max_tentativas}: tabela não encontrada")
                    time.sleep(2)
            
            if not tabela:
                self.logger.error("Nenhuma tabela encontrada")
                return pd.DataFrame()
            
            # Extrair cabeçalhos
            headers = []
            try:
                # Tentar buscar cabeçalhos em thead
                thead = tabela.find_elements(By.TAG_NAME, "thead")
                if thead:
                    header_cells = thead[0].find_elements(By.TAG_NAME, "th")
                else:
                    # Tentar primeira linha
                    first_row = tabela.find_element(By.TAG_NAME, "tr")
                    header_cells = first_row.find_elements(By.TAG_NAME, "th")
                    if not header_cells:
                        header_cells = first_row.find_elements(By.TAG_NAME, "td")
                
                for cell in header_cells:
                    texto = cell.text.strip()
                    if texto:
                        headers.append(texto)
                        
                self.logger.debug(f"Cabeçalhos: {headers}")
                        
            except Exception as e:
                self.logger.warning(f"Erro ao extrair cabeçalhos: {str(e)}")
            
            # Extrair dados das linhas
            dados = []
            try:
                rows = tabela.find_elements(By.TAG_NAME, "tr")
                start_idx = 1 if headers else 0
                
                for row in rows[start_idx:]:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if cells:
                        row_data = []
                        for cell in cells:
                            texto = cell.text.strip()
                            row_data.append(texto)
                        
                        # Adicionar linha se não estiver vazia
                        if any(row_data):
                            dados.append(row_data)
                            
                self.logger.debug(f"Linhas extraídas: {len(dados)}")
                            
            except Exception as e:
                self.logger.error(f"Erro ao extrair dados: {str(e)}")
            
            # Criar DataFrame
            if dados:
                # Ajustar colunas se necessário
                if headers and len(dados) > 0:
                    max_cols = max(len(headers), max(len(row) for row in dados))
                    
                    # Ajustar headers
                    while len(headers) < max_cols:
                        headers.append(f"Coluna_{len(headers)+1}")
                    headers = headers[:max_cols]
                    
                    # Ajustar dados
                    for i, row in enumerate(dados):
                        while len(row) < max_cols:
                            row.append("")
                        dados[i] = row[:max_cols]
                    
                    df = pd.DataFrame(dados, columns=headers)
                else:
                    df = pd.DataFrame(dados)
                    
                self.logger.debug(f"DataFrame com {len(df)} registros e {len(df.columns)} colunas")
                
                # Voltar do iframe se entrou
                try:
                    self.driver.switch_to.default_content()
                except:
                    pass
                    
                return df
            else:
                self.logger.warning("Nenhum dado extraído")
                
                # Voltar do iframe se entrou
                try:
                    self.driver.switch_to.default_content()
                except:
                    pass
                    
                return pd.DataFrame()
                
        except Exception as e:
            self.logger.error(f"Erro ao extrair tabela: {str(e)}")
            
            # Voltar do iframe se entrou
            try:
                self.driver.switch_to.default_content()
            except:
                pass
                
            return pd.DataFrame()
            
    def salvar_relatorio(self, df, tipo_pagamento='A', formato='csv'):
        """
        Salva o relatório em arquivo
        
        Args:
            tipo_pagamento: 'A' para Contas à Pagar ou 'P' para Contas Pagas
        """
        try:
            if df.empty:
                self.logger.warning("DataFrame vazio - nada para salvar")
                return False
            
            # Criar diretório arquivos_auxiliares se não existir
            output_dir = "arquivos_auxiliares"
            os.makedirs(output_dir, exist_ok=True)
            
            # Definir nome base do arquivo conforme tipo de pagamento
            if tipo_pagamento == 'P':
                nome_base = "relatorio_contas_pagas"
            else:
                nome_base = "relatorio_contas_pagar"
            
            if formato.lower() == 'excel':
                filename = os.path.join(output_dir, f"{nome_base}.xlsx")
                df.to_excel(filename, index=False)
            else:
                filename = os.path.join(output_dir, f"{nome_base}.csv")
                df.to_csv(filename, index=False, encoding='utf-8-sig')
            
            self.logger.info(f"Relatório salvo: {filename}")
            
            # Mostrar informações do arquivo
            if len(df) > 0:
                self.logger.debug(f"Registros salvos: {len(df)}")
                self.logger.debug(f"Colunas: {list(df.columns)}")
                
            return filename
            
        except Exception as e:
            self.logger.error(f"Erro ao salvar: {str(e)}")
            return False
            
    def executar_automacao_completa(self, usuario, senha, data_inicial=None, data_final=None, tipo_pagamento='A', formato='excel'):
        """
        Executa todo o processo de automação
        
        Args:
            tipo_pagamento: 'A' para Contas à Pagar (ABERTOS - padrão) ou 'P' para Contas Pagas
        """
        try:
            tipo_texto = 'Contas Pagas' if tipo_pagamento == 'P' else 'Contas à Pagar'
            self.logger.info(f"Automação {tipo_texto} iniciada")

            if not self.fazer_login(usuario, senha):
                self.logger.error("Falha no login")
                return False
            self.logger.info("Login concluído")

            if not self.navegar_para_menu_relatorio():
                self.logger.error("Falha ao abrir menu Relatório")
                return False
            self.logger.info("Menu Relatório aberto")

            if not self.clicar_contas_a_pagar():
                self.logger.error("Falha ao abrir Contas à Pagar")
                return False
            self.logger.info("Página Contas à Pagar acessada")

            if not self.configurar_formulario(data_inicial, data_final, tipo_pagamento):
                self.logger.error("Falha ao configurar formulário")
                return False
            self.logger.info("Formulário configurado")

            if not self.gerar_relatorio():
                self.logger.error("Falha ao enviar solicitação do relatório")
                return False
            self.logger.info("Solicitação de relatório enviada")

            df = self.extrair_dados_tabela()
            if df.empty:
                self.logger.warning("Nenhum dado foi retornado pelo sistema")
                return False
            self.logger.info(f"Dados extraídos: {len(df)} registros")

            arquivo = self.salvar_relatorio(df, tipo_pagamento, formato)
            if arquivo:
                self.logger.info("Automação finalizada")
                return arquivo
            else:
                self.logger.error("Falha ao salvar arquivo")
                return False
            
        except Exception as e:
            self.logger.error(f"Erro na automação: {str(e)}")
            return False
        finally:
            self.fechar()
            
    def fechar(self):
        """Fecha o navegador e limpa recursos"""
        try:
            if hasattr(self, 'driver'):
                self.driver.quit()
                self.logger.debug("Navegador fechado")
                
            # Limpar diretório temporário do Chrome
            if hasattr(self, 'user_data_dir'):
                try:
                    import shutil
                    import time
                    time.sleep(1)  # Aguardar Chrome liberar arquivos
                    if os.path.exists(self.user_data_dir):
                        shutil.rmtree(self.user_data_dir, ignore_errors=True)
                        self.logger.debug(f"User data dir removido: {self.user_data_dir}")
                except Exception as e:
                    self.logger.debug(f"Aviso ao limpar user data dir: {str(e)}")
        except Exception as e:
            self.logger.error(f"Erro ao fechar: {str(e)}")
    
    def navegar_para_contas_pagar(self):
        """
        Navega para o menu Financeiro > Contas à Pagar
        """
        try:
            self.logger.debug("Navegando para Contas à Pagar")
            
            # Garantir que alertas estão fechados
            self._pos_login_tratar_alertas()
            time.sleep(2)
            
            # Clicar no menu Financeiro
            menu_financeiro = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "li.has_sub > a.subdrop"))
            )
            
            # Verificar se o texto contém "Financeiro"
            if "financeiro" in menu_financeiro.text.lower():
                menu_financeiro.click()
                self.logger.debug("Menu Financeiro expandido")
                time.sleep(2)
            else:
                # Tentar localizar pelo texto
                menu_financeiro = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Financeiro')]/.."))
                )
                menu_financeiro.click()
                self.logger.debug("Menu Financeiro expandido (via XPath)")
                time.sleep(2)
            
            # Clicar em Contas à Pagar
            item_contas_pagar = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='contaPagar']"))
            )
            item_contas_pagar.click()
            self.logger.debug("Navegou para Contas à Pagar")
            
            # Aguardar carregamento da tabela
            self.wait.until(
                EC.presence_of_element_located((By.ID, "TabelaListar"))
            )
            time.sleep(3)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erro ao navegar para Contas à Pagar: {str(e)}")
            return False
    
    def limpar_busca_avancada(self):
        """
        Limpa os campos da busca avançada
        """
        try:
            # Limpar campo Lancto
            campo_lancto = self.driver.find_element(By.CSS_SELECTOR, "input[placeholder='Lancto'].campoBuscaAvancada")
            campo_lancto.clear()
            
            # Limpar campo Vencto
            campo_vencto = self.driver.find_element(By.CSS_SELECTOR, "input[placeholder='Vencto'].campoBuscaAvancada")
            campo_vencto.clear()
            
            # Pressionar Enter para atualizar tabela
            campo_vencto.send_keys(Keys.ENTER)
            time.sleep(1)
            
            self.logger.debug("Filtros de busca limpos")
            return True
        except Exception as e:
            self.logger.warning(f"Erro ao limpar busca: {str(e)}")
            return False
    
    def buscar_titulo_por_documento(self, documento, data_vencimento):
        """
        Busca um título usando a Busca Avançada
        ROBUSTO para ambiente multi-threading
        
        Args:
            documento: Número do lançamento (campo Lancto)
            data_vencimento: Data de vencimento no formato DDMMAAAA
        
        Returns:
            True se encontrou, False caso contrário
        """
        max_tentativas = 3
        tentativa = 0
        
        while tentativa < max_tentativas:
            tentativa += 1
            try:
                self.logger.debug(f"Tentativa {tentativa}/{max_tentativas}: Buscando título Lancto={documento}, Vencto={data_vencimento}")
                
                # Converter data para formato DD/MM/YYYY
                if len(data_vencimento) == 8:
                    data_formatada = f"{data_vencimento[0:2]}/{data_vencimento[2:4]}/{data_vencimento[4:8]}"
                else:
                    data_formatada = data_vencimento
                
                # PASSO 1: Garantir que estamos na página correta
                url_atual = self.driver.current_url
                if 'contaPagar' not in url_atual:
                    self.logger.debug("Navegando para Contas à Pagar...")
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(3)
                    # Aguardar tabela carregar
                    WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located((By.ID, "TabelaListar"))
                    )
                
                # PASSO 2: Abrir Busca Avançada (garantir que está visível)
                try:
                    # Verificar se já está aberta
                    campo_doc_test = self.driver.find_elements(By.CSS_SELECTOR, "input[placeholder='Doc.'].campoBuscaAvancada")
                    if not campo_doc_test or not campo_doc_test[0].is_displayed():
                        # Clicar no botão de busca avançada
                        self.logger.debug("Abrindo Busca Avançada...")
                        btn_busca = WebDriverWait(self.driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick='hide()']"))
                        )
                        btn_busca.click()
                        time.sleep(1.5)
                except Exception as e:
                    self.logger.debug(f"Busca avançada já aberta ou erro: {str(e)}")
                
                # PASSO 3: Limpar TODOS os campos E resetar DataTables completamente
                self.logger.debug("Limpando campos de busca e resetando DataTables...")
                self.driver.execute_script("""
                    // Limpar todos os campos de busca avançada
                    var campos = document.querySelectorAll('input.campoBuscaAvancada');
                    campos.forEach(function(campo) {
                        campo.value = '';
                    });
                    
                    // Resetar o DataTables para estado inicial
                    if (typeof $ !== 'undefined' && $.fn.DataTable) {
                        var table = $('#TabelaListar').DataTable();
                        if (table) {
                            // Limpar pesquisa global
                            table.search('');
                            // Limpar pesquisa de todas as colunas
                            table.columns().search('');
                            // Redesenhar sem aplicar filtros
                            table.draw();
                        }
                    }
                """)
                time.sleep(1.5)
                
                # PASSO 4: Preencher APENAS o campo Lancto (sem vencimento por enquanto)
                campo_lancto = WebDriverWait(self.driver, 10).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder='Lancto'].campoBuscaAvancada"))
                )
                
                # Limpar e preencher com interação real
                campo_lancto.clear()
                time.sleep(0.3)
                campo_lancto.send_keys(documento.strip())
                time.sleep(0.5)
                
                # PASSO 5: Disparar filtro do DataTables de forma robusta
                self.logger.debug(f"Disparando filtro DataTables para Lancto={documento.strip()}")
                self.driver.execute_script("""
                    var campo = arguments[0];
                    var valorBusca = arguments[1];
                    
                    // Garantir valor está no campo
                    campo.value = valorBusca;
                    
                    // Disparar eventos na sequência correta
                    campo.dispatchEvent(new Event('focus', { bubbles: true }));
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    campo.dispatchEvent(new Event('change', { bubbles: true }));
                    
                    // Disparar keyup que o DataTables escuta
                    var event = new KeyboardEvent('keyup', {
                        bubbles: true,
                        cancelable: true,
                        key: 'Enter',
                        keyCode: 13
                    });
                    campo.dispatchEvent(event);
                    
                    // Forçar atualização do DataTables se disponível
                    if (typeof $ !== 'undefined' && $.fn.DataTable) {
                        var table = $('#TabelaListar').DataTable();
                        if (table) {
                            // Usar API de pesquisa de coluna específica
                            table.draw();
                        }
                    }
                """, campo_lancto, documento.strip())
                
                # PASSO 6: Aguardar processamento do DataTables
                self.logger.debug("Aguardando DataTables processar...")
                time.sleep(3)
                
                # Aguardar indicador de processamento desaparecer
                try:
                    WebDriverWait(self.driver, 10).until_not(
                        EC.visibility_of_element_located((By.ID, "TabelaListar_processing"))
                    )
                except:
                    pass
                
                time.sleep(1)
                
                # PASSO 7: Verificar resultados
                tabela = self.driver.find_element(By.ID, "TabelaListar")
                linhas = tabela.find_elements(By.CSS_SELECTOR, "tbody tr")
                linhas_visiveis = [l for l in linhas if l.is_displayed() and "No data available" not in l.text]
                
                self.logger.debug(f"Resultados encontrados: {len(linhas_visiveis)}")
                
                if not linhas_visiveis:
                    self.logger.warning(f"⚠️  Nenhum resultado para Lancto={documento}")
                    if tentativa < max_tentativas:
                        time.sleep(2)
                        continue
                    return False
                
                # PASSO 8: Verificar se documento está nos resultados
                documento_encontrado = False
                for linha in linhas_visiveis:
                    texto_linha = linha.text
                    # Verificar múltiplas formas do documento
                    doc_limpo = documento.strip()
                    doc_sem_zeros = doc_limpo.lstrip('0')
                    
                    if doc_limpo in texto_linha or (doc_sem_zeros and doc_sem_zeros in texto_linha):
                        documento_encontrado = True
                        self.logger.info(f"✅ Título validado com isolamento confirmado: Lancto={doc_limpo}, {len(linhas_visiveis)} resultado(s)")
                        return True
                
                # Se não encontrou exatamente, logar o que foi encontrado para debug
                if not documento_encontrado:
                    self.logger.warning(f"⚠️  Documento {documento.strip()} não encontrado nos {len(linhas_visiveis)} resultado(s) (tentativa {tentativa}/{max_tentativas})")
                    # Log dos primeiros resultados para debug
                    for i, linha in enumerate(linhas_visiveis[:3]):
                        self.logger.debug(f"   Resultado {i+1}: {linha.text[:100]}...")
                    
                    if tentativa < max_tentativas:
                        time.sleep(3)
                        continue
                    return False
                    
            except Exception as e:
                self.logger.error(f"Erro na busca (tentativa {tentativa}/{max_tentativas}): {str(e)}")
                if tentativa < max_tentativas:
                    time.sleep(3)
                    continue
                return False
        
        return False
    
    def clicar_botao_pagar(self, documento_esperado):
        """
        Clica no botão PAGAR do título filtrado, validando que é o documento correto
        
        Args:
            documento_esperado: Número do documento que deve estar na linha
        """
        try:
            # VALIDAÇÃO DE SESSÃO: Verificar que ainda estamos na página correta
            url_atual = self.driver.current_url
            if 'contaPagar' not in url_atual:
                self.logger.error(f"❌ Sessão corrompida: URL inesperada {url_atual}")
                return False
                
            self.logger.debug(f"Procurando botão PAGAR para documento {documento_esperado.strip()}")
            
            # Buscar todas as linhas visíveis da tabela
            tabela = self.driver.find_element(By.ID, "TabelaListar")
            linhas = tabela.find_elements(By.CSS_SELECTOR, "tbody tr")
            linhas_visiveis = [l for l in linhas if l.is_displayed() and "No data available" not in l.text]
            
            # Procurar a linha que contém o documento correto
            linha_correta = None
            for linha in linhas_visiveis:
                texto_linha = linha.text
                # Verificar se contém o documento (com ou sem zeros à esquerda)
                if documento_esperado.strip() in texto_linha or documento_esperado.strip().lstrip('0') in texto_linha:
                    linha_correta = linha
                    self.logger.debug(f"✅ Linha correta encontrada: {texto_linha[:100]}...")
                    break
            
            if not linha_correta:
                self.logger.error(f"❌ Linha com documento {documento_esperado.strip()} não encontrada entre {len(linhas_visiveis)} resultado(s)")
                return False
            
            # Localizar botão PAGAR na linha correta
            try:
                btn_pagar = linha_correta.find_element(By.CSS_SELECTOR, "a[data-original-title='PAGAR'], a[href*='pagar/']")
            except:
                # Fallback: tentar outros seletores
                btns = linha_correta.find_elements(By.TAG_NAME, "a")
                btn_pagar = None
                for btn in btns:
                    if 'pagar' in btn.get_attribute('href').lower():
                        btn_pagar = btn
                        break
                
                if not btn_pagar:
                    self.logger.error("❌ Botão PAGAR não encontrado na linha")
                    return False
            
            # Rolar até o botão e clicar
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_pagar)
            time.sleep(0.5)
            btn_pagar.click()
            self.logger.debug("✅ Botão PAGAR clicado na linha correta")
            
            # Aguardar carregamento da página de parcelas
            time.sleep(3)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erro ao clicar no botão PAGAR: {str(e)}")
            return False
    
    def selecionar_parcela_por_vencimento(self, data_vencimento):
        """
        Seleciona a parcela correta na tabela de parcelas
        
        Args:
            data_vencimento: Data de vencimento no formato DDMMAAAA ou DD/MM/YYYY
        """
        try:
            self.logger.debug(f"Selecionando parcela com vencimento {data_vencimento}")
            
            # Converter data para formato DD/MM/YYYY se necessário
            if len(data_vencimento) == 8:
                data_formatada = f"{data_vencimento[0:2]}/{data_vencimento[2:4]}/{data_vencimento[4:8]}"
            else:
                data_formatada = data_vencimento
            
            # ESTRATÉGIA 1: Buscar botão btnPagarParcela visível diretamente na página
            # Só clica se a linha do botão tiver o vencimento pedido (mesma coluna da estratégia 2).
            # Sem essa conferência, com a parcela do vencimento já baixada, o botão visível era o
            # da próxima parcela em aberto e ela era baixada no lugar.
            botoes_pagar = self.driver.find_elements(By.CSS_SELECTOR, ".btnPagarParcela, [id^='btnPagar_']:not(#btnPagar)")
            for btn in botoes_pagar:
                try:
                    if btn.is_displayed() and btn.get_attribute("id") != "btnPagar":
                        celulas = btn.find_element(By.XPATH, "./ancestor::tr[1]").find_elements(By.TAG_NAME, "td")
                        if len(celulas) < 2 or celulas[1].text.strip() != data_formatada:
                            continue
                        self.logger.info(f"✅ Parcela encontrada - botão direto na página: Vencto={data_formatada}")
                        btn.click()
                        time.sleep(2)
                        return True
                except:
                    continue
            
            # ESTRATÉGIA 2: Tentar via tabela parcelasTable (múltiplas parcelas)
            try:
                tabela_parcelas = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "parcelasTable"))
                )
                time.sleep(1)
                
                # PASSO 1: Exibir 50 registros na tabela
                # NOTA: O valor "-1" (Todos) não funciona devido a bug na aplicação WEB
                try:
                    select_length = self.driver.find_element(By.CSS_SELECTOR, "#parcelasTable_length > label > select")
                    self.driver.execute_script("""
                        var select = arguments[0];
                        select.value = '50';
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                        $(select).trigger('change');
                    """, select_length)
                    time.sleep(2)
                    self.logger.debug("Seletor de paginação alterado para 50 registros")
                except Exception as e:
                    self.logger.debug(f"Não foi possível alterar paginação: {str(e)[:50]}")
                
                # PASSO 2: Ordenar tabela por data de vencimento (coluna Vencto)
                # O sistema Acade tem bug que exibe parcelas fora de ordem cronológica
                # Clicar no cabeçalho da coluna "Vencto" para ordenar
                try:
                    self._ordenar_tabela_por_vencimento()
                except Exception as e:
                    self.logger.debug(f"Não foi possível ordenar tabela: {str(e)[:50]}")
                
                # PASSO 3: Buscar parcela na tabela ordenada com navegação por páginas
                resultado = self._buscar_parcela_com_paginacao(tabela_parcelas, data_formatada)
                if resultado:
                    return True
                        
            except Exception as e:
                self.logger.debug(f"Tabela parcelasTable não disponível: {str(e)[:50]}")
            
            self.logger.warning(f"⚠️  Parcela não encontrada com vencimento {data_formatada}")
            return False
            
        except Exception as e:
            self.logger.error(f"Erro ao selecionar parcela: {str(e)}")
            return False
    
    def _ordenar_tabela_por_vencimento(self):
        """
        Ordena a tabela de parcelas pela coluna Vencto (data de vencimento).
        Contorna bug do sistema Acade que exibe parcelas fora de ordem cronológica.
        
        Clica no cabeçalho da coluna "Vencto" para ordenar em ordem crescente.
        """
        try:
            self.logger.debug("Ordenando tabela por data de vencimento...")
            
            # Localizar o cabeçalho da coluna Vencto (índice 1, segunda coluna)
            # O DataTables usa classe 'sorting' para colunas ordenáveis
            header_vencto = self.driver.find_element(
                By.CSS_SELECTOR, "#parcelasTable thead th:nth-child(2)"
            )
            
            if not header_vencto:
                self.logger.debug("Cabeçalho Vencto não encontrado")
                return False
            
            # Verificar classe atual para determinar estado de ordenação
            classe_atual = header_vencto.get_attribute("class") or ""
            self.logger.debug(f"Classe atual do cabeçalho Vencto: {classe_atual}")
            
            # Se já está ordenado ascendente (sorting_asc), não precisa clicar
            if "sorting_asc" in classe_atual:
                self.logger.debug("Tabela já está ordenada por Vencto (ascendente)")
                return True
            
            # Clicar para ordenar
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", header_vencto)
            time.sleep(0.3)
            header_vencto.click()
            self.logger.debug("Clicou no cabeçalho Vencto para ordenar")
            time.sleep(1.5)
            
            # Verificar se ordenou corretamente (deve ter classe sorting_asc)
            classe_apos = header_vencto.get_attribute("class") or ""
            
            # Se ficou descendente (sorting_desc), clicar novamente para ascendente
            if "sorting_desc" in classe_apos:
                self.logger.debug("Ordenação está descendente, clicando novamente para ascendente")
                header_vencto.click()
                time.sleep(1.5)
            
            self.logger.debug("✅ Tabela ordenada por data de vencimento")
            return True
            
        except Exception as e:
            self.logger.debug(f"Erro ao ordenar tabela: {str(e)[:50]}")
            return False
    
    def _buscar_parcela_com_paginacao(self, tabela_parcelas, data_formatada):
        """
        Busca a parcela na tabela, navegando pelas páginas se necessário.
        Contorna bug do sistema Acade que não exibe todos os registros na paginação.
        
        Args:
            tabela_parcelas: Elemento WebElement da tabela de parcelas
            data_formatada: Data formatada DD/MM/YYYY
            
        Returns:
            True se encontrou e clicou na parcela, False caso contrário
        """
        pagina_atual = 1
        max_paginas = 20  # Limite de segurança para evitar loop infinito
        
        while pagina_atual <= max_paginas:
            self.logger.debug(f"Buscando parcela na página {pagina_atual}...")
            
            # Buscar todas as linhas da tabela na página atual
            linhas = tabela_parcelas.find_elements(By.CSS_SELECTOR, "tbody tr")
            
            for idx, linha in enumerate(linhas):
                try:
                    celulas = linha.find_elements(By.TAG_NAME, "td")
                    if len(celulas) < 2:
                        continue
                    
                    # Coluna 1 (índice 1) contém a data de vencimento
                    data_celula = celulas[1].text.strip()
                    
                    if data_celula == data_formatada:
                        self.logger.info(f"✅ Parcela encontrada na página {pagina_atual}: Vencto={data_celula}")
                        
                        # Tentar encontrar botão na linha
                        btn_pagar_parcela = None
                        seletores = [".btnPagarParcela", "button.btnPagarParcela", "[id^='btnPagar_']", "button[id^='btnPagar_']"]
                        
                        for seletor in seletores:
                            try:
                                btn_pagar_parcela = linha.find_element(By.CSS_SELECTOR, seletor)
                                if btn_pagar_parcela and btn_pagar_parcela.is_displayed():
                                    break
                            except:
                                btn_pagar_parcela = None
                        
                        # Fallback: buscar ícone fa-usd e subir para o botão
                        if not btn_pagar_parcela:
                            try:
                                icone = linha.find_element(By.CSS_SELECTOR, "i.fa-usd")
                                btn_pagar_parcela = icone.find_element(By.XPATH, "./ancestor::button")
                            except:
                                pass
                        
                        if btn_pagar_parcela:
                            btn_pagar_parcela.click()
                            time.sleep(2)
                            return True
                        else:
                            self.logger.warning(f"⚠️  Parcela Vencto={data_celula} sem botão de pagar "
                                                f"(pode já estar baixada); nenhuma outra parcela foi baixada")
                            return False
                            
                except Exception as e:
                    continue
            
            # Parcela não encontrada nesta página, verificar se há mais páginas
            if not self._tem_proxima_pagina_parcelas():
                self.logger.debug("Não há mais páginas para navegar")
                break
            
            # Navegar para próxima página
            if not self._navegar_proxima_pagina_parcelas():
                self.logger.warning("Falha ao navegar para próxima página")
                break
            
            pagina_atual += 1
            time.sleep(1)  # Aguardar carregamento da nova página
        
        return False
    
    def _tem_proxima_pagina_parcelas(self):
        """
        Verifica se existe botão de próxima página na paginação da tabela de parcelas
        
        Returns:
            True se há próxima página disponível, False caso contrário
        """
        try:
            # Verificar se existe o elemento de paginação
            paginacao = self.driver.find_elements(By.CSS_SELECTOR, "#parcelasTable_paginate > ul")
            if not paginacao:
                return False
            
            # Verificar se há mais de uma página (existem itens de página além de prev/next)
            paginas = self.driver.find_elements(By.CSS_SELECTOR, "#parcelasTable_paginate > ul > li")
            if len(paginas) <= 2:  # Apenas prev e next, sem páginas
                return False
            
            # Verificar se o botão "next" não está desabilitado
            btn_next = self.driver.find_elements(By.CSS_SELECTOR, "#parcelasTable_paginate > ul > li.next")
            if not btn_next:
                return False
            
            # Verificar se o botão next não tem classe "disabled"
            classe_next = btn_next[0].get_attribute("class") or ""
            if "disabled" in classe_next:
                return False
            
            return True
            
        except Exception as e:
            self.logger.debug(f"Erro ao verificar paginação: {str(e)[:50]}")
            return False
    
    def _navegar_proxima_pagina_parcelas(self):
        """
        Clica no botão de próxima página da tabela de parcelas
        
        Returns:
            True se navegou com sucesso, False caso contrário
        """
        try:
            # Encontrar e clicar no botão next
            btn_next = self.driver.find_element(By.CSS_SELECTOR, "#parcelasTable_paginate > ul > li.next > a")
            
            # Rolar até o botão para garantir visibilidade
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_next)
            time.sleep(0.3)
            
            btn_next.click()
            self.logger.debug("✅ Navegou para próxima página de parcelas")
            time.sleep(1.5)  # Aguardar carregamento da nova página
            
            return True
            
        except Exception as e:
            self.logger.debug(f"Erro ao navegar para próxima página: {str(e)[:50]}")
            return False
    
    def preencher_modal_pagamento(self, agencia, conta, data_pagamento):
        """
        Preenche o modal de pagamento e confirma
        COM VALIDAÇÃO DE ISOLAMENTO ENTRE SESSÕES
        
        Args:
            agencia: Agência (ex: "3357")
            conta: Conta (ex: "0000000055263")
            data_pagamento: Data do pagamento no formato DDMMAAAA
        """
        try:
            self.logger.info("🔄 [1/8] Iniciando preenchimento do modal de pagamento")
            
            # VALIDAÇÃO DE SESSÃO: Verificar que estamos na URL correta
            url_atual = self.driver.current_url
            if 'pagar' not in url_atual.lower():
                self.logger.error(f"❌ URL inesperada no modal: {url_atual}")
                return False
            
            # Converter data para formato DD/MM/YYYY
            if len(data_pagamento) == 8:
                data_formatada = f"{data_pagamento[0:2]}/{data_pagamento[2:4]}/{data_pagamento[4:8]}"
            else:
                data_formatada = data_pagamento
            
            # Aguardar modal estar visível - ESTRATÉGIA MÚLTIPLA
            self.logger.debug("🔄 [2/8] Aguardando modal de pagamento ficar visível...")
            
            modal = None
            estrategias = [
                ("ID: mPagar", (By.ID, "mPagar")),
                ("CSS: .modal.show", (By.CSS_SELECTOR, ".modal.show")),
                ("CSS: div[role='dialog']", (By.CSS_SELECTOR, "div[role='dialog']")),
                ("XPATH: modal com título Pagar", (By.XPATH, "//div[contains(@class, 'modal')]//h4[contains(text(), 'Pagar')]/../..")),
            ]
            
            for descricao, selector in estrategias:
                try:
                    self.logger.debug(f"Tentando estratégia: {descricao}")
                    modal = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located(selector)
                    )
                    self.logger.debug(f"✅ Modal encontrado com: {descricao}")
                    break
                except:
                    self.logger.debug(f"Estratégia {descricao} falhou")
                    continue
            
            if not modal:
                self.logger.error("❌ Modal não encontrado com nenhuma estratégia!")
                
                # Debug: listar todos os modals presentes
                try:
                    all_modals = self.driver.find_elements(By.CSS_SELECTOR, "div[class*='modal']")
                    self.logger.debug(f"Total de elementos com 'modal' na classe: {len(all_modals)}")
                    for idx, m in enumerate(all_modals[:3]):
                        self.logger.debug(f"  Modal {idx+1}: id='{m.get_attribute('id')}', class='{m.get_attribute('class')}', display={m.is_displayed()}")
                except Exception as debug_err:
                    self.logger.debug(f"Erro ao debugar modals: {debug_err}")
                
                return False
            
            self.logger.debug("✅ Modal detectado!")
            time.sleep(1)
            
            # 1. Conta Movimento (Select2)
            # Remover zeros à esquerda para exibição e busca
            agencia_sem_zeros = agencia.strip().lstrip('0')
            conta_sem_zeros = conta.strip().lstrip('0')
            self.logger.debug(f"🔄 [3/7] Selecionando Conta Movimento: Ag={agencia_sem_zeros}, Conta={conta_sem_zeros}")
            
            # Clicar no container do Select2
            self.logger.debug("Buscando #select2-cdContaMovimento-container...")
            container_conta = self.wait.until(
                EC.element_to_be_clickable((By.ID, "select2-cdContaMovimento-container"))
            )
            self.logger.debug("Clicando no select de Conta Movimento...")
            container_conta.click()
            time.sleep(0.5)
            
            # Buscar no campo de pesquisa
            self.logger.debug("Aguardando campo de pesquisa do Select2...")
            search_input = self.wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "span.select2-container--open input.select2-search__field"))
            )
            
            # Formatar busca: agencia (sem zeros à esquerda) + espaço + conta (sem zeros à esquerda)
            # Exemplo: "3357 29475" ao invés de "03357 00000029475"
            agencia_formatada = agencia.strip().lstrip('0')
            conta_formatada = conta.strip().lstrip('0')
            termo_busca = f"{agencia_formatada} {conta_formatada}"
            self.logger.debug(f"Digitando termo de busca: '{termo_busca}' (Ag={agencia_formatada}, Conta={conta_formatada})")
            search_input.clear()
            search_input.send_keys(termo_busca)
            time.sleep(1)
            self.logger.debug("Pressionando ENTER...")
            search_input.send_keys(Keys.ENTER)
            time.sleep(1)
            
            self.logger.debug("✅ Conta Movimento selecionada")
            
            # 2. Forma de Pagamento (Select2) - "Débito em Conta"
            self.logger.debug("🔄 [4/7] Selecionando Forma de Pagamento: Débito em Conta")
            
            self.logger.debug("Buscando #select2-cdFormaPagamento-container...")
            container_forma = self.wait.until(
                EC.element_to_be_clickable((By.ID, "select2-cdFormaPagamento-container"))
            )
            self.logger.debug("Clicando no select de Forma de Pagamento...")
            container_forma.click()
            time.sleep(0.5)
            
            self.logger.debug("Aguardando campo de pesquisa...")
            search_input = self.wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "span.select2-container--open input.select2-search__field"))
            )
            self.logger.debug("Digitando 'Débito em Conta'...")
            search_input.clear()
            search_input.send_keys("Débito em Conta")
            time.sleep(1)
            self.logger.debug("Pressionando ENTER...")
            search_input.send_keys(Keys.ENTER)
            time.sleep(1)
            
            self.logger.debug("✅ Forma de Pagamento selecionada")
            
            # 3. Data de Pagamento
            self.logger.debug(f"🔄 [5/7] Preenchendo Data de Pagamento: {data_formatada}")
            campo_dt_pagamento = self.driver.find_element(By.ID, "dtPagamento")
            # Usar JavaScript para preencher e disparar eventos
            self.driver.execute_script(f"arguments[0].value = '{data_formatada}';", campo_dt_pagamento)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", campo_dt_pagamento)
            time.sleep(0.3)
            # Fechar calendário se abriu
            try:
                self.driver.execute_script("$('.datepicker').hide();")
            except:
                pass
            self.logger.debug("✅ Data de Pagamento preenchida")
            
            # 4. Data de Débito
            self.logger.debug(f"🔄 [6/7] Preenchendo Data de Débito: {data_formatada}")
            campo_dt_debito = self.driver.find_element(By.ID, "dtDebito")
            # Usar JavaScript para preencher e disparar eventos
            self.driver.execute_script(f"arguments[0].value = '{data_formatada}';", campo_dt_debito)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", campo_dt_debito)
            time.sleep(0.3)
            # Fechar calendário se abriu
            try:
                self.driver.execute_script("$('.datepicker').hide();")
            except:
                pass
            self.logger.debug("✅ Data de Débito preenchida")
            
            # Garantir que qualquer popup/calendário foi fechado
            try:
                self.driver.execute_script("$('.datepicker').hide();")  # Forçar fechar calendários
            except:
                pass
            
            # 5. Clicar no botão Salvar
            self.logger.debug("🔄 [7/7] Clicando em Salvar (#btnPagar)")
            btn_salvar = self.driver.find_element(By.ID, "btnPagar")
            self.logger.debug(f"Botão encontrado, texto: '{btn_salvar.text}'")
            
            # Rolar até o botão e usar JavaScript para clicar (evita interceptação)
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_salvar)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", btn_salvar)
            self.logger.debug("Botão clicado via JavaScript")
            
            # Aguardar confirmação
            self.logger.debug("⏳ Aguardando confirmação (3s)...")
            time.sleep(3)
            
            self.logger.debug("✅ Pagamento confirmado no sistema")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ ERRO ao preencher modal de pagamento: {str(e)}")
            self.logger.error(f"❌ Tipo do erro: {type(e).__name__}")
            
            # Tentar capturar screenshot se possível
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = f"./relatorios/erro_modal_{timestamp}.png"
                self.driver.save_screenshot(screenshot_path)
                self.logger.error(f"📸 Screenshot salvo em: {screenshot_path}")
            except:
                pass
            
            return False
    
    def baixar_titulo(self, pagamento):
        """
        Realiza a baixa de um título completo - OPERAÇÃO ATÔMICA
        
        Args:
            pagamento: Dicionário com dados do pagamento (retorno do parser)
        
        Returns:
            dict: Status da baixa com campos:
                - sucesso (bool): True se baixa foi confirmada
                - seu_numero (str): Número do documento
                - nome (str): Nome do favorecido
                - agencia (str): Agência da conta
                - conta (str): Número da conta
                - data_pagamento (str): Data do pagamento (formato DDMMAAAA)
                - motivo (str, opcional): Motivo do erro (apenas se sucesso=False)
        """
        # Extrair dados do pagamento
        seu_numero = pagamento.get('seu_numero', '').strip()
        data_vencimento = pagamento.get('data_pagamento', '')
        data_real = pagamento.get('data_real', '')
        agencia = pagamento.get('agencia', '')
        conta = pagamento.get('conta', '')
        nome = pagamento.get('nome_favorecido', '')
        
        try:
            self.logger.debug(f"Processando: {seu_numero} - {nome}")
            
            # IMPORTANTE: Garantir que estamos na página de Contas à Pagar antes de buscar
            # Isso evita problemas de estado inconsistente em ambiente paralelo
            try:
                url_atual = self.driver.current_url
                if 'contaPagar' not in url_atual:
                    self.logger.debug("Navegando para Contas à Pagar...")
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(3)
                    # Aguardar tabela carregar
                    self.wait.until(
                        EC.presence_of_element_located((By.ID, "TabelaListar"))
                    )
                    time.sleep(2)
            except Exception as e:
                self.logger.warning(f"Aviso ao verificar URL: {str(e)}")
            
            # 1. Buscar título
            if not self.buscar_titulo_por_documento(seu_numero, data_vencimento):
                self.logger.debug(f"Título não encontrado, retornando à lista")
                # Voltar para lista de contas a pagar
                try:
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(2)
                except:
                    pass
                return {
                    'sucesso': False,
                    'seu_numero': seu_numero,
                    'nome': nome,
                    'agencia': agencia,
                    'conta': conta,
                    'data_pagamento': data_real,
                    'motivo': 'Título não encontrado'
                }
            
            # 2. Clicar em PAGAR (validando que é o documento correto)
            if not self.clicar_botao_pagar(seu_numero):
                self.logger.debug(f"Erro ao clicar em PAGAR, retornando à lista")
                # Voltar para lista de contas a pagar
                try:
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(2)
                except:
                    pass
                return {
                    'sucesso': False,
                    'seu_numero': seu_numero,
                    'nome': nome,
                    'agencia': agencia,
                    'conta': conta,
                    'data_pagamento': data_real,
                    'motivo': 'Erro ao clicar PAGAR'
                }
            
            # 3. Selecionar parcela
            if not self.selecionar_parcela_por_vencimento(data_vencimento):
                self.logger.debug(f"Parcela não encontrada, retornando à lista")
                # Voltar para lista de contas a pagar
                try:
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(2)
                except:
                    pass
                return {
                    'sucesso': False,
                    'seu_numero': seu_numero,
                    'nome': nome,
                    'agencia': agencia,
                    'conta': conta,
                    'data_pagamento': data_real,
                    'motivo': 'Parcela não encontrada'
                }
            
            # 4. Preencher modal e confirmar
            if not self.preencher_modal_pagamento(agencia, conta, data_real):
                self.logger.debug(f"Erro ao preencher modal, retornando à lista")
                # Voltar para lista de contas a pagar
                try:
                    self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                    time.sleep(2)
                except:
                    pass
                return {
                    'sucesso': False,
                    'seu_numero': seu_numero,
                    'nome': nome,
                    'agencia': agencia,
                    'conta': conta,
                    'data_pagamento': data_real,
                    'motivo': 'Erro ao preencher modal'
                }
            
            # Voltar para lista de contas a pagar
            self.logger.debug("Baixa confirmada, retornando à lista")
            time.sleep(2)
            try:
                self.driver.get(f"{self.base_url}/acade/finan/contaPagar/")
                time.sleep(3)
            except Exception as e:
                self.logger.warning(f"Erro ao retornar à lista: {str(e)}")
            
            return {
                'sucesso': True,
                'seu_numero': seu_numero,
                'nome': nome,
                'agencia': agencia,
                'conta': conta,
                'data_pagamento': data_real,
                'motivo': 'Baixa confirmada'
            }
            
        except Exception as e:
            self.logger.error(f"Exceção ao baixar título: {str(e)}")
            # Verificar se é erro de sessão/browser
            if 'no such window' in str(e).lower() or 'chrome not reachable' in str(e).lower():
                from exceptions import AcadeBrowserCrashException
                raise AcadeBrowserCrashException(f"Browser crashou: {str(e)}")
                
            return {
                'sucesso': False,
                'seu_numero': seu_numero,
                'nome': nome,
                'agencia': agencia,
                'conta': conta,
                'data_pagamento': data_real,
                'motivo': f"Exceção: {str(e)}"
            }
    
    def processar_arquivos_retorno(self, diretorio_retorno='./retorno'):
        """
        Processa todos os arquivos .RET do diretório de retorno
        Após processar, renomeia arquivo para .RET.bak
        
        Args:
            diretorio_retorno: Caminho do diretório com arquivos de retorno
        
        Returns:
            dict: Estatísticas do processamento
        """
        try:
            self.logger.info(f"🔍 Buscando arquivos em: {diretorio_retorno}")
            
            # Buscar todos os arquivos .RET
            pattern = os.path.join(diretorio_retorno, "*.RET")
            arquivos_ret = glob.glob(pattern)
            
            if not arquivos_ret:
                self.logger.warning(f"⚠️  Nenhum arquivo .RET encontrado em {diretorio_retorno}")
                return {
                    'total_arquivos': 0,
                    'total_confirmados': 0,
                    'total_processados': 0,
                    'total_sucesso': 0,
                    'total_erro': 0
                }
            
            self.logger.info(f"📂 Encontrados {len(arquivos_ret)} arquivo(s) .RET")
            
            todos_confirmados = []
            todos_nao_confirmados = []
            resultados_baixa = []
            
            # Processar cada arquivo
            arquivos_processados = []
            for arquivo in arquivos_ret:
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"📄 Processando: {os.path.basename(arquivo)}")
                self.logger.info(f"{'='*60}")
                
                parser = ParserCNAB240Retorno(arquivo)
                confirmados, nao_confirmados = parser.processar_arquivo()
                
                todos_confirmados.extend(confirmados)
                todos_nao_confirmados.extend(nao_confirmados)
                
                # Marcar arquivo como processado
                arquivos_processados.append(arquivo)
            
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"📊 RESUMO DO PROCESSAMENTO")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"Arquivos: {len(arquivos_ret)} | Confirmados: {len(todos_confirmados)} | Não confirmados: {len(todos_nao_confirmados)}")
            
            # Salvar arquivo de não processados em formato texto tabulado
            if todos_nao_confirmados:
                arquivo_nao_processados = os.path.join(diretorio_retorno, f"nao_processados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
                with open(arquivo_nao_processados, 'w', encoding='utf-8') as f:
                    # Cabeçalho
                    f.write("="*100 + "\n")
                    f.write("PAGAMENTOS NÃO PROCESSADOS - ARQUIVO CNAB RETORNO\n")
                    f.write("="*100 + "\n\n")
                    f.write(f"{'Documento':<12} {'Nome Favorecido':<35} {'Ocorrências':<15} {'Dt Pagto':<12} {'Valor':>15}\n")
                    f.write("-"*100 + "\n")
                    
                    # Dados
                    for item in todos_nao_confirmados:
                        doc = item.get('seu_numero', '').strip()
                        nome = item.get('nome_favorecido', '')[:35]  # Limitar a 35 chars
                        ocorrencias = ', '.join(item.get('ocorrencias', []))
                        data = item.get('data_pagamento', '')
                        # Formatar data DD/MM/YYYY
                        if len(data) == 8:
                            data_fmt = f"{data[0:2]}/{data[2:4]}/{data[4:8]}"
                        else:
                            data_fmt = data
                        valor = item.get('valor', 0)
                        valor_fmt = f"R$ {valor:,.2f}".replace(',', '_').replace('.', ',').replace('_', '.')
                        
                        f.write(f"{doc:<12} {nome:<35} {ocorrencias:<15} {data_fmt:<12} {valor_fmt:>15}\n")
                    
                    f.write("\n" + "="*100 + "\n")
                    f.write(f"Total de pagamentos não processados: {len(todos_nao_confirmados)}\n")
                    f.write("="*100 + "\n")
                
                self.logger.info(f"📝 Arquivo de não processados: {arquivo_nao_processados}")
            
            # Processar baixas no ACADE
            if todos_confirmados:
                self.logger.info(f"\n🚀 Iniciando baixas no ACADE ({len(todos_confirmados)} pagamentos)...\n")
                
                # Navegar para Contas à Pagar
                if not self.navegar_para_contas_pagar():
                    self.logger.error("❌ Falha ao acessar Contas à Pagar")
                    return {
                        'total_arquivos': len(arquivos_ret),
                        'total_confirmados': len(todos_confirmados),
                        'total_nao_confirmados': len(todos_nao_confirmados),
                        'total_processados': 0,
                        'total_sucesso': 0,
                        'total_erro': 0
                    }
                
                # Processar cada pagamento
                for idx, pagamento in enumerate(todos_confirmados, 1):
                    seu_numero = pagamento['seu_numero'].strip()
                    nome = pagamento['nome_favorecido']
                    self.logger.info(f"[{idx}/{len(todos_confirmados)}] {seu_numero} - {nome}")
                    resultado = self.baixar_titulo(pagamento)
                    resultados_baixa.append(resultado)
                    
                    if resultado.get('sucesso'):
                        self.logger.info(f"  ✅ Sucesso")
                    else:
                        self.logger.error(f"  ❌ Erro: {resultado.get('motivo', 'Erro desconhecido')}")
            
            # Estatísticas finais
            total_sucesso = sum(1 for r in resultados_baixa if r['sucesso'])
            total_erro = len(resultados_baixa) - total_sucesso
            
            # Salvar relatório de baixas em formato TXT
            arquivo_relatorio = os.path.join(diretorio_retorno, f"relatorio_baixas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            with open(arquivo_relatorio, 'w', encoding='utf-8') as f:
                # Cabeçalho
                agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                f.write(f"RELATÓRIO DE BAIXAS AUTOMÁTICAS - {agora}\n")
                f.write("-" * 138 + "\n")
                f.write(f"RESUMO: Total: {len(resultados_baixa)} | Sucesso: {total_sucesso} | Erros: {total_erro}\n")
                f.write("-" * 138 + "\n")
                
                # Cabeçalho das colunas
                f.write(f"{'DT. PAGTO':<12} | {'AGÊNCIA':<8} | {'CONTA':<16} | {'DOC':<13} | {'NOME':<31} | {'STATUS':<11} | {'DETALHES'}\n")
                f.write("-" * 138 + "\n")
                
                # Dados
                for resultado in resultados_baixa:
                    # Formatar data
                    data_pagto = formatar_data_cnab(resultado.get('data_pagamento', ''))
                    
                    # Preparar dados
                    agencia = resultado.get('agencia', '')[:8]
                    conta = resultado.get('conta', '')[:16]
                    doc = resultado.get('seu_numero', '')[:13]
                    nome = resultado.get('nome', '')[:31]
                    status = 'SUCESSO' if resultado.get('sucesso') else 'ERRO'
                    detalhes = resultado.get('motivo', '')[:50]
                    
                    # Escrever linha
                    f.write(f"{data_pagto:<12} | {agencia:<8} | {conta:<16} | {doc:<13} | {nome:<31} | {status:<11} | {detalhes}\n")
                
                f.write("-" * 138 + "\n")
            
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"✅ CONCLUÍDO | Sucesso: {total_sucesso} | Erro: {total_erro}")
            self.logger.info(f"📄 Relatório: {arquivo_relatorio}")
            self.logger.info(f"{'='*60}")
            
            # Renomear arquivos .RET processados para .RET.bak
            self.logger.info(f"\n📦 Renomeando arquivos processados...")
            for arquivo in arquivos_processados:
                try:
                    arquivo_bak = arquivo + ".bak"
                    os.rename(arquivo, arquivo_bak)
                    self.logger.info(f"✅ {os.path.basename(arquivo)} → {os.path.basename(arquivo_bak)}")
                except Exception as e:
                    self.logger.error(f"❌ Erro ao renomear {arquivo}: {str(e)}")
            
            return {
                'total_arquivos': len(arquivos_ret),
                'total_confirmados': len(todos_confirmados),
                'total_nao_confirmados': len(todos_nao_confirmados),
                'total_processados': len(resultados_baixa),
                'total_sucesso': total_sucesso,
                'total_erro': total_erro,
                'arquivo_relatorio': arquivo_relatorio
            }
            
        except Exception as e:
            self.logger.error(f"Erro ao processar arquivos de retorno: {str(e)}")
            return {
                'total_arquivos': 0,
                'total_confirmados': 0,
                'total_nao_confirmados': 0,
                'total_processados': 0,
                'total_sucesso': 0,
                'total_erro': 0,
                'erro': str(e)
            }


def input_com_timeout(prompt, timeout=3, default=""):
    """
    Input com timeout - versão multiplataforma
    """
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError()
    
    # Configurar o handler de timeout (apenas Unix/macOS)
    if hasattr(signal, 'SIGALRM'):
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
        
        try:
            resultado = input(prompt)
            signal.alarm(0)  # Cancelar o alarme
            return resultado if resultado else default
        except TimeoutError:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            print(f"\n⏱️  Timeout ({timeout}s) - usando valor padrão")
            return default
        except Exception:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            return default
    else:
        # Windows - fallback sem timeout
        try:
            resultado = input(prompt)
            return resultado if resultado else default
        except:
            return default


def main():
    """Função principal"""
    print("🎯 AUTOMATIZADOR FINAL - ACADE ONE")
    print("="*50)
    print("\n⚡ Modo Autônomo: Pressione Enter sem digitar para usar valores padrão:")
    print("   📅 Datas: Hoje")
    print("   📊 Formato: CSV")
    print("   � Pagamentos: ABERTOS (A)")
    print("   �👁️  Modo: Navegador oculto")
    print("="*50)
    
    # Obter credenciais
    usuario = os.getenv('ACADE_USUARIO')
    senha = os.getenv('ACADE_SENHA')
    
    if not usuario or not senha:
        print("\n⚠️  Credenciais não encontradas no .env")
        usuario = input("Usuário: ")
        senha = input("Senha: ")
    
    # Configurar parâmetros
    print("\n📅 Configuração de período:")
    
    data_inicial = input("Data inicial (DD/MM/YYYY) ou Enter para hoje: ").strip()
    if not data_inicial:
        data_inicial = None
        print(f"   ✅ Usando data de hoje: {datetime.now().strftime('%d/%m/%Y')}")
    
    data_final = input("Data final (DD/MM/YYYY) ou Enter para hoje: ").strip()
    if not data_final:
        data_final = None
        print(f"   ✅ Usando data de hoje: {datetime.now().strftime('%d/%m/%Y')}")
    
    print("\n💰 Tipo de pagamentos:")
    tipo_pagamento = input("Pagamentos (A=Abertos, P=Pagos) [A]: ").strip().upper()
    if not tipo_pagamento or tipo_pagamento not in ['A', 'P']:
        tipo_pagamento = 'A'
        print(f"   ✅ Usando tipo padrão: ABERTOS")
    else:
        tipo_texto = 'PAGOS' if tipo_pagamento == 'P' else 'ABERTOS'
        print(f"   ✅ Tipo selecionado: {tipo_texto}")
    
    print("\n📊 Formato de saída:")
    formato = input("Formato (excel/csv) [csv]: ").strip().lower()
    if not formato or formato not in ['excel', 'csv']:
        formato = 'csv'
        print(f"   ✅ Usando formato padrão: CSV")
    
    print("\n👀 Modo de execução:")
    modo = input("Executar em modo visível? (s/n) [n]: ").strip().lower()
    headless = not modo.startswith('s')
    if headless:
        print(f"   ✅ Usando modo padrão: Navegador oculto")
    
    tipo_texto = 'Pagos' if tipo_pagamento == 'P' else 'Abertos'
    print(f"\n🚀 Iniciando automação...")
    print(f"   Modo: {'Invisível (headless)' if headless else 'Visível'}")
    print(f"   Formato: {formato.upper()}")
    print(f"   Pagamentos: {tipo_texto}")
    print(f"   Período: {data_inicial or 'hoje'} até {data_final or 'hoje'}")
    print()
    
    # Executar automação
    automatizador = AutomatizadorAcadeOneFINAL(headless=headless)
    
    arquivo = automatizador.executar_automacao_completa(
        usuario=usuario,
        senha=senha,
        data_inicial=data_inicial,
        data_final=data_final,
        tipo_pagamento=tipo_pagamento,
        formato=formato
    )
    
    if arquivo:
        print(f"\n🎉 SUCESSO!")
        print(f"📄 Arquivo gerado: {arquivo}")
        
        # Mostrar informações do arquivo
        if os.path.exists(arquivo):
            size = os.path.getsize(arquivo)
            print(f"📏 Tamanho: {size:,} bytes")
            
            # Tentar mostrar preview dos dados
            try:
                if formato == 'excel':
                    df = pd.read_excel(arquivo)
                else:
                    df = pd.read_csv(arquivo)
                    
                print(f"📊 Registros: {len(df)} linhas")
                print(f"📋 Colunas: {len(df.columns)}")
                
                if len(df) > 0:
                    print(f"\n🔍 Preview (primeiras 3 linhas):")
                    print(df.head(3).to_string())
                    
            except Exception as e:
                print(f"📝 Arquivo salvo (erro ao fazer preview: {str(e)})")
                
    else:
        print(f"\n❌ ERRO!")
        print("📄 Verifique o arquivo de log: automatizador_final.log")

if __name__ == "__main__":
    main()