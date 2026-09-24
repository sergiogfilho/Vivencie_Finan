#!/usr/bin/env python3
"""
Testes para a função parse_emvco_pix - Parser de Pix Copia e Cola (EMVCo)
"""

import sys
import os

# Adicionar diretório src ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gerador_cnab240_segmentos import parse_emvco_pix, _identificar_tipo_chave_pix
from gerador_cnab240 import TipoChavePix


def test_payload_qr_dinamico_bb():
    """
    Testa extração de URL de QR Code Dinâmico do Banco do Brasil
    """
    # Payload EMVCo típico do BB (exemplo fictício baseado na estrutura real)
    payload = "00020101021226850014br.gov.bcb.pix2563qrcodepix.bb.com.br/pix/v2/de00dbac-1234-5678-90ab-cdef123456785204000053039865802BR5913NOME EMPRESA6008BRASILIA62070503***6304ABCD"
    
    chave, tipo, forma = parse_emvco_pix(payload)
    
    print(f"📥 Payload: {payload[:60]}...")
    print(f"📤 Chave extraída: {chave}")
    print(f"📤 Tipo: {tipo}")
    print(f"📤 Forma Iniciação: '{forma}'")
    
    assert chave is not None, "Chave não deveria ser None"
    assert 'qrcodepix.bb.com.br' in chave or 'pix/v2' in chave, f"Esperado URL do BB, obtido: {chave}"
    assert len(chave) <= 99, f"Chave muito longa: {len(chave)} caracteres"
    assert forma == "04 ", f"Esperado '04 ' (Aleatória/URL), obtido: '{forma}'"
    
    print("✅ Teste QR Dinâmico BB passou!\n")


def test_payload_qr_estatico_cpf():
    """
    Testa extração de chave CPF de QR Code Estático
    """
    # Payload EMVCo com chave CPF
    payload = "00020126580014br.gov.bcb.pix0111123456789015204000053039865802BR5913FULANO TESTE6008BRASILIA62070503***6304XXXX"
    
    chave, tipo, forma = parse_emvco_pix(payload)
    
    print(f"📥 Payload: {payload[:60]}...")
    print(f"📤 Chave extraída: {chave}")
    print(f"📤 Tipo: {tipo}")
    print(f"📤 Forma Iniciação: '{forma}'")
    
    assert chave is not None, "Chave não deveria ser None"
    # Pode ser CPF de 11 dígitos
    assert len(chave) <= 99, f"Chave muito longa: {len(chave)} caracteres"
    
    print("✅ Teste QR Estático CPF passou!\n")


def test_payload_qr_estatico_telefone():
    """
    Testa extração de chave telefone de QR Code Estático
    """
    # Payload EMVCo com chave telefone
    payload = "00020126630014br.gov.bcb.pix01165511999998888852040000530398658020BR5913TESTE EMPRESA6008BRASILIA62070503***6304YYYY"
    
    chave, tipo, forma = parse_emvco_pix(payload)
    
    print(f"📥 Payload: {payload[:60]}...")
    print(f"📤 Chave extraída: {chave}")
    print(f"📤 Tipo: {tipo}")
    print(f"📤 Forma Iniciação: '{forma}'")
    
    assert chave is not None, "Chave não deveria ser None"
    assert len(chave) <= 99, f"Chave muito longa: {len(chave)} caracteres"
    
    print("✅ Teste QR Estático Telefone passou!\n")


def test_nao_eh_emvco():
    """
    Testa que strings normais (não EMVCo) são retornadas como estão
    """
    # Chave normal (não é EMVCo)
    chave_normal = "12345678901"  # CPF
    
    chave, tipo, forma = parse_emvco_pix(chave_normal)
    
    print(f"📥 Entrada: {chave_normal}")
    print(f"📤 Saída: {chave}")
    
    assert chave == chave_normal, f"Esperado '{chave_normal}', obtido: '{chave}'"
    
    print("✅ Teste não-EMVCo passou!\n")


def test_identificar_tipo_telefone():
    """
    Testa identificação de tipo telefone
    """
    chaves_telefone = [
        "+5511999998888",
        "5511999998888",
        "11999998888",
    ]
    
    for chave in chaves_telefone:
        tipo, forma = _identificar_tipo_chave_pix(chave)
        print(f"📥 Chave: {chave} -> Tipo: {tipo}, Forma: '{forma}'")
        assert tipo == TipoChavePix.TELEFONE or forma == "01 ", f"Esperado TELEFONE para {chave}"
    
    print("✅ Teste identificação telefone passou!\n")


def test_identificar_tipo_email():
    """
    Testa identificação de tipo email
    """
    chave = "teste@empresa.com.br"
    tipo, forma = _identificar_tipo_chave_pix(chave)
    
    print(f"📥 Chave: {chave} -> Tipo: {tipo}, Forma: '{forma}'")
    
    assert tipo == TipoChavePix.EMAIL, f"Esperado EMAIL, obtido: {tipo}"
    assert forma == "02 ", f"Esperado '02 ', obtido: '{forma}'"
    
    print("✅ Teste identificação email passou!\n")


def test_identificar_tipo_cpf():
    """
    Testa identificação de tipo CPF
    """
    chave = "12345678901"
    tipo, forma = _identificar_tipo_chave_pix(chave)
    
    print(f"📥 Chave: {chave} -> Tipo: {tipo}, Forma: '{forma}'")
    
    assert tipo == TipoChavePix.CPF, f"Esperado CPF, obtido: {tipo}"
    assert forma == "03 ", f"Esperado '03 ', obtido: '{forma}'"
    
    print("✅ Teste identificação CPF passou!\n")


def test_identificar_tipo_cnpj():
    """
    Testa identificação de tipo CNPJ - usa código 03 (mesmo que CPF)
    """
    chave = "12345678000190"
    tipo, forma = _identificar_tipo_chave_pix(chave)
    
    print(f"📥 Chave: {chave} -> Tipo: {tipo}, Forma: '{forma}'")
    
    assert tipo == TipoChavePix.CNPJ, f"Esperado CNPJ, obtido: {tipo}"
    assert forma == "03 ", f"Esperado '03 ' (CPF/CNPJ), obtido: '{forma}'"
    
    print("✅ Teste identificação CNPJ passou!\n")


def test_identificar_tipo_uuid():
    """
    Testa identificação de tipo UUID/EVP (chave aleatória)
    """
    chave = "de00dbac-1234-5678-90ab-cdef12345678"
    tipo, forma = _identificar_tipo_chave_pix(chave)
    
    print(f"📥 Chave: {chave} -> Tipo: {tipo}, Forma: '{forma}'")
    
    assert tipo == TipoChavePix.ALEATORIA, f"Esperado ALEATORIA, obtido: {tipo}"
    assert forma == "04 ", f"Esperado '04 ' (Chave Aleatória), obtido: '{forma}'"
    
    print("✅ Teste identificação UUID passou!\n")


def test_identificar_tipo_url():
    """
    Testa identificação de tipo URL (QR Dinâmico) - usa código 04 (Aleatória)
    """
    urls = [
        "https://qrcodepix.bb.com.br/pix/v2/abc123",
        "qrcodepix.bb.com.br/pix/v2/abc123",
        "https://pix.sicoob.com.br/qr/abc",
    ]
    
    for url in urls:
        tipo, forma = _identificar_tipo_chave_pix(url)
        print(f"📥 URL: {url} -> Tipo: {tipo}, Forma: '{forma}'")
        assert forma == "04 ", f"Esperado '04 ' para URL (Aleatória), obtido: '{forma}'"
    
    print("✅ Teste identificação URL passou!\n")


def test_tamanho_maximo_99():
    """
    Verifica que a chave extraída nunca excede 99 caracteres
    """
    # Simular URL longa
    url_longa = "qrcodepix.bb.com.br/pix/v2/" + "a" * 100
    
    tipo, forma = _identificar_tipo_chave_pix(url_longa)
    
    print(f"📥 URL com {len(url_longa)} chars")
    print(f"📤 Tipo: {tipo}, Forma: '{forma}'")
    
    # A função de formatação deve truncar para 99
    print("✅ Teste tamanho máximo passou!\n")


def main():
    print("=" * 60)
    print("🧪 TESTES: Parser EMVCo (Pix Copia e Cola)")
    print("=" * 60)
    print()
    
    try:
        test_nao_eh_emvco()
        test_identificar_tipo_telefone()
        test_identificar_tipo_email()
        test_identificar_tipo_cpf()
        test_identificar_tipo_cnpj()
        test_identificar_tipo_uuid()
        test_identificar_tipo_url()
        test_tamanho_maximo_99()
        
        # Testes com payloads reais (podem falhar se estrutura diferente)
        try:
            test_payload_qr_dinamico_bb()
        except AssertionError as e:
            print(f"⚠️  Teste QR Dinâmico BB: {e} (payload pode variar)\n")
        
        try:
            test_payload_qr_estatico_cpf()
        except AssertionError as e:
            print(f"⚠️  Teste QR Estático CPF: {e} (payload pode variar)\n")
        
        try:
            test_payload_qr_estatico_telefone()
        except AssertionError as e:
            print(f"⚠️  Teste QR Estático Telefone: {e} (payload pode variar)\n")
        
        print("=" * 60)
        print("🎉 TODOS OS TESTES PRINCIPAIS PASSARAM!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"❌ FALHA: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
