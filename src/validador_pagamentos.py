"""
Módulo de Interpretação e Validação de Pagamentos
Baseado no PADRAO_INTERPRETACAO_OBS.md

Motor de análise de texto não estruturado do campo 'obs' para determinar
tipo de pagamento e validar dados extraídos.
"""

import re
import unicodedata
from typing import Optional, Tuple, Dict
from enum import Enum


class TipoPagamento(Enum):
    """Tipos de pagamento suportados"""
    BOLETO = "BOLETO"
    CONVENIO = "CONVENIO"  # Convênios, tributos, concessionárias (48 dígitos, começa com 8)
    TED = "TED"
    TRANSFERENCIA = "TRANSFERENCIA"
    PIX = "PIX"


class TipoChavePix(Enum):
    """Tipos de chave PIX"""
    CPF = "CPF"
    CNPJ = "CNPJ"
    TELEFONE = "TELEFONE"
    EMAIL = "EMAIL"
    EVP = "EVP"  # Chave aleatória UUID
    QR_CODE = "QR_CODE"


class ValidadorPagamentos:
    """Motor de interpretação e validação de pagamentos"""
    
    @staticmethod
    def normalizar_texto(texto: str) -> str:
        """
        Normaliza texto conforme Seção 2 do padrão:
        - Lowercase
        - Remove acentos
        - Remove espaços extras
        """
        if not texto:
            return ""
        
        # Lowercase
        texto = texto.lower()
        
        # Remove acentos (NFD normalization)
        texto = unicodedata.normalize('NFD', texto)
        texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
        
        # Limpa espaços extras
        texto = texto.strip()
        texto = re.sub(r'\s+', ' ', texto)
        
        return texto
    
    @staticmethod
    def limpar_numeros(valor: str) -> str:
        """Remove tudo exceto dígitos numéricos"""
        return ''.join(c for c in valor if c.isdigit())
    
    # ==================== VALIDADORES MATEMÁTICOS ====================
    
    @staticmethod
    def modulo_10(numero: str) -> int:
        """
        Algoritmo Módulo 10 (Seção 5)
        Retorna o dígito verificador calculado
        """
        soma = 0
        multiplicador = 2
        
        # Percorre da direita para esquerda
        for digito in reversed(numero):
            resultado = int(digito) * multiplicador
            
            # Se resultado >= 10, soma os dígitos
            if resultado >= 10:
                resultado = sum(int(d) for d in str(resultado))
            
            soma += resultado
            multiplicador = 1 if multiplicador == 2 else 2
        
        resto = soma % 10
        return 0 if resto == 0 else (10 - resto)
    
    @staticmethod
    def modulo_11(numero: str) -> int:
        """
        Algoritmo Módulo 11 (Seção 5)
        Retorna o dígito verificador calculado
        """
        soma = 0
        peso = 2
        
        # Percorre da direita para esquerda
        for digito in reversed(numero):
            soma += int(digito) * peso
            peso = peso + 1 if peso < 9 else 2
        
        resto = soma % 11
        
        # Regras especiais
        if resto in (0, 1):
            return 0
        
        dv = 11 - resto
        return 0 if dv == 10 else dv
    
    @staticmethod
    def validar_boleto_tipo_a(linha: str) -> Tuple[bool, str]:
        """
        Valida boleto bancário (Tipo A - 47 dígitos)
        Seção 4.1 do padrão
        """
        linha_limpa = ValidadorPagamentos.limpar_numeros(linha)
        
        if len(linha_limpa) != 47:
            return False, f"Tamanho inválido: {len(linha_limpa)} dígitos (esperado 47)"
        
        # Campo 1: posições 0-8, DV na posição 9
        campo1_dados = linha_limpa[0:9]
        campo1_dv = int(linha_limpa[9])
        dv1_calculado = ValidadorPagamentos.modulo_10(campo1_dados)
        
        if campo1_dv != dv1_calculado:
            return False, f"Campo 1 inválido: DV esperado {dv1_calculado}, encontrado {campo1_dv}"
        
        # Campo 2: posições 10-19, DV na posição 20
        campo2_dados = linha_limpa[10:20]
        campo2_dv = int(linha_limpa[20])
        dv2_calculado = ValidadorPagamentos.modulo_10(campo2_dados)
        
        if campo2_dv != dv2_calculado:
            return False, f"Campo 2 inválido: DV esperado {dv2_calculado}, encontrado {campo2_dv}"
        
        # Campo 3: posições 21-30, DV na posição 31
        campo3_dados = linha_limpa[21:31]
        campo3_dv = int(linha_limpa[31])
        dv3_calculado = ValidadorPagamentos.modulo_10(campo3_dados)
        
        if campo3_dv != dv3_calculado:
            return False, f"Campo 3 inválido: DV esperado {dv3_calculado}, encontrado {campo3_dv}"
        
        return True, "Boleto Tipo A válido"
    
    @staticmethod
    def validar_boleto_tipo_b(linha: str) -> Tuple[bool, str]:
        """
        Valida boleto de arrecadação (Tipo B - 48 dígitos)
        Seção 4.2 do padrão
        """
        linha_limpa = ValidadorPagamentos.limpar_numeros(linha)
        
        if len(linha_limpa) != 48:
            return False, f"Tamanho inválido: {len(linha_limpa)} dígitos (esperado 48)"
        
        if linha_limpa[0] != '8':
            return False, "Boleto de arrecadação deve começar com 8"
        
        # Determina algoritmo pela posição 2 (3º dígito)
        tipo_mod = linha_limpa[2]
        if tipo_mod in ('6', '7'):
            algoritmo = ValidadorPagamentos.modulo_10
            nome_alg = "Módulo 10"
        elif tipo_mod in ('8', '9'):
            algoritmo = ValidadorPagamentos.modulo_11
            nome_alg = "Módulo 11"
        else:
            return False, f"Dígito tipo inválido na posição 2: {tipo_mod} (esperado 6,7,8 ou 9)"
        
        # Valida 4 blocos
        blocos = [
            (linha_limpa[0:11], int(linha_limpa[11]), "Bloco 1"),
            (linha_limpa[12:23], int(linha_limpa[23]), "Bloco 2"),
            (linha_limpa[24:35], int(linha_limpa[35]), "Bloco 3"),
            (linha_limpa[36:47], int(linha_limpa[47]), "Bloco 4"),
        ]
        
        for dados, dv_informado, nome_bloco in blocos:
            dv_calculado = algoritmo(dados)
            if dv_informado != dv_calculado:
                return False, f"{nome_bloco} ({nome_alg}) inválido: DV esperado {dv_calculado}, encontrado {dv_informado}"
        
        return True, f"Boleto Tipo B válido ({nome_alg})"
    
    # ==================== EXTRATORES POR TIPO ====================
    
    @staticmethod
    def extrair_boleto(obs_normalizada: str) -> Optional[Dict]:
        """
        Extrai e valida linha digitável de boleto ou convênio (Seção 3.1)
        Trigger: 'bto:' ou 'boleto'
        
        DIFERENCIAÇÃO:
        - 47 dígitos → Boleto bancário (Tipo A) → Segmentos J + J-52
        - 48 dígitos (começa com 8) → Convênio/Tributo (Tipo B) → Segmento O
        - 44 dígitos → Código de barras direto (verifica primeiro dígito)
        
        Converte linha digitável para código de barras (44 dígitos)
        """
        # Aceita tanto 'bto:' quanto menção a 'boleto'
        if 'bto:' not in obs_normalizada and 'boleto' not in obs_normalizada:
            return None
        
        # Busca padrão numérico: 44 (código barras), 47 (linha digitável tipo A) ou 48 (tipo B)
        # Aceita espaços, pontos e traços como separadores
        match = re.search(r'\d[\d\s.-]{42,}\d', obs_normalizada)
        if not match:
            return {
                "tipo_pagamento": TipoPagamento.BOLETO,
                "validacao": {
                    "valido": False,
                    "mensagem": "Linha digitável não encontrada"
                },
                "dados": {}
            }
        
        linha_digitavel = ValidadorPagamentos.limpar_numeros(match.group(0))
        
        # Valida e converte para código de barras
        codigo_barras = None
        tipo_boleto = None
        tipo_pagamento = TipoPagamento.BOLETO  # Padrão
        
        # Determina tipo por tamanho E primeiro dígito
        if len(linha_digitavel) == 44:
            # Já é código de barras - verifica primeiro dígito
            if linha_digitavel[0] == '8':
                # Convênio/Tributo (código de barras direto tipo B)
                tipo_pagamento = TipoPagamento.CONVENIO
                tipo_boleto = "Convênio/Tributo (código barras direto)"
            else:
                # Boleto bancário
                tipo_boleto = "Boleto bancário (código barras direto)"
            valido = True
            mensagem = f"Código de barras aceito (44 dígitos) - {tipo_boleto}"
            codigo_barras = linha_digitavel
            
        elif len(linha_digitavel) == 47:
            # Boleto bancário (Tipo A) - usa Segmentos J + J-52
            valido, mensagem = ValidadorPagamentos.validar_boleto_tipo_a(linha_digitavel)
            tipo_boleto = "Boleto bancário (Tipo A - 47 dígitos)"
            tipo_pagamento = TipoPagamento.BOLETO
            
            # SEMPRE converte, independente da validação DV
            # Formato linha: AAABC.CCCCX DDDDD.DDDDDY EEEEE.EEEEEZ K UUUUVVVVVVVVVV
            # Código barras: AAABKUUUUVVVVVVVVVVCCCCCCCCCCDDDDDDDDDDEEEEEEEEEEE
            codigo_barras = (
                linha_digitavel[0:4] +      # Banco + moeda (AAAB)
                linha_digitavel[32:33] +    # DV geral (K)
                linha_digitavel[33:47] +    # Fator vencimento + valor (UUUUVVVVVVVVVV)
                linha_digitavel[4:9] +      # Campo livre 1 (CCCCC)
                linha_digitavel[10:20] +    # Campo livre 2 (DDDDDDDDDD)
                linha_digitavel[21:31]      # Campo livre 3 (EEEEEEEEEE)
            )
            
            # Adiciona warning se DV inválido
            if not valido:
                mensagem = f"AVISO: {mensagem} (convertido mesmo assim)"
                
        elif len(linha_digitavel) == 48:
            # Convênio/Tributo/Concessionária (Tipo B) - usa Segmento O
            valido, mensagem = ValidadorPagamentos.validar_boleto_tipo_b(linha_digitavel)
            tipo_boleto = "Convênio/Tributo (Tipo B - 48 dígitos)"
            tipo_pagamento = TipoPagamento.CONVENIO  # <<< DIFERENÇA PRINCIPAL
            
            # SEMPRE converte, independente da validação DV
            # Remove os 4 DVs das posições 11, 23, 35, 47
            codigo_barras = (
                linha_digitavel[0:11] +     # Bloco 1 sem DV
                linha_digitavel[12:23] +    # Bloco 2 sem DV
                linha_digitavel[24:35] +    # Bloco 3 sem DV
                linha_digitavel[36:47]      # Bloco 4 sem DV
            )
            
            # Adiciona warning se DV inválido
            if not valido:
                mensagem = f"AVISO: {mensagem} (convertido mesmo assim)"
        else:
            valido = False
            mensagem = f"Tamanho inválido: {len(linha_digitavel)} dígitos (esperado 44, 47 ou 48)"
            tipo_boleto = "Desconhecido"
        
        dados_retorno = {
            "linha_digitavel": linha_digitavel,
            "tipo_boleto": tipo_boleto
        }
        
        if codigo_barras:
            dados_retorno["codigo_barras"] = codigo_barras
        
        return {
            "tipo_pagamento": tipo_pagamento,
            "validacao": {
                "valido": valido,
                "mensagem": mensagem
            },
            "dados": dados_retorno
        }
    
    @staticmethod
    def extrair_ted(obs_normalizada: str) -> Optional[Dict]:
        """
        Extrai dados de TED (Seção 3.2)
        Trigger: 'ted'
        Campos: bco:, ag:, cc:
        """
        if 'ted' not in obs_normalizada:
            return None
        
        # Extrai banco
        match_bco = re.search(r'bco:\s*(\d{3})', obs_normalizada)
        banco = match_bco.group(1) if match_bco else None
        
        # Extrai agência
        match_ag = re.search(r'ag:\s*(\d{4,5})', obs_normalizada)
        if match_ag:
            agencia_completa = match_ag.group(1)
            if len(agencia_completa) == 5:
                agencia = agencia_completa[:4]
                agencia_dv = agencia_completa[4]
            else:
                agencia = agencia_completa
                agencia_dv = None
        else:
            agencia = agencia_dv = None
        
        # Extrai conta
        match_cc = re.search(r'cc:\s*([\d-]+)', obs_normalizada)
        conta = match_cc.group(1) if match_cc else None
        
        # Valida campos obrigatórios
        campos_faltantes = []
        if not banco:
            campos_faltantes.append("bco")
        if not agencia:
            campos_faltantes.append("ag")
        if not conta:
            campos_faltantes.append("cc")
        
        valido = len(campos_faltantes) == 0
        mensagem = "TED válido" if valido else f"Campos faltantes: {', '.join(campos_faltantes)}"
        
        return {
            "tipo_pagamento": TipoPagamento.TED,
            "validacao": {
                "valido": valido,
                "mensagem": mensagem
            },
            "dados": {
                "banco": banco,
                "agencia": agencia,
                "agencia_dv": agencia_dv,
                "conta": conta
            }
        }
    
    @staticmethod
    def extrair_transferencia(obs_normalizada: str) -> Optional[Dict]:
        """
        Extrai dados de transferência (Seção 3.3)
        Trigger: 'transf'
        Campos: ag:, cc: (sem bco:)
        """
        if 'transf' not in obs_normalizada:
            return None
        
        # Extrai agência
        match_ag = re.search(r'ag:\s*(\d{4,5})', obs_normalizada)
        if match_ag:
            agencia_completa = match_ag.group(1)
            if len(agencia_completa) == 5:
                agencia = agencia_completa[:4]
                agencia_dv = agencia_completa[4]
            else:
                agencia = agencia_completa
                agencia_dv = None
        else:
            agencia = agencia_dv = None
        
        # Extrai conta
        match_cc = re.search(r'cc:\s*([\d-]+)', obs_normalizada)
        conta = match_cc.group(1) if match_cc else None
        
        # Valida campos obrigatórios
        campos_faltantes = []
        if not agencia:
            campos_faltantes.append("ag")
        if not conta:
            campos_faltantes.append("cc")
        
        valido = len(campos_faltantes) == 0
        mensagem = "Transferência válida" if valido else f"Campos faltantes: {', '.join(campos_faltantes)}"
        
        return {
            "tipo_pagamento": TipoPagamento.TRANSFERENCIA,
            "validacao": {
                "valido": valido,
                "mensagem": mensagem
            },
            "dados": {
                "agencia": agencia,
                "agencia_dv": agencia_dv,
                "conta": conta
            }
        }
    
    @staticmethod
    def classificar_chave_pix(chave: str) -> Tuple[Optional[TipoChavePix], str]:
        """
        Classifica tipo de chave PIX (Seção 3.4)
        """
        chave_limpa = chave.strip()
        chave_lower = chave_limpa.lower()
        
        # QR Code (Copia e Cola) - PRESERVA O CASE ORIGINAL
        if chave_lower.startswith('000201'):
            return TipoChavePix.QR_CODE, chave_limpa
        
        # UUID (EVP) - aceita tanto minúsculas quanto maiúsculas
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        if re.match(uuid_pattern, chave_lower):
            return TipoChavePix.EVP, chave_lower
        
        # Email
        email_pattern = r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'
        if re.match(email_pattern, chave_lower):
            return TipoChavePix.EMAIL, chave_lower
        
        # Apenas números - CPF, CNPJ ou Telefone
        numeros = ValidadorPagamentos.limpar_numeros(chave_lower)
        
        if len(numeros) == 11:
            # Pode ser CPF ou telefone
            # Tenta validar como CPF primeiro
            if ValidadorPagamentos._cpf_valido(numeros):
                return TipoChavePix.CPF, numeros
            else:
                # Se não for CPF válido, assume telefone
                return TipoChavePix.TELEFONE, numeros
        
        if len(numeros) == 14:
            # CNPJ
            return TipoChavePix.CNPJ, numeros
        
        # Telefone com 10 dígitos (DDD + 8 dígitos)
        if len(numeros) == 10:
            return TipoChavePix.TELEFONE, numeros
        
        # Telefone com DDI (+55...)
        tel_pattern = r'^\+?55\s*\d{10,11}$'
        if re.match(tel_pattern, chave_lower.replace(' ', '')):
            tel_normalizado = ValidadorPagamentos.limpar_numeros(chave_lower)
            # Remove 55 do início se presente
            if tel_normalizado.startswith('55'):
                tel_normalizado = tel_normalizado[2:]
            return TipoChavePix.TELEFONE, tel_normalizado
        
        return None, chave_lower
    
    @staticmethod
    def _cpf_valido(cpf: str) -> bool:
        """Valida CPF matematicamente"""
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
        except (ValueError, IndexError):
            return False
    
    @staticmethod
    def extrair_cpf_cnpj_de_obs(obs_normalizada: str) -> Optional[str]:
        """
        Extrai CPF ou CNPJ explicitamente mencionado na obs
        Busca por padrões: 'cpf: 123.456.789-01' ou 'cnpj: 12.345.678/0001-90'
        """
        # CNPJ primeiro (mais específico)
        cnpj_pattern = r'cnpj[:\s]*(\d{2}\.?\d{3}\.?\d{3}/?[0-9]{4}-?\d{2})'
        match_cnpj = re.search(cnpj_pattern, obs_normalizada)
        if match_cnpj:
            return re.sub(r'[^\d]', '', match_cnpj.group(1))
        
        # CPF
        cpf_pattern = r'cpf[:\s]*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})'
        match_cpf = re.search(cpf_pattern, obs_normalizada)
        if match_cpf:
            return re.sub(r'[^\d]', '', match_cpf.group(1))
        
        return None
    
    @staticmethod
    def extrair_pix(obs_normalizada: str, obs_original: str) -> Optional[Dict]:
        """
        Extrai e classifica chave PIX (Seção 3.4)
        Trigger: 'pix'
        
        Busca por TODAS as ocorrências de 'pix' e tenta extrair uma chave válida
        de cada uma, retornando a primeira que for classificada com sucesso.
        """
        if 'pix' not in obs_normalizada:
            return None
        
        # Primeiro tenta extrair CPF/CNPJ explícito da obs
        cpf_cnpj_obs = ValidadorPagamentos.extrair_cpf_cnpj_de_obs(obs_normalizada)
        
        # Encontra TODAS as ocorrências de 'pix' na observação
        pix_pattern = r'pix'
        todas_ocorrencias = list(re.finditer(pix_pattern, obs_normalizada))
        
        chave = None
        tipo_chave = None
        chave_normalizada = None
        
        # Itera sobre todas as ocorrências de 'pix'
        for match_pix in todas_ocorrencias:
            pix_pos = match_pix.start()
            texto_apos_pix = obs_normalizada[pix_pos:]
            # Para QR Code, usamos a observação original para preservar o case
            texto_apos_pix_original = obs_original[pix_pos:]
            candidato_chave = None
            
            # Procura por padrões VÁLIDOS após esta ocorrência de 'pix'
            # ORDEM DE PRIORIDADE: mais específico primeiro
            
            # 1. QR Code PIX (Copia e Cola) - deve vir ANTES do UUID
            # QR Code sempre começa com '000201' e pode ter até ~500 caracteres
            # Contém: dígitos, letras, pontos, hífens, barras, dois-pontos, arrobas, asteriscos, espaços
            # Para capturar o QR completo, usamos uma abordagem em duas etapas:
            # - Primeiro: verifica se começa com '000201'
            # - Segundo: captura tudo até encontrar delimitadores (vírgula, ponto-vírgula, quebra de linha, ou fim)
            if texto_apos_pix.startswith('pix'):
                # Remove 'pix' + espaços/dois-pontos do início
                temp_normalizado = re.sub(r'^pix[:\s]+', '', texto_apos_pix)
                temp_original = re.sub(r'^pix[:\s]+', '', texto_apos_pix_original, flags=re.IGNORECASE)
                if temp_normalizado.startswith('000201'):
                    # Captura QR Code da string ORIGINAL (preserva case)
                    # Para em vírgula, ponto-vírgula, quebra de linha ou 2+ espaços consecutivos
                    match_qr = re.match(r'(000201[^\n\r,;]*?)(?:\s{2,}|[,;\n\r]|$)', temp_original, re.IGNORECASE)
                    if match_qr:
                        candidato_chave = match_qr.group(1).strip()
            
            # 2. Telefone internacional com +55 (padrão brasileiro)
            if not candidato_chave:
                tel_internacional = r'\+55\s*\d{10,11}'
                match_tel = re.search(tel_internacional, texto_apos_pix)
                if match_tel:
                    candidato_chave = match_tel.group(0)
            
            # 3. Email (formato completo)
            if not candidato_chave:
                email_pattern = r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'
                match_email = re.search(email_pattern, texto_apos_pix)
                if match_email:
                    candidato_chave = match_email.group(0)
            
            # 4. UUID (EVP) - chave aleatória
            # Deve vir DEPOIS do QR Code para não capturar UUID dentro do QR
            if not candidato_chave:
                uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                match_uuid = re.search(uuid_pattern, texto_apos_pix)
                if match_uuid:
                    candidato_chave = match_uuid.group(0)
            # 4. UUID (EVP) - chave aleatória
            # Deve vir DEPOIS do QR Code para não capturar UUID dentro do QR
            if not candidato_chave:
                uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                match_uuid = re.search(uuid_pattern, texto_apos_pix)
                if match_uuid:
                    candidato_chave = match_uuid.group(0)
            
            # 5. Padrão direto após 'pix:' ou 'pix ' (mais específico que sequências genéricas)
            if not candidato_chave:
                match_direto = re.search(r'pix[:\s]+([^\s,;.]+)', texto_apos_pix)
                if match_direto:
                    candidato = match_direto.group(1).strip()
                    # Rejeita palavras comuns que não são chaves
                    palavras_invalidas = ['do', 'da', 'de', 'pra', 'para', 'no', 'na', 'em']
                    if candidato.lower() not in palavras_invalidas:
                        candidato_chave = candidato
            
            # 6. Sequências numéricas longas (10-14 dígitos) - telefones, CPF, CNPJ
            # Busca apenas após 'pix ' ou 'pix:' para evitar capturar números de contexto
            if not candidato_chave:
                # Limita busca aos primeiros 50 caracteres após 'pix' para evitar números distantes
                texto_proximo = texto_apos_pix[:50]
                numeros_longos = r'\d[\d\s-]{8,}\d'
                matches = re.finditer(numeros_longos, texto_proximo)
                for match in matches:
                    candidato = match.group(0)
                    apenas_digitos = ValidadorPagamentos.limpar_numeros(candidato)
                    # Valida tamanho: 10-11 (telefone), 11 (CPF), 14 (CNPJ)
                    if len(apenas_digitos) in [10, 11, 14]:
                        candidato_chave = candidato
                        break
            
            # Se encontrou um candidato, tenta classificá-lo
            if candidato_chave:
                tipo_temp, normalizado_temp = ValidadorPagamentos.classificar_chave_pix(candidato_chave)
                # Se a classificação foi bem-sucedida, usa esta chave
                if tipo_temp is not None:
                    chave = candidato_chave
                    tipo_chave = tipo_temp
                    chave_normalizada = normalizado_temp
                    break  # Encontrou uma chave válida, para a busca
        
        # Se não encontrou chave válida em nenhuma ocorrência
        if not chave:
            dados_falha = {}
            if cpf_cnpj_obs:
                dados_falha["cpf_cnpj_obs"] = cpf_cnpj_obs
            return {
                "tipo_pagamento": TipoPagamento.PIX,
                "validacao": {
                    "valido": False,
                    "mensagem": "Chave PIX não encontrada após trigger 'pix'"
                },
                "dados": dados_falha
            }
        
        # Retorna resultado incluindo CPF/CNPJ explícito se encontrado
        dados_resultado = {
            "chave_pix": chave_normalizada,
            "tipo_chave": tipo_chave
        }
        
        if cpf_cnpj_obs:
            dados_resultado["cpf_cnpj_obs"] = cpf_cnpj_obs
        
        return {
            "tipo_pagamento": TipoPagamento.PIX,
            "validacao": {
                "valido": True,
                "mensagem": f"Chave PIX {tipo_chave.value} válida"
            },
            "dados": dados_resultado
        }
    
    @staticmethod
    def interpretar(obs: str) -> Dict:
        """
        Método principal de interpretação (Seção 6 - Fluxo)
        Retorna estrutura padronizada de pagamento
        """
        if not obs:
            return {
                "tipo_pagamento": None,
                "validacao": {
                    "valido": False,
                    "mensagem": "Campo obs vazio"
                },
                "dados": {}
            }
        
        # Passo 1 e 2: Normalização
        obs_normalizada = ValidadorPagamentos.normalizar_texto(obs)
        
        # Passo 3 e 4: Identificação por prioridade
        # Ordem: BOLETO > TED > TRANSFERENCIA > PIX
        
        resultado = ValidadorPagamentos.extrair_boleto(obs_normalizada)
        if resultado:
            return resultado
        
        resultado = ValidadorPagamentos.extrair_ted(obs_normalizada)
        if resultado:
            return resultado
        
        resultado = ValidadorPagamentos.extrair_transferencia(obs_normalizada)
        if resultado:
            return resultado
        
        resultado = ValidadorPagamentos.extrair_pix(obs_normalizada, obs)
        if resultado:
            return resultado
        
        # Nenhum trigger identificado
        return {
            "tipo_pagamento": None,
            "validacao": {
                "valido": False,
                "mensagem": "Tipo de pagamento não identificado"
            },
            "dados": {
                "obs_original": obs[:100]  # Primeiros 100 chars para debug
            }
        }
