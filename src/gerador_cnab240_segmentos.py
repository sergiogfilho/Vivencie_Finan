#!/usr/bin/env python3
"""Geração dos segmentos CNAB 240 alinhados ao layout Sicoob."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import List, Optional, Tuple

from gerador_cnab240 import (
    BankProfile,
    DadosArquivo,
    DadosLote,
    FormatadorCNAB,
    Modalidade,
    Pagamento,
    TipoChavePix,
)


def parse_emvco_pix(payload: str) -> Tuple[Optional[str], Optional[TipoChavePix], str]:
    """
    Faz parse de payload EMVCo (Pix Copia e Cola) para extrair a chave Pix real.
    
    O padrão EMVCo/BR Code usa estrutura TLV (Tag-Length-Value):
    - Cada campo tem: ID (2 dígitos) + Tamanho (2 dígitos) + Valor
    - Tag 26: Merchant Account Information (contém dados PIX)
    - Dentro da Tag 26:
      - Sub-tag 00: GUI (br.gov.bcb.pix)
      - Sub-tag 01: Chave PIX (QR Estático) ou URL (QR Dinâmico)
      - Sub-tag 25: URL do payload dinâmico
    
    Args:
        payload: String do Pix Copia e Cola (inicia com 000201...)
    
    Returns:
        Tuple com:
        - chave_pix: Chave extraída (pode ser telefone, email, CPF, CNPJ, UUID ou URL)
        - tipo_chave: TipoChavePix identificado
        - forma_iniciacao: Código para campo 15-17 do Segmento B ('01 ', '02 ', etc.)
    """
    if not payload or not isinstance(payload, str):
        return None, None, "04 "
    
    payload = payload.strip()
    
    # Verificar se é payload EMVCo (deve começar com 000201)
    if not payload.startswith('000201'):
        # Não é EMVCo, retornar como está (será tratado como chave normal)
        return payload, None, "04 "
    
    def parse_tlv(data: str, start: int = 0) -> dict:
        """Parse recursivo da estrutura TLV."""
        result = {}
        pos = start
        while pos < len(data) - 3:  # Mínimo: 2 (ID) + 2 (length)
            try:
                tag_id = data[pos:pos+2]
                if not tag_id.isdigit():
                    break
                length = int(data[pos+2:pos+4])
                value = data[pos+4:pos+4+length]
                result[tag_id] = value
                pos = pos + 4 + length
            except (ValueError, IndexError):
                break
        return result
    
    # Parse do payload principal
    campos = parse_tlv(payload)
    
    # Buscar Merchant Account Information (tags 26-51 para PIX)
    chave_pix = None
    url_dinamica = None
    
    # Tag 26 é a padrão para PIX no Brasil
    merchant_info = campos.get('26', '')
    if merchant_info:
        # Parse dos sub-campos dentro da tag 26
        sub_campos = parse_tlv(merchant_info)
        
        # Sub-tag 00: GUI (deve ser br.gov.bcb.pix)
        gui = sub_campos.get('00', '')
        
        if 'bcb.pix' in gui.lower() or 'pix' in gui.lower():
            # Sub-tag 01: Chave PIX (QR Estático)
            chave_pix = sub_campos.get('01', '')
            
            # Sub-tag 25: URL para QR Code Dinâmico
            url_dinamica = sub_campos.get('25', '')
            
            # Se não encontrou em 01, pode estar em outras sub-tags
            if not chave_pix and not url_dinamica:
                # Tentar sub-tag 02 (algumas implementações usam)
                chave_pix = sub_campos.get('02', '')
    
    # Se encontrou URL dinâmica, usar ela como chave
    if url_dinamica:
        chave_pix = url_dinamica
    
    # Se ainda não encontrou, procurar em outras tags de merchant account (27-51)
    if not chave_pix:
        for tag_num in range(27, 52):
            tag_str = f"{tag_num:02d}"
            merchant_info_alt = campos.get(tag_str, '')
            if merchant_info_alt:
                sub_campos_alt = parse_tlv(merchant_info_alt)
                gui_alt = sub_campos_alt.get('00', '')
                if 'pix' in gui_alt.lower() or 'bcb' in gui_alt.lower():
                    chave_pix = sub_campos_alt.get('01', '') or sub_campos_alt.get('25', '')
                    if chave_pix:
                        break
    
    if not chave_pix:
        # Não conseguiu extrair - retornar None para sinalizar erro
        # Default '04 ' = Chave Aleatória
        return None, None, "04 "
    
    # Identificar tipo da chave extraída
    tipo_chave, forma_iniciacao = _identificar_tipo_chave_pix(chave_pix)
    
    return chave_pix, tipo_chave, forma_iniciacao


def _identificar_tipo_chave_pix(chave: str) -> Tuple[TipoChavePix, str]:
    """
    Identifica o tipo de chave PIX e retorna o código de Forma de Iniciação.
    
    Códigos CNAB 240 Sicoob (campo 15-17 Segmento B) - MAPEAMENTO OFICIAL:
    '01 ' = Telefone (+55...)
    '02 ' = Email (@...)
    '03 ' = CPF ou CNPJ (apenas números)
    '04 ' = Chave Aleatória (UUID/EVP)
    '05 ' = Dados Bancários (agência/conta)
    
    IMPORTANTE: Códigos '06' e '07' NÃO EXISTEM no padrão Sicoob!
    
    Args:
        chave: Chave PIX a ser identificada
    
    Returns:
        Tuple (TipoChavePix, código_forma_iniciacao)
    """
    chave = chave.strip()
    chave_lower = chave.lower()
    
    # UUID/EVP (chave aleatória) - 36 caracteres com hífens
    # Verifica PRIMEIRO para evitar confusão com outros tipos
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    if re.match(uuid_pattern, chave_lower):
        return TipoChavePix.ALEATORIA, "04 "  # Chave Aleatória = código 04
    
    # URL (QR Code Dinâmico) - trata como Chave Aleatória (código 04)
    # URLs são usadas em QR Codes dinâmicos mas o campo forma_iniciacao usa 04
    if chave_lower.startswith('http://') or chave_lower.startswith('https://'):
        return TipoChavePix.ALEATORIA, "04 "  # URL tratada como Aleatória
    
    # URL sem protocolo (comum em QR codes do BB/Sicoob)
    if '.com.br/' in chave_lower or 'qrcodepix' in chave_lower or 'pix/v2/' in chave_lower:
        return TipoChavePix.ALEATORIA, "04 "  # URL tratada como Aleatória
    
    # Telefone (+55...)
    if chave.startswith('+55') or chave.startswith('55'):
        numeros = re.sub(r'\D', '', chave)
        if len(numeros) >= 12 and len(numeros) <= 14:  # 55 + DDD + número
            return TipoChavePix.TELEFONE, "01 "
    
    # Email (contém @)
    if '@' in chave and '.' in chave.split('@')[-1]:
        return TipoChavePix.EMAIL, "02 "
    
    # CPF/CNPJ (apenas números)
    apenas_numeros = re.sub(r'\D', '', chave)
    
    # CPF/CNPJ (apenas números)
    apenas_numeros = re.sub(r'\D', '', chave)
    
    if len(apenas_numeros) == 11:
        # CPF ou telefone com 11 dígitos
        # Se começa com DDD válido (11-99), pode ser telefone
        ddd = int(apenas_numeros[:2])
        if 11 <= ddd <= 99:
            # Verificar se parece telefone (9 dígitos após DDD começando com 9)
            if apenas_numeros[2] == '9':
                return TipoChavePix.TELEFONE, "01 "
        # Caso contrário, assume CPF
        return TipoChavePix.CPF, "03 "  # CPF = código 03
    
    if len(apenas_numeros) == 14:
        return TipoChavePix.CNPJ, "03 "  # CNPJ = código 03 (mesmo que CPF)
    
    if len(apenas_numeros) == 10 or len(apenas_numeros) == 11:
        # Telefone com 10 ou 11 dígitos
        return TipoChavePix.TELEFONE, "01 "
    
    # Default: chave aleatória (UUID) = código 04
    return TipoChavePix.ALEATORIA, "04 "


class GeradorSegmentos:
    """Responsável por montar cada registro de 240 posições."""

    def __init__(self, bank_profile: BankProfile, empresa):
        self.bank_profile = bank_profile
        self.empresa = empresa
        self.fmt = FormatadorCNAB()

    def gerar_header_arquivo(self, dados_arquivo: DadosArquivo) -> str:
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += "0000"
        linha += "0"
        linha += self.fmt.formatar_alfanumerico("", 9)
        linha += str(self.empresa.tipo_inscricao)
        linha += self.fmt.formatar_numerico(self.empresa.numero_inscricao, 14)
        linha += self.fmt.formatar_alfanumerico(self.empresa.convenio, 20)
        linha += self.fmt.formatar_numerico(self.empresa.agencia_mantenedora, 5)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_agencia or " ", 1)
        linha += self.fmt.formatar_numerico(self.empresa.numero_conta, 12)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_conta or " ", 1)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_agencia_conta or " ", 1)
        linha += self.fmt.formatar_alfanumerico(self.empresa.nome_empresa, 30)
        linha += self.fmt.formatar_alfanumerico(self.bank_profile.nome_banco, 30)
        linha += self.fmt.formatar_alfanumerico("", 10)
        linha += "1"
        linha += dados_arquivo.data_geracao
        linha += dados_arquivo.hora_geracao
        linha += self.fmt.formatar_numerico(dados_arquivo.numero_sequencial_arquivo, 6)
        linha += self.bank_profile.versao_layout_arquivo
        linha += dados_arquivo.densidade_gravacao
        linha += self.fmt.formatar_alfanumerico("", 20)
        linha += self.fmt.formatar_alfanumerico("", 20)
        linha += self.fmt.formatar_alfanumerico("", 29)
        return self.fmt.formatar_linha(linha)

    def gerar_header_lote(self, dados_lote: DadosLote) -> str:
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += self.fmt.formatar_numerico(dados_lote.numero_lote, 4)
        linha += "1"
        linha += dados_lote.tipo_operacao
        linha += dados_lote.tipo_servico
        linha += dados_lote.forma_lancamento
        linha += dados_lote.versao_layout
        linha += " "
        linha += str(self.empresa.tipo_inscricao)
        linha += self.fmt.formatar_numerico(self.empresa.numero_inscricao, 14)
        linha += self.fmt.formatar_alfanumerico(self.empresa.convenio, 20)
        linha += self.fmt.formatar_numerico(self.empresa.agencia_mantenedora, 5)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_agencia or " ", 1)
        linha += self.fmt.formatar_numerico(self.empresa.numero_conta, 12)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_conta or " ", 1)
        linha += self.fmt.formatar_alfanumerico(self.empresa.dv_agencia_conta or " ", 1)
        linha += self.fmt.formatar_alfanumerico(self.empresa.nome_empresa, 30)
        linha += self.fmt.formatar_alfanumerico(dados_lote.mensagem or "", 40)
        linha += self.fmt.formatar_alfanumerico(self.empresa.logradouro or "", 30)
        numero_local = self.fmt.formatar_numerico(
            self.fmt.limpar_numerico(self.empresa.numero_endereco or "0"), 5
        )
        linha += numero_local
        linha += self.fmt.formatar_alfanumerico(self.empresa.complemento or "", 15)
        linha += self.fmt.formatar_alfanumerico(self.empresa.cidade or "", 20)
        linha += self.fmt.formatar_numerico(self.empresa.cep or "00000000", 8)
        linha += self.fmt.formatar_alfanumerico(self.empresa.uf or "  ", 2)
        linha += self.fmt.formatar_alfanumerico("", 8)
        linha += self.fmt.formatar_alfanumerico("", 10)
        return self.fmt.formatar_linha(linha)

    def gerar_segmento_a(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        config = self.bank_profile.get_modalidade_config(pagamento.modalidade)
        camara = pagamento.codigo_camara_centralizadora or config.camara_centralizadora

        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += self.fmt.formatar_numerico(lote, 4)
        linha += "3"
        linha += self.fmt.formatar_numerico(sequencial, 5)
        linha += "A"
        linha += "0"
        linha += "00"
        linha += self.fmt.formatar_numerico(camara, 3)
        linha += self.fmt.formatar_numerico(pagamento.favorecido.banco or "000", 3)
        linha += self.fmt.formatar_numerico(pagamento.favorecido.agencia or "0", 5)
        linha += self.fmt.formatar_alfanumerico(pagamento.favorecido.dv_agencia or " ", 1)
        linha += self.fmt.formatar_numerico(pagamento.favorecido.conta or "0", 12)
        linha += self.fmt.formatar_alfanumerico(pagamento.favorecido.dv_conta or "0", 1)
        linha += self.fmt.formatar_alfanumerico(pagamento.favorecido.dv_agencia_conta or " ", 1)
        linha += self.fmt.formatar_alfanumerico(pagamento.favorecido.nome_favorecido, 30)
        linha += self.fmt.formatar_alfanumerico(pagamento.numero_documento, 20)
        linha += pagamento.data_pagamento
        linha += self.fmt.formatar_alfanumerico((pagamento.moeda or "BRL")[:3], 3)
        linha += self.fmt.formatar_numerico(0, 15)
        linha += self.fmt.formatar_valor_monetario(pagamento.valor_pagamento, 15)
        linha += self.fmt.formatar_alfanumerico("", 20)
        linha += "00000000"
        linha += self.fmt.formatar_numerico(0, 15)

        # Campo G031 - Informação 2 (pos 178-217, 40 chars)
        # Para PIX (Forma Lançamento 45): 38 espaços + tipo conta (2 dígitos)
        if pagamento.modalidade in (Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO):
            # Tipo conta: 01=Corrente, 02=Pagamento, 03=Poupança
            tipo_conta = "01"  # Padrão: Conta Corrente
            linha += self.fmt.formatar_alfanumerico("", 38)  # 38 espaços
            linha += tipo_conta  # 2 dígitos
        else:
            # Para outras modalidades, usa informação livre
            info = pagamento.informacao_favorecido or pagamento.informacao_recebedor or ""
            linha += self.fmt.formatar_alfanumerico(info, 40)

        # === CAMPOS DE FINALIDADE (Pos 218-230) - Conforme Manual CNAB 240 ===
        # P005 - Código Finalidade DOC (Pos 218-219, 2 chars) - Alfanumérico
        finalidade_doc = pagamento.finalidade_doc or config.finalidade_banco or ""
        linha += self.fmt.formatar_alfanumerico(finalidade_doc, 2)
        
        # P011 - Código Finalidade TED (Pos 220-224, 5 chars) - Alfanumérico (ex: 00010)
        # Obrigatório para TEDs (Câmara 018). Código BACEN de finalidade.
        # Exemplos: 00005=Fornecedores, 00010=Crédito em Conta, 00004=Salários
        if pagamento.modalidade == Modalidade.TED:
            finalidade_ted = pagamento.finalidade_ted or config.finalidade_bacen or "00010"
        else:
            # Para outras modalidades (PIX, Crédito Conta), deixar em branco
            finalidade_ted = ""
        linha += self.fmt.formatar_alfanumerico(finalidade_ted, 5)
        
        # P013 - Código Finalidade Complementar (Pos 225-226, 2 chars) - Alfanumérico
        finalidade_compl = pagamento.codigo_finalidade_complementar or ""
        linha += self.fmt.formatar_alfanumerico(finalidade_compl, 2)
        
        # G004 - CNAB/Brancos (Pos 227-229, 3 chars) - Alfanumérico
        linha += self.fmt.formatar_alfanumerico("", 3)
        
        # P006 - Aviso ao Favorecido (Pos 230, 1 char) - Numérico
        linha += "1" if pagamento.aviso_favorecido else "0"
        
        # G059 - Ocorrências para Retorno (Pos 231-240, 10 chars) - Alfanumérico (brancos na remessa)
        linha += self.fmt.formatar_alfanumerico("", 10)
        
        return self.fmt.formatar_linha(linha)

    def gerar_segmento_b(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        """
        Gera Segmento B conforme layout e modalidade
        - PIX (Layout 045): Inclui chave PIX
        - Boleto (Layout 040): Inclui dados de boleto
        - TED/Transferência (Layout 045): Dados bancários
        """
        # Determina se é PIX pelo modalidade
        eh_pix = pagamento.modalidade in (Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO)
        
        if eh_pix:
            return self._gerar_segmento_b_pix(pagamento, lote, sequencial)
        else:
            return self._gerar_segmento_b_boleto(pagamento, lote, sequencial)
    
    def _gerar_segmento_b_pix(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        """
        Gera Segmento B para PIX (Layout 045) conforme Guia Sicoob pág. 677
        Estrutura específica com chave PIX nas posições 128-226
        """
        fav = pagamento.favorecido
        linha = ""
        
        # Pos 1-3: Código Banco
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        # Pos 4-7: Lote
        linha += self.fmt.formatar_numerico(lote, 4)
        # Pos 8: Tipo Registro "3"
        linha += "3"
        # Pos 9-13: Sequencial
        linha += self.fmt.formatar_numerico(sequencial, 5)
        # Pos 14: Segmento "B"
        linha += "B"
        # Pos 15-17: Forma de Iniciação (PIX) - ALFANUMÉRICO (ex: '03 ')
        linha += self.fmt.formatar_alfanumerico(self._forma_inicializacao_segmento_b(pagamento), 3)
        # Pos 18: Tipo Inscrição Favorecido
        linha += str(fav.tipo_inscricao)
        # Pos 19-32: CPF/CNPJ Favorecido (14)
        linha += self.fmt.formatar_numerico(fav.numero_inscricao, 14)
        # Pos 33-62: Logradouro (30)
        endereco = (fav.logradouro or "")[:30]
        linha += self.fmt.formatar_alfanumerico(endereco, 30)
        # Pos 63-67: Número (5)
        numero_local = self.fmt.limpar_numerico(fav.numero_endereco or "") or "0"
        linha += self.fmt.formatar_numerico(numero_local, 5)
        # Pos 68-82: Complemento (15)
        linha += self.fmt.formatar_alfanumerico(fav.complemento or "", 15)
        # Pos 83-97: Bairro (15)
        linha += self.fmt.formatar_alfanumerico(fav.bairro or "", 15)
        # Pos 98-117: Cidade (20)
        linha += self.fmt.formatar_alfanumerico(fav.cidade or "", 20)
        # Pos 118-125: CEP (8)
        linha += self.fmt.formatar_numerico(fav.cep or "00000000", 8)
        # Pos 126-127: UF (2)
        linha += self.fmt.formatar_alfanumerico(fav.uf or "  ", 2)
        
        # **Pos 128-226: Chave PIX (99 posições)** - CAMPO CRÍTICO
        chave_pix = self._formatar_chave_pix(pagamento)
        linha += self.fmt.formatar_alfanumerico(chave_pix, 99)
        
        # Pos 227-240: Uso Exclusivo FEBRABAN/CNAB (14)
        linha += self.fmt.formatar_alfanumerico("", 14)
        
        return self.fmt.formatar_linha(linha)
    
    def _gerar_segmento_b_boleto(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        """
        Gera Segmento B para Boleto/TED (Layout 040/045 tradicional)
        Estrutura conforme manual Sicoob seção 7.3
        
        Layout completo:
        Pos 01-03: Código Banco (3) - Numérico
        Pos 04-07: Lote (4) - Numérico
        Pos 08: Tipo Registro (1) - "3"
        Pos 09-13: Nº Sequencial (5) - Numérico
        Pos 14: Segmento (1) - "B"
        Pos 15-17: CNAB (3) - Alfa (brancos)
        Pos 18: Tipo Inscrição Favorecido (1) - Numérico
        Pos 19-32: Nº Inscrição Favorecido (14) - Numérico
        Pos 33-62: Logradouro (30) - Alfa
        Pos 63-67: Número (5) - Numérico
        Pos 68-82: Complemento (15) - Alfa
        Pos 83-97: Bairro (15) - Alfa
        Pos 98-117: Cidade (20) - Alfa
        Pos 118-122: CEP (5) - Numérico
        Pos 123-125: Complemento CEP (3) - Alfa
        Pos 126-127: UF (2) - Alfa
        Pos 128-135: Data Vencimento (8) - Numérico
        Pos 136-150: Valor Documento (15,2) - Numérico
        Pos 151-165: Abatimento (15,2) - Numérico
        Pos 166-180: Desconto (15,2) - Numérico
        Pos 181-195: Mora (15,2) - Numérico
        Pos 196-210: Multa (15,2) - Numérico
        Pos 211-225: Código/Documento Favorecido (15) - Alfa
        Pos 226: Aviso ao Favorecido (1) - Numérico
        Pos 227-232: Código UG Centralizadora (6) - Numérico
        Pos 233-240: CNAB (8) - Alfa
        """
        fav = pagamento.favorecido
        linha = ""
        
        # Pos 01-03: Código Banco (3)
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        # Pos 04-07: Lote (4)
        linha += self.fmt.formatar_numerico(lote, 4)
        # Pos 08: Tipo Registro (1)
        linha += "3"
        # Pos 09-13: Nº Sequencial (5)
        linha += self.fmt.formatar_numerico(sequencial, 5)
        # Pos 14: Segmento (1)
        linha += "B"
        # Pos 15-17: CNAB/Forma Inicialização (3) - Alfa
        linha += self.fmt.formatar_alfanumerico(self._forma_inicializacao_segmento_b(pagamento), 3)
        # Pos 18: Tipo Inscrição Favorecido (1)
        linha += str(fav.tipo_inscricao)
        # Pos 19-32: Nº Inscrição Favorecido (14)
        linha += self.fmt.formatar_numerico(fav.numero_inscricao, 14)
        
        # === CAMPOS DE ENDEREÇO (Opcionais - Preencher com valores padrão se vazios) ===
        # Pos 33-62: Logradouro (30) - Alfa (brancos se vazio)
        logradouro = (fav.logradouro or "")[:30] if fav.logradouro else ""
        linha += self.fmt.formatar_alfanumerico(logradouro, 30)
        # Pos 63-67: Número (5) - Numérico (zeros se vazio)
        numero_local = self.fmt.limpar_numerico(fav.numero_endereco or "") if fav.numero_endereco else "0"
        linha += self.fmt.formatar_numerico(numero_local, 5)
        # Pos 68-82: Complemento (15) - Alfa (brancos se vazio)
        linha += self.fmt.formatar_alfanumerico(fav.complemento or "", 15)
        # Pos 83-97: Bairro (15) - Alfa (brancos se vazio)
        linha += self.fmt.formatar_alfanumerico(fav.bairro or "", 15)
        # Pos 98-117: Cidade (20) - Alfa (brancos se vazio)
        linha += self.fmt.formatar_alfanumerico(fav.cidade or "", 20)
        # Pos 118-122: CEP (5) - Numérico (zeros se vazio)
        cep_principal = (fav.cep or "00000")[:5] if fav.cep else "00000"
        linha += self.fmt.formatar_numerico(cep_principal, 5)
        # Pos 123-125: Complemento CEP (3) - Alfa (brancos se vazio)
        cep_complemento = (fav.cep or "")[5:8] if fav.cep and len(fav.cep) > 5 else ""
        linha += self.fmt.formatar_alfanumerico(cep_complemento, 3)
        # Pos 126-127: UF (2) - Alfa (brancos se vazio)
        linha += self.fmt.formatar_alfanumerico(fav.uf or "", 2)
        
        # === CAMPOS DE VALORES ===
        # Pos 128-135: Data Vencimento (8) - Numérico (zeros se não houver)
        data_venc = pagamento.data_vencimento if pagamento.data_vencimento else "00000000"
        linha += self.fmt.formatar_numerico(data_venc, 8)
        # Pos 136-150: Valor Documento (15,2) - Numérico
        valor_documento = pagamento.dados_boleto.valor_documento if pagamento.dados_boleto else pagamento.valor_pagamento
        linha += self.fmt.formatar_valor_monetario(valor_documento or 0, 15)
        # Pos 151-165: Abatimento (15,2) - Numérico
        linha += self.fmt.formatar_numerico(0, 15)
        # Pos 166-180: Desconto (15,2) - Numérico
        linha += self.fmt.formatar_numerico(0, 15)
        # Pos 181-195: Mora (15,2) - Numérico
        linha += self.fmt.formatar_numerico(0, 15)
        # Pos 196-210: Multa (15,2) - Numérico
        linha += self.fmt.formatar_numerico(0, 15)
        # Pos 211-225: Código/Documento Favorecido (15) - Alfa
        linha += self.fmt.formatar_alfanumerico("", 15)
        # Pos 226: Aviso ao Favorecido (1) - Numérico (0 = sem aviso)
        linha += self.fmt.formatar_numerico(0, 1)
        # Pos 227-232: Código UG Centralizadora (6) - Numérico (uso SIAPE, preencher com zeros)
        linha += self.fmt.formatar_numerico(0, 6)
        # Pos 233-240: CNAB (8) - Alfa (brancos)
        linha += self.fmt.formatar_alfanumerico("", 8)
        
        return self.fmt.formatar_linha(linha)

    def gerar_segmento_j(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        dados = self._obter_dados_boleto(pagamento)
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += self.fmt.formatar_numerico(lote, 4)
        linha += "3"
        linha += self.fmt.formatar_numerico(sequencial, 5)
        linha += "J"
        tipo_movimento = getattr(pagamento, "tipo_movimento", None)
        linha += self.fmt.formatar_numerico(0 if tipo_movimento is None else tipo_movimento, 1)
        codigo_instrucao = getattr(pagamento, "codigo_instrucao", None)
        if codigo_instrucao is None:
            codigo_instrucao = getattr(pagamento, "codigo_movimento", None)
        linha += self.fmt.formatar_numerico(0 if codigo_instrucao is None else codigo_instrucao, 2)
        linha += self.fmt.formatar_codigo_barras(dados.codigo_barras)
        benef = dados.nome_beneficiario or pagamento.favorecido.nome_favorecido
        linha += self.fmt.formatar_alfanumerico(benef, 30)
        linha += self.fmt.formatar_numerico(dados.data_vencimento or pagamento.data_vencimento or "00000000", 8)
        linha += self.fmt.formatar_valor_monetario(dados.valor_documento or pagamento.valor_pagamento, 15)
        valor_descontos = (dados.desconto or 0) + (dados.abatimento or 0)
        linha += self.fmt.formatar_valor_monetario(valor_descontos, 15)
        valor_acrescimos = (dados.mora or 0) + (dados.multa or 0)
        linha += self.fmt.formatar_valor_monetario(valor_acrescimos, 15)
        data_pagamento = pagamento.data_pagamento or pagamento.data_vencimento or "00000000"
        linha += self.fmt.formatar_numerico(data_pagamento, 8)
        linha += self.fmt.formatar_valor_monetario(pagamento.valor_pagamento, 15)
        linha += self.fmt.formatar_numerico(0, 15)
        linha += self.fmt.formatar_alfanumerico(pagamento.numero_documento, 20)
        nosso_numero = pagamento.nosso_numero or dados.documento_beneficiario or pagamento.numero_documento
        linha += self.fmt.formatar_alfanumerico(nosso_numero, 20)
        linha += self.fmt.formatar_numerico(self._codigo_moeda(pagamento.moeda), 2)
        linha += self.fmt.formatar_alfanumerico("", 6)
        linha += self.fmt.formatar_alfanumerico("", 10)
        return self.fmt.formatar_linha(linha)

    def gerar_segmento_o(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        """
        Gera Segmento O para pagamento de convênios, tributos e concessionárias
        Conforme manual CNAB 240 Sicoob - Seção 9.2
        
        Layout (240 posições):
        - Pos 1-3: Código Banco (756)
        - Pos 4-7: Lote de Serviço
        - Pos 8: Tipo Registro (3)
        - Pos 9-13: Nº Sequencial do Registro no Lote
        - Pos 14: Segmento (O)
        - Pos 15: Tipo de Movimento (0=Inclusão)
        - Pos 16-17: Código da Instrução de Movimento (00)
        - Pos 18-61: Código de Barras (44 posições)
        - Pos 62-91: Nome da Concessionária/Órgão Público (30)
        - Pos 92-99: Data do Vencimento (DDMMAAAA)
        - Pos 100-107: Data do Pagamento (DDMMAAAA)
        - Pos 108-122: Valor do Pagamento (13 inteiros + 2 decimais)
        - Pos 123-142: Seu Número (Nº Doc Empresa) (20)
        - Pos 143-162: Nosso Número (Nº Doc Banco) (20)
        - Pos 163-230: Uso Exclusivo FEBRABAN/CNAB (68 brancos)
        - Pos 231-240: Códigos das Ocorrências (10) - só retorno
        """
        dados = self._obter_dados_boleto(pagamento)
        
        linha = ""
        # Pos 1-3: Código Banco (3)
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        # Pos 4-7: Lote (4)
        linha += self.fmt.formatar_numerico(lote, 4)
        # Pos 8: Tipo Registro "3" (1)
        linha += "3"
        # Pos 9-13: Sequencial (5)
        linha += self.fmt.formatar_numerico(sequencial, 5)
        # Pos 14: Segmento "O" (1)
        linha += "O"
        # Pos 15: Tipo de Movimento (1) - 0=Inclusão
        tipo_movimento = getattr(pagamento, "tipo_movimento", 0)
        linha += self.fmt.formatar_numerico(tipo_movimento, 1)
        # Pos 16-17: Código da Instrução de Movimento (2) - 00=Inclusão
        codigo_instrucao = getattr(pagamento, "codigo_instrucao", None)
        if codigo_instrucao is None:
            codigo_instrucao = getattr(pagamento, "codigo_movimento", 0)
        linha += self.fmt.formatar_numerico(codigo_instrucao, 2)
        # Pos 18-61: Código de Barras (44)
        linha += self.fmt.formatar_codigo_barras(dados.codigo_barras)
        # Pos 62-91: Nome da Concessionária/Órgão Público (30)
        nome_conc = dados.nome_beneficiario or pagamento.favorecido.nome_favorecido
        linha += self.fmt.formatar_alfanumerico(nome_conc, 30)
        # Pos 92-99: Data do Vencimento (8)
        data_vencimento = dados.data_vencimento or pagamento.data_vencimento or pagamento.data_pagamento or "00000000"
        linha += self.fmt.formatar_numerico(data_vencimento, 8)
        # Pos 100-107: Data do Pagamento (8)
        data_pagamento = pagamento.data_pagamento or pagamento.data_vencimento or "00000000"
        linha += self.fmt.formatar_numerico(data_pagamento, 8)
        # Pos 108-122: Valor do Pagamento (15 = 13 inteiros + 2 decimais)
        linha += self.fmt.formatar_valor_monetario(pagamento.valor_pagamento, 15)
        # Pos 123-142: Seu Número (20) - Nº documento atribuído pela empresa
        linha += self.fmt.formatar_alfanumerico(pagamento.numero_documento, 20)
        # Pos 143-162: Nosso Número (20) - Nº documento atribuído pelo banco (vazio na remessa)
        nosso_numero = pagamento.nosso_numero or ""
        linha += self.fmt.formatar_alfanumerico(nosso_numero, 20)
        # Pos 163-230: Uso Exclusivo FEBRABAN/CNAB (68 brancos)
        linha += self.fmt.formatar_alfanumerico("", 68)
        # Pos 231-240: Ocorrências (10) - só usado no retorno, brancos na remessa
        linha += self.fmt.formatar_alfanumerico("", 10)
        
        return self.fmt.formatar_linha(linha)

    def gerar_segmento_j52(self, pagamento: Pagamento, lote: int, sequencial: int) -> str:
        """
        Gera Segmento J-52 (Complemento do J para boletos)
        Formato correto: J [espaço] 00 52
        Posições 14-19: J (14), Espaço (15), Movimento 00 (16-17), Código 52 (18-19)
        """
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += self.fmt.formatar_numerico(lote, 4)
        linha += "3"
        linha += self.fmt.formatar_numerico(sequencial, 5)
        linha += "J"  # Segmento J (pos 14)
        linha += " "  # Espaço em branco (pos 15)
        linha += "00"  # Tipo movimento (pos 16-17)
        linha += "52"  # Código registro opcional (pos 18-19)

        # Sacado = Pagador customizado (se houver) ou Empresa (quem paga o boleto)
        sacado_tipo, sacado_numero, sacado_nome = self._obter_dados_sacado(pagamento)
        linha += self.fmt.formatar_numerico(sacado_tipo, 1)
        linha += self.fmt.formatar_numerico(sacado_numero, 15)
        linha += self.fmt.formatar_alfanumerico(sacado_nome, 40)

        # Cedente = Favorecido (quem vai receber o pagamento)
        fav = pagamento.favorecido
        linha += self.fmt.formatar_numerico(fav.tipo_inscricao, 1)
        linha += self.fmt.formatar_numerico(fav.numero_inscricao, 15)
        linha += self.fmt.formatar_alfanumerico(fav.nome_favorecido, 40)

        sacador_tipo, sacador_numero, sacador_nome, cnab = self._obter_dados_sacador(pagamento)
        linha += self.fmt.formatar_numerico(sacador_tipo, 1)
        linha += self.fmt.formatar_numerico(sacador_numero, 15)
        linha += self.fmt.formatar_alfanumerico(sacador_nome, 40)
        linha += self.fmt.formatar_alfanumerico(cnab, 52)
        return self.fmt.formatar_linha(linha)

    def _obter_dados_sacado(self, pagamento: Pagamento) -> tuple[int, str, str]:
        pagador = pagamento.pagador
        if pagador:
            return pagador.tipo_inscricao, pagador.numero_inscricao, pagador.nome
        return self.empresa.tipo_inscricao, self.empresa.numero_inscricao, self.empresa.nome_empresa

    def _obter_dados_sacador(self, pagamento: Pagamento) -> tuple[int, str, str, str]:
        pix = pagamento.dados_pix
        if not pix:
            return 0, "0", "", ""

        chave_visivel = pix.chave or pix.url_qr or pix.payload_emv or pix.descricao or ""
        chave_limpa = self.fmt.limpar_numerico(pix.chave) if pix.chave else ""
        tipo_chave = pix.tipo_chave

        if tipo_chave == TipoChavePix.CPF:
            tipo = 1
            numero = chave_limpa or pix.chave or "0"
        elif tipo_chave == TipoChavePix.CNPJ:
            tipo = 2
            numero = chave_limpa or pix.chave or "0"
        elif tipo_chave == TipoChavePix.TELEFONE:
            tipo = 1
            numero = chave_limpa or "0"
        else:
            tipo = 0
            numero = "0"

        cnab = pix.txid or pix.payload_emv or pix.url_qr or pix.descricao or ""
        return tipo, numero, chave_visivel, cnab

    def _obter_dados_boleto(self, pagamento: Pagamento):
        if pagamento.dados_boleto:
            return pagamento.dados_boleto

        codigo_barras = self._codigo_barras_fallback(pagamento)
        data_vencimento = pagamento.data_vencimento or pagamento.data_pagamento or "00000000"

        return SimpleNamespace(
            codigo_barras=codigo_barras,
            nome_beneficiario=pagamento.favorecido.nome_favorecido,
            data_vencimento=data_vencimento,
            valor_documento=pagamento.valor_pagamento,
            desconto=0,
            abatimento=0,
            mora=0,
            multa=0,
            documento_beneficiario=pagamento.favorecido.numero_inscricao,
        )

    def _codigo_barras_fallback(self, pagamento: Pagamento) -> str:
        partes = [
            self.fmt.limpar_numerico(pagamento.favorecido.numero_inscricao),
            self.fmt.limpar_numerico(pagamento.numero_documento),
        ]

        pix = pagamento.dados_pix
        if pix and pix.chave:
            partes.append(self.fmt.limpar_numerico(pix.chave))

        partes.append(pagamento.data_pagamento or "")
        partes.append(f"{pagamento.valor_pagamento:015d}")

        base = "".join(part for part in partes if part)
        if not base:
            base = "0"

        codigo = base[:44]
        if len(codigo) < 44:
            codigo = codigo.ljust(44, "0")

        return codigo

    def _forma_inicializacao_segmento_b(self, pagamento: Pagamento) -> str:
        """
        Define o código da forma de iniciação do pagamento PIX (campo 15-17).
        IMPORTANTE: Campo ALFANUMÉRICO com 3 caracteres (ex: '03 ' com espaço)
        Conforme Guia Sicoob CNAB 240 pág. 660-668:
        '01 ' = Telefone
        '02 ' = Email  
        '03 ' = CPF
        '04 ' = CNPJ
        '05 ' = Dados Bancários (agência/conta)
        '06 ' = Chave Aleatória (EVP/UUID)
        '07 ' = QR Code Dinâmico (URL)
        
        NOTA: Se a chave for um payload EMVCo (Pix Copia e Cola), 
        o tipo é identificado a partir da chave extraída.
        """

        if pagamento.modalidade not in (Modalidade.PIX_CHAVE, Modalidade.PIX_QR_DINAMICO):
            return "00 "

        pix = pagamento.dados_pix
        if not pix:
            return "00 "

        # === VERIFICAR SE É PAYLOAD EMVCo ===
        if pix.chave and pix.chave.strip().startswith('000201'):
            _, _, forma_iniciacao = parse_emvco_pix(pix.chave)
            return forma_iniciacao

        # === MAPEAMENTO PADRÃO - CÓDIGOS OFICIAIS SICOOB ===
        # '01' = Telefone, '02' = Email, '03' = CPF/CNPJ, '04' = Chave Aleatória, '05' = Dados Bancários
        # IMPORTANTE: Códigos '06' e '07' NÃO EXISTEM!
        mapa = {
            TipoChavePix.TELEFONE: "01 ",
            TipoChavePix.EMAIL: "02 ",
            TipoChavePix.CPF: "03 ",
            TipoChavePix.CNPJ: "03 ",      # CNPJ usa mesmo código que CPF
            TipoChavePix.ALEATORIA: "04 ",  # Chave Aleatória (UUID/EVP) = código 04
        }

        if pix.tipo_chave:
            return mapa.get(pix.tipo_chave, "04 ")

        # QR Code dinâmico (URL) - trata como Chave Aleatória
        if pix.payload_emv or pix.url_qr:
            return "04 "

        return "04 "  # Default: chave aleatória = código 04

    def _formatar_chave_pix(self, pagamento: Pagamento) -> str:
        """
        Formata a chave PIX conforme o tipo (pos 128-226, 99 posições)
        
        IMPORTANTE: Se a entrada for um payload EMVCo (Pix Copia e Cola),
        extrai a chave real antes de formatar. NUNCA grava o payload completo
        (000201...) no arquivo CNAB.
        
        Aplica regras específicas por tipo de chave:
        - TELEFONE: adiciona +55 se não houver
        - CPF/CNPJ: apenas dígitos
        - EMAIL/ALEATORIA/URL: formato original (truncado se > 99 chars)
        """
        if not pagamento.dados_pix or not pagamento.dados_pix.chave:
            return ""
        
        chave = pagamento.dados_pix.chave.strip()
        tipo = pagamento.dados_pix.tipo_chave
        
        # === PRÉ-PROCESSAMENTO: Detectar e parsear payload EMVCo ===
        if chave.startswith('000201'):
            chave_extraida, tipo_extraido, _ = parse_emvco_pix(chave)
            if chave_extraida:
                chave = chave_extraida
                # Atualizar tipo se foi identificado
                if tipo_extraido:
                    tipo = tipo_extraido
            else:
                # Falha ao extrair - logar aviso e usar vazio
                # (não podemos gravar o payload completo)
                return ""
        
        # === VALIDAÇÃO DE TAMANHO ===
        # Campo tem 99 posições - garantir que não excede
        if len(chave) > 99:
            # Para URLs, truncar preservando o máximo possível
            chave = chave[:99]
        
        # === FORMATAÇÃO POR TIPO ===
        
        # TELEFONE: deve ter formato +55DDNNNNNNNNN
        if tipo == TipoChavePix.TELEFONE:
            # Remove caracteres não numéricos
            apenas_digitos = ''.join(c for c in chave if c.isdigit())
            # Remove 55 do início se presente (será readicionado)
            if apenas_digitos.startswith('55'):
                apenas_digitos = apenas_digitos[2:]
            # Adiciona +55
            chave = f"+55{apenas_digitos}"
        
        # CPF/CNPJ: apenas dígitos
        elif tipo in (TipoChavePix.CPF, TipoChavePix.CNPJ):
            chave = ''.join(c for c in chave if c.isdigit())
        
        # EMAIL, ALEATORIA, URL: manter formato original
        # (já vem normalizado do validador ou do parser EMVCo)
        
        return chave

    def _codigo_moeda(self, moeda: str) -> str:
        codigo = (moeda or "").strip()
        if codigo.isdigit() and len(codigo) == 2:
            return codigo
        if codigo.upper() in {"BRL", "REAL", "REAIS", "R$"}:
            return "09"
        return "09"

    def gerar_trailer_lote(self, dados_lote: DadosLote, quantidade_registros: int, valor_total: int) -> str:
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += self.fmt.formatar_numerico(dados_lote.numero_lote, 4)
        linha += "5"
        linha += self.fmt.formatar_alfanumerico("", 9)
        linha += self.fmt.formatar_numerico(quantidade_registros, 6)
        linha += self.fmt.formatar_valor_monetario(valor_total, 18)
        linha += self.fmt.formatar_numerico(0, 18)
        linha += self.fmt.formatar_numerico(0, 6)
        linha += self.fmt.formatar_alfanumerico("", 165)
        linha += self.fmt.formatar_alfanumerico("", 10)
        return self.fmt.formatar_linha(linha)

    def gerar_trailer_arquivo(self, quantidade_lotes: int, quantidade_registros_total: int) -> str:
        linha = ""
        linha += self.fmt.formatar_numerico(self.bank_profile.codigo_banco_compensacao, 3)
        linha += "9999"
        linha += "9"
        linha += self.fmt.formatar_alfanumerico("", 9)
        linha += self.fmt.formatar_numerico(quantidade_lotes, 6)
        linha += self.fmt.formatar_numerico(quantidade_registros_total, 6)
        linha += self.fmt.formatar_numerico(0, 6)
        linha += self.fmt.formatar_alfanumerico("", 205)
        return self.fmt.formatar_linha(linha)

