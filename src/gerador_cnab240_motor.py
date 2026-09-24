#!/usr/bin/env python3
"""
GERADOR CNAB 240 - PARTE 3: MOTOR DE GERAÇÃO
Orquestra a geração completa do arquivo CNAB 240
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple
from dataclasses import dataclass, field

from gerador_cnab240 import (
    BankProfile,
    Empresa,
    Pagamento,
    DadosLote,
    DadosArquivo,
    ModalidadeConfig,
    ValidadorCNAB,
    Modalidade,
    logger,
)
from gerador_cnab240_segmentos import GeradorSegmentos


@dataclass
class TotalizadorLote:
    """Totalizadores de um lote"""
    numero_lote: int
    quantidade_registros: int = 0
    valor_total: int = 0  # Em centavos
    quantidade_pagamentos: int = 0
    
    def adicionar_pagamento(self, valor: int):
        """Adiciona um pagamento aos totalizadores"""
        self.quantidade_pagamentos += 1
        self.valor_total += valor


@dataclass
class TotalizadorArquivo:
    """Totalizadores do arquivo completo"""
    quantidade_lotes: int = 0
    quantidade_registros_total: int = 0
    valor_total_arquivo: int = 0  # Em centavos
    quantidade_pagamentos_total: int = 0
    lotes: List[TotalizadorLote] = field(default_factory=list)
    
    def adicionar_lote(self, totalizador_lote: TotalizadorLote):
        """Adiciona um lote aos totalizadores"""
        self.lotes.append(totalizador_lote)
        self.quantidade_lotes += 1
        self.quantidade_registros_total += totalizador_lote.quantidade_registros
        self.valor_total_arquivo += totalizador_lote.valor_total
        self.quantidade_pagamentos_total += totalizador_lote.quantidade_pagamentos


class MotorCNAB240:
    """Motor principal de geração de arquivo CNAB 240"""
    
    def __init__(self, config_path: str):
        """
        Inicializa o motor com arquivo de configuração JSON
        
        Args:
            config_path: Caminho para o arquivo JSON de configuração
        """
        self.config_path = config_path
        self.config = self._carregar_config()
        self.bank_profile = self._criar_bank_profile()
        self.empresa = self._criar_empresa()
        self.parametros_lote = self.config['parametros_lote']
        self.opcoes = self.config.get('opcoes_geracao', {})
        
        self.gerador = GeradorSegmentos(self.bank_profile, self.empresa)
        self.validador = ValidadorCNAB()
        
        self.linhas: List[str] = []
    
    @classmethod
    def from_objects(cls, bank_profile: BankProfile, empresa: Empresa, opcoes: Dict = None):
        """
        Cria instância do motor a partir de objetos BankProfile e Empresa
        (sem necessidade de arquivo JSON de configuração)
        
        Args:
            bank_profile: Objeto BankProfile configurado
            empresa: Objeto Empresa configurado
            opcoes: Dicionário opcional com opções de geração
        
        Returns:
            Instância de MotorCNAB240
        """
        motor = cls.__new__(cls)
        motor.config_path = None
        motor.config = None
        motor.bank_profile = bank_profile
        motor.empresa = empresa
        motor.parametros_lote = {}
        motor.opcoes = opcoes or {}
        
        motor.gerador = GeradorSegmentos(bank_profile, empresa)
        motor.validador = ValidadorCNAB()
        motor.linhas = []
        
        logger.info(f"Motor CNAB240 criado a partir de objetos - Banco: {bank_profile.nome_banco}")
        
        return motor
        self.totalizador_arquivo = TotalizadorArquivo()
        self.log_operacoes: List[Dict] = []
        
        logger.info(f"Motor CNAB 240 inicializado para banco {self.bank_profile.codigo_banco_compensacao}")
    
    def _carregar_config(self) -> Dict:
        """Carrega e valida arquivo de configuração JSON"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Validação básica de estrutura
            campos_obrigatorios = ['bank_profile', 'empresa', 'parametros_lote']
            for campo in campos_obrigatorios:
                if campo not in config:
                    raise ValueError(f"Campo obrigatório ausente no JSON: {campo}")
            
            logger.info(f"Configuração carregada de {self.config_path}")
            return config
            
        except FileNotFoundError:
            raise FileNotFoundError(f"Arquivo de configuração não encontrado: {self.config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Erro ao decodificar JSON: {e}")
    
    def _criar_bank_profile(self) -> BankProfile:
        """Cria objeto BankProfile a partir da configuração"""
        bp_config = self.config['bank_profile']
        modalidades_cfg = {}
        modalidades_json = bp_config.get('modalidades', {})
        for nome_modalidade, dados_modalidade in modalidades_json.items():
            try:
                modalidade_enum = Modalidade[nome_modalidade]
            except KeyError as exc:
                raise ValueError(f"Modalidade desconhecida em bank_profile.modalidades: {nome_modalidade}") from exc
            modalidades_cfg[modalidade_enum] = ModalidadeConfig(
                forma_lancamento=dados_modalidade['forma_lancamento'],
                camara_centralizadora=dados_modalidade['camara_centralizadora'],
                versao_layout=dados_modalidade['versao_layout'],  # Layout específico da modalidade
                tipo_compromisso=dados_modalidade.get('tipo_compromisso', '01'),
                finalidade_bacen=dados_modalidade.get('finalidade_bacen'),
                finalidade_banco=dados_modalidade.get('finalidade_banco'),
                codigo_finalidade_complementar=dados_modalidade.get('codigo_finalidade_complementar'),
                codigo_finalidade_complementar_banco=dados_modalidade.get('codigo_finalidade_complementar_banco'),
            )

        bank_profile = BankProfile(
            codigo_banco_compensacao=bp_config['codigo_banco_compensacao'],
            nome_banco=bp_config['nome_banco'],
            versao_layout_arquivo=bp_config['versao_layout_arquivo'],
            tipo_servico_pagamento=bp_config.get('tipo_servico_pagamento', '20'),
            modalidades=modalidades_cfg,
            densidade_gravacao=bp_config.get('densidade_gravacao', '01600'),
            exige_ispb_favorecido=bp_config.get('exige_ispb_favorecido', False),
            permite_data_futura=bp_config.get('permite_data_futura', True),
            observacoes=bp_config.get('observacoes'),
        )

        erros = self.validador.validar_bank_profile(bank_profile)
        if erros:
            raise ValueError("Erros na configuração do bank_profile: " + "; ".join(erros))

        return bank_profile
    
    def _criar_empresa(self) -> Empresa:
        """Cria objeto Empresa a partir da configuração"""
        emp_config = self.config['empresa']
        
        empresa = Empresa(
            tipo_inscricao=emp_config['tipo_inscricao'],
            numero_inscricao=emp_config['numero_inscricao'],
            convenio=emp_config['convenio'],
            agencia_mantenedora=emp_config['agencia_mantenedora'],
            dv_agencia=emp_config.get('dv_agencia', ' '),
            numero_conta=emp_config['numero_conta'],
            dv_conta=emp_config['dv_conta'],
            dv_agencia_conta=emp_config.get('dv_agencia_conta', ' '),
            nome_empresa=emp_config['nome_empresa'],
            logradouro=emp_config.get('logradouro'),
            numero_endereco=emp_config.get('numero_endereco'),
            complemento=emp_config.get('complemento'),
            bairro=emp_config.get('bairro'),
            cidade=emp_config.get('cidade'),
            cep=emp_config.get('cep'),
            uf=emp_config.get('uf')
        )
        
        erros = self.validador.validar_empresa(empresa)
        if erros:
            raise ValueError("Erros na configuração da empresa: " + "; ".join(erros))
        return empresa
    
    def gerar_remessa(self, pagamentos: List[Pagamento], 
                     data_geracao: str = None,
                     numero_arquivo: int = None) -> Tuple[str, Dict]:
        """
        Gera arquivo de remessa CNAB 240 completo
        
        Args:
            pagamentos: Lista de objetos Pagamento
            data_geracao: Data de geração no formato DDMMAAAA (opcional, usa hoje se omitido)
            numero_arquivo: Número sequencial do arquivo (opcional, usa 1 se omitido)
        
        Returns:
            Tupla (conteúdo_arquivo, relatorio_validacao)
        """
        logger.info(f"Iniciando geração de remessa com {len(pagamentos)} pagamento(s)")
        
        # Limpa estado anterior
        self.linhas = []
        self.totalizador_arquivo = TotalizadorArquivo()
        self.log_operacoes = []
        
        # Define data de geração
        if not data_geracao:
            data_geracao = self.parametros_lote.get('data_geracao_padrao')
        if not data_geracao:
            hoje = datetime.now()
            data_geracao = hoje.strftime("%d%m%Y")
        
        # Define número do arquivo
        if not numero_arquivo:
            numero_arquivo = self.opcoes.get('numero_arquivo_sequencial', 1)
        
        # Valida todos os pagamentos antes de iniciar
        erros_validacao = []
        for i, pag in enumerate(pagamentos, 1):
            erros_pag = self.validador.validar_pagamento(pag, self.bank_profile)
            if erros_pag:
                identificador = pag.numero_documento or pag.favorecido.nome_favorecido
                for erro in erros_pag:
                    erros_validacao.append(f"Pagamento {i} ({identificador}): {erro}")
        
        if erros_validacao:
            raise ValueError(f"Erros de validação encontrados:\n" + "\n".join(erros_validacao))
        
        # Agrupa pagamentos por lote (por enquanto, um lote único)
        # Em implementação futura, pode agrupar por data, modalidade, etc.
        lotes = self._agrupar_pagamentos_em_lotes(pagamentos)
        
        # Gera Header de Arquivo
        dados_arquivo = DadosArquivo(
            data_geracao=data_geracao,
            hora_geracao=datetime.now().strftime("%H%M%S"),
            numero_sequencial_arquivo=numero_arquivo,
            versao_layout_arquivo=self.bank_profile.versao_layout_arquivo,
            densidade_gravacao=self.opcoes.get('densidade_gravacao', self.bank_profile.densidade_gravacao)
        )
        
        header_arquivo = self.gerador.gerar_header_arquivo(dados_arquivo)
        self.linhas.append(header_arquivo)
        self.totalizador_arquivo.quantidade_registros_total += 1
        self._log("Header de Arquivo gerado")
        
        # Gera cada lote
        for numero_lote, (modalidade, pagamentos_lote) in enumerate(lotes, 1):
            self._gerar_lote(numero_lote, modalidade, pagamentos_lote, data_geracao)
        
        # Gera Trailer de Arquivo
        trailer_arquivo = self.gerador.gerar_trailer_arquivo(
            self.totalizador_arquivo.quantidade_lotes,
            self.totalizador_arquivo.quantidade_registros_total + 1
        )
        self.linhas.append(trailer_arquivo)
        self.totalizador_arquivo.quantidade_registros_total += 1
        self._log("Trailer de Arquivo gerado")
        
        # Monta conteúdo final
        conteudo = "\n".join(self.linhas)
        
        # Gera relatório
        relatorio = self._gerar_relatorio_validacao(data_geracao, numero_arquivo)
        
        logger.info(f"Remessa gerada com sucesso: {self.totalizador_arquivo.quantidade_lotes} lote(s), "
                   f"{self.totalizador_arquivo.quantidade_pagamentos_total} pagamento(s), "
                   f"R$ {self.totalizador_arquivo.valor_total_arquivo / 100:.2f}")
        
        return conteudo, relatorio
    
    def _agrupar_pagamentos_em_lotes(self, pagamentos: List[Pagamento]) -> List[Tuple[Modalidade, List[Pagamento]]]:
        """Agrupa pagamentos por modalidade, gerando um lote por modalidade."""
        agrupados: Dict[Modalidade, List[Pagamento]] = {}
        for pagamento in pagamentos:
            agrupados.setdefault(pagamento.modalidade, []).append(pagamento)
        return list(agrupados.items())
    
    def _gerar_lote(self, numero_lote: int, modalidade: Modalidade, pagamentos: List[Pagamento], data_geracao: str):
        """Gera um lote completo (header + detalhes + trailer) para a modalidade informada."""
        logger.info(
            f"Gerando lote {numero_lote} modalidade {modalidade.value} com {len(pagamentos)} pagamento(s)"
        )

        config_modalidade = self.bank_profile.get_modalidade_config(modalidade)

        dados_lote = DadosLote(
            numero_lote=numero_lote,
            tipo_operacao='C',
            tipo_servico=self.bank_profile.tipo_servico_pagamento,
            forma_lancamento=config_modalidade.forma_lancamento,
            versao_layout=config_modalidade.versao_layout,  # Usa layout da modalidade
            tipo_compromisso=config_modalidade.tipo_compromisso,
            mensagem=self.parametros_lote.get('mensagem_padrao'),
        )

        totalizador_lote = TotalizadorLote(numero_lote=numero_lote)

        header_lote = self.gerador.gerar_header_lote(dados_lote)
        self.linhas.append(header_lote)
        totalizador_lote.quantidade_registros += 1
        self._log(f"Header do Lote {numero_lote} gerado")

        numero_registro = 1
        for pagamento in pagamentos:
            pagamento.lote = numero_lote
            pagamento.codigo_forma_lancamento = config_modalidade.forma_lancamento
            pagamento.codigo_camara_centralizadora = config_modalidade.camara_centralizadora
            pagamento.tipo_compromisso = config_modalidade.tipo_compromisso

            if pagamento.modalidade == Modalidade.TED:
                pagamento.finalidade_ted = (
                    pagamento.finalidade_ted
                    or config_modalidade.finalidade_bacen
                    or self.parametros_lote.get('finalidade_ted')
                )
            else:
                pagamento.finalidade_doc = (
                    pagamento.finalidade_doc
                    or config_modalidade.finalidade_banco
                    or self.parametros_lote.get('finalidade_doc')
                )

            if not pagamento.codigo_finalidade_complementar:
                pagamento.codigo_finalidade_complementar = config_modalidade.codigo_finalidade_complementar
            if not pagamento.codigo_finalidade_complementar_banco:
                pagamento.codigo_finalidade_complementar_banco = (
                    config_modalidade.codigo_finalidade_complementar_banco
                )

            if pagamento.modalidade == Modalidade.BOLETO:
                # Layout 040: Boletos usam APENAS Segmentos J + J-52 (SEM Segmento B)
                segmento_j = self.gerador.gerar_segmento_j(pagamento, numero_lote, numero_registro)
                self.linhas.append(segmento_j)
                totalizador_lote.quantidade_registros += 1
                numero_registro += 1

                segmento_j52 = self.gerador.gerar_segmento_j52(pagamento, numero_lote, numero_registro)
                self.linhas.append(segmento_j52)
                totalizador_lote.quantidade_registros += 1
                numero_registro += 1

                self._log(f"Segmentos J + J-52 gerados (BOLETO Doc: {pagamento.numero_documento})")

            elif pagamento.modalidade == Modalidade.CONVENIO:
                # Layout 012: Convênios/Tributos/Concessionárias usam APENAS Segmento O
                segmento_o = self.gerador.gerar_segmento_o(pagamento, numero_lote, numero_registro)
                self.linhas.append(segmento_o)
                totalizador_lote.quantidade_registros += 1
                numero_registro += 1

                self._log(f"Segmento O gerado (CONVENIO Doc: {pagamento.numero_documento})")

            else:
                # Layout 045: PIX/TED/Transferências usam Segmentos A + B
                segmento_a = self.gerador.gerar_segmento_a(pagamento, numero_lote, numero_registro)
                self.linhas.append(segmento_a)
                totalizador_lote.quantidade_registros += 1
                numero_registro += 1

                segmento_b = self.gerador.gerar_segmento_b(pagamento, numero_lote, numero_registro)
                self.linhas.append(segmento_b)
                totalizador_lote.quantidade_registros += 1
                numero_registro += 1

                self._log(
                    f"Segmentos A + B gerados para {pagamento.modalidade.value} (Doc: {pagamento.numero_documento})"
                )

            totalizador_lote.adicionar_pagamento(pagamento.valor_pagamento)

        trailer_lote = self.gerador.gerar_trailer_lote(
            dados_lote,
            totalizador_lote.quantidade_registros + 1,
            totalizador_lote.valor_total,
        )
        self.linhas.append(trailer_lote)
        totalizador_lote.quantidade_registros += 1
        self._log(f"Trailer do Lote {numero_lote} gerado")

        self.totalizador_arquivo.adicionar_lote(totalizador_lote)
    
    def _gerar_relatorio_validacao(self, data_geracao: str, numero_arquivo: int) -> Dict:
        """Gera relatório de validação da remessa"""
        relatorio = {
            'status': 'sucesso',
            'data_geracao': data_geracao,
            'hora_geracao': datetime.now().strftime("%H:%M:%S"),
            'arquivo': {
                'numero_sequencial': numero_arquivo,
                'total_linhas': len(self.linhas),
                'total_registros': self.totalizador_arquivo.quantidade_registros_total,
                'total_lotes': self.totalizador_arquivo.quantidade_lotes
            },
            'banco': {
                'codigo': self.bank_profile.codigo_banco_compensacao,
                'nome': self.bank_profile.nome_banco
            },
            'empresa': {
                'nome': self.empresa.nome_empresa,
                'inscricao': self.empresa.numero_inscricao,
                'conta': f"Ag {self.empresa.agencia_mantenedora} / Conta {self.empresa.numero_conta}-{self.empresa.dv_conta}"
            },
            'totalizadores': {
                'quantidade_pagamentos': self.totalizador_arquivo.quantidade_pagamentos_total,
                'valor_total_centavos': self.totalizador_arquivo.valor_total_arquivo,
                'valor_total_reais': f"R$ {self.totalizador_arquivo.valor_total_arquivo / 100:.2f}"
            },
            'lotes': []
        }
        
        # Detalha cada lote
        for tot_lote in self.totalizador_arquivo.lotes:
            relatorio['lotes'].append({
                'numero': tot_lote.numero_lote,
                'quantidade_registros': tot_lote.quantidade_registros,
                'quantidade_pagamentos': tot_lote.quantidade_pagamentos,
                'valor_total_centavos': tot_lote.valor_total,
                'valor_total_reais': f"R$ {tot_lote.valor_total / 100:.2f}"
            })
        
        return relatorio
    
    def _log(self, mensagem: str):
        """Registra operação no log interno"""
        self.log_operacoes.append({
            'timestamp': datetime.now().isoformat(),
            'mensagem': mensagem
        })
    
    def exportar_arquivo(self, conteudo: str, nome_arquivo: str = None) -> str:
        """
        Exporta conteúdo para arquivo texto
        
        Args:
            conteudo: Conteúdo do arquivo CNAB 240
            nome_arquivo: Nome do arquivo (opcional, gera automático se omitido)
        
        Returns:
            Caminho completo do arquivo gerado
        """
        # Define diretório de saída
        dir_saida = self.opcoes.get('diretorio_saida', './remessas')
        Path(dir_saida).mkdir(parents=True, exist_ok=True)
        
        # Define nome do arquivo
        if not nome_arquivo:
            prefixo = self.opcoes.get('prefixo_arquivo_saida', 'remessa_cnab240')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_arquivo = f"{prefixo}_{timestamp}.txt"
        
        caminho_completo = os.path.join(dir_saida, nome_arquivo)
        
        # Escreve arquivo
        with open(caminho_completo, 'w', encoding='latin-1') as f:
            f.write(conteudo)
        
        logger.info(f"Arquivo CNAB 240 exportado: {caminho_completo}")
        return caminho_completo
    
    def exportar_relatorio(self, relatorio: Dict, nome_arquivo: str = None) -> str:
        """
        Exporta relatório de validação para JSON
        
        Args:
            relatorio: Dicionário com relatório
            nome_arquivo: Nome do arquivo (opcional)
        
        Returns:
            Caminho completo do arquivo gerado
        """
        if not self.opcoes.get('gerar_relatorio_validacao', True):
            return None
        
        dir_saida = self.opcoes.get('diretorio_saida', './remessas')
        Path(dir_saida).mkdir(parents=True, exist_ok=True)
        
        if not nome_arquivo:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_arquivo = f"validacao_{timestamp}.json"
        
        caminho_completo = os.path.join(dir_saida, nome_arquivo)
        
        with open(caminho_completo, 'w', encoding='utf-8') as f:
            json.dump(relatorio, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Relatório de validação exportado: {caminho_completo}")
        return caminho_completo
    
    def exportar_log(self, nome_arquivo: str = None) -> str:
        """
        Exporta log de operações para arquivo
        
        Args:
            nome_arquivo: Nome do arquivo (opcional)
        
        Returns:
            Caminho completo do arquivo gerado
        """
        if not self.opcoes.get('gerar_log_detalhado', True):
            return None
        
        dir_saida = self.opcoes.get('diretorio_saida', './remessas')
        Path(dir_saida).mkdir(parents=True, exist_ok=True)
        
        if not nome_arquivo:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_arquivo = f"log_geracao_{timestamp}.json"
        
        caminho_completo = os.path.join(dir_saida, nome_arquivo)
        
        with open(caminho_completo, 'w', encoding='utf-8') as f:
            json.dump(self.log_operacoes, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Log de operações exportado: {caminho_completo}")
        return caminho_completo


if __name__ == '__main__':
    # Exemplo de uso
    print("Motor CNAB 240 - Use via import ou veja processar_contas_pagar_cnab.py")
