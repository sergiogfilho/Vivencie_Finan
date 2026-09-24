#!/usr/bin/env python3
"""
Testes para garantir conversão correta de valores monetários para centavos
"""

import unittest
from decimal import Decimal, ROUND_HALF_UP


def converter_reais_para_centavos(valor_reais):
    """
    Converte valor em reais para centavos usando Decimal para precisão
    
    Args:
        valor_reais: Valor em reais (float, str ou Decimal)
    
    Returns:
        Valor em centavos (int)
    """
    try:
        valor_decimal = Decimal(str(valor_reais))
        valor_centavos_decimal = valor_decimal * Decimal('100')
        # Arredonda para o inteiro mais próximo
        valor_centavos = int(valor_centavos_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        return valor_centavos
    except Exception as e:
        raise ValueError(f"Erro na conversão de valor: {valor_reais} - {e}")


class TestConversaoValor(unittest.TestCase):
    """Testes de conversão de valor monetário"""
    
    def test_valor_simples(self):
        """Testa valores simples"""
        self.assertEqual(converter_reais_para_centavos(100.00), 10000)
        self.assertEqual(converter_reais_para_centavos(1.00), 100)
        self.assertEqual(converter_reais_para_centavos(0.01), 1)
    
    def test_valor_com_centavos(self):
        """Testa valores com centavos"""
        self.assertEqual(converter_reais_para_centavos(132.95), 13295)
        self.assertEqual(converter_reais_para_centavos(10.50), 1050)
        self.assertEqual(converter_reais_para_centavos(99.99), 9999)
    
    def test_valor_problematico_float(self):
        """Testa casos conhecidos de problemas com float"""
        # 132.95 * 100 em float pode resultar em 13294.999999999998
        valor_float = 132.95
        self.assertEqual(converter_reais_para_centavos(valor_float), 13295)
        
        # Outros casos problemáticos
        self.assertEqual(converter_reais_para_centavos(0.1 + 0.2), 30)  # 0.30000000000000004
        self.assertEqual(converter_reais_para_centavos(1.1 + 2.2), 330)  # 3.3000000000000003
    
    def test_valor_string(self):
        """Testa conversão a partir de string"""
        self.assertEqual(converter_reais_para_centavos("132.95"), 13295)
        self.assertEqual(converter_reais_para_centavos("100"), 10000)
        self.assertEqual(converter_reais_para_centavos("0.01"), 1)
    
    def test_valor_decimal(self):
        """Testa conversão a partir de Decimal"""
        self.assertEqual(converter_reais_para_centavos(Decimal("132.95")), 13295)
        self.assertEqual(converter_reais_para_centavos(Decimal("100.00")), 10000)
    
    def test_arredondamento(self):
        """Testa arredondamento correto"""
        # ROUND_HALF_UP: arredonda 0.5 para cima
        self.assertEqual(converter_reais_para_centavos(1.005), 101)  # 100.5 centavos
        self.assertEqual(converter_reais_para_centavos(1.004), 100)  # 100.4 centavos
        self.assertEqual(converter_reais_para_centavos(1.006), 101)  # 100.6 centavos
    
    def test_valores_grandes(self):
        """Testa valores grandes"""
        self.assertEqual(converter_reais_para_centavos(999999.99), 99999999)
        self.assertEqual(converter_reais_para_centavos(123456.78), 12345678)
    
    def test_valores_pequenos(self):
        """Testa valores muito pequenos"""
        self.assertEqual(converter_reais_para_centavos(0.01), 1)
        self.assertEqual(converter_reais_para_centavos(0.02), 2)
        self.assertEqual(converter_reais_para_centavos(0.99), 99)
    
    def test_comparacao_metodo_antigo_vs_novo(self):
        """Compara método antigo (problemático) com o novo (correto)"""
        valores_problematicos = [
            132.95,
            10.15,
            23.45,
            0.1 + 0.2,  # Clássico problema de float
            1.1 + 2.2,
            99.99
        ]
        
        for valor in valores_problematicos:
            # Método antigo (ERRADO - pode truncar)
            metodo_antigo = int(valor * 100)
            
            # Método novo (CORRETO - arredonda)
            metodo_novo = converter_reais_para_centavos(valor)
            
            # Imprime comparação
            print(f"\nValor: {valor}")
            print(f"  Multiplicação direta: {valor * 100}")
            print(f"  Método antigo int(valor * 100): {metodo_antigo}")
            print(f"  Método novo (Decimal): {metodo_novo}")
            
            # O método novo deve sempre dar o valor correto
            # Para 132.95, esperamos 13295
            if valor == 132.95:
                self.assertEqual(metodo_novo, 13295, 
                    f"Valor 132.95 deve converter para 13295, não {metodo_novo}")


if __name__ == '__main__':
    # Executa testes com verbosidade
    unittest.main(verbosity=2)
