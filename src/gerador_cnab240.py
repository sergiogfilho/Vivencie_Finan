#!/usr/bin/env python3
"""Modelos e utilitários para geração de arquivos CNAB 240."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("cnab240_gerador.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class Modalidade(Enum):
    """Modalidades de pagamento suportadas pelo gerador."""

    CREDITO_CONTA = "CREDITO_CONTA"
    TED = "TED"
    PIX_CHAVE = "PIX_CHAVE"
    PIX_QR_DINAMICO = "PIX_QR_DINAMICO"
    BOLETO = "BOLETO"
    CONVENIO = "CONVENIO"  # Convênios, tributos e concessionárias (48 dígitos)


class TipoChavePix(Enum):
    """Tipos de chave PIX aceitos pelo Sicoob."""

    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    TELEFONE = "TELEFONE"
    ALEATORIA = "ALEATORIA"


@dataclass
class ModalidadeConfig:
    """Configuração bancária específica por modalidade."""

    forma_lancamento: str
    camara_centralizadora: str
    versao_layout: str  # Versão do layout do lote (ex: 040 para boleto, 045 para PIX/TED)
    tipo_compromisso: str = "01"
    finalidade_bacen: Optional[str] = None
    finalidade_banco: Optional[str] = None
    codigo_finalidade_complementar: Optional[str] = None
    codigo_finalidade_complementar_banco: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.forma_lancamento) != 2 or not self.forma_lancamento.isdigit():
            raise ValueError("forma_lancamento deve ter 2 dígitos numéricos")
        if len(self.camara_centralizadora) != 3 or not self.camara_centralizadora.isdigit():
            raise ValueError("camara_centralizadora deve ter 3 dígitos numéricos")
        if len(self.versao_layout) != 3 or not self.versao_layout.isdigit():
            raise ValueError("versao_layout deve ter 3 dígitos numéricos")
        if len(self.tipo_compromisso) != 2 or not self.tipo_compromisso.isdigit():
            raise ValueError("tipo_compromisso deve ter 2 dígitos numéricos")
        if self.finalidade_bacen and (len(self.finalidade_bacen) != 5 or not self.finalidade_bacen.isdigit()):
            raise ValueError("finalidade_bacen deve ter 5 dígitos numéricos")
        if self.finalidade_banco and (len(self.finalidade_banco) != 2 or not self.finalidade_banco.isdigit()):
            raise ValueError("finalidade_banco deve ter 2 dígitos numéricos")
        if self.codigo_finalidade_complementar and len(self.codigo_finalidade_complementar) not in (2, 3):
            raise ValueError("codigo_finalidade_complementar deve ter 2 ou 3 caracteres")
        if self.codigo_finalidade_complementar_banco and len(self.codigo_finalidade_complementar_banco) not in (2, 3):
            raise ValueError("codigo_finalidade_complementar_banco deve ter 2 ou 3 caracteres")


@dataclass
class BankProfile:
    """Perfil completo do banco pagador."""

    codigo_banco_compensacao: str
    nome_banco: str
    versao_layout_arquivo: str
    tipo_servico_pagamento: str
    modalidades: Dict[Modalidade, ModalidadeConfig] = field(default_factory=dict)
    densidade_gravacao: str = "01600"
    exige_ispb_favorecido: bool = False
    permite_data_futura: bool = True
    observacoes: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.codigo_banco_compensacao) != 3 or not self.codigo_banco_compensacao.isdigit():
            raise ValueError("codigo_banco_compensacao deve ter 3 dígitos numéricos")
        if len(self.versao_layout_arquivo) != 3 or not self.versao_layout_arquivo.isdigit():
            raise ValueError("versao_layout_arquivo deve ter 3 dígitos numéricos")
        if len(self.tipo_servico_pagamento) != 2 or not self.tipo_servico_pagamento.isdigit():
            raise ValueError("tipo_servico_pagamento deve ter 2 dígitos numéricos")
        if len(self.densidade_gravacao) != 5 or not self.densidade_gravacao.isdigit():
            raise ValueError("densidade_gravacao deve ter 5 dígitos numéricos")
        if not self.modalidades:
            raise ValueError("modalidades não podem estar vazias")

    def get_modalidade_config(self, modalidade: Modalidade) -> ModalidadeConfig:
        try:
            return self.modalidades[modalidade]
        except KeyError as exc:
            raise KeyError(f"Modalidade {modalidade.value} não configurada para o banco") from exc


@dataclass
class Empresa:
    """Dados cadastrais do cedente (empresa pagadora)."""

    tipo_inscricao: int
    numero_inscricao: str
    convenio: str
    agencia_mantenedora: str
    dv_agencia: str
    numero_conta: str
    dv_conta: str
    dv_agencia_conta: str
    nome_empresa: str
    logradouro: Optional[str] = None
    numero_endereco: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    cep: Optional[str] = None
    uf: Optional[str] = None

    def __post_init__(self) -> None:
        if self.tipo_inscricao not in (1, 2):
            raise ValueError("tipo_inscricao deve ser 1 (CPF) ou 2 (CNPJ)")
        if not self.numero_inscricao.isdigit():
            raise ValueError("numero_inscricao deve conter apenas dígitos")
        tamanho_esperado = 11 if self.tipo_inscricao == 1 else 14
        if len(self.numero_inscricao) != tamanho_esperado:
            raise ValueError(f"numero_inscricao deve ter {tamanho_esperado} dígitos")
        if len(self.agencia_mantenedora) > 5:
            raise ValueError("agencia_mantenedora deve ter até 5 dígitos")
        if self.cep and (len(self.cep) != 8 or not self.cep.isdigit()):
            raise ValueError("cep deve conter 8 dígitos")
        if self.uf and len(self.uf) != 2:
            raise ValueError("uf deve ter 2 caracteres")


@dataclass
class Favorecido:
    """Dados do beneficiário do pagamento."""

    tipo_inscricao: int
    numero_inscricao: str
    nome_favorecido: str
    banco: Optional[str] = None
    agencia: Optional[str] = None
    dv_agencia: Optional[str] = None
    conta: Optional[str] = None
    dv_conta: Optional[str] = None
    dv_agencia_conta: Optional[str] = None
    ispb: Optional[str] = None
    logradouro: Optional[str] = None
    numero_endereco: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    cep: Optional[str] = None
    uf: Optional[str] = None

    def __post_init__(self) -> None:
        if self.tipo_inscricao not in (1, 2):
            raise ValueError("favorecido.tipo_inscricao deve ser 1 ou 2")
        if not self.numero_inscricao or not self.numero_inscricao.isdigit():
            raise ValueError("favorecido.numero_inscricao deve conter apenas dígitos")
        if self.cep and (len(self.cep) != 8 or not self.cep.isdigit()):
            raise ValueError("favorecido.cep deve conter 8 dígitos")
        if self.uf and len(self.uf) != 2:
            raise ValueError("favorecido.uf deve ter 2 caracteres")
        if self.banco and (len(self.banco) != 3 or not self.banco.isdigit()):
            raise ValueError("favorecido.banco deve ter 3 dígitos")
        if self.ispb and (len(self.ispb) != 8 or not self.ispb.isdigit()):
            raise ValueError("favorecido.ispb deve ter 8 dígitos")


@dataclass
class Pagador:
    """Dados do pagador utilizados em segmentos opcionais (ex.: J52)."""

    tipo_inscricao: int
    numero_inscricao: str
    nome: str
    logradouro: Optional[str] = None
    numero_endereco: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    cep: Optional[str] = None
    uf: Optional[str] = None

    def __post_init__(self) -> None:
        if self.tipo_inscricao not in (1, 2):
            raise ValueError("pagador.tipo_inscricao deve ser 1 ou 2")
        if not self.numero_inscricao.isdigit():
            raise ValueError("pagador.numero_inscricao deve conter apenas dígitos")
        if self.cep and (len(self.cep) != 8 or not self.cep.isdigit()):
            raise ValueError("pagador.cep deve conter 8 dígitos")
        if self.uf and len(self.uf) != 2:
            raise ValueError("pagador.uf deve ter 2 caracteres")


@dataclass
class DadosPix:
    """Informações específicas para pagamentos PIX."""

    tipo_chave: Optional[TipoChavePix] = None
    chave: Optional[str] = None
    txid: Optional[str] = None
    url_qr: Optional[str] = None
    payload_emv: Optional[str] = None
    descricao: Optional[str] = None

    def __post_init__(self) -> None:
        if self.tipo_chave and not isinstance(self.tipo_chave, TipoChavePix):
            raise ValueError("tipo_chave deve ser uma instância de TipoChavePix")
        if self.txid and len(self.txid) > 35:
            raise ValueError("txid deve possuir até 35 caracteres")
        
        # === PRÉ-PROCESSAMENTO: Detectar e processar payload EMVCo (Pix Copia e Cola) ===
        # Se a chave começa com '000201', é um payload EMVCo que precisa ser parseado
        if self.chave and self.chave.strip().startswith('000201'):
            try:
                from gerador_cnab240_segmentos import parse_emvco_pix
                chave_extraida, tipo_extraido, _ = parse_emvco_pix(self.chave)
                if chave_extraida:
                    # Armazena payload original em payload_emv e usa chave extraída
                    self.payload_emv = self.chave
                    self.chave = chave_extraida
                    # Atualiza tipo se foi identificado e não foi fornecido
                    if tipo_extraido and not self.tipo_chave:
                        self.tipo_chave = tipo_extraido
                else:
                    # Falha ao extrair - logar aviso mas não lançar erro
                    logger.warning(f"Payload EMVCo detectado mas chave não extraída: {self.chave[:50]}...")
            except ImportError:
                # Módulo não disponível - continuar sem processar
                logger.warning("Módulo gerador_cnab240_segmentos não disponível para parse EMVCo")
            except Exception as e:
                logger.warning(f"Erro ao processar payload EMVCo: {e}")
        
        # Validar tamanho da chave (após processamento EMVCo)
        # Limite aumentado para 99 caracteres (campo do Segmento B)
        if self.chave and len(self.chave) > 99:
            raise ValueError(f"chave PIX deve possuir até 99 caracteres (atual: {len(self.chave)})")


@dataclass
class DadosBoleto:
    """Dados necessários para pagamento de boletos (Segmento J)."""

    codigo_barras: str
    data_vencimento: Optional[str] = None
    valor_documento: Optional[int] = None
    desconto: int = 0
    abatimento: int = 0
    mora: int = 0
    multa: int = 0
    nome_beneficiario: Optional[str] = None
    documento_beneficiario: Optional[str] = None

    def __post_init__(self) -> None:
        codigo = re.sub(r"\D", "", self.codigo_barras or "")
        if len(codigo) != 44:
            raise ValueError("codigo_barras deve conter 44 dígitos")
        self.codigo_barras = codigo
        if self.data_vencimento and not FormatadorCNAB.validar_data(self.data_vencimento):
            raise ValueError("data_vencimento deve estar no formato DDMMAAAA")
        if self.valor_documento is not None and self.valor_documento < 0:
            raise ValueError("valor_documento não pode ser negativo")


@dataclass
class Pagamento:
    """Representa um pagamento individual a ser exportado."""

    modalidade: Modalidade
    valor_pagamento: int
    data_pagamento: str
    favorecido: Favorecido
    numero_documento: str
    moeda: str = "BRL"
    data_vencimento: Optional[str] = None
    nosso_numero: Optional[str] = None
    informacao_favorecido: Optional[str] = None
    informacao_recebedor: Optional[str] = None
    dados_pix: Optional[DadosPix] = None
    dados_boleto: Optional[DadosBoleto] = None
    pagador: Optional[Pagador] = None
    finalidade_doc: Optional[str] = None
    finalidade_ted: Optional[str] = None
    codigo_finalidade_complementar: Optional[str] = None
    codigo_finalidade_complementar_banco: Optional[str] = None
    codigo_camara_centralizadora: Optional[str] = None
    codigo_forma_lancamento: Optional[str] = None
    tipo_compromisso: Optional[str] = None
    lote: Optional[int] = None
    aviso_favorecido: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.modalidade, Modalidade):
            raise ValueError("modalidade deve ser uma instância de Modalidade")
        if self.valor_pagamento <= 0:
            raise ValueError("valor_pagamento deve ser maior que zero")
        if not FormatadorCNAB.validar_data(self.data_pagamento):
            raise ValueError("data_pagamento deve estar no formato DDMMAAAA")
        if self.data_vencimento and not FormatadorCNAB.validar_data(self.data_vencimento):
            raise ValueError("data_vencimento deve estar no formato DDMMAAAA")
        if self.modalidade == Modalidade.BOLETO and not self.dados_boleto:
            raise ValueError("Pagamentos BOLETO requerem dados_boleto")
        if self.modalidade in (Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO) and not self.dados_pix:
            raise ValueError("Pagamentos PIX requerem dados_pix")


@dataclass
class DadosLote:
    """Dados consolidados do lote de pagamentos."""

    numero_lote: int
    tipo_operacao: str
    tipo_servico: str
    forma_lancamento: str
    versao_layout: str
    tipo_compromisso: str
    mensagem: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.tipo_operacao) != 1:
            raise ValueError("tipo_operacao deve ter 1 caractere")
        if len(self.tipo_servico) != 2 or not self.tipo_servico.isdigit():
            raise ValueError("tipo_servico deve ter 2 dígitos numéricos")
        if len(self.forma_lancamento) != 2 or not self.forma_lancamento.isdigit():
            raise ValueError("forma_lancamento deve ter 2 dígitos numéricos")
        if len(self.versao_layout) != 3 or not self.versao_layout.isdigit():
            raise ValueError("versao_layout deve ter 3 dígitos numéricos")
        if len(self.tipo_compromisso) != 2 or not self.tipo_compromisso.isdigit():
            raise ValueError("tipo_compromisso deve ter 2 dígitos numéricos")


@dataclass
class DadosArquivo:
    """Metadados do arquivo CNAB."""

    data_geracao: str
    hora_geracao: str
    numero_sequencial_arquivo: int
    versao_layout_arquivo: str
    densidade_gravacao: str

    def __post_init__(self) -> None:
        if not FormatadorCNAB.validar_data(self.data_geracao):
            raise ValueError("data_geracao deve estar no formato DDMMAAAA")
        if not FormatadorCNAB.validar_hora(self.hora_geracao):
            raise ValueError("hora_geracao deve estar no formato HHMMSS")
        if len(self.versao_layout_arquivo) != 3 or not self.versao_layout_arquivo.isdigit():
            raise ValueError("versao_layout_arquivo deve ter 3 dígitos numéricos")
        if len(self.densidade_gravacao) != 5 or not self.densidade_gravacao.isdigit():
            raise ValueError("densidade_gravacao deve ter 5 dígitos numéricos")


class FormatadorCNAB:
    """Utilitários de formatação exigidos pelo layout CNAB 240."""

    @staticmethod
    def remover_acentos(texto: str) -> str:
        if not texto:
            return ""
        nfkd = unicodedata.normalize("NFKD", texto)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    @staticmethod
    def limpar_numerico(valor: str) -> str:
        return re.sub(r"\D", "", valor or "")

    @staticmethod
    def formatar_numerico(valor: Any, tamanho: int, preencher_com: str = "0") -> str:
        valor_str = str(valor or "")
        if len(valor_str) > tamanho:
            logger.warning("Valor numérico '%s' excede tamanho %s. Truncando.", valor_str, tamanho)
            valor_str = valor_str[-tamanho:]
        return valor_str.rjust(tamanho, preencher_com)

    @staticmethod
    def formatar_alfanumerico(valor: Optional[str], tamanho: int, preencher_com: str = " ") -> str:
        valor = valor or ""
        valor_limpo = FormatadorCNAB.remover_acentos(valor)
        valor_limpo = "".join(c for c in valor_limpo if c.isprintable() or c == " ")
        if len(valor_limpo) > tamanho:
            logger.warning("Valor alfanumérico '%s' excede tamanho %s. Truncando.", valor, tamanho)
            valor_limpo = valor_limpo[:tamanho]
        return valor_limpo.ljust(tamanho, preencher_com)

    @staticmethod
    def formatar_valor_monetario(centavos: int, tamanho: int) -> str:
        return FormatadorCNAB.formatar_numerico(centavos, tamanho)

    @staticmethod
    def formatar_codigo_barras(codigo: str) -> str:
        codigo_limpo = FormatadorCNAB.limpar_numerico(codigo)
        if len(codigo_limpo) != 44:
            raise ValueError("Código de barras deve conter 44 dígitos")
        return codigo_limpo

    @staticmethod
    def validar_data(data: str) -> bool:
        if not data or len(data) != 8 or not data.isdigit():
            return False
        try:
            datetime(int(data[4:8]), int(data[2:4]), int(data[0:2]))
        except ValueError:
            return False
        return True

    @staticmethod
    def validar_hora(hora: str) -> bool:
        if not hora or len(hora) != 6 or not hora.isdigit():
            return False
        try:
            hh = int(hora[0:2])
            mm = int(hora[2:4])
            ss = int(hora[4:6])
        except ValueError:
            return False
        return 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59

    @staticmethod
    def formatar_linha(conteudo: str) -> str:
        if len(conteudo) > 240:
            raise ValueError(f"Linha excede 240 caracteres: {len(conteudo)}")
        return conteudo.ljust(240)


class ValidadorCNAB:
    """Valida objetos CNAB antes da geração do arquivo."""

    @staticmethod
    def validar_bank_profile(profile: BankProfile) -> List[str]:
        erros: List[str] = []
        modalidades_obrigatorias = {Modalidade.CREDITO_CONTA, Modalidade.TED, Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO}
        for modalidade in modalidades_obrigatorias:
            if modalidade not in profile.modalidades:
                erros.append(f"Modalidade {modalidade.value} não configurada no bank_profile")
        return erros

    @staticmethod
    def validar_empresa(empresa: Empresa) -> List[str]:
        erros: List[str] = []
        if not empresa.nome_empresa:
            erros.append("nome_empresa é obrigatório")
        if not empresa.convenio:
            erros.append("convenio é obrigatório")
        if not empresa.agencia_mantenedora:
            erros.append("agencia_mantenedora é obrigatória")
        if not empresa.numero_conta:
            erros.append("numero_conta é obrigatório")
        if empresa.cep is None or len(empresa.cep) != 8:
            erros.append("cep deve ter 8 dígitos")
        if empresa.uf is None or len(empresa.uf) != 2:
            erros.append("uf deve ter 2 caracteres")
        return erros

    @staticmethod
    def validar_pagamento(pagamento: Pagamento, bank_profile: BankProfile) -> List[str]:
        erros: List[str] = []

        if not pagamento.numero_documento:
            erros.append("numero_documento é obrigatório")
        if pagamento.modalidade not in bank_profile.modalidades:
            erros.append(f"Modalidade {pagamento.modalidade.value} não configurada para o banco")

        favorecido = pagamento.favorecido
        if not favorecido.nome_favorecido:
            erros.append("favorecido.nome_favorecido é obrigatório")
        if favorecido.tipo_inscricao not in (1, 2):
            erros.append("favorecido.tipo_inscricao deve ser 1 ou 2")

        if pagamento.modalidade in (Modalidade.CREDITO_CONTA, Modalidade.TED):
            if not favorecido.banco:
                erros.append("favorecido.banco é obrigatório para crédito conta/TED")
            if not favorecido.agencia:
                erros.append("favorecido.agencia é obrigatória para crédito conta/TED")
            if not favorecido.conta:
                erros.append("favorecido.conta é obrigatória para crédito conta/TED")

        if pagamento.modalidade == Modalidade.PIX_CHAVE:
            if not pagamento.dados_pix:
                erros.append("dados_pix é obrigatório para PIX_CHAVE")
            else:
                if not pagamento.dados_pix.tipo_chave:
                    erros.append("PIX_CHAVE exige tipo_chave")
                if not pagamento.dados_pix.chave:
                    erros.append("PIX_CHAVE exige chave PIX")
            if bank_profile.exige_ispb_favorecido and not favorecido.ispb:
                erros.append("Banco exige favorecido.ispb para PIX")

        if pagamento.modalidade == Modalidade.PIX_QR_DINAMICO:
            pix = pagamento.dados_pix
            # Relaxa validação: Se tem chave identificada, trata como PIX normal
            # Apenas exige payload/txid se realmente for QR dinâmico (sem chave)
            if pix and pix.chave and pix.tipo_chave:
                # Tem chave identificada → será tratado como PIX_CHAVE na prática
                logger.debug("PIX_QR_DINAMICO com chave identificada será processado como PIX_CHAVE")
            else:
                # QR dinâmico real → exige dados do QR
                if not pix or (not pix.payload_emv and not pix.url_qr):
                    erros.append("PIX_QR_DINAMICO exige payload EMV ou URL do QR")
                if not pix or not pix.txid:
                    erros.append("PIX_QR_DINAMICO exige txid")
                if not pagamento.pagador:
                    erros.append("PIX_QR_DINAMICO exige dados do pagador para segmento J52")

        if pagamento.modalidade == Modalidade.BOLETO:
            if not pagamento.dados_boleto:
                erros.append("dados_boleto é obrigatório para boletos")

        if pagamento.modalidade == Modalidade.CONVENIO:
            # Convênios/Tributos/Concessionárias também usam dados_boleto (armazena código de barras)
            if not pagamento.dados_boleto:
                erros.append("dados_boleto é obrigatório para convênios/tributos")

        return erros

