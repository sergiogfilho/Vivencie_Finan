"""
Testes do Motor de Interpretação e Validação de Pagamentos
Baseado no PADRAO_INTERPRETACAO_OBS.md
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from validador_pagamentos import ValidadorPagamentos, TipoPagamento, TipoChavePix


def test_normalizacao():
    """Testa normalização de texto"""
    print("=== TESTE: Normalização ===")
    
    casos = [
        ("Cliente quer receber via PIX", "cliente quer receber via pix"),
        ("AGÊNCIA: 1234", "agencia: 1234"),
        ("Código: 123-456", "codigo: 123-456"),
        ("  Espaços   extras  ", "espacos extras"),
    ]
    
    for entrada, esperado in casos:
        resultado = ValidadorPagamentos.normalizar_texto(entrada)
        status = "✓" if resultado == esperado else "✗"
        print(f"{status} '{entrada}' → '{resultado}'")
        if resultado != esperado:
            print(f"   Esperado: '{esperado}'")
    print()


def test_boleto_tipo_a():
    """Testa validação de boleto bancário (47 dígitos)"""
    print("=== TESTE: Boleto Tipo A (Bancário) ===")
    
    # Linha digitável válida de exemplo (47 dígitos)
    linha_valida = "34191.79001 01043.510047 91020.150008 1 84770000002000"
    
    valido, msg = ValidadorPagamentos.validar_boleto_tipo_a(linha_valida)
    print(f"Linha válida: {valido} - {msg}")
    
    # Linha com tamanho errado
    linha_curta = "34191.79001 01043.510047"
    valido, msg = ValidadorPagamentos.validar_boleto_tipo_a(linha_curta)
    print(f"Linha curta: {valido} - {msg}")
    print()


def test_boleto_tipo_b():
    """Testa validação de boleto de arrecadação (48 dígitos)"""
    print("=== TESTE: Boleto Tipo B (Arrecadação) ===")
    
    # Exemplo com módulo 10 (posição 2 = 6 ou 7)
    linha_mod10 = "836600000001 234567890104 123456789012 123456789012"
    
    valido, msg = ValidadorPagamentos.validar_boleto_tipo_b(linha_mod10)
    print(f"Tipo B Mod10: {valido} - {msg}")
    
    # Linha que não começa com 8
    linha_invalida = "736600000001 234567890104 123456789012 123456789012"
    valido, msg = ValidadorPagamentos.validar_boleto_tipo_b(linha_invalida)
    print(f"Não começa com 8: {valido} - {msg}")
    print()


def test_classificacao_chave_pix():
    """Testa classificação de chaves PIX"""
    print("=== TESTE: Classificação Chave PIX ===")
    
    casos = [
        ("41935365304", TipoChavePix.CPF, "CPF válido"),  # CPF do Francisco (válido)
        ("12345678000190", TipoChavePix.CNPJ, "CNPJ"),
        ("85987654321", TipoChavePix.TELEFONE, "Telefone"),
        ("user@example.com", TipoChavePix.EMAIL, "Email"),
        ("5ca096a5-f39a-4aa7-b0c5-8347ed8548b8", TipoChavePix.EVP, "UUID válido"),
        ("00020126580014BR.GOV.BCB.PIX", TipoChavePix.QR_CODE, "QR Code"),
    ]
    
    for chave, tipo_esperado, descricao in casos:
        tipo, chave_norm = ValidadorPagamentos.classificar_chave_pix(chave)
        if tipo == tipo_esperado:
            print(f"✓ {descricao}: {chave} → {tipo.value if tipo else 'None'}")
        else:
            print(f"✗ {descricao}: esperado {tipo_esperado.value if tipo_esperado else 'None'}, obtido {tipo.value if tipo else 'None'}")
    print()


def test_interpretacao_completa():
    """Testa interpretação completa de obs"""
    print("=== TESTE: Interpretação Completa ===")
    
    casos = [
        (
            "Cliente quer receber via PIX. CHAVE PIX: 41935365304",
            TipoPagamento.PIX,
            "PIX com CPF válido"
        ),
        (
            "Pagamento BTO: 34191.79001 01043.510047 91020.150008 1 84770000002000",
            TipoPagamento.BOLETO,
            "Boleto bancário"
        ),
        (
            "TED - BCO: 001 AG: 1234 CC: 56789-0",
            TipoPagamento.TED,
            "TED completo"
        ),
        (
            "Transferência AG: 4567 CC: 12345-6",
            TipoPagamento.TRANSFERENCIA,
            "Transferência"
        ),
        (
            "PIX: user@email.com",
            TipoPagamento.PIX,
            "PIX com email"
        ),
    ]
    
    for obs, tipo_esperado, descricao in casos:
        resultado = ValidadorPagamentos.interpretar(obs)
        tipo_obtido = resultado["tipo_pagamento"]
        
        if tipo_obtido == tipo_esperado:
            print(f"✓ {descricao}")
            print(f"  Validação: {resultado['validacao']}")
        else:
            print(f"✗ {descricao}")
            print(f"  Esperado: {tipo_esperado}")
            print(f"  Obtido: {tipo_obtido}")
            print(f"  Resultado: {resultado}")
    print()


def test_casos_reais():
    """Testa com exemplos reais do contas_pagar.csv"""
    print("=== TESTE: Casos Reais ===")
    
    casos = [
        "Cliente quer receber o valor a ser restituído por PIX do Banco Nubank / Agencia 0001 / Conta 86496242-9 . De sua titularidade. chave pix: 5ca096a5-f39a-4aa7-b0c5-8347ed8548b8",
        "Cliente quer receber o valor a ser restituído por PIX do Banco 290 - PagSeguro Internet Instituição de Pagamento S.A. / Agencia 0001 / Conta 29951327-5 . De sua titularidade CHAVE PIX: 85 99130-7027",
        "Cliente deseja receber os valores a serem restituídos via PIX do Banco Caixa Econômica Federal/ Agência: 0648/ Conta: 870833082-2 de sua titularidade. CHAVE PIX: 81986402161",
        "Cliente quer receber o valor a ser restituído por PIX do Banco Santander / Agencia 2476 / Conta 01003146-2. De sua titularidade. PIX: ecfaf0eb-4c56-49e5-a85d-f9c6eb7fceb9",
    ]
    
    for obs in casos:
        resultado = ValidadorPagamentos.interpretar(obs)
        tipo = resultado["tipo_pagamento"]
        valido = resultado["validacao"]["valido"]
        
        print(f"{'✓' if valido else '✗'} {tipo.value if tipo else 'NENHUM'}")
        if valido and tipo == TipoPagamento.PIX:
            dados = resultado["dados"]
            print(f"  Tipo Chave: {dados.get('tipo_chave').value if dados.get('tipo_chave') else 'N/A'}")
            print(f"  Chave: {dados.get('chave_pix', 'N/A')}")
        else:
            print(f"  Mensagem: {resultado['validacao']['mensagem']}")
        print()


def test_cpf_cnpj_explicito():
    """Testa extração de CPF/CNPJ explícito na obs"""
    print("=== TESTE: CPF/CNPJ Explícito ===")
    
    casos = [
        (
            "Pagamento PIX. Chave: 85999887766 CPF: 123.456.789-01",
            "12345678901",
            "CPF formatado com pontuação"
        ),
        (
            "PIX para email@exemplo.com CNPJ: 12.345.678/0001-90",
            "12345678000190",
            "CNPJ formatado com pontuação"
        ),
        (
            "PIX: 85987654321 cpf:98765432100",
            "98765432100",
            "CPF sem pontuação"
        ),
        (
            "Cliente quer PIX UUID: abc-def cnpj:11222333000199",
            "11222333000199",
            "CNPJ sem pontuação"
        ),
    ]
    
    for obs, cpf_cnpj_esperado, descricao in casos:
        resultado = ValidadorPagamentos.interpretar(obs)
        dados = resultado.get("dados", {})
        cpf_cnpj_obs = dados.get("cpf_cnpj_obs")
        
        if cpf_cnpj_obs == cpf_cnpj_esperado:
            print(f"✓ {descricao}: {cpf_cnpj_obs}")
        else:
            print(f"✗ {descricao}")
            print(f"  Esperado: {cpf_cnpj_esperado}")
            print(f"  Obtido: {cpf_cnpj_obs}")
    print()


if __name__ == "__main__":
    print("=" * 70)
    print("TESTES DO MOTOR DE VALIDAÇÃO DE PAGAMENTOS")
    print("=" * 70)
    print()
    
    test_normalizacao()
    test_boleto_tipo_a()
    test_boleto_tipo_b()
    test_classificacao_chave_pix()
    test_interpretacao_completa()
    test_casos_reais()
    test_cpf_cnpj_explicito()
    
    print("=" * 70)
    print("TESTES CONCLUÍDOS")
    print("=" * 70)
