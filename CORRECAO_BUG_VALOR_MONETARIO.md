# Correção de Bug: Conversão de Valor Monetário para Centavos

## Problema Identificado

**Data:** 21 de dezembro de 2025  
**Documento afetado:** 6608  
**Valor esperado:** R$ 132,95 → 13295 centavos  
**Valor gerado:** 13294 centavos (ERRO)

### Causa Raiz

O problema estava na conversão de valores monetários em reais (float) para centavos (int) usando:

```python
valor_centavos = int(valor_reais * 100)  # PROBLEMÁTICO!
```

#### Por que isso é um problema?

Números de ponto flutuante (float) em Python (e na maioria das linguagens) não conseguem representar todos os valores decimais com precisão exata. Isso ocorre porque usam representação binária.

**Exemplo do problema:**
```python
>>> 132.95 * 100
13294.999999999998  # Não é exatamente 13295!

>>> int(132.95 * 100)
13294  # ERRADO! Trunca ao invés de arredondar
```

O `int()` **trunca** (corta) a parte decimal, não arredonda. Assim:
- `13294.999999999998` vira `13294` (perde 1 centavo)

### Outros valores afetados

Qualquer valor que termine em `.95`, `.45`, `.15`, etc., pode sofrer deste problema devido à imprecisão de representação em ponto flutuante.

## Solução Implementada

### Código Corrigido

```python
from decimal import Decimal, ROUND_HALF_UP

# Converte para Decimal (precisão arbitrária)
valor_decimal = Decimal(str(valor_reais))
valor_centavos_decimal = valor_decimal * Decimal('100')

# Arredonda corretamente
valor_centavos = int(valor_centavos_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
```

### Por que funciona?

1. **Decimal:** Usa aritmética decimal (não binária), mantendo precisão exata
2. **str(valor_reais):** Converte o float para string antes de criar Decimal, preservando o valor lido
3. **quantize():** Arredonda explicitamente para inteiro
4. **ROUND_HALF_UP:** Estratégia de arredondamento (0.5 → 1)

**Exemplo corrigido:**
```python
>>> from decimal import Decimal, ROUND_HALF_UP
>>> valor_decimal = Decimal('132.95')
>>> valor_centavos_decimal = valor_decimal * Decimal('100')
>>> int(valor_centavos_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
13295  # CORRETO!
```

## Proteções Adicionadas

1. **Try/except:** Captura erros na conversão
2. **Logging detalhado:** Registra falhas com contexto
3. **Tratamento de tipos:** Aceita float, str ou Decimal
4. **Validação:** Verifica valores negativos ou zero
5. **Comentários explicativos:** Documenta o problema no código

## Testes Criados

Arquivo: `tests/test_conversao_valor.py`

Testa:
- Valores simples (100.00 → 10000)
- Valores problemáticos (132.95 → 13295)
- Comparação método antigo vs novo
- Arredondamento correto
- Valores grandes e pequenos
- Conversão de diferentes tipos (float, str, Decimal)

**Resultado dos testes:** ✅ 9/9 passaram

## Arquivos Modificados

1. **src/processar_contas_pagar_cnab.py**
   - Linha ~544: Substituída conversão `int(valor_reais * 100)`
   - Adicionado import: `from decimal import Decimal, ROUND_HALF_UP`
   - Adicionado bloco try/except com tratamento de erro
   - Adicionados comentários explicativos

## Impacto

- **Crítico:** Valores monetários incorretos podem causar problemas financeiros
- **Abrangência:** Afeta TODOS os arquivos CNAB gerados
- **Recomendação:** Regenerar todos os arquivos CNAB recentes

## Como Verificar

Para verificar se um valor foi afetado:

```python
valor = 132.95
metodo_antigo = int(valor * 100)      # 13294 (ERRADO)
metodo_novo = int(Decimal(str(valor)) * Decimal('100'))  # 13295 (CORRETO)

if metodo_antigo != metodo_novo:
    print(f"⚠️ Diferença detectada: {metodo_novo - metodo_antigo} centavos")
```

## Prevenção Futura

1. ✅ Sempre usar `Decimal` para cálculos monetários
2. ✅ Nunca usar `float` para armazenar valores monetários
3. ✅ Adicionar testes para casos conhecidos de imprecisão
4. ✅ Documentar o problema no código-fonte
5. ✅ Validar com testes automatizados

## Referências

- [Python Decimal Documentation](https://docs.python.org/3/library/decimal.html)
- [Floating Point Arithmetic: Issues and Limitations](https://docs.python.org/3/tutorial/floatingpoint.html)
- [What Every Programmer Should Know About Floating-Point Arithmetic](https://floating-point-gui.de/)
