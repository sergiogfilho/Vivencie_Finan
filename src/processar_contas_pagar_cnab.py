#!/usr/bin/env python3
"""
PROCESSADOR CONTAS A PAGAR → CNAB 240 SICOOB
Lê arquivo de contas a pagar e gera arquivos CNAB 240 agrupados por Centro de Custo
conforme especificações Sicoob versão 08.1+
"""

import argparse
import logging
import sys
import re
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from gerador_cnab240 import (
    Pagamento, Favorecido, Empresa, DadosPix, DadosBoleto, Pagador,
    Modalidade, TipoChavePix, BankProfile, ModalidadeConfig, logger
)
from gerador_cnab240_motor import MotorCNAB240
from gerador_cnab240_segmentos import parse_emvco_pix
from validador_pagamentos import ValidadorPagamentos, TipoPagamento, TipoChavePix as TipoChavePixValidador


PIX_TELEFONE_REGEX = re.compile(r"^\D*([1-9]\d)\D*(9)\D*(\d{4})\D*(\d{4})\D*$")
PIX_TELEFONE_SEARCH_REGEX = re.compile(r"([1-9]\d)\D*(9)\D*(\d{4})\D*(\d{4})")


class ProcessadorContasPagar:
    """Processa arquivo de contas a pagar e gera CNAB 240 por Centro de Custo"""

    MODALIDADES_PERMITIDAS = {
        Modalidade.PIX_CHAVE,
        Modalidade.PIX_QR_DINAMICO,
        Modalidade.CREDITO_CONTA,
        Modalidade.TED,
        Modalidade.BOLETO,
        Modalidade.CONVENIO,  # Convênios, tributos e concessionárias (48 dígitos)
    }
    
    def __init__(self, 
                 path_contas_pagar: str,
                 path_bancos: str,
                 path_pessoas: str):
        """
        Inicializa processador
        
        Args:
            path_contas_pagar: Caminho para CSV de contas a pagar (relatorio_contas_pagar.csv)
            path_bancos: Caminho para CSV de dados bancários do cedente (bancos.csv)
            path_pessoas: Caminho para CSV de pessoas cadastradas
        """
        self.path_contas_pagar = path_contas_pagar
        self.path_bancos = path_bancos
        self.path_pessoas = path_pessoas
        
        # DataFrames
        self.df_contas = None
        self.df_bancos = None
        self.df_pessoas = None
        
        # Mapeamentos
        self.map_pessoas_nome = {}  # Nome → dados completos
        self.map_pessoas_cpf = {}  # CPF/CNPJ → dados completos
        
        # Controle de pagamentos não incluídos
        self.pagamentos_nao_incluidos = []
        self.colunas_contas_sem_obs = []
        
        logger.info("Processador CNAB 240 Sicoob inicializado")
    
    def carregar_dados(self):
        """Carrega todos os arquivos CSV necessários"""
        logger.info("Carregando arquivos CSV...")
        
        # Contas a pagar - todos os campos como string
        self.df_contas = pd.read_csv(
            self.path_contas_pagar, 
            encoding='utf-8',
            dtype=str
        )
        self.colunas_contas_sem_obs = [col for col in self.df_contas.columns if col != 'Obs.']
        # Converte campo Valor para float após leitura
        if 'Valor' in self.df_contas.columns:
            def _convert_valor(v):
                if v is None:
                    return 0.0
                if isinstance(v, float) and pd.isna(v):
                    return 0.0
                texto = str(v).strip()
                if not texto or texto.lower() in {"nan", "none"}:
                    return 0.0
                texto = texto.replace('.', '').replace(',', '.')
                try:
                    return float(texto)
                except ValueError:
                    logger.warning(f"Valor inválido encontrado no CSV: '{v}'")
                    return 0.0
            self.df_contas['Valor'] = self.df_contas['Valor'].apply(_convert_valor)
        logger.info(f"Contas a pagar carregadas: {len(self.df_contas)} registros")
        
        # Bancos (cedentes) - todos os campos como string
        self.df_bancos = pd.read_csv(
            self.path_bancos, 
            encoding='utf-8',
            dtype=str
        )
        logger.info(f"Bancos carregados: {len(self.df_bancos)} registros")
        
        # Pessoas cadastradas - todos os campos como string
        self.df_pessoas = pd.read_csv(
            self.path_pessoas, 
            encoding='utf-8',
            dtype=str
        )
        logger.info(f"Pessoas cadastradas carregadas: {len(self.df_pessoas)} registros")
        
        self._construir_mapeamentos()
    
    def _construir_mapeamentos(self):
        """Constrói dicionários de mapeamento rápido"""
        logger.info("Construindo mapeamentos...")
        
        # Mapa de pessoas por nome e CPF/CNPJ
        for _, row in self.df_pessoas.iterrows():
            nome = str(row.get('Nome', '')).upper().strip()
            cpf_cnpj = self._limpar_cpf_cnpj(row.get('CPF/CNPJ', ''))
            
            dados = row.to_dict()
            
            if nome:
                self.map_pessoas_nome[nome] = dados
            if cpf_cnpj:
                self.map_pessoas_cpf[cpf_cnpj] = dados
        
        logger.info(f"Mapa de pessoas criado: {len(self.map_pessoas_nome)} nomes, {len(self.map_pessoas_cpf)} CPF/CNPJ")
    
    def _registrar_pagamento_nao_incluido(self, row: pd.Series, motivo: str) -> None:
        """Armazena informações de pagamentos que não entraram no CNAB."""
        colunas_base = self.colunas_contas_sem_obs or [col for col in self.df_contas.columns if col != 'Obs.']
        registro = {}
        for col in colunas_base:
            valor = row.get(col) if isinstance(row, pd.Series) else row.get(col, '')
            registro[col] = self._sanitizar_str(valor)
        registro['Motivo Exclusao'] = motivo
        self.pagamentos_nao_incluidos.append(registro)
    
    def _mapear_tipo_pagamento_para_modalidade(self, tipo_pagamento: TipoPagamento) -> Optional[Modalidade]:
        """Mapeia TipoPagamento do validador para Modalidade do CNAB"""
        mapeamento = {
            TipoPagamento.PIX: Modalidade.PIX_CHAVE,
            TipoPagamento.TED: Modalidade.TED,
            TipoPagamento.TRANSFERENCIA: Modalidade.CREDITO_CONTA,
            TipoPagamento.BOLETO: Modalidade.BOLETO,
            TipoPagamento.CONVENIO: Modalidade.CONVENIO,  # Convênios/Tributos/Concessionárias
        }
        return mapeamento.get(tipo_pagamento)
    
    def _mapear_tipo_chave_pix(self, tipo_chave_validador: TipoChavePixValidador) -> TipoChavePix:
        """Mapeia TipoChavePix do validador para TipoChavePix do CNAB"""
        mapeamento = {
            TipoChavePixValidador.CPF: TipoChavePix.CPF,
            TipoChavePixValidador.CNPJ: TipoChavePix.CNPJ,
            TipoChavePixValidador.TELEFONE: TipoChavePix.TELEFONE,
            TipoChavePixValidador.EMAIL: TipoChavePix.EMAIL,
            TipoChavePixValidador.EVP: TipoChavePix.ALEATORIA,
            TipoChavePixValidador.QR_CODE: TipoChavePix.ALEATORIA,
        }
        return mapeamento.get(tipo_chave_validador, TipoChavePix.ALEATORIA)

    def _sanitizar_str(self, valor, default: str = "") -> str:
        """Normaliza strings removendo espaços e tratando valores ausentes/nan"""
        if valor is None:
            return default
        if isinstance(valor, float) and pd.isna(valor):
            return default
        valor_str = str(valor).strip()
        if not valor_str or valor_str.lower() in {"nan", "none"}:
            return default
        return valor_str
    
    def _limpar_cpf_cnpj(self, valor) -> str:
        """Remove pontuação de CPF/CNPJ"""
        valor_str = self._sanitizar_str(valor)
        if not valor_str:
            return ""
        return ''.join(c for c in valor_str if c.isdigit())

    def _documento_valido(self, valor: Optional[str]) -> bool:
        documento = self._limpar_cpf_cnpj(valor)
        if not documento:
            return False
        if len(documento) == 11:
            return self._cpf_valido(documento)
        if len(documento) == 14:
            return self._cnpj_valido(documento)
        return False

    def _cpf_valido(self, cpf: str) -> bool:
        if len(cpf) != 11 or cpf == cpf[0] * 11:
            return False
        try:
            for i in range(9, 11):
                soma = sum(int(cpf[num]) * (i + 1 - num) for num in range(0, i))
                digito = (soma * 10) % 11
                if digito == 10:
                    digito = 0
                if digito != int(cpf[i]):
                    return False
            return True
        except ValueError:
            return False

    def _cnpj_valido(self, cnpj: str) -> bool:
        if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
            return False
        pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        pesos2 = [6] + pesos1
        try:
            soma1 = sum(int(dig) * peso for dig, peso in zip(cnpj[:12], pesos1))
            digito1 = 11 - (soma1 % 11)
            digito1 = 0 if digito1 >= 10 else digito1
            soma2 = sum(int(dig) * peso for dig, peso in zip(cnpj[:12] + str(digito1), pesos2))
            digito2 = 11 - (soma2 % 11)
            digito2 = 0 if digito2 >= 10 else digito2
            return cnpj[-2:] == f"{digito1}{digito2}"
        except ValueError:
            return False

    def _extrair_cnpj_cpf_pagador_de_obs(self, obs: str) -> Optional[str]:
        """
        Extrai CPF/CNPJ do pagador (sacado) do campo obs.
        Formato esperado: cnpj-cpf_pagador:XXXXXXXXXXX (dígitos sem formatação)
        
        Returns:
            CPF/CNPJ limpo (apenas dígitos) ou None se não encontrado
        """
        if not obs:
            return None
        
        # Padrão: cnpj-cpf_pagador: seguido de dígitos
        pattern = r'cnpj-cpf_pagador:\s*(\d+)'
        match = re.search(pattern, obs, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return None

    def _extrair_chave_pix_por_telefone(self, texto: str) -> Optional[str]:
        if not texto:
            return None
        # Procura fragmentos que combinem com o padrão informado
        busca = PIX_TELEFONE_SEARCH_REGEX.search(texto)
        if not busca:
            return None
        candidato = busca.group(0)
        match = PIX_TELEFONE_REGEX.match(candidato)
        if not match:
            return None
        return ''.join(match.groups())
    
    def processar(self) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        Processa contas a pagar e gera arquivos CNAB 240 por Conta e Agência
        
        Returns:
            Dicionário {chave_conta_agencia: [arquivo_cnab, ...]}
        """
        logger.info("Iniciando processamento de contas a pagar...")
        self.pagamentos_nao_incluidos.clear()
        
        # Faz merge com bancos.csv para obter conta e agência
        df_merged = self.df_contas.merge(
            self.df_bancos[['empreendimentos', 'conta', 'agencia', 'banco', 'convenio', 'cnpj_cedente', 'nome_cedente']],
            left_on='Centro Custo',
            right_on='empreendimentos',
            how='left'
        )
        
        # Remove registros sem match
        df_merged = df_merged.dropna(subset=['conta', 'agencia'])
        
        if len(df_merged) == 0:
            logger.warning("Nenhum registro com conta/agência encontrado após merge")
            return {}
        
        # Agrupa por conta e agência
        grupos = df_merged.groupby(['conta', 'agencia'])
        
        resultados = {}
        
        for (conta, agencia), df_grupo in grupos:
            chave = f"{conta}_{agencia}"
            logger.info(f"\n{'='*80}")
            logger.info(f"Processando Conta: {conta} | Agência: {agencia} ({len(df_grupo)} conta(s))")
            
            # Pega o primeiro registro para extrair dados do cedente
            primeira_linha = df_grupo.iloc[0]
            dados_cedente = {
                'codigo': self._sanitizar_str(primeira_linha.get('banco'), '756'),
                'conta': self._sanitizar_str(primeira_linha.get('conta')),
                'agencia': self._sanitizar_str(primeira_linha.get('agencia')),
                'convenio': self._sanitizar_str(primeira_linha.get('convenio')),
                'cnpj_cedente': self._sanitizar_str(primeira_linha.get('cnpj_cedente')),
                'nome_cedente': self._sanitizar_str(primeira_linha.get('nome_cedente')),
                'empreendimentos': self._sanitizar_str(primeira_linha.get('Centro Custo'), chave)
            }
            
            # Cria configuração do banco e empresa
            try:
                bank_profile, empresa = self._criar_configuracao_sicoob(dados_cedente, chave)
            except Exception as e:
                logger.error(f"Erro ao criar configuração para {chave}: {e}")
                continue
            
            # Cria motor CNAB com configuração dinâmica
            try:
                motor = MotorCNAB240.from_objects(bank_profile, empresa)
            except Exception as e:
                logger.error(f"Erro ao inicializar motor para {chave}: {e}")
                import traceback
                traceback.print_exc()
                continue
            
            # Converte contas em lista de Pagamento
            pagamentos = []
            for idx, row in df_grupo.iterrows():
                try:
                    pagamento, motivo_exclusao = self._criar_pagamento(row, dados_cedente)
                    if pagamento:
                        pagamentos.append(pagamento)
                    else:
                        self._registrar_pagamento_nao_incluido(row, motivo_exclusao or "Pagamento não elegível")
                except Exception as e:
                    logger.error(f"Erro ao criar pagamento (Lancto: {row.get('Lancto', 'N/A')}): {e}")
                    import traceback
                    traceback.print_exc()
                    self._registrar_pagamento_nao_incluido(row, f"Erro inesperado: {e}")
                    continue
            
            if not pagamentos:
                logger.warning(f"Nenhum pagamento válido para {chave}")
                continue
            
            # Gera remessa
            try:
                conteudo, _ = motor.gerar_remessa(pagamentos)
                
                # Exporta arquivos com novo formato: YYYYMMDD_CONTA_AGENCIA.txt
                timestamp = datetime.now().strftime("%Y%m%d")
                conta_safe = str(conta).replace('-', '').replace(' ', '')
                agencia_safe = str(agencia).replace('-', '').replace(' ', '')
                
                arquivo_cnab = motor.exportar_arquivo(
                    conteudo, 
                    f"{timestamp}_{conta_safe}_{agencia_safe}.txt"
                )
                
                # Armazena resultados
                if chave not in resultados:
                    resultados[chave] = []
                
                resultados[chave].append(arquivo_cnab)
                
                logger.info(f"✓ Remessa gerada com sucesso para Conta: {conta} | Agência: {agencia}")
                logger.info(f"  - Arquivo: {arquivo_cnab}")
                
            except Exception as e:
                logger.error(f"Erro ao gerar remessa para {chave}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        logger.info(f"\n{'='*80}")
        logger.info(f"Processamento concluído. {len(resultados)} conta(s)/agência(s) processada(s)")

        if self.pagamentos_nao_incluidos:
            print("\nALERTA: Pagamentos não incluídos nos CNAB (apenas campos do arquivo original):")
            df_alerta = pd.DataFrame(self.pagamentos_nao_incluidos)
            print(df_alerta.to_string(index=False))
        else:
            print("\nTodos os pagamentos elegíveis foram incluídos nos CNAB gerados.")
        
        return resultados
    
    def _buscar_cedente_por_empreendimento(self, empreendimento: str) -> Optional[Dict]:
        """
        Busca dados do cedente no arquivo bancos.csv pelo campo empreendimentos
        
        Args:
            empreendimento: Nome do empreendimento (Centro Custo)
        
        Returns:
            Dicionário com dados do cedente ou None
        """
        empreendimento_norm = empreendimento.strip()
        
        # Busca exata
        mask = self.df_bancos['empreendimentos'].str.strip() == empreendimento_norm
        resultados = self.df_bancos[mask]
        
        if len(resultados) > 0:
            return resultados.iloc[0].to_dict()
        
        logger.warning(f"Cedente não encontrado para empreendimento '{empreendimento}'")
        return None
    
    def _criar_configuracao_sicoob(self, dados_cedente: Dict, chave_identificacao: str) -> Tuple[BankProfile, Empresa]:
        """
        Cria objetos BankProfile e Empresa com base nos dados do cedente
        Configuração padrão Sicoob (756)
        
        Args:
            dados_cedente: Dados do cedente do arquivo bancos.csv
            chave_identificacao: Chave de identificação (conta_agencia ou centro custo)
        
        Returns:
            Tupla (BankProfile, Empresa)
        """
        # Configuração padrão Sicoob
        codigo_banco = str(dados_cedente.get('codigo', '756')).zfill(3)
        
        # Modalidades padrão Sicoob conforme documentação
        modalidades = {
            Modalidade.CREDITO_CONTA: ModalidadeConfig(
                forma_lancamento="01",
                camara_centralizadora="000",
                versao_layout="045",  # Layout TED/PIX/Transferências
                tipo_compromisso="01"
            ),
            Modalidade.TED: ModalidadeConfig(
                forma_lancamento="41",
                camara_centralizadora="018",
                versao_layout="045",  # Layout TED/PIX/Transferências
                tipo_compromisso="01",
                finalidade_bacen="00010"
            ),
            Modalidade.PIX_CHAVE: ModalidadeConfig(
                forma_lancamento="45",
                camara_centralizadora="009",
                versao_layout="045",  # Layout TED/PIX/Transferências
                tipo_compromisso="01"
            ),
            Modalidade.PIX_QR_DINAMICO: ModalidadeConfig(
                forma_lancamento="47",
                camara_centralizadora="009",
                versao_layout="045",  # Layout TED/PIX/Transferências
                tipo_compromisso="01"
            ),
            Modalidade.BOLETO: ModalidadeConfig(
                forma_lancamento="31",
                camara_centralizadora="000",
                versao_layout="040",  # Layout Boletos/Cobrança (Segmentos J + J-52)
                tipo_compromisso="02"
            ),
            Modalidade.CONVENIO: ModalidadeConfig(
                forma_lancamento="11",  # G029 - Pagamento de Contas e Tributos com Código de Barras (Manual Sicoob)
                camara_centralizadora="000",
                versao_layout="012",  # Layout Convênios/Tributos (Segmento O)
                tipo_compromisso="11"   # Pagamento de Convênios
            )
        }
        
        bank_profile = BankProfile(
            codigo_banco_compensacao=codigo_banco,
            nome_banco="BANCO SICOOB S.A." if codigo_banco == "756" else f"BANCO {codigo_banco}",
            versao_layout_arquivo="087",  # G018 - Layout versão 08.7 release 1
            tipo_servico_pagamento="20",  # Pagamento Fornecedores
            modalidades=modalidades,
            densidade_gravacao="01600",
            exige_ispb_favorecido=False,
            permite_data_futura=True,
            observacoes=f"Agrupamento: {chave_identificacao}"
        )
        
        # Extrai dados da empresa do arquivo bancos.csv
        cnpj_cedente_raw = self._sanitizar_str(dados_cedente.get('cnpj_cedente'))
        cnpj_cedente = self._limpar_cpf_cnpj(cnpj_cedente_raw)
        
        if not cnpj_cedente or len(cnpj_cedente) < 11:
            logger.warning(f"CNPJ inválido/vazio para {chave_identificacao} - CORRIJA O CADASTRO!")
            cnpj_cedente = "00000000000000"  # Placeholder - DEVE SER CORRIGIDO NO CADASTRO
        
        # Extrai agência e conta (formato: "conta-dv" ou "conta")
        conta_completa = self._sanitizar_str(dados_cedente.get('conta'))
        if '-' in conta_completa:
            numero_conta, dv_conta = conta_completa.rsplit('-', 1)
        else:
            numero_conta = conta_completa
            dv_conta = '0'
        
        tipo_inscricao = 2 if len(cnpj_cedente) == 14 else 1
        
        # Nome da empresa: usa APENAS nome_cedente do arquivo bancos.csv
        nome_empresa = self._sanitizar_str(dados_cedente.get('nome_cedente'), chave_identificacao)
        if not nome_empresa:
            logger.warning(f"Nome do cedente vazio para {chave_identificacao} - usando placeholder")
            nome_empresa = "CEDENTE NAO CADASTRADO"
        nome_empresa = nome_empresa[:30]
        
        convenio = self._sanitizar_str(dados_cedente.get('convenio'), '000000') or '000000'
        agencia_mantenedora = self._sanitizar_str(dados_cedente.get('agencia'), '0000') or '0000'
        
        empresa = Empresa(
            tipo_inscricao=tipo_inscricao,
            numero_inscricao=cnpj_cedente,
            convenio=convenio.zfill(6)[:20],
            agencia_mantenedora=agencia_mantenedora.zfill(4),
            dv_agencia=' ',
            numero_conta=numero_conta.strip().zfill(12),
            dv_conta=dv_conta.strip(),
            dv_agencia_conta=' ',
            nome_empresa=nome_empresa
        )
        
        return bank_profile, empresa
    
    def _criar_pagamento(self, row: pd.Series, dados_cedente: Dict) -> Tuple[Optional[Pagamento], Optional[str]]:
        """
        Cria objeto Pagamento usando ValidadorPagamentos para interpretação de obs
        """
        # Extrai dados básicos (sanitizados)
        beneficiado_nome = self._sanitizar_str(row.get('Beneficiado'), default='SEM NOME')
        lancto = self._sanitizar_str(row.get('Lancto')) or '000000'
        doc = self._sanitizar_str(row.get('Doc')) or '000000'
        obs = self._sanitizar_str(row.get('Obs.'), default="")
        
        # Monta mensagem para CNAB com Comp., Lancto, Conta, Doc
        comp = self._sanitizar_str(row.get('Comp.'), default='')
        conta = self._sanitizar_str(row.get('Conta'), default='')
        mensagem_cnab = ' '.join(part for part in [comp, lancto, conta, doc] if part).strip()
        
        # Obtém valor (já convertido para float no carregamento)
        valor_reais = row.get('Valor', 0.0)
        if isinstance(valor_reais, str):
            # Fallback caso não tenha sido convertido
            valor_limpo = valor_reais.replace('.', '').replace(',', '.')
            try:
                valor_reais = float(valor_limpo)
            except ValueError:
                logger.warning(f"Valor inválido para Lancto {lancto}: '{valor_reais}'")
                return None, "Valor inválido"
        
        # CONVERSÃO SEGURA PARA CENTAVOS
        # Usa Decimal para evitar problemas de precisão de ponto flutuante
        # Exemplo: 132.95 * 100 pode resultar em 13294.999999999998 com float
        # e int() trunca (não arredonda), gerando 13294 ao invés de 13295
        try:
            valor_decimal = Decimal(str(valor_reais))
            valor_centavos_decimal = valor_decimal * Decimal('100')
            # Arredonda para o inteiro mais próximo (banker's rounding)
            valor_centavos = int(valor_centavos_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        except Exception as e:
            logger.warning(f"Erro na conversão de valor para Lancto {lancto}: {valor_reais} - {e}")
            return None, f"Erro na conversão de valor: {e}"
        
        if valor_centavos <= 0:
            logger.warning(f"Valor zero ou negativo para Lancto {lancto}")
            return None, "Valor zero ou negativo"
        
        # Converte data
        vencto_str = self._sanitizar_str(row.get('Vencto'))
        data_pagamento = self._converter_data(vencto_str)
        if not data_pagamento:
            logger.warning(f"Data inválida para Lancto {lancto}: '{vencto_str}'")
            return None, "Data de pagamento inválida"
        
        # NOVA ETAPA: Interpreta obs usando ValidadorPagamentos
        resultado_validacao = ValidadorPagamentos.interpretar(obs)
        tipo_pagamento = resultado_validacao.get("tipo_pagamento")
        validacao = resultado_validacao.get("validacao", {})
        dados_interpretados = resultado_validacao.get("dados", {})
        
        # Verifica se interpretação foi válida
        if not validacao.get("valido", False):
            logger.warning(f"Lancto {lancto}: Obs sem modalidade válida - {validacao.get('mensagem', 'erro desconhecido')}")
            return None, f"Obs inválida: {validacao.get('mensagem')}"
        
        if not tipo_pagamento:
            logger.warning(f"Lancto {lancto}: Tipo de pagamento não identificado na obs")
            return None, "Obs sem tipo de pagamento (use trigger: PIX, TED, TRANSF ou BTO)"
        
        # Mapeia TipoPagamento → Modalidade
        modalidade = self._mapear_tipo_pagamento_para_modalidade(tipo_pagamento)
        if not modalidade:
            logger.warning(f"Lancto {lancto}: Tipo de pagamento {tipo_pagamento.value} não mapeado")
            return None, f"Tipo de pagamento {tipo_pagamento.value} não suportado"
        
        if modalidade not in self.MODALIDADES_PERMITIDAS:
            logger.info(f"Lancto {lancto}: Modalidade {modalidade.value} não permitida para o CNAB atual")
            return None, f"Modalidade {modalidade.value} não permitida"
        
        # Obtém CPF/CNPJ do favorecido
        cpf_cnpj_favorecido = None
        chave_pix_extraida = None
        tipo_chave_extraida = None
        
        # Para PIX, extrai chave e tipo
        if tipo_pagamento == TipoPagamento.PIX:
            tipo_chave_validador = dados_interpretados.get('tipo_chave')
            chave_pix_extraida = dados_interpretados.get('chave_pix')
            tipo_chave_extraida = tipo_chave_validador
            cpf_cnpj_obs = dados_interpretados.get('cpf_cnpj_obs')  # CPF/CNPJ explícito da obs
            
            # PRIORIDADE 1: CPF/CNPJ explicitamente mencionado na obs (cpf: ou cnpj:)
            if cpf_cnpj_obs:
                cpf_cnpj_favorecido = cpf_cnpj_obs
                logger.info(f"Lancto {lancto}: CPF/CNPJ explícito da obs tem prioridade: {cpf_cnpj_favorecido}")
            # PRIORIDADE 2: Se chave PIX for CPF ou CNPJ, usa ela
            elif tipo_chave_validador == TipoChavePixValidador.CPF:
                cpf_cnpj_favorecido = chave_pix_extraida
                logger.info(f"Lancto {lancto}: CPF extraído de chave PIX: {cpf_cnpj_favorecido}")
            elif tipo_chave_validador == TipoChavePixValidador.CNPJ:
                cpf_cnpj_favorecido = chave_pix_extraida
                logger.info(f"Lancto {lancto}: CNPJ extraído de chave PIX: {cpf_cnpj_favorecido}")
        
        # IMPORTANTE: Para CONVENIO e BOLETO (tributos/concessionárias), o CPF/CNPJ do favorecido 
        # NÃO é obrigatório nos segmentos O e N do CNAB240 (apenas nome da concessionária/órgão)
        # Somente valida CPF/CNPJ para modalidades que requerem: PIX, TED, CREDITO_CONTA
        modalidades_que_requerem_cpf_cnpj = {
            Modalidade.PIX_CHAVE, 
            Modalidade.PIX_QR_DINAMICO, 
            Modalidade.CREDITO_CONTA, 
            Modalidade.TED
        }
        
        if modalidade in modalidades_que_requerem_cpf_cnpj:
            # Para TED/TRANSFERENCIA ou PIX com chave não-documento, busca CPF/CNPJ no cadastro
            if not cpf_cnpj_favorecido:
                dados_pessoa = self._buscar_pessoa_por_nome(beneficiado_nome)
                if dados_pessoa:
                    cpf_cnpj_favorecido = self._limpar_cpf_cnpj(dados_pessoa.get('CPF/CNPJ', ''))
                    logger.info(f"Lancto {lancto}: CPF/CNPJ do beneficiado '{beneficiado_nome}' obtido do cadastro → {cpf_cnpj_favorecido}")
                else:
                    logger.warning(f"Lancto {lancto}: Beneficiado '{beneficiado_nome}' não encontrado no cadastro")
                    return None, "Favorecido não encontrado no cadastro"
            
            if not cpf_cnpj_favorecido:
                logger.warning(f"Lancto {lancto}: CPF/CNPJ do favorecido não identificado")
                return None, "CPF/CNPJ do favorecido ausente"

            if not self._documento_valido(cpf_cnpj_favorecido):
                logger.warning(f"Lancto {lancto}: CPF/CNPJ inválido ({cpf_cnpj_favorecido})")
                return None, "CPF/CNPJ do favorecido inválido"
        else:
            # Para CONVENIO e BOLETO, CPF/CNPJ não é obrigatório (segmentos O e N não possuem esse campo)
            logger.info(f"Lancto {lancto}: Modalidade {modalidade.value} não requer CPF/CNPJ do favorecido")
        
        # Prepara dados bancários para Favorecido
        dados_bancarios = {}
        if tipo_pagamento == TipoPagamento.TED:
            dados_bancarios = {
                'banco': dados_interpretados.get('banco'),
                'agencia': dados_interpretados.get('agencia'),
                'agencia_dv': dados_interpretados.get('agencia_dv'),
                'conta': dados_interpretados.get('conta'),
            }
        elif tipo_pagamento == TipoPagamento.TRANSFERENCIA:
            dados_bancarios = {
                'agencia': dados_interpretados.get('agencia'),
                'agencia_dv': dados_interpretados.get('agencia_dv'),
                'conta': dados_interpretados.get('conta'),
            }
        
        # Cria Favorecido
        favorecido = self._criar_favorecido_completo(
            cpf_cnpj=cpf_cnpj_favorecido,
            nome=beneficiado_nome,
            modalidade=modalidade,
            dados_bancarios=dados_bancarios
        )

        # Validação de dados bancários conforme modalidade:
        # - TED: requer banco + agência + conta (transferência para outro banco)
        # - CREDITO_CONTA: requer apenas agência + conta (transferência interna Sicoob)
        if modalidade == Modalidade.TED:
            if not (favorecido.banco and favorecido.agencia and favorecido.conta):
                return None, "Dados bancários obrigatórios ausentes para TED (bco, ag, cc)"
        elif modalidade == Modalidade.CREDITO_CONTA:
            if not (favorecido.agencia and favorecido.conta):
                return None, "Dados bancários obrigatórios ausentes para transferência (ag, cc)"
            # Para transferência interna, usa código do banco Sicoob (756)
            if not favorecido.banco:
                favorecido.banco = "756"
        
        # Cria dados específicos
        dados_pix = None
        dados_boleto = None
        
        if modalidade in (Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO):
            # Mapeia TipoChavePixValidador → TipoChavePix
            tipo_chave_cnab = self._mapear_tipo_chave_pix(tipo_chave_extraida)
            
            # === PRÉ-PROCESSAMENTO: Detectar e processar payload EMVCo (Pix Copia e Cola) ===
            chave_para_usar = chave_pix_extraida
            if chave_pix_extraida and chave_pix_extraida.strip().startswith('000201'):
                logger.info(f"Lancto {lancto}: Payload EMVCo detectado ({len(chave_pix_extraida)} chars) - processando...")
                chave_extraida, tipo_extraido, _ = parse_emvco_pix(chave_pix_extraida)
                if chave_extraida:
                    logger.info(f"Lancto {lancto}: Chave extraída do EMVCo: {chave_extraida[:50]}... (tipo: {tipo_extraido})")
                    chave_para_usar = chave_extraida
                    # Atualizar tipo se foi identificado
                    if tipo_extraido:
                        tipo_chave_cnab = tipo_extraido
                else:
                    logger.warning(f"Lancto {lancto}: Falha ao extrair chave do payload EMVCo")
                    return None, "Falha ao extrair chave PIX do QR Code (Copia e Cola)"
            
            dados_pix = DadosPix(
                chave=chave_para_usar,
                tipo_chave=tipo_chave_cnab
            )
        
        elif modalidade == Modalidade.BOLETO:
            # Extrai código de barras da obs (47 dígitos → boleto bancário)
            codigo_barras = dados_interpretados.get('codigo_barras')
            if not codigo_barras:
                logger.warning(f"Lancto {lancto}: Código de barras não encontrado para boleto")
                return None, "Código de barras do boleto ausente"
            
            # Cria DadosBoleto
            dados_boleto = DadosBoleto(
                codigo_barras=codigo_barras,
                data_vencimento=data_pagamento,
                valor_documento=valor_centavos
            )
            
            logger.info(f"Lancto {lancto}: Boleto extraído - Código: {codigo_barras[:10]}...")
        
        elif modalidade == Modalidade.CONVENIO:
            # Extrai código de barras da obs (48 dígitos → convênio/tributo/concessionária)
            codigo_barras = dados_interpretados.get('codigo_barras')
            if not codigo_barras:
                logger.warning(f"Lancto {lancto}: Código de barras não encontrado para convênio")
                return None, "Código de barras do convênio ausente"
            
            # Convênio também usa DadosBoleto (armazena código de barras 44 dígitos)
            dados_boleto = DadosBoleto(
                codigo_barras=codigo_barras,
                data_vencimento=data_pagamento,
                valor_documento=valor_centavos,
                nome_beneficiario=beneficiado_nome  # Nome da concessionária/órgão
            )
            
            tipo_boleto = dados_interpretados.get('tipo_boleto', 'Convênio')
            logger.info(f"Lancto {lancto}: Convênio/Tributo extraído ({tipo_boleto}) - Código: {codigo_barras[:10]}...")
        
        # Para BOLETO: Verifica se há cnpj-cpf_pagador customizado na obs
        pagador_customizado = None
        if modalidade == Modalidade.BOLETO:
            cnpj_cpf_pagador = self._extrair_cnpj_cpf_pagador_de_obs(obs)
            if cnpj_cpf_pagador:
                # Valida o CPF/CNPJ informado
                if not self._documento_valido(cnpj_cpf_pagador):
                    logger.warning(f"Lancto {lancto}: cnpj-cpf_pagador inválido: {cnpj_cpf_pagador}")
                    return None, f"cnpj-cpf_pagador inválido: {cnpj_cpf_pagador}"
                
                # Determina tipo de inscrição (1=CPF, 2=CNPJ)
                tipo_inscricao_pagador = 1 if len(cnpj_cpf_pagador) == 11 else 2
                
                # Busca nome do pagador no cadastro, se existir
                dados_pagador = self.map_pessoas_cpf.get(cnpj_cpf_pagador)
                nome_pagador = dados_pagador.get('Nome', beneficiado_nome) if dados_pagador else beneficiado_nome
                
                pagador_customizado = Pagador(
                    tipo_inscricao=tipo_inscricao_pagador,
                    numero_inscricao=cnpj_cpf_pagador,
                    nome=nome_pagador
                )
                
                logger.info(f"Lancto {lancto}: Pagador customizado - Tipo: {'CPF' if tipo_inscricao_pagador == 1 else 'CNPJ'}, Número: {cnpj_cpf_pagador}")
        
        # Cria Pagamento
        pagamento = Pagamento(
            modalidade=modalidade,
            valor_pagamento=valor_centavos,
            data_pagamento=data_pagamento,
            data_vencimento=data_pagamento,
            favorecido=favorecido,
            numero_documento=lancto,
            finalidade_ted=None,
            informacao_recebedor=mensagem_cnab[:40] if mensagem_cnab else None,
            dados_pix=dados_pix,
            dados_boleto=dados_boleto,
            pagador=pagador_customizado,
            lote=None
        )
        
        logger.info(f"✓ Lancto {lancto}: Pagamento criado - Modalidade: {modalidade.value}, Valor: R$ {valor_reais:.2f}")
        
        return pagamento, None
    
    def _extrair_cpf_cnpj_de_obs(self, obs: str) -> Optional[str]:
        """Extrai CPF ou CNPJ do campo observações usando regex"""
        if not obs:
            return None
        
        # Padrões
        cpf_pattern = r'cpf[:\s]*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})'
        cnpj_pattern = r'cnpj[:\s]*(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})'
        
        # CNPJ primeiro (mais específico)
        match_cnpj = re.search(cnpj_pattern, obs, re.IGNORECASE)
        if match_cnpj:
            return self._limpar_cpf_cnpj(match_cnpj.group(1))
        
        # CPF
        match_cpf = re.search(cpf_pattern, obs, re.IGNORECASE)
        if match_cpf:
            return self._limpar_cpf_cnpj(match_cpf.group(1))
        
        # Padrões soltos
        cpf_solto = re.search(r'\d{3}\.?\d{3}\.?\d{3}-?\d{2}', obs)
        if cpf_solto:
            cpf_limpo = self._limpar_cpf_cnpj(cpf_solto.group(0))
            if len(cpf_limpo) == 11:
                return cpf_limpo
        
        cnpj_solto = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', obs)
        if cnpj_solto:
            cnpj_limpo = self._limpar_cpf_cnpj(cnpj_solto.group(0))
            if len(cnpj_limpo) == 14:
                return cnpj_limpo
        
        return None
    
    def _analisar_obs_e_determinar_modalidade(self, obs_lower: str, obs_original: str, cpf_cnpj: str) -> Tuple[Optional[Modalidade], Dict]:
        """
        Analisa campo obs e determina modalidade conforme regras do prompt
        """
        dados_bancarios = {}
        
        # Verifica PIX
        if 'pix' in obs_lower:
            chave_pix = self._extrair_chave_pix(obs_original)
            chave_pix = self._sanitizar_str(chave_pix)
            if not chave_pix:
                chave_pix = self._extrair_chave_pix_por_telefone(obs_original)
            
            if chave_pix:
                tipo_chave, chave_normalizada = self._identificar_tipo_chave_pix(chave_pix)
                if chave_normalizada:
                    chave_pix = chave_normalizada
                dados_bancarios['chave_pix'] = chave_pix
                dados_bancarios['tipo_chave'] = tipo_chave
                
                if tipo_chave:
                    return Modalidade.PIX_CHAVE, dados_bancarios
                else:
                    return Modalidade.PIX_QR_DINAMICO, dados_bancarios
            else:
                # Usa CPF como chave
                dados_bancarios['chave_pix'] = cpf_cnpj
                dados_bancarios['tipo_chave'] = TipoChavePix.CPF if len(cpf_cnpj) == 11 else TipoChavePix.CNPJ
                return Modalidade.PIX_CHAVE, dados_bancarios
        
        # Verifica TED explícito
        if 'ted' in obs_lower:
            banco, agencia, conta = self._extrair_dados_bancarios(obs_original)
            dados_bancarios['banco'] = banco
            dados_bancarios['agencia'] = agencia
            dados_bancarios['conta'] = conta
            return Modalidade.TED, dados_bancarios
        
        # Verifica transferência
        if 'transferencia' in obs_lower or 'transferência' in obs_lower:
            banco, agencia, conta = self._extrair_dados_bancarios(obs_original)
            dados_bancarios['banco'] = banco
            dados_bancarios['agencia'] = agencia
            dados_bancarios['conta'] = conta
            
            if banco and banco != '756':
                return Modalidade.TED, dados_bancarios
            else:
                return Modalidade.CREDITO_CONTA, dados_bancarios
        
        # Padrão: crédito em conta
        return Modalidade.CREDITO_CONTA, dados_bancarios
    
    def _extrair_chave_pix(self, obs: str) -> Optional[str]:
        """Extrai chave PIX procurando o último "pix" e lendo dali em diante."""
        if not obs:
            return None

        texto = str(obs)
        texto_lower = texto.lower()
        idx = texto_lower.rfind('pix')
        if idx == -1:
            logger.debug("Nenhuma ocorrência de PIX encontrada para extração")
            return None

        # Recorta texto a partir da última ocorrência de "pix"
        trecho = texto[idx + 3:]
        trecho = trecho.lstrip(' :=-\t')
        if not trecho:
            return None

        # Considera apenas a primeira linha após o termo (até quebra de linha ou ponto-e-vírgula)
        linha = re.split(r'[\n\r;]', trecho, maxsplit=1)[0].strip()
        if not linha:
            return None

        tokens = linha.split()
        if not tokens:
            return None

        chave_tokens: List[str] = []
        modo = None  # email | numeric | alpha
        keywords = {'chave', 'do', 'da', 'de', 'via'}

        for token in tokens:
            token_limpo = token.strip().strip(':').strip(',;')
            if not token_limpo:
                continue

            token_lower = token_limpo.lower()
            if not chave_tokens and token_lower in keywords:
                # Termos de ligação antes da chave
                continue
            if token_lower.startswith('dsc') or token_lower.startswith('obs'):
                break

            if not chave_tokens:
                chave_tokens.append(token_limpo)
                if '@' in token_limpo:
                    modo = 'email'
                    break  # emails não possuem espaços
                if any(c.isalpha() for c in token_limpo):
                    modo = 'alpha'
                    break  # chave randômica geralmente é um token único
                modo = 'numeric'
                continue

            if modo == 'numeric':
                if any(c.isalpha() for c in token_limpo):
                    break  # texto regular após chave numérica
                chave_tokens.append(token_limpo)
            else:
                break

        chave = ''.join(chave_tokens).strip(' :;')
        if not chave:
            return None

        logger.debug(f"Chave PIX extraída (última ocorrência): '{chave}' do texto: '{obs[:100]}'")
        return chave
    
    def _identificar_tipo_chave_pix(self, chave: str) -> Tuple[Optional[TipoChavePix], Optional[str]]:
        """
        Identifica tipo de chave PIX por regex
        IMPORTANTE: Aceita chaves formatadas (com pontos/hífens) e limpa antes de validar
        CPF/CNPJ/Telefone devem conter APENAS números (após limpeza)
        """
        if not chave:
            return None, None
        
        chave = chave.strip()
        if not chave:
            return None, None

        match_telefone = PIX_TELEFONE_REGEX.match(chave)
        if match_telefone:
            numero = ''.join(match_telefone.groups())
            return TipoChavePix.TELEFONE, numero

        # Verifica se é email (formato simples usuario@dominio)
        if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', chave):
            logger.debug(f"Chave identificada como EMAIL: {chave}")
            return TipoChavePix.EMAIL, None
        
        # Remove toda pontuação/formatação para análise numérica
        # Aceita: pontos, hífens, espaços, parênteses, barras
        chave_limpa = re.sub(r'[^\d]', '', chave)
        
        # CPF: exatamente 11 dígitos numéricos (após limpeza)
        # Aceita formatos: 123.456.789-00 ou 12345678900
        if len(chave_limpa) == 11 and chave_limpa.isdigit():
            logger.debug(f"Chave identificada como CPF: {chave} → {chave_limpa}")
            return TipoChavePix.CPF, chave_limpa
        
        # CNPJ: exatamente 14 dígitos numéricos (após limpeza)
        # Aceita formatos: 12.345.678/0001-00 ou 12345678000100
        if len(chave_limpa) == 14 and chave_limpa.isdigit():
            logger.debug(f"Chave identificada como CNPJ: {chave} → {chave_limpa}")
            return TipoChavePix.CNPJ, chave_limpa
        
        # Telefone: entre 10 e 13 dígitos (após limpeza)
        # Aceita formatos: (85) 9 1234-5678, 85912345678, +55 85 9 1234-5678
        # Exemplos válidos: 85912345678 (11 dígitos), 5585912345678 (13 dígitos)
        if len(chave_limpa) >= 10 and len(chave_limpa) <= 13 and chave_limpa.isdigit():
            logger.debug(f"Chave identificada como TELEFONE (fallback): {chave} → {chave_limpa}")
            return TipoChavePix.TELEFONE, chave_limpa
        
        # Chave aleatória: qualquer chave alfanumérica (sem formato de email)
        if any(c.isalpha() for c in chave):
            logger.debug(f"Chave identificada como ALEATORIA: {chave}")
            return TipoChavePix.ALEATORIA, None
        
        # Não identificado → será tratado como QR dinâmico
        logger.debug(f"Chave NÃO identificada (QR Dinâmico): {chave} → limpa: {chave_limpa}")
        return None, None
    
    def _extrair_dados_bancarios(self, obs: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extrai banco, agência e conta do campo observações"""
        banco = None
        agencia = None
        conta = None
        
        if not obs:
            return banco, agencia, conta
        
        # Extrai banco
        banco_match = re.search(r'banco[:\s]+([a-zA-Z0-9\s]+?)(?:[/,;\n]|agencia|ag|conta)', obs, re.IGNORECASE)
        if banco_match:
            banco_raw = banco_match.group(1).strip()
            banco_digitos = ''.join(c for c in banco_raw if c.isdigit())
            if len(banco_digitos) == 3:
                banco = banco_digitos
            else:
                banco = self._mapear_nome_banco(banco_raw)
        
        # Extrai agência
        agencia_match = re.search(r'ag(?:encia|ência)?[:\s]+(\d+)', obs, re.IGNORECASE)
        if agencia_match:
            agencia = agencia_match.group(1).strip()
        
        # Extrai conta
        conta_match = re.search(r'conta[:\s]+([\d\-]+)', obs, re.IGNORECASE)
        if conta_match:
            conta = conta_match.group(1).strip()
        
        return banco, agencia, conta
    
    def _mapear_nome_banco(self, nome: str) -> Optional[str]:
        """Mapeia nome de banco para código COMPE"""
        mapa = {
            'bradesco': '237',
            'itau': '341',
            'itaú': '341',
            'santander': '033',
            'caixa': '104',
            'caixa econômica': '104',
            'caixa economica': '104',
            'banco do brasil': '001',
            'bb': '001',
            'nubank': '260',
            'nu pagamentos': '260',
            'inter': '077',
            'sicoob': '756',
            'next': '237',
            'will': '280',
            'picpay': '380',
        }
        
        nome_lower = nome.lower().strip()
        for chave, codigo in mapa.items():
            if chave in nome_lower:
                return codigo
        
        return None
    
    def _criar_favorecido_completo(self, cpf_cnpj: str, nome: str, modalidade: Modalidade, dados_bancarios: Dict) -> Favorecido:
        """Cria objeto Favorecido completo"""
        # Para convênios e boletos, cpf_cnpj pode ser None (não é obrigatório no CNAB240)
        # Segmentos O e N não possuem campos para CPF/CNPJ do favorecido
        # Nesses casos, usamos valores fictícios que não são realmente gravados no arquivo
        if cpf_cnpj:
            tipo_inscricao = 1 if len(cpf_cnpj) == 11 else 2
            numero_inscricao = cpf_cnpj
        else:
            # Para convênios/tributos (Segmentos O e N), usa CNPJ fictício
            # (não é gravado no arquivo, apenas o nome da concessionária/órgão)
            tipo_inscricao = 2  
            numero_inscricao = '00000000000000'  # CNPJ fictício (14 dígitos)
        
        banco = self._sanitizar_str(dados_bancarios.get('banco'))
        agencia = self._sanitizar_str(dados_bancarios.get('agencia'))
        conta = self._sanitizar_str(dados_bancarios.get('conta'))
        nome = self._sanitizar_str(nome, default='SEM NOME')
        
        # Conta corrente: CNAB exige DV em campo separado
        # Se vier com hífen (ex: "4816-0") → conta=4816, dv=0
        # Se vier sem hífen (ex: "12345") → conta=1234, dv=5 (último dígito é o DV)
        if conta and '-' in conta:
            # Formato explícito: "4816-0" → conta=4816, dv=0
            numero_conta, dv_conta = conta.rsplit('-', 1)
        elif conta and len(conta) > 1:
            # Sem hífen: último dígito é o DV
            # Ex: "12345" → conta="1234", dv="5"
            numero_conta = conta[:-1]  # Todos exceto o último
            dv_conta = conta[-1]       # Último dígito é o DV
        else:
            # Conta vazia ou com apenas 1 dígito
            # CNAB exige DV numérico - usar '0' em vez de espaço
            numero_conta = conta
            dv_conta = '0'
        
        favorecido = Favorecido(
            tipo_inscricao=tipo_inscricao,
            numero_inscricao=numero_inscricao,
            nome_favorecido=nome[:30] or 'SEM NOME',
            banco=banco.zfill(3) if banco else None,
            agencia=agencia if agencia else None,
            dv_agencia=' ',
            conta=numero_conta if numero_conta else None,
            dv_conta=dv_conta if dv_conta else None,
            dv_agencia_conta=' ',
            ispb=None,
            logradouro=None,
            numero_endereco=None,
            complemento=None,
            bairro=None,
            cidade=None,
            cep=None,
            uf=None
        )
        
        return favorecido
    
    def _criar_dados_pix_de_obs(self, mensagem: str, modalidade: Modalidade, dados_bancarios: Dict) -> Optional[DadosPix]:
        """
        Cria objeto DadosPix a partir dos dados extraídos
        Para CPF/CNPJ/Telefone: armazena apenas números (limpa formatação)
        
        Args:
            mensagem: Mensagem a ser incluída no campo descricao (Comp. Lancto Conta Doc)
            modalidade: Modalidade do pagamento
            dados_bancarios: Dicionário com dados bancários extraídos
        """
        if modalidade == Modalidade.PIX_CHAVE:
            chave_original = self._sanitizar_str(dados_bancarios.get('chave_pix'))
            tipo_chave = dados_bancarios.get('tipo_chave')
            
            if not chave_original:
                return None
            
            # Para CPF, CNPJ e Telefone: limpa formatação (apenas números)
            if tipo_chave in (TipoChavePix.CPF, TipoChavePix.CNPJ, TipoChavePix.TELEFONE):
                chave_limpa = re.sub(r'[^\d]', '', chave_original)
                logger.debug(f"Chave PIX limpa para {tipo_chave.value}: {chave_original} → {chave_limpa}")
            else:
                # Email e Aleatória: mantém formato original
                chave_limpa = chave_original
            
            return DadosPix(
                tipo_chave=tipo_chave,
                chave=chave_limpa,
                txid=None,
                url_qr=None,
                payload_emv=None,
                descricao=mensagem[:100] if mensagem else None
            )
        
        elif modalidade == Modalidade.PIX_QR_DINAMICO:
            # Para QR dinâmico com chave identificada, trata como PIX_CHAVE
            chave_original = self._sanitizar_str(dados_bancarios.get('chave_pix'))
            tipo_chave = dados_bancarios.get('tipo_chave')
            
            if chave_original and tipo_chave:
                # Tem chave identificada → limpa se for numérica
                if tipo_chave in (TipoChavePix.CPF, TipoChavePix.CNPJ, TipoChavePix.TELEFONE):
                    chave_limpa = re.sub(r'[^\d]', '', chave_original)
                else:
                    chave_limpa = chave_original
                
                return DadosPix(
                    tipo_chave=tipo_chave,
                    chave=chave_limpa,
                    txid=None,
                    url_qr=None,
                    payload_emv=None,
                    descricao=mensagem[:100] if mensagem else None
                )
            else:
                # QR dinâmico real (sem chave)
                return DadosPix(
                    tipo_chave=None,
                    chave=None,
                    txid=None,
                    url_qr=dados_bancarios.get('url_qr'),
                    payload_emv=dados_bancarios.get('payload_emv'),
                    descricao=mensagem[:100] if mensagem else None
                )
        
        return None
    
    def _converter_data(self, data_str: str) -> Optional[str]:
        """Converte data para formato DDMMAAAA"""
        if not data_str or pd.isna(data_str):
            return None
        
        data_str = str(data_str).strip()
        
        formatos = [
            "%d/%m/%Y",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%y",
        ]
        
        for fmt in formatos:
            try:
                dt = datetime.strptime(data_str, fmt)
                return dt.strftime("%d%m%Y")
            except ValueError:
                continue
        
        return None
    
    def _buscar_pessoa_por_nome(self, nome: str) -> Optional[Dict]:
        """Busca pessoa no mapa por nome (busca aproximada)"""
        nome_normalizado = nome.upper().strip()
        
        if nome_normalizado in self.map_pessoas_nome:
            return self.map_pessoas_nome[nome_normalizado]
        
        for nome_cadastrado, dados in self.map_pessoas_nome.items():
            if nome_normalizado in nome_cadastrado or nome_cadastrado in nome_normalizado:
                logger.info(f"Match parcial: '{nome}' → '{nome_cadastrado}'")
                return dados
        
        return None


def main():
    """Função principal"""
    print("="*80)
    print("PROCESSADOR CONTAS A PAGAR → CNAB 240 SICOOB")
    print("="*80)

    parser = argparse.ArgumentParser(
        description="Processa contas a pagar e gera remessas CNAB 240 por conta/agência"
    )
    parser.add_argument("contas", help="CSV de contas a pagar (relatorio_contas_pagar.csv)")
    parser.add_argument("bancos", help="CSV com dados bancários do cedente (bancos.csv)")
    parser.add_argument("pessoas", help="CSV com cadastro de pessoas/fornecedores")
    parser.add_argument(
        "--warnings-only",
        action="store_true",
        help="Oculta logs informativos, exibindo apenas avisos e erros"
    )

    args = parser.parse_args()

    if args.warnings_only:
        logger.setLevel(logging.WARNING)
        for handler in logger.handlers:
            handler.setLevel(logging.WARNING)

    path_contas = args.contas
    path_bancos = args.bancos
    path_pessoas = args.pessoas
    
    for path in [path_contas, path_bancos, path_pessoas]:
        if not Path(path).exists():
            print(f"❌ Arquivo não encontrado: {path}")
            sys.exit(1)
    
    processador = ProcessadorContasPagar(
        path_contas,
        path_bancos,
        path_pessoas
    )
    
    try:
        processador.carregar_dados()
    except Exception as e:
        print(f"❌ Erro ao carregar dados: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    try:
        resultados = processador.processar()
    except Exception as e:
        print(f"❌ Erro no processamento: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "="*80)
    print("RESUMO")
    print("="*80)
    
    if not resultados:
        print("⚠️  Nenhum arquivo CNAB 240 foi gerado")
    else:
        for chave_conta_agencia, arquivos in resultados.items():
            print(f"\nConta/Agência {chave_conta_agencia}:")
            for cnab in arquivos:
                print(f"  ✓ {Path(cnab).name}")
    
    print("\n✅ Processamento concluído!")


if __name__ == '__main__':
    main()
