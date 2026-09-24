#!/usr/bin/env python3
"""Teste da lógica de conta corrente"""

from src.processar_contas_pagar_cnab import ProcessadorContasPagarCNAB
from src.gerador_cnab240 import Modalidade

class MockProcessor:
    def _sanitizar_str(self, valor, default=""):
        if valor is None:
            return default
        return str(valor).strip() or default

processor = MockProcessor()
processor._criar_favorecido_completo = ProcessadorContasPagarCNAB._criar_favorecido_completo.__get__(processor)

# Teste 1: TED com conta 25283
dados = {"banco": "237", "agencia": "2367", "conta": "25283"}
fav = processor._criar_favorecido_completo("12345678901234", "TESTE", Modalidade.TED, dados)
print("Teste TED cc:25283 ->", "conta:", fav.conta, "dv:", repr(fav.dv_conta))

# Teste 2: Transferencia interna 38300
dados = {"agencia": "3357", "conta": "38300"}
fav = processor._criar_favorecido_completo("12345678901234", "TESTE", Modalidade.CREDITO_CONTA, dados)
print("Teste TRANSF cc:38300 ->", "conta:", fav.conta, "dv:", repr(fav.dv_conta))

# Teste 3: Com hifen 4816-0
dados = {"banco": "237", "agencia": "2367", "conta": "4816-0"}
fav = processor._criar_favorecido_completo("12345678901234", "TESTE", Modalidade.TED, dados)
print("Teste TED cc:4816-0 ->", "conta:", fav.conta, "dv:", repr(fav.dv_conta))

# Teste 4: Conta 137800
dados = {"banco": "001", "agencia": "1234", "conta": "137800"}
fav = processor._criar_favorecido_completo("12345678901234", "TESTE", Modalidade.TED, dados)
print("Teste TED cc:137800 ->", "conta:", fav.conta, "dv:", repr(fav.dv_conta))
