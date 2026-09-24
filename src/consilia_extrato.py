import pandas as pd
import re
import codecs
from decimal import Decimal, InvalidOperation
from fuzzywuzzy import fuzz  # Usando fuzzywuzzy
from ofxparse import OfxParser # Para ler OFX
from typing import Union
from datetime import datetime, timedelta
import uuid
import subprocess
import sys
import os
from pathlib import Path
# Imports para formatação do Excel
from openpyxl.styles import Font, PatternFill
from openpyxl.formatting.rule import CellIsRule

# ==================== CONSTANTES DE CAMINHOS ====================
PASTA_RAIZ = Path(__file__).parent.parent
PASTA_SRC = PASTA_RAIZ / "src"
PASTA_OFX = PASTA_RAIZ / "ofx_a_processar"
PASTA_AUX = PASTA_RAIZ / "arquivos_auxiliares"
PASTA_RELATORIOS = PASTA_RAIZ / "relatorios"

# ==================== CONSTANTES DE CAMINHOS ====================
PASTA_RAIZ = Path(__file__).parent.parent
PASTA_SRC = PASTA_RAIZ / "src"
PASTA_OFX = PASTA_RAIZ / "ofx_a_processar"
PASTA_AUX = PASTA_RAIZ / "arquivos_auxiliares"
PASTA_RELATORIOS = PASTA_RAIZ / "relatorios"

# ==================== FUNÇÕES AUXILIARES ====================

# --- Função 1: Leitor de OFX (usando ofxparse) ---
def converter_ofx_para_dataframe(caminho_arquivo: str) -> Union[tuple, None]:
    """
    Converte arquivo OFX para DataFrame incluindo dados da conta.
    Retorna: (df_transacoes, info_conta) ou None
    """
    print(f"Processando arquivo OFX: {caminho_arquivo}...")
    try:
        with codecs.open(caminho_arquivo, 'r', encoding='latin-1') as f:
            ofx = OfxParser.parse(f)
    except FileNotFoundError:
        print(f"Erro: Arquivo OFX não encontrado em '{caminho_arquivo}'")
        return None
    except Exception as e:
        print(f"Erro ao ler o arquivo OFX: {e}")
        return None
    
    try:
        statement = ofx.account.statement
        if not statement:
            print("Erro: Nenhum extrato (statement) encontrado no arquivo.")
            return None
    except Exception:
        print("Erro ao acessar o extrato. Verifique se o arquivo OFX é válido.")
        return None

    # Extrair informações da conta
    info_conta = {
        'banco_codigo': getattr(ofx.account, 'routing_number', ''),
        'banco_agencia': getattr(ofx.account, 'branch_id', ''),
        'banco_conta': getattr(ofx.account, 'account_id', '')
    }
    
    print(f"Conta: Banco={info_conta['banco_codigo']}, Agência={info_conta['banco_agencia']}, Conta={info_conta['banco_conta']}")
    
    transactions = statement.transactions
    print(f"Total de transações: {len(transactions)}")
    
    if len(transactions) == 0:
        return None
    
    lista_de_transacoes = []
    for transacao in transactions:
        lista_de_transacoes.append({
            "data": transacao.date,
            "valor": transacao.amount,
            "tipo": str(transacao.type),
            "descricao": str(transacao.payee),
            "memo": str(transacao.memo),
            "id_transacao": str(transacao.id)
        })
    
    df = pd.DataFrame(lista_de_transacoes)
    return (df, info_conta)

# --- Função 2: Limpeza de Texto (Extrair Números) ---
def extrair_numeros(texto: str) -> str:
    if not isinstance(texto, str):
        return ""
    numeros = re.findall(r'\d+', texto)
    return "".join(numeros)

# --- Função 3: Limpeza de Valores Monetários (do Excel) ---
def limpar_valor_monetario(valor_obj) -> Decimal:
    if isinstance(valor_obj, Decimal):
        return valor_obj
    if isinstance(valor_obj, (int, float)):
        return Decimal(str(valor_obj))
    valor_str = str(valor_obj)
    valor_limpo = valor_str.strip().replace(".", "").replace(",", ".")
    valor_limpo = valor_limpo.replace('"', '')
    try:
        return Decimal(valor_limpo)
    except (InvalidOperation, ValueError):
        return Decimal('0.0')

# ==================== FUNÇÕES DE PREPARAÇÃO ====================

def carregar_arquivos_ofx() -> pd.DataFrame:
    """
    Varre a pasta OFX e consolida todos os arquivos .ofx em um único DataFrame.
    Adiciona colunas: banco_codigo, banco_agencia, banco_conta
    """
    print(f"\n{'='*60}")
    print("ETAPA 1: CARREGAMENTO DE ARQUIVOS OFX")
    print(f"{'='*60}")
    
    if not PASTA_OFX.exists():
        print(f"ERRO: Pasta {PASTA_OFX} não existe!")
        sys.exit(1)
    
    arquivos_ofx = list(PASTA_OFX.glob("*.ofx"))
    
    if not arquivos_ofx:
        print(f"ERRO: Nenhum arquivo .ofx encontrado em {PASTA_OFX}")
        sys.exit(1)
    
    print(f"Arquivos OFX encontrados: {len(arquivos_ofx)}")
    
    lista_dfs = []
    for arquivo in arquivos_ofx:
        print(f"\nProcessando: {arquivo.name}")
        resultado = converter_ofx_para_dataframe(str(arquivo))
        
        if resultado is None:
            print(f"Aviso: Arquivo {arquivo.name} não pôde ser processado.")
            continue
        
        df_trans, info_conta = resultado
        
        # Adicionar colunas de identificação da conta
        df_trans['banco_codigo'] = info_conta['banco_codigo']
        df_trans['banco_agencia'] = info_conta['banco_agencia']
        df_trans['banco_conta'] = info_conta['banco_conta']
        
        lista_dfs.append(df_trans)
    
    if not lista_dfs:
        print("ERRO: Nenhum arquivo OFX válido foi processado!")
        sys.exit(1)
    
    df_consolidado = pd.concat(lista_dfs, ignore_index=True)
    
    # Remover dígito verificador da agência (formato: XXXX-D)
    df_consolidado['banco_agencia'] = df_consolidado['banco_agencia'].astype(str).str.replace(r'-\d$', '', regex=True)
    
    print(f"\n✅ Total de transações consolidadas: {len(df_consolidado)}")
    print(f"✅ Contas bancárias únicas: {df_consolidado[['banco_codigo', 'banco_agencia', 'banco_conta']].drop_duplicates().shape[0]}")
    
    return df_consolidado


def executar_script_com_stdin(script_path: Path, stdin_data: str = None) -> bool:
    """
    Executa um script Python via subprocess, enviando dados via stdin e capturando saída.
    Usa communicate() para evitar deadlocks e problemas de sincronização com input().
    """
    print(f"Executando: {script_path.name}...")
    print(f"{'-' * 40}")
    
    try:
        # Obter o interpretador Python atual
        python_executable = sys.executable
        
        # Usar PIPE para stdin/stdout/stderr
        process = subprocess.Popen(
            [python_executable, '-u', str(script_path)],  # -u para unbuffered output
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        # communicate() envia stdin e captura toda saída de forma segura
        # Timeout de 25 minutos
        try:
            stdout, _ = process.communicate(input=stdin_data, timeout=1500)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
            print(f"❌ Timeout ao executar {script_path.name}")
            return False
        
        # Imprimir saída (limitada a 200 linhas)
        if stdout:
            linhas = stdout.splitlines()
            max_linhas = 200
            for i, linha in enumerate(linhas):
                if i < max_linhas:
                    print(linha)
                elif i == max_linhas:
                    print(f"[...output truncado, mostrando apenas primeiras {max_linhas} linhas de {len(linhas)}...]")
                    break
        
        print(f"{'-' * 40}")
        
        if process.returncode == 0:
            print(f"✅ {script_path.name} concluído com sucesso")
            return True
        else:
            print(f"❌ {script_path.name} falhou com código {process.returncode}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao executar {script_path.name}: {e}")
        return False


def preparar_arquivos_auxiliares(data_min: str, data_max: str):
    """
    Gera os 3 arquivos auxiliares necessários e valida sua existência/atualização.
    Só gera os arquivos se eles não existirem ou tiverem mais de 4 dias de idade.
    
    Args:
        data_min: Data no formato DD/MM/YYYY
        data_max: Data no formato DD/MM/YYYY
    """
    print(f"\n{'='*60}")
    print("ETAPA 2: PREPARAÇÃO DE ARQUIVOS AUXILIARES")
    print(f"{'='*60}")
    print(f"Período: {data_min} a {data_max}")
    
    # Definir caminhos dos scripts e arquivos de saída
    script_pagas = PASTA_SRC / "automatizador_final.py"
    script_pessoas = PASTA_SRC / "capturador_pessoas.py"
    
    arquivo_pagas = PASTA_AUX / "relatorio_contas_pagas.csv"
    arquivo_pessoas = PASTA_AUX / "pessoas_cadastradas.csv"
    arquivo_bancos = PASTA_AUX / "bancos.csv"
    
    # Definir limite de idade (4 dias)
    limite_idade = datetime.now() - timedelta(days=4)
    
    # Função auxiliar para verificar se arquivo precisa ser gerado
    def precisa_gerar(caminho: Path) -> bool:
        if not caminho.exists():
            return True
        data_modificacao = datetime.fromtimestamp(os.path.getmtime(caminho))
        return data_modificacao < limite_idade
    
    # 2.1: Gerar relatorio_contas_pagas.csv (se necessário)
    if precisa_gerar(arquivo_pagas):
        print(f"\n--- Gerando {arquivo_pagas.name} ---")
        print(f"Parâmetros: Data inicial={data_min}, Data final={data_max}, Tipo=P, Formato=CSV")
        # Formato: data_inicial + \n + data_final + \n + tipo(P) + \n + formato(enter=csv) + \n + modo(enter=headless) + \n
        stdin_pagas = f"{data_min}\n{data_max}\nP\n\nn\n"
        executar_script_com_stdin(script_pagas, stdin_pagas)
    else:
        idade_dias = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(arquivo_pagas))).days
        print(f"\n✓ {arquivo_pagas.name} já existe e está atualizado (idade: {idade_dias} dias)")
    
    # 2.2: Gerar pessoas_cadastradas.csv (se necessário)
    if precisa_gerar(arquivo_pessoas):
        print(f"\n--- Gerando {arquivo_pessoas.name} ---")
        # Passa "n" para executar em modo visível (resposta para: "Executar em modo visível? (s/n) [s]:")
        # Passa "n" novamente para não executar em paralelo
        stdin_pessoas = "n\nn\n"
        executar_script_com_stdin(script_pessoas, stdin_pessoas)
    else:
        idade_dias = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(arquivo_pessoas))).days
        print(f"\n✓ {arquivo_pessoas.name} já existe e está atualizado (idade: {idade_dias} dias)")
    
    # 2.3: Validação de arquivos (regra de 5 dias - exceto bancos.csv)
    print(f"\n--- Validando arquivos gerados ---")
    arquivos_validar = [
        ("relatorio_contas_pagas.csv", arquivo_pagas),
        ("pessoas_cadastradas.csv", arquivo_pessoas),
    ]
    
    limite_data = datetime.now() - timedelta(days=5)
    erros = []
    
    for nome, caminho in arquivos_validar:
        if not caminho.exists():
            erros.append(f"❌ Arquivo ausente: {nome}")
            continue
        
        data_modificacao = datetime.fromtimestamp(os.path.getmtime(caminho))
        idade_dias = (datetime.now() - data_modificacao).days
        
        if data_modificacao < limite_data:
            erros.append(f"❌ Arquivo desatualizado: {nome} (idade: {idade_dias} dias)")
        else:
            print(f"✅ {nome} - OK (idade: {idade_dias} dias)")
    
    # Verificar apenas existência do bancos.csv (sem validar idade)
    if not arquivo_bancos.exists():
        erros.append(f"❌ Arquivo ausente: bancos.csv")
    else:
        print(f"✅ bancos.csv - OK (existente)")
    
    if erros:
        print("\n" + "="*60)
        print("ERRO: ARQUIVOS AUXILIARES INVÁLIDOS")
        print("="*60)
        for erro in erros:
            print(erro)
        print("\nOs arquivos auxiliares devem ter no máximo 5 dias de idade.")
        print("Execute os scripts manualmente ou verifique os erros acima.")
        sys.exit(1)
    
    print("\n✅ Todos os arquivos auxiliares estão válidos!")


def carregar_arquivos_auxiliares() -> tuple:
    """
    Carrega os 3 arquivos CSV auxiliares.
    Retorna: (df_pagamentos, df_cadastro, df_bancos)
    """
    print(f"\n{'='*60}")
    print("ETAPA 3: CARREGAMENTO DE DADOS AUXILIARES")
    print(f"{'='*60}")
    
    arquivo_pagas = PASTA_AUX / "relatorio_contas_pagas.csv"
    arquivo_pessoas = PASTA_AUX / "pessoas_cadastradas.csv"
    arquivo_bancos = PASTA_AUX / "bancos.csv"
    
    try:
        df_pagamentos = pd.read_csv(arquivo_pagas)
        # Normalizar campo Centro Custo: uppercase e sem acentuação
        if 'Centro Custo' in df_pagamentos.columns:
            df_pagamentos['Centro Custo'] = df_pagamentos['Centro Custo'].astype(str).str.upper()
            df_pagamentos['Centro Custo'] = df_pagamentos['Centro Custo'].str.normalize('NFKD').str.encode('ascii', errors='ignore').str.decode('utf-8')
        print(f"✅ Pagamentos carregados: {len(df_pagamentos)} registros")
    except Exception as e:
        print(f"❌ Erro ao carregar {arquivo_pagas.name}: {e}")
        sys.exit(1)
    
    try:
        df_cadastro = pd.read_csv(arquivo_pessoas, sep=',')
        df_cadastro['Nome'] = df_cadastro['Nome'].str.strip()
        print(f"✅ Cadastro carregado: {len(df_cadastro)} registros")
    except Exception as e:
        print(f"❌ Erro ao carregar {arquivo_pessoas.name}: {e}")
        sys.exit(1)
    
    try:
        df_bancos = pd.read_csv(arquivo_bancos)
        # Normalizar campo empreendimentos: uppercase e sem acentuação
        if 'empreendimentos' in df_bancos.columns:
            df_bancos['empreendimentos'] = df_bancos['empreendimentos'].astype(str).str.upper()
            df_bancos['empreendimentos'] = df_bancos['empreendimentos'].str.normalize('NFKD').str.encode('ascii', errors='ignore').str.decode('utf-8')
        print(f"✅ Bancos carregados: {len(df_bancos)} registros")
    except Exception as e:
        print(f"❌ Erro ao carregar {arquivo_bancos.name}: {e}")
        sys.exit(1)
    
    return df_pagamentos, df_cadastro, df_bancos

# ==================== CONFIGURAÇÕES DE CONCILIAÇÃO ====================

# Colunas-chave
COL_BANCO_DATA = "data"
COL_BANCO_VALOR = "valor"
COL_BANCO_TEXTO_1 = "descricao"  # (OFX NAME)
COL_BANCO_TEXTO_2 = "memo"       # (OFX MEMO)
COL_PAGTO_DATA = "Pagto"
COL_PAGTO_VALOR = "Valor"
COL_PAGTO_TEXTO_1 = "Obs."
COL_PAGTO_TEXTO_2 = "Beneficiado"  # (Chave para o merge)

# Regras de Pontuação
TOLERANCIA_VALOR_REAIS = Decimal(10.0)
TOLERANCIA_DIAS = 5
PESO_VALOR = 0.40
PESO_DATA = 0.30
PESO_TEXTO = 0.30
LIMITE_CONFIANCA = 0.70

# ==================== FUNÇÃO DE CONCILIAÇÃO ====================

def conciliar_grupo(df_banco_grupo, df_pagamentos_grupo, df_cadastro):
    """
    Executa a lógica de conciliação para um grupo específico de conta bancária.
    
    Args:
        df_banco_grupo: DataFrame com movimentos bancários do grupo
        df_pagamentos_grupo: DataFrame com pagamentos filtrados para o grupo
        df_cadastro: DataFrame completo do cadastro de pessoas
    
    Returns:
        tuple: (df_conciliados, df_pag_nao_encontrados, df_banco_nao_encontrados)
    """
    print(f"  Transações bancárias: {len(df_banco_grupo)}")
    print(f"  Pagamentos a conciliar: {len(df_pagamentos_grupo)}")
    
    # Preparar dados do banco (filtrar apenas débitos)
    df_banco = df_banco_grupo[df_banco_grupo[COL_BANCO_VALOR] < 0].copy()
    df_banco[COL_BANCO_VALOR] = df_banco[COL_BANCO_VALOR].abs()
    df_banco[COL_BANCO_DATA] = pd.to_datetime(df_banco[COL_BANCO_DATA]).dt.tz_localize(None)
    
    # Criar chaves de busca para banco
    df_banco['banco_chave_numerica'] = (
        df_banco[COL_BANCO_TEXTO_1].fillna('') + ' ' +
        df_banco[COL_BANCO_TEXTO_2].fillna('')
    ).apply(extrair_numeros)
    df_banco['banco_chave_nome'] = df_banco[COL_BANCO_TEXTO_1].fillna('').str.lower()
    df_banco['match_id'] = None
    df_banco['match_score'] = None
    
    # Preparar dados de pagamentos
    df_pagamentos = df_pagamentos_grupo.copy()
    
    # Fazer merge com cadastro de pessoas
    df_pagamentos[COL_PAGTO_TEXTO_2] = df_pagamentos[COL_PAGTO_TEXTO_2].str.strip()
    df_pagamentos = pd.merge(
        df_pagamentos,
        df_cadastro,
        left_on=COL_PAGTO_TEXTO_2,
        right_on="Nome",
        how="left"
    )
    
    # Converter e limpar dados de pagamentos
    df_pagamentos[COL_PAGTO_DATA] = pd.to_datetime(
        df_pagamentos[COL_PAGTO_DATA],
        errors='coerce',
        dayfirst=True
    )
    df_pagamentos[COL_PAGTO_VALOR] = df_pagamentos[COL_PAGTO_VALOR].apply(limpar_valor_monetario)
    
    # Criar chaves de busca para pagamentos
    df_pagamentos['pag_chave_doc'] = df_pagamentos['CPF/CNPJ'].fillna('').apply(extrair_numeros)
    df_pagamentos['pag_chave_nome'] = df_pagamentos['Nome'].fillna('').str.lower()
    df_pagamentos['pag_chave_obs'] = df_pagamentos[COL_PAGTO_TEXTO_1].fillna('').apply(extrair_numeros)
    
    df_pagamentos.dropna(subset=[COL_PAGTO_DATA, COL_PAGTO_VALOR], inplace=True)
    df_pagamentos['match_id'] = None
    df_pagamentos['match_score'] = None
    df_pagamentos['delta_valor'] = None
    
    # Motor de Conciliação (Score Ponderado)
    print("  Executando conciliação...")
    matches_encontrados = []
    
    for pag in df_pagamentos.itertuples():
        data_pagamento = getattr(pag, COL_PAGTO_DATA)
        valor_pagamento = getattr(pag, COL_PAGTO_VALOR)
        
        if pd.isna(data_pagamento) or valor_pagamento == 0:
            continue
        
        best_score = -1
        best_match_info = None
        
        for banco in df_banco.itertuples():
            if banco.match_id is not None:
                continue
            
            delta_valor = abs(valor_pagamento - banco.valor)
            score_valor = max(0, 1 - (float(delta_valor) / float(TOLERANCIA_VALOR_REAIS)))
            
            delta_dias = abs((data_pagamento - banco.data).days)
            score_data = max(0, 1 - (delta_dias / TOLERANCIA_DIAS))
            
            score_txt_1 = fuzz.partial_ratio(pag.pag_chave_doc, banco.banco_chave_numerica)
            score_txt_2 = fuzz.partial_ratio(pag.pag_chave_obs, banco.banco_chave_numerica)
            score_txt_3 = fuzz.token_set_ratio(pag.pag_chave_nome, banco.banco_chave_nome)
            
            score_texto_raw = max(score_txt_1, score_txt_2, score_txt_3)
            score_texto = score_texto_raw / 100.0
            
            if score_texto_raw == 100:
                score_texto = 1.1  # Bônus
            
            score_final = (score_valor * PESO_VALOR) + \
                         (score_data * PESO_DATA) + \
                         (score_texto * PESO_TEXTO)
            
            if score_final > best_score:
                best_score = score_final
                best_match_info = {
                    'pag_index': pag.Index,
                    'banco_index': banco.Index,
                    'score': score_final,
                    'delta_valor': valor_pagamento - banco.valor,
                    'delta_dias': delta_dias,
                    'score_texto': score_texto_raw
                }
        
        if best_score >= LIMITE_CONFIANCA:
            matches_encontrados.append(best_match_info)
    
    # Processar os Matches
    matches_encontrados.sort(key=lambda x: x['score'], reverse=True)
    pagamentos_usados = set()
    banco_usados = set()
    matches_finais = []
    
    for match in matches_encontrados:
        if match['pag_index'] not in pagamentos_usados and \
           match['banco_index'] not in banco_usados:
            matches_finais.append(match)
            pagamentos_usados.add(match['pag_index'])
            banco_usados.add(match['banco_index'])
            
            match_id = str(uuid.uuid4())
            df_pagamentos.at[match['pag_index'], 'match_id'] = match_id
            df_pagamentos.at[match['pag_index'], 'match_score'] = match['score']
            df_pagamentos.at[match['pag_index'], 'delta_valor'] = match['delta_valor']
            df_banco.at[match['banco_index'], 'match_id'] = match_id
            df_banco.at[match['banco_index'], 'match_score'] = match['score']
    
    print(f"  ✅ Conciliações encontradas: {len(matches_finais)}")
    
    # Gerar relatórios
    conciliados_pag = df_pagamentos[df_pagamentos['match_id'].notnull()]
    conciliados_banco = df_banco[df_banco['match_id'].notnull()]
    
    df_relatorio_final = conciliados_pag.merge(
        conciliados_banco,
        on='match_id',
        suffixes=('_pag', '_banco')
    )
    
    pag_nao_encontrados = df_pagamentos[df_pagamentos['match_id'].isnull()]
    banco_nao_encontrados = df_banco[df_banco['match_id'].isnull()]
    
    # Ordenar débitos não encontrados por MEMO e DATA
    if not banco_nao_encontrados.empty:
        banco_nao_encontrados = banco_nao_encontrados.sort_values(
            by=[COL_BANCO_TEXTO_2, COL_BANCO_DATA]
        )
    
    # Adicionar coluna ATENÇÃO
    if not df_relatorio_final.empty:
        def definir_atencao(row):
            data_pag = row[COL_PAGTO_DATA]
            data_banco = row[COL_BANCO_DATA]
            data_diferente = data_pag.date() != data_banco.date()
            valor_diferente = row['delta_valor'] != Decimal('0.0')
            
            if data_diferente and valor_diferente:
                return "DT e VR"
            elif data_diferente:
                return "DATA"
            elif valor_diferente:
                return "VALOR"
            else:
                return "OK"
        
        df_relatorio_final['ATENÇÃO'] = df_relatorio_final.apply(definir_atencao, axis=1)
        cols = df_relatorio_final.columns.tolist()
        cols.insert(0, cols.pop(cols.index('ATENÇÃO')))
        df_relatorio_final = df_relatorio_final[cols]
    
    return df_relatorio_final, pag_nao_encontrados, banco_nao_encontrados


def salvar_relatorio_excel(df_conciliados, df_pag_nao_enc, df_banco_nao_enc, caminho_saida: Path):
    """
    Salva os resultados da conciliação em arquivo Excel com formatação profissional avançada.
    """
    try:
        from openpyxl.styles import Alignment, Border, Side
        
        # Criar cópias dos DataFrames para não alterar os originais
        df_conciliados_export = df_conciliados.copy()
        df_pag_nao_enc_export = df_pag_nao_enc.copy()
        df_banco_nao_enc_export = df_banco_nao_enc.copy()
        
        # Remover colunas específicas da aba Conciliados
        colunas_remover_conciliados = ['pag_chave_nome', 'pag_chave_obs', 'match_id', 'data', 'tipo']
        for coluna in colunas_remover_conciliados:
            if coluna in df_conciliados_export.columns:
                df_conciliados_export = df_conciliados_export.drop(columns=[coluna])
        
        # Remover colunas específicas da aba Pagtos_Nao_Encontrados
        colunas_remover_pagtos = ['Conta Movimento', 'Tipo', 'pag_chave_nome', 'pag_chave_obs']
        for coluna in colunas_remover_pagtos:
            if coluna in df_pag_nao_enc_export.columns:
                df_pag_nao_enc_export = df_pag_nao_enc_export.drop(columns=[coluna])
        
        with pd.ExcelWriter(caminho_saida, engine='openpyxl') as writer:
            df_conciliados_export.to_excel(writer, sheet_name="Conciliados", index=False, startrow=1)
            df_pag_nao_enc_export.to_excel(writer, sheet_name="Pagtos_Nao_Encontrados", index=False, startrow=1)
            df_banco_nao_enc_export.to_excel(writer, sheet_name="Debitos_Nao_Encontrados", index=False, startrow=1)
            
            workbook = writer.book
            
            # Estilos globais
            header_fill = PatternFill(start_color='366092', end_color='366092', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True, size=11)
            border_style = Border(
                left=Side(style='thin', color='D3D3D3'),
                right=Side(style='thin', color='D3D3D3'),
                top=Side(style='thin', color='D3D3D3'),
                bottom=Side(style='thin', color='D3D3D3')
            )
            center_alignment = Alignment(horizontal='center', vertical='center')
            
            # Formatação condicional para coluna ATENÇÃO
            green_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
            green_font = Font(color='006100', bold=True)
            red_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
            red_font = Font(color='9C0006', bold=True)
            yellow_fill = PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid')
            yellow_font = Font(color='9C6500', bold=True)
            
            # Função auxiliar para formatar planilha
            def formatar_planilha(worksheet, df, titulo, tem_atencao=False):
                if df.empty:
                    return
                
                # Adicionar título
                worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(df.columns))
                title_cell = worksheet.cell(row=1, column=1)
                title_cell.value = titulo
                title_cell.font = Font(size=14, bold=True, color='FFFFFF')
                title_cell.fill = PatternFill(start_color='203864', end_color='203864', fill_type='solid')
                title_cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Formatar cabeçalho (linha 2)
                for col_num, column_title in enumerate(df.columns, 1):
                    cell = worksheet.cell(row=2, column=col_num)
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = center_alignment
                    cell.border = border_style
                
                # Congelar painéis (título + cabeçalho)
                worksheet.freeze_panes = 'A3'
                
                # Formatar dados e aplicar bordas
                for row_num in range(3, len(df) + 3):
                    for col_num in range(1, len(df.columns) + 1):
                        cell = worksheet.cell(row=row_num, column=col_num)
                        cell.border = border_style
                        cell.alignment = Alignment(vertical='center', wrap_text=True)
                        
                        # Zebrar linhas (cinza claro nas linhas pares)
                        if row_num % 2 == 0:
                            cell.fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
                
                # Calcular largura média das colunas baseada no conteúdo
                for col_num, column_title in enumerate(df.columns, 1):
                    column_letter = worksheet.cell(row=2, column=col_num).column_letter
                    
                    # Coletar comprimentos de todos os valores da coluna
                    comprimentos = []
                    
                    # Adicionar comprimento do título da coluna
                    comprimentos.append(len(str(column_title)))
                    
                    # Adicionar comprimentos dos valores (amostra das primeiras 100 linhas)
                    for row_num in range(3, min(len(df) + 3, 103)):
                        cell_value = worksheet.cell(row=row_num, column=col_num).value
                        if cell_value is not None:
                            comprimentos.append(len(str(cell_value)))
                    
                    if comprimentos:
                        # Calcular média dos comprimentos
                        media_comprimento = sum(comprimentos) / len(comprimentos)
                        # Adicionar margem de 20%
                        adjusted_width = min(max(media_comprimento * 1.2, 10), 60)
                        worksheet.column_dimensions[column_letter].width = adjusted_width
                
                # Aplicar filtros automáticos
                worksheet.auto_filter.ref = f'A2:{worksheet.cell(row=2, column=len(df.columns)).coordinate}'
                
                # Formatação condicional para coluna ATENÇÃO (aplicar no final)
                if tem_atencao and 'ATENÇÃO' in df.columns:
                    col_idx = df.columns.get_loc('ATENÇÃO') + 1
                    
                    for row_num in range(3, len(df) + 3):
                        cell = worksheet.cell(row=row_num, column=col_idx)
                        valor = str(cell.value).strip() if cell.value else ''
                        
                        if valor == 'OK':
                            cell.fill = green_fill
                            cell.font = green_font
                            cell.alignment = center_alignment
                        elif valor in ['DATA', 'VALOR', 'DT e VR']:
                            if valor == 'DT e VR':
                                cell.fill = red_fill
                                cell.font = red_font
                            else:
                                cell.fill = yellow_fill
                                cell.font = yellow_font
                            cell.alignment = center_alignment
            
            # Aplicar formatação em cada aba (usar os DataFrames exportados)
            if 'Conciliados' in writer.sheets:
                formatar_planilha(
                    writer.sheets['Conciliados'],
                    df_conciliados_export,
                    '📊 RELATÓRIO DE CONCILIAÇÃO BANCÁRIA - ITENS CONCILIADOS',
                    tem_atencao=True
                )
            
            if 'Pagtos_Nao_Encontrados' in writer.sheets:
                formatar_planilha(
                    writer.sheets['Pagtos_Nao_Encontrados'],
                    df_pag_nao_enc_export,
                    '⚠️  PAGAMENTOS NÃO ENCONTRADOS NO EXTRATO BANCÁRIO'
                )
            
            if 'Debitos_Nao_Encontrados' in writer.sheets:
                formatar_planilha(
                    writer.sheets['Debitos_Nao_Encontrados'],
                    df_banco_nao_enc_export,
                    '⚠️  DÉBITOS BANCÁRIOS SEM REGISTRO DE PAGAMENTO'
                )
        
        print(f"  ✅ Relatório salvo: {caminho_saida.name}")
        return True
        
    except Exception as e:
        print(f"  ❌ Erro ao salvar Excel: {e}")
        return False


# ==================== FUNÇÃO PRINCIPAL ====================

def main():
    """
    Função principal que orquestra todo o processo de conciliação bancária em lotes.
    """
    print("\n" + "="*70)
    print(" SISTEMA DE CONCILIAÇÃO BANCÁRIA AUTOMATIZADA ".center(70, "="))
    print("="*70)
    
    # ETAPA 1: Carregar arquivos OFX
    df_ofx = carregar_arquivos_ofx()
    
    # Obter período de datas
    df_ofx['data'] = pd.to_datetime(df_ofx['data']).dt.tz_localize(None)
    data_min = df_ofx['data'].min().strftime("%d/%m/%Y")
    data_max = df_ofx['data'].max().strftime("%d/%m/%Y")
    
    print(f"\n📅 Período detectado: {data_min} a {data_max}")
    
    # ETAPA 2: Preparar arquivos auxiliares
    preparar_arquivos_auxiliares(data_min, data_max)
    
    # ETAPA 3: Carregar dados auxiliares
    df_pagamentos, df_cadastro, df_bancos = carregar_arquivos_auxiliares()
    
    # ETAPA 4: Processar conciliação em lotes (por conta bancária)
    print(f"\n{'='*60}")
    print("ETAPA 4: PROCESSAMENTO EM LOTES")
    print(f"{'='*60}")
    
    # Criar diretório de relatórios
    PASTA_RELATORIOS.mkdir(exist_ok=True)
    
    # Agrupar por conta bancária
    grupos = df_ofx.groupby(['banco_codigo', 'banco_agencia', 'banco_conta'])
    total_grupos = len(grupos)
    
    print(f"\nTotal de contas bancárias a processar: {total_grupos}\n")
    
    contador = 0
    for (codigo_b, agencia_b, conta_b), ofx_grupo in grupos:
        contador += 1
        print(f"\n{'─'*60}")
        print(f"[{contador}/{total_grupos}] Processando conta:")
        print(f"  Banco: {codigo_b} | Agência: {agencia_b} | Conta: {conta_b}")
        print(f"{'─'*60}")
        
        # 4.1: Identificar empreendimentos vinculados à conta
        # Converter para string para garantir comparação correta
        banco_filtrado = df_bancos[
            (df_bancos['codigo'].astype(str) == str(codigo_b)) &
            (df_bancos['agencia'].astype(str) == str(agencia_b)) &
            (df_bancos['conta'].astype(str) == str(conta_b))
        ]
        
        if banco_filtrado.empty:
            print(f"  ⚠️  Aviso: Conta não encontrada no cadastro de bancos!")
            print(f"  Pulando este grupo...")
            continue
        
        lista_empreendimentos = banco_filtrado['empreendimentos'].unique().tolist()
        print(f"  Empreendimentos vinculados: {lista_empreendimentos}")
        
        # 4.2: Filtrar pagamentos pelos empreendimentos
        pagamentos_grupo = df_pagamentos[
            df_pagamentos['Centro Custo'].isin(lista_empreendimentos)
        ].copy()
        
        if pagamentos_grupo.empty:
            print(f"  ⚠️  Nenhum pagamento encontrado para estes empreendimentos!")
            print(f"  Pulando este grupo...")
            continue
        
        # 4.3: Executar conciliação específica
        df_conciliados, df_pag_nao_enc, df_banco_nao_enc = conciliar_grupo(
            ofx_grupo,
            pagamentos_grupo,
            df_cadastro
        )
        
        # 4.4: Salvar relatório XLSX
        nome_arquivo = f"conciliacao_{codigo_b}_{agencia_b}_{conta_b}.xlsx"
        caminho_saida = PASTA_RELATORIOS / nome_arquivo
        
        salvar_relatorio_excel(
            df_conciliados,
            df_pag_nao_enc,
            df_banco_nao_enc,
            caminho_saida
        )
        
        # Exibir resumo
        print(f"\n  📊 Resumo da conciliação:")
        print(f"     • Conciliados: {len(df_conciliados)}")
        print(f"     • Pagamentos não encontrados: {len(df_pag_nao_enc)}")
        print(f"     • Débitos não encontrados: {len(df_banco_nao_enc)}")
    
    # CONCLUSÃO
    print(f"\n{'='*70}")
    print(" CONCILIAÇÃO CONCLUÍDA COM SUCESSO ".center(70, "="))
    print(f"{'='*70}")
    print(f"\n✅ {contador} conta(s) processada(s)")
    print(f"📁 Relatórios salvos em: {PASTA_RELATORIOS}")
    print()


if __name__ == "__main__":
    main()