#!/usr/bin/env python3
"""
Teste de conversão de linha digitável (48 dígitos) para código de barras (44 dígitos)
Tributos tipo B - Receita Federal, GPS, etc.
"""

def converter_linha_digitavel_para_codigo_barras(linha_digitavel: str) -> str:
    """
    Converte linha digitável (48 dígitos) para código de barras (44 dígitos)
    
    Estrutura da linha digitável (48 dígitos):
    - Bloco 1: Posições 0-10 (11 dígitos) + DV na posição 11
    - Bloco 2: Posições 12-22 (11 dígitos) + DV na posição 23
    - Bloco 3: Posições 24-34 (11 dígitos) + DV na posição 35
    - Bloco 4: Posições 36-46 (11 dígitos) + DV na posição 47
    
    Código de barras: Concatenação dos 4 blocos SEM os DVs
    """
    if len(linha_digitavel) != 48:
        raise ValueError(f"Linha digitável deve ter 48 dígitos, recebeu {len(linha_digitavel)}")
    
    codigo_barras = (
        linha_digitavel[0:11] +     # Bloco 1 (posições 0-10)
        linha_digitavel[12:23] +    # Bloco 2 (posições 12-22)
        linha_digitavel[24:35] +    # Bloco 3 (posições 24-34)
        linha_digitavel[36:47]      # Bloco 4 (posições 36-46)
    )
    
    return codigo_barras


def test_conversao_cofins_quixere():
    """
    Teste com exemplo real: COFINS 11/2025 - Vivencie Quixeré
    Documento: 48417
    Valor: R$ 725,10
    """
    # Linha digitável capturada do CSV (48 dígitos)
    linha_digitavel = "858000000070251003852536580701253568739969208090"
    
    print("=" * 80)
    print("TESTE: Conversão Linha Digitável → Código de Barras")
    print("=" * 80)
    print(f"Linha Digitável (48 dígitos): {linha_digitavel}")
    print(f"Tamanho: {len(linha_digitavel)} dígitos")
    print()
    
    # Exibe a estrutura detalhada
    print("Estrutura da Linha Digitável:")
    print(f"  Bloco 1 (pos 0-10):  {linha_digitavel[0:11]} + DV[11]={linha_digitavel[11]}")
    print(f"  Bloco 2 (pos 12-22): {linha_digitavel[12:23]} + DV[23]={linha_digitavel[23]}")
    print(f"  Bloco 3 (pos 24-34): {linha_digitavel[24:35]} + DV[35]={linha_digitavel[35]}")
    print(f"  Bloco 4 (pos 36-46): {linha_digitavel[36:47]} + DV[47]={linha_digitavel[47]}")
    print()
    
    # Converte para código de barras
    codigo_barras = converter_linha_digitavel_para_codigo_barras(linha_digitavel)
    
    print(f"Código de Barras (44 dígitos): {codigo_barras}")
    print(f"Tamanho: {len(codigo_barras)} dígitos")
    print()
    
    # Validações
    assert len(codigo_barras) == 44, f"Código de barras deve ter 44 dígitos, tem {len(codigo_barras)}"
    assert codigo_barras[0] == '8', "Código de barras de tributo deve começar com 8"
    
    print("✅ Conversão realizada com sucesso!")
    print()
    print("Comparação com arquivo CNAB gerado:")
    print(f"  Esperado no CNAB: {codigo_barras}")
    
    # O que está no arquivo de remessa (linha 7, posições 18-61)
    codigo_no_arquivo = "85800000007251003852536580701253568739969208090"
    print(f"  No arquivo atual: {codigo_no_arquivo}")
    print()
    
    if codigo_barras == codigo_no_arquivo:
        print("✅ CORRETO: O código no arquivo está correto!")
    else:
        print("❌ ERRO: O código no arquivo está diferente!")
        print("\nDiferenças:")
        for i, (esperado, atual) in enumerate(zip(codigo_barras, codigo_no_arquivo)):
            if esperado != atual:
                print(f"  Posição {i}: Esperado '{esperado}', Encontrado '{atual}'")
    
    print("=" * 80)
    
    return codigo_barras


def test_conversao_pis_lago_camocim():
    """
    Teste com outro exemplo: PIS 11/2025 - Vivencie Lago Camocim
    Documento: 41931
    Valor: R$ 1.087,97
    """
    # Linha digitável capturada do CSV (48 dígitos)
    linha_digitavel = "858100000102879703852537580701253568766764559106"
    
    print("\n" + "=" * 80)
    print("TESTE 2: PIS Lago Camocim")
    print("=" * 80)
    print(f"Linha Digitável: {linha_digitavel}")
    
    codigo_barras = converter_linha_digitavel_para_codigo_barras(linha_digitavel)
    print(f"Código de Barras: {codigo_barras}")
    print(f"Tamanho: {len(codigo_barras)} dígitos")
    
    assert len(codigo_barras) == 44
    print("✅ Conversão OK")
    print("=" * 80)
    
    return codigo_barras


if __name__ == "__main__":
    test_conversao_cofins_quixere()
    test_conversao_pis_lago_camocim()
    
    print("\n🎯 Todos os testes concluídos!")
