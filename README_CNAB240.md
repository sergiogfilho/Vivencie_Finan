# Gerador CNAB 240 - Sistema Completo

Sistema para geração de arquivos CNAB 240 (padrão FEBRABAN) a partir de contas a pagar.

## 📁 Arquivos do Sistema

### Módulos Principais

1. **gerador_cnab240.py** - Estruturas base (dataclasses, validadores, formatadores)
2. **gerador_cnab240_segmentos.py** - Geradores de todos os segmentos CNAB
3. **gerador_cnab240_motor.py** - Motor de geração e orquestração
4. **processar_contas_pagar_cnab.py** - Script de integração CSV → CNAB

### Arquivos de Configuração

1. **config_cnab240_schema.json** - Schema completo (documentação JSON Schema)
2. **config_cnab240_exemplo.json** - Template para preencher com dados do seu banco

## 🚀 Como Usar

### Passo 1: Configure seu Banco

1. Copie o arquivo `config_cnab240_exemplo.json` com novo nome:
   ```bash
   cp config_cnab240_exemplo.json configs_bancos/bradesco.json
   ```

2. Preencha todos os campos obrigatórios:
   - **bank_profile**: Dados do banco (códigos, versões, formas de lançamento)
   - **empresa**: Dados da sua empresa (CNPJ, conta, endereço)
   - **parametros_lote**: Configurações padrão dos lotes
   - **opcoes_geracao**: Opções de saída (diretório, prefixos)

3. Consulte o manual CNAB 240 do seu banco para:
   - Código do banco (3 dígitos)
   - Versões de layout (lote e arquivo)
   - Formas de lançamento por modalidade
   - Códigos de câmara centralizadora

### Passo 2: Prepare os Arquivos CSV

Você precisa de 3 arquivos CSV:

#### A) Contas a Pagar (`contas_pagar.csv`)

Formato esperado:
```csv
Centro Custo,Vencto,Comp.,Lancto,Conta,Doc,Beneficiado,Parc,Valor,Obs.
1001,27/10/2025,10/2025,26/10/2025,Fornecedores,12345,FORNECEDOR XYZ LTDA,1/1,1500.00,Pagamento nota fiscal
1002,28/10/2025,10/2025,26/10/2025,Fornecedores,12346,EMPRESA ABC SA,1/1,2500.50,TED
```

#### B) Correlação Centro Custo → Banco (`correlacao_banco.csv`)

Associa cada centro de custo a um arquivo de configuração de banco:
```csv
centro_custo,banco_config_file
1001,bradesco.json
1002,itau.json
1003,bradesco.json
```

#### C) Pessoas Cadastradas (`pessoas_cadastradas.csv`)

Gerado pelo `capturador_pessoas.py` ou criado manualmente:
```csv
CPF/CNPJ,Nome,Tipo Pessoa,Banco,Agência,DV Ag,Conta,DV Conta,Endereço,Número,Complemento,Bairro,Cidade,CEP,UF,Chave PIX
12345678000199,FORNECEDOR XYZ LTDA,PJ,237,1234,0,123456,7,RUA EXEMPLO,100,SALA 10,CENTRO,SAO PAULO,01310100,SP,12345678000199
```

### Passo 3: Execute o Processador

```bash
python processar_contas_pagar_cnab.py contas_pagar.csv correlacao_banco.csv relatorios/pessoas_cadastradas.csv configs_bancos/
```

Para execuções mais silenciosas (apenas avisos/erros), acrescente o parâmetro `--warnings-only` ao final do comando:

```bash
python processar_contas_pagar_cnab.py contas.csv bancos.csv pessoas.csv configs/ --warnings-only
```

### Passo 4: Verifique os Arquivos Gerados

O sistema cria no diretório `./remessas/` apenas o arquivo CNAB:

- **remessa_[BANCO]_[CENTRO_CUSTO]_[TIMESTAMP].txt** - Arquivo CNAB 240 (240 caracteres por linha)

## 📋 Modalidades Suportadas

1. **CREDITO_CONTA** - Crédito em conta corrente (mesmo banco)
2. **TED** - Transferência Eletrônica Disponível
3. **PIX_CHAVE** - PIX via chave (CPF, CNPJ, e-mail, telefone, aleatória)
4. **PIX_QR_DINAMICO** - PIX via QR Code dinâmico

## 🔍 Estrutura do Arquivo CNAB 240

```
Header de Arquivo (Tipo 0)
  ↓
Header de Lote (Tipo 1)
  ↓
Segmento A (Tipo 3A) - Dados bancários e valor
Segmento B (Tipo 3B) - Dados cadastrais do favorecido
  ou
Segmento J (Tipo 3J) - Dados do título PIX
Segmento J52 (Tipo 3J52) - Código PIX e QR Code
  ↓
[Repetir segmentos para cada pagamento]
  ↓
Trailer de Lote (Tipo 5)
  ↓
[Repetir lote se houver mais]
  ↓
Trailer de Arquivo (Tipo 9)
```

## ⚙️ Configurações Importantes

### Códigos de Banco Principais

- 001 - Banco do Brasil
- 033 - Santander
- 104 - Caixa Econômica Federal
- 237 - Bradesco
- 341 - Itaú
- 756 - Sicoob

### Versões de Layout Comuns

- Layout Lote: 040, 045, 050, 060
- Layout Arquivo: 080, 085, 090, 103

### Formas de Lançamento

Consulte o manual do seu banco. Exemplos:
- Crédito em Conta: 01, 03
- TED: 41, 43
- PIX Chave: 45
- PIX QR: 47

### Câmaras Centralizadoras

- 000 - Mesmo banco
- 009 - PIX
- 018 - TED

## 🛠️ Uso Programático

Você também pode usar o motor diretamente em Python:

```python
from gerador_cnab240_motor import MotorCNAB240
from gerador_cnab240 import Pagamento, Favorecido, Modalidade

# Inicializa motor com configuração
motor = MotorCNAB240('configs_bancos/bradesco.json')

# Cria favorecido
favorecido = Favorecido(
    tipo_inscricao=2,
    numero_inscricao="12345678000199",
    nome_favorecido="FORNECEDOR XYZ LTDA",
    banco="237",
    agencia="1234",
    dv_agencia="0",
    conta="123456",
    dv_conta="7",
    # ... demais campos
)

# Cria pagamento
pagamento = Pagamento(
    modalidade=Modalidade.CREDITO_CONTA,
    valor_pagamento=150000,  # Em centavos (R$ 1.500,00)
    data_pagamento="27102025",
    data_vencimento="27102025",
    favorecido=favorecido,
    numero_documento="12345"
)

# Gera remessa
conteudo, relatorio = motor.gerar_remessa([pagamento])

# Exporta arquivos
motor.exportar_arquivo(conteudo)
motor.exportar_relatorio(relatorio)
motor.exportar_log()
```

## ✅ Validações Realizadas

O sistema valida automaticamente:

- ✓ Completude de todos os campos obrigatórios
- ✓ Tipos de dados (numéricos, alfanuméricos)
- ✓ Tamanhos de campos
- ✓ Formatos de datas (DDMMAAAA)
- ✓ Formatos de CPF/CNPJ
- ✓ Coerência entre modalidade e dados (ex: PIX requer chave)
- ✓ Códigos ISPB quando exigidos pelo banco
- ✓ Caracteres especiais (remove acentuação automaticamente)
- ✓ Linhas com exatamente 240 caracteres

## 🐛 Resolução de Problemas

### Erro: "Campo obrigatório ausente no JSON"

Verifique se preencheu todos os campos obrigatórios no arquivo de configuração.
Consulte `config_cnab240_schema.json` para ver todos os campos.

### Erro: "Favorecido não encontrado na base de pessoas"

Certifique-se que o nome do beneficiado no contas a pagar existe no CSV de pessoas.
O sistema faz busca por nome (exata ou parcial).

### Erro: "Configuração de banco não encontrada"

Verifique se:
1. O arquivo JSON do banco existe no diretório `configs_bancos/`
2. O nome do arquivo está correto em `correlacao_banco.csv`
3. O centro de custo está mapeado corretamente

### Aviso: "Código PIX truncado"

O código EMV do QR Code PIX tem limite de 85 caracteres no CNAB 240.
Se seu código for maior, será truncado (pode causar problemas).

## 📚 Referências

- [FEBRABAN - Padrão CNAB 240](https://portal.febraban.org.br/pagina/3053/33/pt-br/layout-240)
- Manual CNAB 240 do seu banco específico
- [Documentação PIX - Banco Central](https://www.bcb.gov.br/estabilidadefinanceira/pix)

## 📝 Logs e Depuração

O sistema gera logs em múltiplos níveis:

1. **Console** - Mensagens principais e erros
2. **Log JSON** - Log detalhado de cada operação com timestamps
3. **Relatório JSON** - Validação completa com totalizadores

Para debug adicional, ative no início do script:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## ⚠️ Importante

- **Sempre teste** os arquivos gerados em ambiente homologação do banco antes de usar em produção
- **Verifique manualmente** os valores e totalizadores no relatório de validação
- **Mantenha backup** dos arquivos de configuração
- **Consulte o manual** CNAB 240 específico do seu banco para códigos e particularidades
- **Não presuma valores** - todos os campos devem ser preenchidos conforme documentação

## 📞 Suporte

Este sistema foi desenvolvido seguindo rigorosamente as especificações FEBRABAN CNAB 240.
Consulte sempre o manual do seu banco para códigos e particularidades específicas.

---

**Desenvolvido para processamento de contas a pagar com máxima precisão e conformidade ao padrão CNAB 240.**
