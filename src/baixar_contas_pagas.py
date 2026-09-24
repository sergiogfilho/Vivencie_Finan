#!/usr/bin/env python3
"""
Script CLI para processamento paralelo de baixas de contas pagas via CNAB 240
Utiliza multi-threading para processar múltiplos pagamentos simultaneamente.
"""

import argparse
import logging
import os
import sys
import json
import glob
import shutil
from datetime import datetime
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv

# Adicionar diretório src ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Carregar variáveis de ambiente
load_dotenv()

from automatizador_final import ParserCNAB240Retorno
from worker_thread import WorkerThread


def configurar_logging():
    """Configura sistema de logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('automatizador_final.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    # Reduzir verbosidade do Selenium
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def processar_arquivo_retorno(arquivo_path: str) -> tuple:
    """
    Processa um arquivo .RET e extrai pagamentos confirmados
    
    Returns:
        tuple: (confirmados, nao_confirmados, nome_arquivo)
    """
    parser = ParserCNAB240Retorno(arquivo_path)
    confirmados, nao_confirmados = parser.processar_arquivo()
    nome_arquivo = os.path.basename(arquivo_path)
    
    return confirmados, nao_confirmados, nome_arquivo


def mover_para_processados(arquivo_path: str, dir_processados: str):
    """Move arquivo para diretório de processados"""
    nome_arquivo = os.path.basename(arquivo_path)
    destino = os.path.join(dir_processados, f"{nome_arquivo}.bak")
    shutil.move(arquivo_path, destino)
    return destino


def salvar_nao_processados(nao_processados: list, diretorio_retorno: str):
    """Salva lista de não processados em arquivo texto tabulado"""
    if not nao_processados:
        return None
    
    # Dicionário de tradução de códigos de ocorrência
    traducao_ocorrencias = {
        "AJ": "Conflito Informes",
        "BD": "Inclusão Efetuada com Sucesso",
        "PD": "Transação Pendente de Assinatura",
        "BF": "Transação Rejeitada",
        "PG": "PIX chave # favorecido",
        "PJ": "Chave não cadastrada"
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo_saida = os.path.join(diretorio_retorno, f"nao_processados_{timestamp}.txt")
    
    with open(arquivo_saida, 'w', encoding='utf-8') as f:
        # Cabeçalho
        f.write("=" * 100 + "\n")
        f.write("PAGAMENTOS NÃO PROCESSADOS - RETORNO CNAB 240\n")
        f.write(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        f.write("=" * 100 + "\n\n")
        
        # Tabela
        f.write(f"{'Documento':<15} {'Nome Favorecido':<40} {'Ocorrências':<30} {'Valor':<15}\n")
        f.write("-" * 100 + "\n")
        
        for pagamento in nao_processados:
            doc = pagamento.get('seu_numero', '').strip()
            nome = pagamento.get('nome_favorecido', '')[:38]
            # Traduzir códigos de ocorrência para descrições
            codigos_ocorrencias = pagamento.get('ocorrencias', [])
            ocorrencias_traduzidas = [traducao_ocorrencias.get(cod, cod) for cod in codigos_ocorrencias]
            ocorrencias = ', '.join(ocorrencias_traduzidas)
            
            # Converter valor para float (pode vir como string do CNAB)
            try:
                valor_raw = pagamento.get('valor_pagamento', '0')
                if isinstance(valor_raw, str):
                    # Remover zeros à esquerda e converter para float
                    # Formato CNAB: últimos 2 dígitos são decimais
                    valor_raw = valor_raw.strip()
                    if valor_raw:
                        valor_float = float(valor_raw) / 100.0
                    else:
                        valor_float = 0.0
                else:
                    valor_float = float(valor_raw)
                valor = f"R$ {valor_float:,.2f}"
            except:
                valor = "R$ 0,00"
            
            f.write(f"{doc:<15} {nome:<40} {ocorrencias:<30} {valor:<15}\n")
        
        f.write("\n" + "=" * 100 + "\n")
        f.write(f"Total de pagamentos não processados: {len(nao_processados)}\n")
    
    return arquivo_saida


def salvar_relatorio_consolidado(resultados: dict, diretorio_retorno: str):
    """Salva relatório consolidado em formato TXT com colunas fixas"""
    from automatizador_final import formatar_data_cnab
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo_txt = os.path.join(diretorio_retorno, f"relatorio_baixas_{timestamp}.txt")
    
    # Combinar sucessos e erros em uma única lista
    todos_resultados = []
    for item in resultados['sucessos']:
        todos_resultados.append({
            'status': 'sucesso',
            **item
        })
    for item in resultados['erros']:
        todos_resultados.append({
            'status': 'erro',
            **item
        })
    
    with open(arquivo_txt, 'w', encoding='utf-8') as f:
        # Cabeçalho
        agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        f.write(f"RELATÓRIO DE BAIXAS AUTOMÁTICAS - {agora}\n")
        f.write("-" * 138 + "\n")
        
        total = len(resultados['sucessos']) + len(resultados['erros'])
        f.write(f"RESUMO: Total: {total} | Sucesso: {len(resultados['sucessos'])} | Erros: {len(resultados['erros'])}\n")
        f.write("-" * 138 + "\n")
        
        # Cabeçalho das colunas
        f.write(f"{'DT. PAGTO':<12} | {'AGÊNCIA':<8} | {'CONTA':<16} | {'DOC':<13} | {'NOME':<31} | {'STATUS':<11} | {'DETALHES'}\n")
        f.write("-" * 138 + "\n")
        
        # Dados
        for resultado in todos_resultados:
            # Formatar data
            data_pagto = formatar_data_cnab(resultado.get('data_pagamento', ''))
            
            # Preparar dados
            agencia = resultado.get('agencia', '')[:8]
            conta = resultado.get('conta', '')[:16]
            doc = resultado.get('documento', '')[:13]
            nome = resultado.get('nome', '')[:31]
            status = 'SUCESSO' if resultado.get('status') == 'sucesso' else 'ERRO'
            
            # Detalhes: motivo ou erro
            if resultado.get('status') == 'sucesso':
                detalhes = resultado.get('motivo', 'Baixa confirmada')[:50]
            else:
                detalhes = resultado.get('erro', 'Erro desconhecido')[:50]
            
            # Escrever linha
            f.write(f"{data_pagto:<12} | {agencia:<8} | {conta:<16} | {doc:<13} | {nome:<31} | {status:<11} | {detalhes}\n")
        
        f.write("-" * 138 + "\n")
    
    return arquivo_txt


def main():
    parser = argparse.ArgumentParser(
        description='Processamento paralelo de baixas de contas pagas via CNAB 240'
    )
    parser.add_argument(
        '--diretorio_retorno',
        default='./retorno',
        help='Diretório contendo arquivos .RET (padrão: ./retorno)'
    )
    parser.add_argument(
        '--no_hide',
        action='store_true',
        help='Executar com browser visível (modo debug)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=4,
        help='Número de workers paralelos (padrão: 4)'
    )
    parser.add_argument(
        '--max_retries',
        type=int,
        default=3,
        help='Tentativas máximas por pagamento (padrão: 3)'
    )
    
    args = parser.parse_args()
    
    # Configurar logging
    configurar_logging()
    logger = logging.getLogger('main')
    
    # Obter credenciais do .env
    usuario = os.getenv('ACADE_USUARIO')
    senha = os.getenv('ACADE_SENHA')
    
    if not usuario or not senha:
        logger.error("❌ Credenciais não encontradas no arquivo .env")
        logger.error("   Configure ACADE_USUARIO e ACADE_SENHA no arquivo .env")
        sys.exit(1)
    
    # Banner
    print("=" * 70)
    print("🎯 AUTOMATIZADOR ACADE ONE - BAIXA DE CONTAS PAGAS (PARALELO)")
    print("=" * 70)
    print()
    print(f"📂 Diretório de retorno: {args.diretorio_retorno}")
    print(f"👁️  Modo: {'Visível (debug)' if args.no_hide else 'Headless (invisível)'}")
    print(f"⚙️  Workers: {args.workers}")
    print(f"🔄 Tentativas por pagamento: {args.max_retries}")
    print()
    
    try:
        # Criar diretório de processados
        dir_processados = os.path.join(args.diretorio_retorno, 'processados', 'bak')
        os.makedirs(dir_processados, exist_ok=True)
        
        # Buscar arquivos .RET
        pattern = os.path.join(args.diretorio_retorno, '*.RET')
        arquivos_ret = glob.glob(pattern)
        
        if not arquivos_ret:
            logger.info(f"Nenhum arquivo .RET encontrado em {args.diretorio_retorno}")
            return
        
        logger.info(f"📂 Encontrados {len(arquivos_ret)} arquivo(s) .RET")
        
        # ====================================================================
        # FASE 1: PARSING E MOVIMENTAÇÃO DE ARQUIVOS
        # ====================================================================
        print("🚀 Iniciando processamento...")
        print()
        
        todos_confirmados = []
        todos_nao_confirmados = []
        
        for arquivo in arquivos_ret:
            logger.info(f"📄 Processando: {os.path.basename(arquivo)}")
            confirmados, nao_confirmados, nome = processar_arquivo_retorno(arquivo)
            
            logger.info(f"   ✅ Confirmados: {len(confirmados)}")
            logger.info(f"   ⚠️  Não confirmados: {len(nao_confirmados)}")
            
            todos_confirmados.extend(confirmados)
            todos_nao_confirmados.extend(nao_confirmados)
            
            # Mover arquivo para processados
            destino = mover_para_processados(arquivo, dir_processados)
            logger.info(f"   📦 Movido para: {destino}")
        
        logger.info(f"\n📊 RESUMO DO PARSING")
        logger.info(f"   Total de pagamentos confirmados: {len(todos_confirmados)}")
        logger.info(f"   Total de pagamentos não confirmados: {len(todos_nao_confirmados)}")
        
        # Salvar não processados
        if todos_nao_confirmados:
            arquivo_nao_proc = salvar_nao_processados(todos_nao_confirmados, args.diretorio_retorno)
            logger.info(f"   📝 Não processados salvos em: {arquivo_nao_proc}")
        
        # Se não há pagamentos confirmados, encerrar
        if not todos_confirmados:
            logger.info("\n✅ Nenhum pagamento confirmado para processar")
            return
        
        # ====================================================================
        # FASE 2: PROCESSAMENTO PARALELO
        # ====================================================================
        print()
        print("=" * 70)
        print("🚀 INICIANDO PROCESSAMENTO PARALELO")
        print("=" * 70)
        print()
        
        # Criar fila com todos os pagamentos
        pagamentos_queue = Queue()
        for pagamento in todos_confirmados:
            pagamentos_queue.put(pagamento)
        
        logger.info(f"📋 {pagamentos_queue.qsize()} pagamento(s) na fila")
        
        # Estrutura thread-safe para resultados
        resultados = {
            'sucessos': [],
            'erros': [],
            'inicio': datetime.now().isoformat(),
            'fim': None,
            'total_processado': 0,
            'num_workers': args.workers
        }
        
        # Criar e iniciar workers
        workers = []
        for worker_id in range(1, args.workers + 1):
            worker = WorkerThread(
                worker_id=worker_id,
                pagamentos_queue=pagamentos_queue,
                resultados=resultados,
                usuario=usuario,
                senha=senha,
                headless=not args.no_hide,
                max_retries=args.max_retries
            )
            workers.append(worker)
        
        # Executar workers em threads
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(worker.run) for worker in workers]
            
            # Aguardar conclusão de todas as threads
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Erro em worker: {str(e)}")
        
        # Aguardar fila esvaziar completamente
        pagamentos_queue.join()
        
        # Finalizar estatísticas
        resultados['fim'] = datetime.now().isoformat()
        resultados['total_processado'] = len(resultados['sucessos']) + len(resultados['erros'])
        
        # ====================================================================
        # FASE 3: RELATÓRIOS
        # ====================================================================
        print()
        print("=" * 70)
        print("📊 RESULTADO FINAL")
        print("=" * 70)
        print()
        
        logger.info(f"✅ Sucessos: {len(resultados['sucessos'])}")
        logger.info(f"❌ Erros: {len(resultados['erros'])}")
        logger.info(f"📊 Total processado: {resultados['total_processado']}")
        
        # Salvar relatório consolidado
        arquivo_relatorio = salvar_relatorio_consolidado(resultados, args.diretorio_retorno)
        logger.info(f"📄 Relatório consolidado: {arquivo_relatorio}")
        
        # Exibir erros se houver
        if resultados['erros']:
            print()
            print("❌ Pagamentos com erro:")
            for erro in resultados['erros'][:10]:  # Mostrar apenas primeiros 10
                print(f"   • {erro['documento']} - {erro['nome'][:40]}: {erro['erro']}")
            if len(resultados['erros']) > 10:
                print(f"   ... e mais {len(resultados['erros']) - 10} erro(s)")
        
        print()
        print("🎉 Processamento paralelo concluído!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Processamento interrompido pelo usuário")
        logger.warning("Processamento interrompido")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Erro fatal: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
