#!/usr/bin/env python3
"""
TESTE DO PARSER CNAB 240 RETORNO
Valida a leitura de arquivos de retorno antes de processar baixas
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from automatizador_final import ParserCNAB240Retorno
import json
from datetime import datetime


def testar_parser(arquivo_ret):
    """
    Testa o parser em um arquivo de retorno específico
    """
    print("="*70)
    print("🧪 TESTE DO PARSER CNAB 240 RETORNO")
    print("="*70)
    print(f"\n📄 Arquivo: {arquivo_ret}\n")
    
    if not os.path.exists(arquivo_ret):
        print(f"❌ Arquivo não encontrado: {arquivo_ret}")
        return False
    
    # Criar parser
    parser = ParserCNAB240Retorno(arquivo_ret)
    
    # Processar arquivo
    confirmados, nao_confirmados = parser.processar_arquivo()
    
    # Exibir resultados
    print("\n" + "="*70)
    print("📊 RESUMO DO PROCESSAMENTO")
    print("="*70)
    print(f"✅ Pagamentos confirmados (00): {len(confirmados)}")
    print(f"⚠️  Pagamentos não confirmados: {len(nao_confirmados)}")
    
    # Detalhar confirmados
    if confirmados:
        print("\n" + "-"*70)
        print("✅ PAGAMENTOS CONFIRMADOS (SERÃO BAIXADOS NO ACADE)")
        print("-"*70)
        for idx, pag in enumerate(confirmados, 1):
            print(f"\n[{idx}] Seu Número: {pag['seu_numero'].strip()}")
            print(f"    Favorecido: {pag['nome_favorecido']}")
            print(f"    Data Pagamento: {pag['data_pagamento']} (usar como vencimento)")
            print(f"    Data Real: {pag['data_real']}")
            print(f"    Valor: {pag.get('valor_pagamento', 'N/A')}")
            print(f"    Conta: Ag {pag['agencia']} / Conta {pag['conta']}")
            print(f"    Ocorrências: {', '.join(pag['ocorrencias'])}")
    
    # Detalhar não confirmados
    if nao_confirmados:
        print("\n" + "-"*70)
        print("⚠️  PAGAMENTOS NÃO CONFIRMADOS (NÃO SERÃO PROCESSADOS)")
        print("-"*70)
        for idx, pag in enumerate(nao_confirmados, 1):
            print(f"\n[{idx}] Seu Número: {pag['seu_numero'].strip()}")
            print(f"    Favorecido: {pag['nome_favorecido']}")
            print(f"    Motivo: {pag['motivo']}")
    
    # Salvar em JSON para análise
    output_file = f"teste_parser_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'arquivo_testado': arquivo_ret,
            'data_teste': datetime.now().isoformat(),
            'total_confirmados': len(confirmados),
            'total_nao_confirmados': len(nao_confirmados),
            'confirmados': confirmados,
            'nao_confirmados': nao_confirmados
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n📄 Resultado salvo em: {output_file}")
    print("\n✅ Teste concluído com sucesso!")
    
    return True


def main():
    """Função principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Teste do Parser CNAB 240 Retorno')
    parser.add_argument('arquivo', nargs='?', 
                       help='Arquivo .RET para testar (opcional, usa primeiro do diretório ./retorno se não especificado)')
    
    args = parser.parse_args()
    
    # Determinar arquivo a testar
    if args.arquivo:
        arquivo_ret = args.arquivo
    else:
        # Buscar primeiro arquivo .RET em ./retorno
        import glob
        arquivos = glob.glob('./retorno/*.RET')
        if not arquivos:
            print("❌ Nenhum arquivo .RET encontrado em ./retorno")
            print("Use: python teste_parser.py caminho/para/arquivo.RET")
            return 1
        arquivo_ret = arquivos[0]
        print(f"ℹ️  Usando primeiro arquivo encontrado: {arquivo_ret}")
    
    # Testar
    sucesso = testar_parser(arquivo_ret)
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
