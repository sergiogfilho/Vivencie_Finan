#!/usr/bin/env python3
"""
CAPTURADOR DE PESSOAS - ACADE ONE
Extrai dados de Pessoas Físicas e Jurídicas
"""

import time
import logging
import pandas as pd
import os
import hashlib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException, NoAlertPresentException
from dotenv import load_dotenv
import concurrent.futures
import threading

# Carrega variáveis de ambiente
load_dotenv()


class CapturadorPessoasAcadeOne:
    """
    Captura dados de Pessoas Físicas e Jurídicas do AcadeOne
    Reutiliza lógica de login do automatizador_final.py
    """
    
    def __init__(self, headless=False, timeout=60, tipo_pessoa="Física", execucao_id=None,
                 output_dir=None, partial_interval=None, max_repeated_pages=None):
        """
        Inicializa o capturador
        
        Args:
            headless: Execução sem interface gráfica
            timeout: Timeout padrão para aguardar elementos
            tipo_pessoa: "Física" ou "Jurídica"
        """
        self.timeout = timeout
        self.tipo_pessoa = tipo_pessoa
        self.setup_logging()
        self.setup_driver(headless)
        self.base_url = os.getenv("ACADE_BASE_URL", "https://martins.acadeone.com.br").rstrip('/')
        self.execucao_id = None
        self.output_dir = None
        self.partial_interval = None
        self.max_repeated_pages = None
        self.execucao_id = execucao_id or datetime.now().strftime("%Y%m%d_%H%M%S")

        self.output_dir = output_dir or os.getenv("OUTPUT_DIR", "./arquivos_auxiliares/")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"Não foi possível preparar diretório de saída '{self.output_dir}': {e}")

        intervalo_config = partial_interval if partial_interval is not None else os.getenv(
            "CAPTURADOR_SERIALIZACAO_INTERVALO", "0"
        )
        try:
            intervalo_valor = int(intervalo_config)
        except (TypeError, ValueError):
            intervalo_valor = 0
        self.partial_interval = intervalo_valor if intervalo_valor > 0 else None

        repeticao_config = max_repeated_pages if max_repeated_pages is not None else os.getenv(
            "CAPTURADOR_MAX_PAGINA_REPETIDA", "2"
        )
        try:
            repeticao_valor = int(repeticao_config)
        except (TypeError, ValueError):
            repeticao_valor = 2
        self.max_repeated_pages = repeticao_valor if repeticao_valor > 0 else 0

        self.partial_path = None
        self.partial_final_path = None
        self.login_url = f"{self.base_url}/acade/"
        
    def setup_logging(self):
        """Configura o sistema de logs"""
        level = logging.DEBUG if os.getenv("CAPTURADOR_DEBUG", "0") == "1" else logging.INFO

        logging.basicConfig(
            level=level,
            format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('capturador_pessoas.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_driver(self, headless):
        """Configura o driver do Selenium usando Selenium Manager integrado"""
        try:
            chrome_options = Options()
            if headless:
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
            
            self.logger.info(f"Driver configurado para captura de Pessoa {self.tipo_pessoa} usando Selenium Manager")
            
        except Exception as e:
            self.logger.error(f"Erro ao configurar driver: {str(e)}")
            raise
            
    def fazer_login(self, usuario, senha):
        """
        Realiza o login no sistema (idêntico ao automatizador_final)
        """
        try:
            self.logger.info(f"Iniciando login para captura Pessoa {self.tipo_pessoa}")

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

            # Aguardar redirecionamento
            time.sleep(10)

            url_atual = self.driver.current_url
            if "finan" in url_atual:
                self.logger.info("Login realizado com sucesso")
                self._pos_login_tratar_alertas()
                return True

            self.logger.error(f"Falha no login - URL atual: {url_atual}")
            return False

        except Exception as e:
            self.logger.error(f"Erro durante o login: {str(e)}")
            return False

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
            self.logger.debug("Alerta de senha fechado")

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

    def navegar_menu_cadastro(self):
        """
        Clica no menu "Cadastro" do sidebar
        """
        try:
            self.logger.info("Navegando para menu Cadastro")
            
            # Garantir alertas fechados
            self._pos_login_tratar_alertas()
            time.sleep(3)

            # Localizar menu Cadastro (ícone + texto)
            menu_cadastro = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Cadastro']/.."))
            )

            self.logger.debug("Menu Cadastro encontrado")
            menu_cadastro.click()

            time.sleep(3)
            self.logger.info("Menu Cadastro expandido")
            return True

        except TimeoutException:
            self.logger.error("Timeout ao aguardar menu Cadastro")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao navegar para menu Cadastro: {str(e)}")
            return False

    def clicar_tipo_pessoa(self):
        """
        Clica em "Pessoa Física" ou "Pessoa Jurídica" no submenu
        """
        try:
            self.logger.info(f"Clicando em Pessoa {self.tipo_pessoa}")

            time.sleep(2)
            
            # Localizar item no submenu
            xpath = f"//span[text()='Pessoa {self.tipo_pessoa}']/.."
            item_pessoa = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )

            self.logger.debug(f"Item Pessoa {self.tipo_pessoa} encontrado")
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item_pessoa)
            time.sleep(0.5)
            item_pessoa.click()

            # Aguardar carregamento da página de listagem
            time.sleep(5)
            self.logger.info(f"Página Pessoa {self.tipo_pessoa} carregada")
            return True

        except TimeoutException:
            self.logger.error(f"Timeout ao aguardar Pessoa {self.tipo_pessoa}")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao clicar em Pessoa {self.tipo_pessoa}: {str(e)}")
            return False

    def configurar_50_registros(self):
        """
        Configura o seletor de quantidade para exibir 50 registros por página
        """
        try:
            self.logger.info("Configurando exibição de 50 registros por página")
            
            # Localizar o select
            select_element = self.wait.until(
                EC.presence_of_element_located((By.NAME, "TabelaListar_length"))
            )
            
            # Usar Select do Selenium
            select = Select(select_element)
            select.select_by_value("50")
            
            self.logger.debug("Seletor configurado para 50")
            
            # Aguardar atualização da tabela
            time.sleep(5)
            
            self.logger.info("Tabela atualizada com 50 registros")
            return True
            
        except Exception as e:
            self.logger.error(f"Erro ao configurar quantidade de registros: {str(e)}")
            return False

    def forcar_scroll_tabela(self):
        """
        Força scroll na tabela para garantir que todas as linhas sejam renderizadas
        Isso ajuda quando o navegador está com lazy loading ou virtualização
        """
        try:
            self.logger.debug("Forçando scroll na tabela para renderizar todas as linhas")
            
            # Scroll até o final da página
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.3)
            
            # Scroll na tabela se ela tiver scroll próprio
            tabela = self.driver.find_element(By.ID, "TabelaListar")
            self.driver.execute_script("""
                const tabela = arguments[0];
                const tbody = tabela.querySelector('tbody');
                if (tbody) {
                    // Scroll dentro do tbody se houver
                    tbody.scrollTop = tbody.scrollHeight;
                    
                    // Garantir que todas as linhas estejam visíveis
                    const linhas = tbody.querySelectorAll('tr');
                    if (linhas.length > 0) {
                        linhas[linhas.length - 1].scrollIntoView({block: 'end'});
                    }
                }
            """, tabela)
            time.sleep(0.3)
            
            # Scroll de volta ao topo
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.2)
            
            self.logger.debug("Scroll forçado concluído")
            
        except Exception as e:
            self.logger.warning(f"Erro ao forçar scroll: {str(e)}")

    def reforcar_carregamento_tabela(self, tentativa):
        """
        Dispara um redraw/refresh na tabela atual via DataTables quando possível.
        Ajuda em cenários onde o carregamento parcial persiste após a primeira extração.
        """
        try:
            resultado = self.driver.execute_script(
                """
                const selector = '#TabelaListar';
                const resposta = { ok: false, motivo: 'indefinido' };
                try {
                    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.dataTable) {
                        const isDataTable = window.jQuery.fn.dataTable.isDataTable(selector);
                        if (!isDataTable) {
                            resposta.motivo = 'datatable-nao-inicializado';
                            return resposta;
                        }

                        const tabela = window.jQuery(selector).DataTable();
                        if (!tabela) {
                            resposta.motivo = 'datatable-sem-instancia';
                            return resposta;
                        }

                        const settings = tabela.settings ? tabela.settings()[0] : null;
                        const serverSide = !!(settings && settings.oFeatures && settings.oFeatures.bServerSide);

                        if (serverSide && tabela.ajax && typeof tabela.ajax.reload === 'function') {
                            tabela.ajax.reload(null, false);
                            resposta.ok = true;
                            resposta.acao = 'ajax.reload';
                            resposta.serverSide = true;
                            return resposta;
                        }

                        if (typeof tabela.draw === 'function') {
                            tabela.draw(false);
                            resposta.ok = true;
                            resposta.acao = 'draw';
                            resposta.serverSide = !!serverSide;
                            return resposta;
                        }

                        resposta.motivo = 'sem-metodo-de-refresh';
                        return resposta;
                    }
                    resposta.motivo = 'jquery-indisponivel';
                    return resposta;
                } catch (erro) {
                    return { ok: false, motivo: 'erro', detalhe: String(erro) };
                }
                """
            ) or {}

            if resultado.get("ok"):
                acao = resultado.get("acao", "desconhecida")
                self.logger.info(
                    f"Tentativa {tentativa}: recarregamento da tabela disparado via {acao}"
                )
            else:
                motivo = resultado.get("motivo", "desconhecido")
                detalhe = resultado.get("detalhe")
                if detalhe:
                    self.logger.debug(
                        f"Tentativa {tentativa}: falha ao reforçar carregamento ({motivo}) - {detalhe}"
                    )
                else:
                    self.logger.debug(
                        f"Tentativa {tentativa}: não foi possível reforçar carregamento ({motivo})"
                    )

        except Exception as e:
            self.logger.debug(f"Tentativa {tentativa}: erro ao reforçar carregamento - {str(e)}")

    def obter_info_datatable(self):
        """Retorna informações da paginação atual do DataTables, se disponível."""
        try:
            info = self.driver.execute_script(
                """
                try {
                    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.dataTable) {
                        const selector = '#TabelaListar';
                        const isDataTable = window.jQuery.fn.dataTable.isDataTable(selector);
                        if (!isDataTable) {
                            return { status: 'not-initialized' };
                        }
                        const tabela = window.jQuery(selector).DataTable();
                        if (!tabela || typeof tabela.page !== 'function') {
                            return { status: 'no-page' };
                        }
                        const infoLocal = tabela.page.info();
                        return {
                            status: 'ok',
                            page: infoLocal ? infoLocal.page : null,
                            pages: infoLocal ? infoLocal.pages : null,
                            start: infoLocal ? infoLocal.start : null,
                            end: infoLocal ? infoLocal.end : null,
                            recordsDisplay: infoLocal ? infoLocal.recordsDisplay : null,
                            recordsTotal: infoLocal ? infoLocal.recordsTotal : null
                        };
                    }
                    return { status: 'jquery-unavailable' };
                } catch (erro) {
                    return { status: 'error', detalhe: String(erro) };
                }
                """
            )
            if isinstance(info, dict):
                return info
            return None
        except Exception as e:
            self.logger.debug(f"Erro ao consultar info do DataTables: {str(e)}")
            return None

    def confirmar_ultima_pagina(self, info, registros_pagina, total_parcial):
        """Avalia dados do DataTables para identificar se a página atual é a última."""
        if not info or not isinstance(info, dict):
            return False, None

        status = info.get("status")
        if status not in ("ok", "ready"):
            return False, {"info": info, "razoes": []}

        razoes = []

        page = info.get("page")
        pages = info.get("pages")
        try:
            if page is not None and pages is not None:
                page_int = int(page)
                pages_int = int(pages)
                if pages_int <= 0:
                    razoes.append("DataTables indicou 0 páginas restantes")
                elif page_int >= pages_int - 1:
                    razoes.append(f"Índice da página atual ({page_int}) é o último dentre {pages_int}")
        except (TypeError, ValueError):
            pass

        limite_total = None
        for chave in ("recordsDisplay", "recordsTotal"):
            try:
                valor = info.get(chave)
                if valor is not None:
                    valor_int = int(valor)
                    if valor_int >= 0:
                        limite_total = max(limite_total or 0, valor_int)
            except (TypeError, ValueError):
                continue

        end = info.get("end")
        try:
            if end is not None:
                end_int = int(end)
                if limite_total is not None and end_int >= limite_total:
                    razoes.append(f"Último índice exibido ({end_int}) >= total ({limite_total})")
        except (TypeError, ValueError):
            pass

        try:
            if limite_total is not None and total_parcial is not None and int(total_parcial) >= limite_total:
                razoes.append(f"Registros acumulados ({int(total_parcial)}) >= total esperado ({limite_total})")
        except (TypeError, ValueError):
            pass

        try:
            if (
                limite_total is None
                and registros_pagina is not None
                and int(registros_pagina) > 0
                and int(registros_pagina) < 50
                and total_parcial is not None
            ):
                total_int = int(total_parcial)
                if total_int % 50 != 0:
                    razoes.append(
                        "Quantidade da página (<50) e total acumulado não múltiplo de 50 sugerem última página"
                    )
        except (TypeError, ValueError):
            pass

        return bool(razoes), {"info": info, "razoes": razoes}

    def gerar_fingerprint_pagina(self, dados):
        """Gera um hash compacto da página atual para detectar repetições."""
        if not dados:
            return None
        try:
            primeiros = dados[:5]
            ultimos = dados[-5:] if len(dados) > 5 else dados
            partes = []
            for registro in primeiros + ultimos:
                codigo = registro.get('Código') or ''
                documento = registro.get('CPF/CNPJ') or ''
                nome = registro.get('Nome') or ''
                partes.append(f"{codigo}|{documento}|{nome}")
            partes_unicas = "::".join(partes)
            base = f"{len(dados)}::{partes_unicas}"
            return hashlib.sha1(base.encode('utf-8', 'ignore')).hexdigest()
        except Exception as e:
            self.logger.debug(f"Erro ao gerar fingerprint da página: {str(e)}")
            return None

    def salvar_serializacao_parcial(self, dados, pagina_atual, final=False):
        """Grava um snapshot parcial ou final dos dados capturados até o momento."""
        if not self.partial_interval:
            return

        if final:
            destino = self.partial_final_path
        else:
            destino = self.partial_path

        if not dados or not destino:
            return

        try:
            df_parcial = pd.DataFrame(dados)
            if df_parcial.empty:
                return

            df_parcial.to_csv(destino, index=False, encoding='utf-8-sig')
            contexto = "final" if final else f"parcial (página {pagina_atual})"
            self.logger.info(
                f"Serialização {contexto} gravada em {destino} ({len(df_parcial)} registros)"
            )
        except Exception as e:
            tipo = "final" if final else "parcial"
            self.logger.error(f"Erro ao realizar serialização {tipo}: {str(e)}")

    def aguardar_carregamento_completo_tabela(self, timeout=None):
        """
        Aguarda até que:
        1. Não existam indicadores de loading visíveis
        2. O número de linhas na tabela estabilize (não mude por 3 verificações consecutivas)
        
        Retorna: número de linhas detectadas ou 0 se timeout
        """
        try:
            timeout = timeout or min(self.timeout, 30)
            self.logger.debug("Aguardando carregamento completo da tabela")
            tempo_inicio = time.time()
            linhas_anterior = 0
            contagem_estavel = 0
            tentativas = 0
            ultimo_html = None
            
            while (time.time() - tempo_inicio) < timeout:
                try:
                    # Verificar se há loading/spinner ativo usando JavaScript
                    loading_ativo = self.driver.execute_script("""
                        const tabelaSelector = '#TabelaListar';

                        // Verificar spinners/loading comuns
                        const spinners = document.querySelectorAll('.loading, .spinner, [class*="load"]');
                        for (let el of spinners) {
                            if (el.offsetParent !== null && getComputedStyle(el).display !== 'none') {
                                return true;
                            }
                        }

                        // Verificar DataTables processing visível no DOM
                        const dtProcessing = document.querySelector('.dataTables_processing');
                        if (dtProcessing && dtProcessing.offsetParent !== null && getComputedStyle(dtProcessing).display !== 'none') {
                            return true;
                        }

                        // Verificar via API do DataTables se existir
                        if (window.jQuery && window.jQuery.fn && window.jQuery.fn.dataTable) {
                            const isDataTable = window.jQuery.fn.dataTable.isDataTable(tabelaSelector);
                            if (!isDataTable) {
                                return true; // DataTable ainda não inicializado
                            }

                            const tabelaApi = window.jQuery(tabelaSelector).DataTable();
                            if (tabelaApi && typeof tabelaApi.processing === 'function' && tabelaApi.processing()) {
                                return true;
                            }

                            if (tabelaApi && tabelaApi.settings && tabelaApi.settings().length > 0) {
                                const settings = tabelaApi.settings()[0];

                                if (settings && settings._bInitComplete === false) {
                                    return true;
                                }

                                if (settings && settings.jqXHR && settings.jqXHR.readyState !== 4) {
                                    return true;
                                }

                                if (settings && settings.nTableWrapper) {
                                    const processingNode = settings.nTableWrapper.querySelector('.dataTables_processing');
                                    if (processingNode && processingNode.offsetParent !== null && getComputedStyle(processingNode).display !== 'none') {
                                        return true;
                                    }
                                }
                            }
                        }

                        // Verificar se há overlay/bloqueio
                        const overlays = document.querySelectorAll('.blockUI, .overlay, [class*="block"]');
                        for (let el of overlays) {
                            if (el.offsetParent !== null && getComputedStyle(el).display !== 'none') {
                                return true;
                            }
                        }

                        return false;
                    """)
                    
                    if loading_ativo:
                        self.logger.debug("Loading/spinner detectado - aguardando...")
                        time.sleep(0.3)
                        continue
                    
                    tabela = self.driver.find_element(By.ID, "TabelaListar")
                    tbody = tabela.find_element(By.TAG_NAME, "tbody")
                    linhas = tbody.find_elements(By.TAG_NAME, "tr")
                    ultimo_html = tbody.get_attribute("outerHTML")

                    datatable_info = self.driver.execute_script("""
                        try {
                            if (window.jQuery && window.jQuery.fn && window.jQuery.fn.dataTable) {
                                const selector = '#TabelaListar';
                                const isDataTable = window.jQuery.fn.dataTable.isDataTable(selector);
                                if (!isDataTable) {
                                    return { status: 'not-initialized' };
                                }

                                const tabela = window.jQuery(selector).DataTable();
                                if (tabela) {
                                    const info = tabela.page ? tabela.page.info() : null;
                                    return {
                                        status: 'ready',
                                        rowsCurrent: tabela.rows({ page: 'current' }).data().length,
                                        recordsDisplay: info ? info.recordsDisplay : null,
                                        recordsTotal: info ? info.recordsTotal : null,
                                        draw: info ? info.draw : null
                                    };
                                }
                            }
                        } catch (e) {
                            return { status: 'error', error: String(e) };
                        }
                        return null;
                    """)

                    linhas_validas = []
                    mensagem_vazia = None
                    detalhes_brutos = []
                    for idx, linha in enumerate(linhas):
                        classes = (linha.get_attribute("class") or "").lower()
                        colunas = linha.find_elements(By.TAG_NAME, "td")
                        texto_linha = colunas[0].text.strip().lower() if colunas else ""

                        if "datatable" in classes and "empty" in classes:
                            mensagem_vazia = texto_linha
                            continue

                        if "datatables_empty" in classes or "dataTables_empty" in classes or "carregando" in texto_linha or "processando" in texto_linha:
                            mensagem_vazia = texto_linha
                            continue

                        if len(colunas) < 2:
                            if idx < 5:
                                detalhes_brutos.append({
                                    "classes": classes,
                                    "tds": len(colunas),
                                    "texto": [td.text.strip() for td in colunas]
                                })
                            continue

                        if idx < 5:
                            detalhes_brutos.append({
                                "classes": classes,
                                "tds": len(colunas),
                                "texto": [td.text.strip() for td in colunas]
                            })

                        linhas_validas.append(linha)

                    if not linhas_validas and mensagem_vazia:
                        if "nenhum" in mensagem_vazia or "sem registro" in mensagem_vazia:
                            self.logger.debug("Tabela confirmada vazia pela mensagem DataTables")
                            return 0

                    if not linhas_validas:
                        if detalhes_brutos:
                            self.logger.debug(f"Linhas encontradas mas não consideradas válidas: {detalhes_brutos}")
                        if datatable_info:
                            self.logger.debug(f"DataTables info (sem linhas válidas): {datatable_info}")

                    linhas_atual = len(linhas_validas)
                    if linhas_atual == 0 and datatable_info and datatable_info.get("rowsCurrent"):
                        linhas_atual = datatable_info.get("rowsCurrent")
                    
                    # Verificar estabilização
                    if linhas_atual == linhas_anterior and linhas_atual > 0:
                        contagem_estavel += 1
                        self.logger.debug(f"Linhas estáveis: {linhas_atual} (verificação {contagem_estavel}/3)")
                        
                        # Se o número de linhas ficou estável por 3 verificações consecutivas
                        if contagem_estavel >= 3:
                            self.logger.debug(f"Tabela estabilizada com {linhas_atual} linhas após {tentativas} tentativas")
                            return linhas_atual
                    else:
                        # Número de linhas mudou, resetar contador
                        contagem_estavel = 0
                        linhas_anterior = linhas_atual
                    
                    tentativas += 1
                    time.sleep(0.3)
                    
                except Exception as e:
                    self.logger.debug(f"Erro ao verificar carregamento: {str(e)}")
                    time.sleep(0.3)
            
            # Timeout atingido
            self.logger.warning(f"Timeout ao aguardar carregamento completo - {linhas_anterior} linhas detectadas")
            if ultimo_html:
                snippet = ultimo_html[:800].replace("\n", " ")
                self.logger.debug(f"Snapshot tbody (primeiros 800 chars): {snippet}")
            return linhas_anterior
            
        except Exception as e:
            self.logger.error(f"Erro ao aguardar carregamento: {str(e)}")
            return 0

    def extrair_dados_tabela(self, timeout=None):
        """
        Extrai dados da tabela atual
    Colunas relevantes: Código, Nome, CPF/CNPJ (demais colunas são ignoradas)
        """
        try:
            self.logger.debug("Extraindo dados da tabela atual")
            
            # Aguardar que a tabela carregue completamente
            num_linhas = self.aguardar_carregamento_completo_tabela(timeout=timeout)
            
            if num_linhas == 0:
                self.logger.warning("Nenhuma linha detectada na tabela")
                return []
            
            # Garantir que todas as linhas estejam renderizadas
            self.forcar_scroll_tabela()
            
            self.logger.debug(f"Iniciando extração de {num_linhas} linhas")
            
            # Localizar tabela novamente para extrair dados
            tabela = self.driver.find_element(By.ID, "TabelaListar")
            tbody = tabela.find_element(By.TAG_NAME, "tbody")
            linhas = tbody.find_elements(By.TAG_NAME, "tr")
            
            dados = []
            for linha in linhas:
                try:
                    classes = (linha.get_attribute("class") or "").lower()
                    if "datatables_empty" in classes or "dataTables_empty" in classes:
                        continue

                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if not colunas:
                        continue

                    valores = [td.text.strip() for td in colunas]
                    if not any(valores):
                        continue

                    def somente_digitos(valor):
                        if not valor:
                            return ""
                        return ''.join(ch for ch in valor if ch.isdigit())

                    def eh_codigo(valor):
                        digitos = somente_digitos(valor)
                        return bool(digitos) and 3 <= len(digitos) <= 8

                    def eh_documento(valor):
                        digitos = somente_digitos(valor)
                        return len(digitos) in (11, 14)

                    idx_codigo = next((i for i, v in enumerate(valores) if eh_codigo(v)), None)
                    idx_documento = next((i for i, v in enumerate(valores) if eh_documento(v)), None)

                    if idx_codigo is None:
                        idx_codigo = 0

                    if idx_documento is not None and idx_documento <= idx_codigo:
                        idx_documento = next(
                            (i for i, v in enumerate(valores[idx_codigo + 1:], start=idx_codigo + 1) if eh_documento(v)),
                            idx_documento
                        )

                    codigo = valores[idx_codigo].strip() if idx_codigo < len(valores) else ""

                    nome = ""
                    for i, valor in enumerate(valores):
                        if i == idx_codigo or i == idx_documento:
                            continue
                        if valor:
                            nome = valor.strip()
                            break

                    documento = valores[idx_documento].strip() if idx_documento is not None and idx_documento < len(valores) else ""

                    if nome and documento and nome == documento:
                        documento = ""

                    registro = {
                        'Código': codigo,
                        'Nome': nome,
                        'CPF/CNPJ': documento,
                        'Tipo': f"Pessoa {self.tipo_pessoa}"
                    }
                    dados.append(registro)
                except Exception as e:
                    self.logger.warning(f"Erro ao extrair linha: {str(e)}")
                    continue
            
            self.logger.debug(f"Extraídos {len(dados)} registros da página atual")
            return dados
            
        except Exception as e:
            self.logger.error(f"Erro ao extrair dados da tabela: {str(e)}")
            return []

    def clicar_proxima_pagina(self):
        """
        Clica no botão "Próximo" para navegar para a próxima página
        """
        try:
            self.logger.debug("Navegando para próxima página")
            
            # Localizar botão próximo usando o título
            btn_proximo = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//a[@title='Próximo']"))
            )
            
            # Verificar se está desabilitado
            classes = btn_proximo.get_attribute("class") or ""
            if "disabled" in classes:
                self.logger.info("Botão próximo está desabilitado - última página alcançada")
                return False
            
            # Usar JavaScript para clicar, evitando interceptação
            self.driver.execute_script("arguments[0].click();", btn_proximo)
            self.logger.debug("Clicou em Próximo via JavaScript")
            
            # Aguardar inicialização do carregamento AJAX
            time.sleep(1)
            
            return True
            
        except TimeoutException:
            self.logger.info("Botão próximo não encontrado - última página")
            return False
        except Exception as e:
            self.logger.error(f"Erro ao clicar em próxima página: {str(e)}")
            return False

    def capturar_todas_paginas(self):
        """
        Captura dados de TODAS as páginas até não haver mais páginas disponíveis
        """
        try:
            self.logger.info(f"Iniciando captura de todas páginas - Pessoa {self.tipo_pessoa}")
            
            todos_dados = []
            pagina = 1
            try:
                max_retries_config = max(1, int(os.getenv("CAPTURADOR_RETRY_TENTATIVAS", "3")))
            except ValueError:
                max_retries_config = 3

            try:
                tempo_base_retry_config = max(2, int(os.getenv("CAPTURADOR_RETRY_ESPERA_BASE", "5")))
            except ValueError:
                tempo_base_retry_config = 5

            ultimo_fingerprint = None
            ultimo_info = None
            repeticoes_pagina = 0
            
            while True:
                self.logger.info(f"Capturando página {pagina}")

                # Extrair dados da página atual
                dados_pagina = self.extrair_dados_tabela(timeout=min(self.timeout, 25))

                if not dados_pagina:
                    self.logger.warning(f"Página {pagina} sem dados - encerrando captura")
                    break
                
                info_atual = self.obter_info_datatable()
                total_parcial = len(todos_dados) + len(dados_pagina)
                ultima_pagina_confirmada, detalhes_ultima = self.confirmar_ultima_pagina(
                    info_atual, len(dados_pagina), total_parcial
                )

                # VALIDAÇÃO: Se capturou menos de 50 registros, pode ser carregamento incompleto
                # Tentar recarregar a página atual (exceto se for provável última página)
                if (
                    len(dados_pagina) < 50
                    and len(dados_pagina) > 0
                    and not ultima_pagina_confirmada
                ):
                    self.logger.warning(
                        f"Página {pagina}: apenas {len(dados_pagina)} registros capturados - possível carregamento incompleto"
                    )

                    melhor_resultado = list(dados_pagina)
                    max_retries = max_retries_config
                    tempo_base = tempo_base_retry_config

                    for tentativa_retry in range(1, max_retries + 1):
                        tempo_espera = min(tempo_base * tentativa_retry, 20)
                        self.logger.info(
                            f"Tentativa {tentativa_retry}/{max_retries}: preparando nova extração após {tempo_espera}s"
                        )

                        self.reforcar_carregamento_tabela(tentativa_retry)
                        time.sleep(tempo_espera)

                        dados_pagina_retry = self.extrair_dados_tabela(timeout=min(self.timeout, 40))

                        if len(dados_pagina_retry) >= 50:
                            self.logger.info(
                                f"Retry {tentativa_retry} recuperou {len(dados_pagina_retry)} registros - sequência completa"
                            )
                            melhor_resultado = dados_pagina_retry
                            break

                        if len(dados_pagina_retry) > len(melhor_resultado):
                            ganho = len(dados_pagina_retry) - len(melhor_resultado)
                            self.logger.info(
                                f"Retry {tentativa_retry} melhorou resultado para {len(dados_pagina_retry)} registros (+{ganho})"
                            )
                            melhor_resultado = dados_pagina_retry
                        else:
                            self.logger.warning(
                                f"Retry {tentativa_retry} não trouxe novos registros (mantidos {len(melhor_resultado)})"
                            )

                    if len(melhor_resultado) < 50:
                        self.logger.warning(
                            f"Página {pagina}: mantidos {len(melhor_resultado)} registros após {max_retries} tentativas"
                        )

                    dados_pagina = melhor_resultado
                    total_parcial = len(todos_dados) + len(dados_pagina)
                    info_atual = self.obter_info_datatable()
                    nova_confirmacao, detalhes_retry = self.confirmar_ultima_pagina(
                        info_atual, len(dados_pagina), total_parcial
                    )
                    if nova_confirmacao:
                        ultima_pagina_confirmada = True
                        detalhes_ultima = detalhes_retry

                if ultima_pagina_confirmada and detalhes_ultima and detalhes_ultima.get("razoes"):
                    razao_log = "; ".join(detalhes_ultima.get("razoes"))
                    self.logger.info(
                        f"Página {pagina}: DataTables indica última página ({razao_log})"
                    )

                fingerprint_atual = self.gerar_fingerprint_pagina(dados_pagina)

                fingerprint_igual = bool(
                    fingerprint_atual and ultimo_fingerprint and fingerprint_atual == ultimo_fingerprint
                )
                info_igual = False
                if info_atual and ultimo_info:
                    info_igual = (
                        info_atual.get("page") == ultimo_info.get("page") and
                        info_atual.get("start") == ultimo_info.get("start") and
                        info_atual.get("end") == ultimo_info.get("end")
                    )

                pagina_repetida = fingerprint_igual or (fingerprint_atual is None and info_igual)

                if pagina_repetida:
                    repeticoes_pagina += 1
                    limite = self.max_repeated_pages or 0
                    contexto_limite = f"/{limite}" if limite else ""
                    self.logger.warning(
                        f"Página {pagina}: conteúdo repetido detectado ({repeticoes_pagina}{contexto_limite})"
                    )
                else:
                    repeticoes_pagina = 0

                if fingerprint_atual is not None:
                    ultimo_fingerprint = fingerprint_atual
                elif ultimo_fingerprint is None:
                    ultimo_fingerprint = fingerprint_atual

                ultimo_info = info_atual or ultimo_info

                if (
                    self.max_repeated_pages
                    and fingerprint_igual
                    and repeticoes_pagina >= self.max_repeated_pages
                ):
                    self.logger.error(
                        f"Página {pagina}: repetição detectada {repeticoes_pagina} vezes consecutivas - encerrando captura para evitar loop"
                    )
                    break

                if repeticoes_pagina == 0:
                    todos_dados.extend(dados_pagina)
                    self.logger.info(
                        f"Página {pagina}: {len(dados_pagina)} registros capturados (Total: {len(todos_dados)})"
                    )
                else:
                    self.logger.info(
                        f"Página {pagina}: registros repetidos ignorados para evitar duplicidade"
                    )

                if ultima_pagina_confirmada:
                    if repeticoes_pagina == 0:
                        self.logger.info(
                            f"Página {pagina}: última página confirmada - encerrando captura"
                        )
                    else:
                        self.logger.info(
                            f"Página {pagina}: última página confirmada com conteúdo repetido - encerrando captura"
                        )
                    break
                
                # Tentar navegar para próxima página
                if not self.clicar_proxima_pagina():
                    self.logger.info(f"Última página alcançada (página {pagina})")
                    break
                
                pagina += 1
            
            self.logger.info(f"Captura concluída - Pessoa {self.tipo_pessoa}: {pagina} páginas, {len(todos_dados)} registros totais")
            return pd.DataFrame(todos_dados)
            
        except Exception as e:
            self.logger.error(f"Erro durante captura de páginas: {str(e)}")
            return pd.DataFrame()

    def executar_captura_completa(self, usuario, senha):
        """
        Executa o fluxo completo de captura
        """
        try:
            self.logger.info(f"=== Iniciando captura Pessoa {self.tipo_pessoa} ===")
            
            # Login
            if not self.fazer_login(usuario, senha):
                self.logger.error("Falha no login")
                return None
            
            # Navegar para Cadastro
            if not self.navegar_menu_cadastro():
                self.logger.error("Falha ao abrir menu Cadastro")
                return None
            
            # Clicar em Pessoa Física/Jurídica
            if not self.clicar_tipo_pessoa():
                self.logger.error(f"Falha ao abrir Pessoa {self.tipo_pessoa}")
                return None
            
            # Configurar 50 registros
            if not self.configurar_50_registros():
                self.logger.error("Falha ao configurar quantidade de registros")
                return None
            
            # Capturar todas as páginas disponíveis
            df = self.capturar_todas_paginas()
            
            if df.empty:
                self.logger.warning("Nenhum dado capturado")
                return None
            
            self.logger.info(f"=== Captura Pessoa {self.tipo_pessoa} concluída: {len(df)} registros ===")
            return df
            
        except Exception as e:
            self.logger.error(f"Erro na captura completa: {str(e)}")
            return None
        finally:
            self.fechar()
            
    def fechar(self):
        """Fecha o navegador"""
        try:
            if hasattr(self, 'driver'):
                self.driver.quit()
                self.logger.debug("Navegador fechado")
        except Exception as e:
            self.logger.error(f"Erro ao fechar: {str(e)}")


def capturar_tipo_pessoa(tipo_pessoa, usuario, senha, headless=False, execucao_id=None,
                          output_dir=None, partial_interval=None, max_repeated_pages=None):
    """
    Função auxiliar para captura de um tipo de pessoa
    Usada para execução paralela
    """
    capturador = CapturadorPessoasAcadeOne(
        headless=headless,
        tipo_pessoa=tipo_pessoa,
        execucao_id=execucao_id,
        output_dir=output_dir,
        partial_interval=partial_interval,
        max_repeated_pages=max_repeated_pages
    )
    return capturador.executar_captura_completa(usuario, senha)


def main():
    """Função principal"""
    print("🎯 CAPTURADOR DE PESSOAS - ACADE ONE")
    print("="*60)
    print("\n📋 Este script captura dados de:")
    print("   • Pessoa Física (TODAS as páginas disponíveis)")
    print("   • Pessoa Jurídica (TODAS as páginas disponíveis)")
    print("\n⚡ Execução paralela disponível para otimizar tempo")
    print("="*60)
    
    # Obter credenciais
    usuario = os.getenv('ACADE_USUARIO')
    senha = os.getenv('ACADE_SENHA')
    
    if not usuario or not senha:
        print("\n⚠️  Credenciais não encontradas no .env")
        usuario = input("Usuário: ")
        senha = input("Senha: ")
    
    # Configurar modo de execução
    print("\n👀 Modo de execução:")
    modo = input("Executar em modo visível? (s/n) [s]: ").strip().lower()
    headless = modo.startswith('n')
    
    if headless:
        print("   ✅ Modo: Navegador oculto")
    else:
        print("   ✅ Modo: Navegador visível")
    
    # Perguntar sobre execução paralela
    print("\n⚡ Execução paralela:")
    paralelo_input = input("Executar capturas em paralelo? (s/n) [n]: ").strip().lower()
    executar_paralelo = paralelo_input.startswith('s')
    
    if executar_paralelo:
        print("   ✅ Modo: Paralelo (2 navegadores simultâneos)")
    else:
        print("   ✅ Modo: Sequencial (um tipo por vez)")
    
    print(f"\n🚀 Iniciando captura...")
    print()
    
    execucao_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.getenv('OUTPUT_DIR', './arquivos_auxiliares/')
    os.makedirs(output_dir, exist_ok=True)

    intervalo_env = os.getenv("CAPTURADOR_SERIALIZACAO_INTERVALO")
    try:
        intervalo_parcial = int(intervalo_env) if intervalo_env else None
    except ValueError:
        intervalo_parcial = None

    repeticao_env = os.getenv("CAPTURADOR_MAX_PAGINA_REPETIDA")
    try:
        max_repeticoes = int(repeticao_env) if repeticao_env else None
    except ValueError:
        max_repeticoes = None

    kwargs_captura = {
        "execucao_id": execucao_id,
        "output_dir": output_dir,
        "partial_interval": intervalo_parcial,
        "max_repeated_pages": max_repeticoes,
    }

    inicio = time.time()
    
    if executar_paralelo and not headless:
        print("⚠️  ATENÇÃO: Modo paralelo com navegador visível abrirá 2 janelas simultâneas")
        print("    Para melhor experiência, recomendamos executar em modo sequencial")
        confirmacao = input("    Deseja continuar com execução paralela? (s/n) [n]: ").strip().lower()
        if not confirmacao.startswith('s'):
            print("    ✅ Alterando para modo sequencial")
            executar_paralelo = False
    
    df_fisica = None
    df_juridica = None
    
    if executar_paralelo:
        # Execução paralela apenas em headless para evitar conflitos no ChromeDriver
        if headless:
            print("🔄 Executando capturas em paralelo...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_fisica = executor.submit(
                    capturar_tipo_pessoa, "Física", usuario, senha, headless, **kwargs_captura
                )
                future_juridica = executor.submit(
                    capturar_tipo_pessoa, "Jurídica", usuario, senha, headless, **kwargs_captura
                )
                
                df_fisica = future_fisica.result()
                df_juridica = future_juridica.result()
        else:
            # Sequencial com pequeno delay para evitar conflito no download do driver
            print("🔄 Executando capturas em paralelo (com inicialização sequencial)...")
            print("🔄 Iniciando captura Pessoa Física...")
            df_fisica = capturar_tipo_pessoa("Física", usuario, senha, headless, **kwargs_captura)
            
            print("\n🔄 Iniciando captura Pessoa Jurídica...")
            df_juridica = capturar_tipo_pessoa("Jurídica", usuario, senha, headless, **kwargs_captura)
    else:
        # Execução sequencial
        print("🔄 Executando captura Pessoa Física...")
        df_fisica = capturar_tipo_pessoa("Física", usuario, senha, headless, **kwargs_captura)
        
        print("\n🔄 Executando captura Pessoa Jurídica...")
        df_juridica = capturar_tipo_pessoa("Jurídica", usuario, senha, headless, **kwargs_captura)
    
    fim = time.time()
    tempo_total = fim - inicio
    
    # Consolidar dados
    dataframes = []
    if df_fisica is not None and not df_fisica.empty:
        dataframes.append(df_fisica)
        print(f"\n✅ Pessoa Física: {len(df_fisica)} registros capturados")
    else:
        print("\n❌ Pessoa Física: Nenhum dado capturado")
    
    if df_juridica is not None and not df_juridica.empty:
        dataframes.append(df_juridica)
        print(f"✅ Pessoa Jurídica: {len(df_juridica)} registros capturados")
    else:
        print("❌ Pessoa Jurídica: Nenhum dado capturado")
    
    if not dataframes:
        print(f"\n❌ ERRO: Nenhum dado foi capturado!")
        print("📄 Verifique o arquivo de log: capturador_pessoas.log")
        return
    
    # Concatenar DataFrames
    df_consolidado = pd.concat(dataframes, ignore_index=True)

    # Remover coluna legada "Perfil" caso ainda exista em alguma captura
    if 'Perfil' in df_consolidado.columns:
        df_consolidado = df_consolidado.drop(columns=['Perfil'])
    
    # Salvar arquivo CSV principal
    arquivo_principal = "pessoas_cadastradas.csv"
    caminho_principal = os.path.join(output_dir, arquivo_principal)
    df_consolidado.to_csv(caminho_principal, index=False, encoding='utf-8-sig')
    
    print(f"\n🎉 SUCESSO!")
    print(f"📄 Arquivo principal: {caminho_principal}")
    print(f"📊 Total de registros: {len(df_consolidado)}")
    print(f"⏱️  Tempo de execução: {tempo_total:.1f}s")
    
    # Preview dos dados
    print(f"\n🔍 Preview dos dados:")
    print(df_consolidado.head(10).to_string(index=False))
    
    # Estatísticas por tipo
    print(f"\n📈 Estatísticas:")
    print(df_consolidado['Tipo'].value_counts())


if __name__ == "__main__":
    main()
