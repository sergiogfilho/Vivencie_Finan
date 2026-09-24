#!/usr/bin/env python3
"""
Demonstração do problema de conversão de valor e sua solução
"""

from decimal import Decimal, ROUND_HALF_UP

print("=" * 80)
print("DEMONSTRAÇÃO: Problema na Conversão de Valor Monetário")
print("=" * 80)
print()

# Exemplo do problema real: R$ 132,95
valor_reais = 132.95

print(f"Valor original: R$ {valor_reais:.2f}")
print()

# MÉTODO ANTIGO (PROBLEMÁTICO)
print("1. MÉTODO ANTIGO (com bug):")
print("-" * 40)
multiplicacao = valor_reais * 100
print(f"   {valor_reais} * 100 = {multiplicacao}")
print(f"   (Note a imprecisão: {multiplicacao:.20f})")
metodo_antigo = int(multiplicacao)
print(f"   int({multiplicacao}) = {metodo_antigo}")
print(f"   ❌ RESULTADO: {metodo_antigo} centavos")
print()

# MÉTODO NOVO (CORRETO)
print("2. MÉTODO NOVO (corrigido com Decimal):")
print("-" * 40)
valor_decimal = Decimal(str(valor_reais))
valor_centavos_decimal = valor_decimal * Decimal('100')
print(f"   Decimal('{valor_reais}') * Decimal('100') = {valor_centavos_decimal}")
metodo_novo = int(valor_centavos_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
print(f"   Arredondado: {metodo_novo}")
print(f"   ✅ RESULTADO: {metodo_novo} centavos")
print()

# COMPARAÇÃO
print("3. COMPARAÇÃO:")
print("-" * 40)
diferenca = metodo_novo - metodo_antigo
print(f"   Método antigo: {metodo_antigo} centavos")
print(f"   Método novo:   {metodo_novo} centavos")
print(f"   Diferença:     {diferenca} centavo(s)")
print()

if diferenca != 0:
    print(f"   ⚠️  PERDA DE {diferenca} CENTAVO(S) COM O MÉTODO ANTIGO!")
else:
    print(f"   ✅ Ambos os métodos geraram o mesmo resultado")

print()
print("=" * 80)
print("CONCLUSÃO:")
print("=" * 80)
print("""
O problema ocorre porque números de ponto flutuante (float) não conseguem
representar todos os valores decimais com precisão exata. Ao multiplicar
132.95 por 100, obtemos 13294.999999999998 ao invés de 13295.0 exato.

A função int() TRUNCA (corta) a parte decimal, não arredonda. Portanto:
- int(13294.999999999998) = 13294  (ERRADO!)

A solução é usar a classe Decimal do Python, que trabalha com aritmética
decimal (base 10) ao invés de binária, mantendo precisão exata. Depois,
usamos quantize() com ROUND_HALF_UP para arredondar corretamente.

IMPACTO: Este problema afeta TODOS os valores em arquivos CNAB gerados,
podendo causar divergências financeiras de centavos.
""")
