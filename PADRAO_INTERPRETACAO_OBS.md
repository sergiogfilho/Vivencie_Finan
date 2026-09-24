# PADRÃO DE INTERPRETAÇÃO E VALIDAÇÃO NO CAMPO OBS DO CONTAS A PAGAR

## 1. Definição do Papel
O modelo atuará como um **Motor de Interpretação e Validação de Pagamentos**. Sua função é analisar texto não estruturado proveniente do campo `obs`, normalizar os dados, identificar o método de pagamento e validar matematicamente as chaves e códigos extraídos.

## 2. Pré-Processamento (Normalização)
Antes de qualquer extração, aplique rigorosamente as seguintes transformações no texto de entrada:
1.  **Caixa Baixa:** Converter todo o texto para minúsculas (`lowercase`).
2.  **Remoção de Acentos:** Normalizar caracteres para ASCII simples (ex: `á` → `a`, `ç` → `c`).
3.  **Limpeza:** Remover espaços extras no início e fim.

---

## 3. Identificação e Extração por Tipo de Pagamento

A identificação deve ser feita buscando **palavras-chave (triggers)** específicas. Siga a ordem de prioridade abaixo para evitar conflitos.

### 3.1. Pagamento via BOLETO
* **Trigger:** O termo `"bto:"` deve estar presente.
* **Extração:** Capturar a sequência numérica imediatamente após o trigger. Remover pontos, espaços e traços.
* **Validação:** Aplicar regras da **Seção 4** (Validação de Linha Digitável).

### 3.2. Pagamento via TED
* **Trigger:** O termo `"ted"` deve estar presente.
* **Campos Obrigatórios:** Devem ser extraídos os valores após os seguintes marcadores:
    * `"bco:"` → Código do Banco (3 dígitos).
    * `"ag:"` → Agência.
    * `"cc:"` → Conta Corrente.
* **Regra de Agência (ag):**
    * Se 4 dígitos: Agência sem dígito.
    * Se 5 dígitos: Os primeiros 4 são a agência, o 5º é o dígito verificador.

### 3.3. Pagamento via TRANSFERÊNCIA (Mesmo Banco)
* **Trigger:** O termo `"transf"` deve estar presente.
* **Campos Obrigatórios:**
    * `"ag:"` → Agência (aplicar mesma regra da TED).
    * `"cc:"` → Conta Corrente.
* **Restrição:** Este método não exige o código do banco (`bco:`), pois assume-se intra-bancário.

### 3.4. Pagamento via PIX
* **Trigger:** O termo `"pix"` deve estar presente.
* **Extração:** Capturar o texto após o trigger e classificar o tipo de chave:
    * **CPF:** 11 dígitos numéricos.
    * **CNPJ:** 14 dígitos numéricos.
    * **Telefone:** Formato `+55` seguido de DDD e número (10 ou 11 dígitos).
    * **E-mail:** Padrão regex de e-mail.
    * **Chave Aleatória (EVP):** Formato UUID (ex: `8 chars - 4 chars - 4 chars - 4 chars - 12 chars`).
    * **QR Code (Copia e Cola):** String longa iniciando obrigatoriamente com `000201`.

**⚠️ REGRA ESPECIAL - CPF/CNPJ EXPLÍCITO:**

Quando a obs contiver os termos `cpf:` ou `cnpj:` seguidos de um documento, esse documento tem **prioridade absoluta** para identificação do beneficiário, sobrepondo:
1. CPF/CNPJ extraído da chave PIX (quando a chave for CPF/CNPJ)
2. CPF/CNPJ do cadastro de pessoas

**Padrões aceitos:**
- `cpf: 123.456.789-01` ou `cpf:12345678901`
- `cnpj: 12.345.678/0001-90` ou `cnpj:12345678000190`

**Uso prático:** Permite identificar corretamente o beneficiário quando a chave PIX não é CPF/CNPJ (ex: telefone, email, UUID), mas é necessário especificar o documento do favorecido para o CNAB.

**Exemplo:**
```
OBS: Cliente quer receber via PIX. Chave: 85999887766 CPF: 123.456.789-01
→ Chave PIX: 85999887766 (TELEFONE)
→ CPF do Favorecido: 12345678901 (extraído da obs, não do cadastro)
```

---

## 4. Regras de Validação: Linha Digitável (Boletos)

A validação matemática é **obrigatória**. O script deve contar a quantidade de dígitos numéricos limpos e aplicar a lógica correspondente.

### 4.1. TIPO A: Boleto Bancário (Título de Cobrança)
* **Tamanho:** 47 dígitos.
* **Algoritmo:** Módulo 10.
* **Estrutura de Verificação:** A linha possui 3 Campos com Dígitos Verificadores (DV) explícitos que devem ser recalculados.

| Campo | Posições dos Dados (Índice Base 0) | Posição do DV (Índice Base 0) |
| :--- | :--- | :--- |
| **Campo 1** | Dígitos 00 a 08 (9 números) | Dígito 09 |
| **Campo 2** | Dígitos 10 a 19 (10 números) | Dígito 20 |
| **Campo 3** | Dígitos 21 a 30 (10 números) | Dígito 31 |

> *Nota: Os dígitos restantes (32 a 46) compõem o código de barras mas não possuem validação interna na linha digitável.*

### 4.2. TIPO B: Boleto de Arrecadação (Convênio/Tributos)
* **Tamanho:** 48 dígitos.
* **Identificador:** Começa obrigatoriamente com `8`.
* **Seleção de Algoritmo:** Verificar o dígito na **posição 2** (3º dígito da linha):
    * Se dígito for **6** ou **7**: Usar **Módulo 10**.
    * Se dígito for **8** ou **9**: Usar **Módulo 11**.
* **Estrutura de Verificação:** A linha é dividida em 4 blocos independentes.

| Bloco | Posições dos Dados (Índice Base 0) | Posição do DV (Índice Base 0) |
| :--- | :--- | :--- |
| **Bloco 1** | Dígitos 00 a 10 (11 números) | Dígito 11 |
| **Bloco 2** | Dígitos 12 a 22 (11 números) | Dígito 23 |
| **Bloco 3** | Dígitos 24 a 34 (11 números) | Dígito 35 |
| **Bloco 4** | Dígitos 36 a 46 (11 números) | Dígito 47 |

---

## 5. Algoritmos Matemáticos

Use estas definições para realizar os cálculos de validação acima.

### Algoritmo Módulo 10
1.  Percorra o número da **direita para a esquerda**.
2.  Multiplique os dígitos alternadamente por **2** e **1**.
3.  Se o resultado da multiplicação for ≥ 10, some os dígitos do resultado (ex: 16 → 1+6=7).
4.  Some todos os resultados.
5.  Obtenha o resto da divisão da soma por 10.
6.  **DV Calculado:** Se o resto for 0, DV é 0. Caso contrário, DV é 10 - Resto.

### Algoritmo Módulo 11 (Padrão Geral)
1.  Percorra o número da **direita para a esquerda**.
2.  Multiplique os dígitos por pesos de **2 a 9**. Reinicie o peso em 2 se passar de 9.
3.  Some os resultados das multiplicações.
4.  Obtenha o resto da divisão da soma por 11.
5.  **DV Calculado:**
    * Se resto for 0 ou 1: DV é 0.
    * Se resto > 1: DV é 11 - Resto.
    * *Exceção:* Se o resultado calculado for 10, o DV deve ser considerado 0.

---

## 6. Fluxo de Processamento Recomendado

```
┌─────────────────────────────────────────────┐
│ 1. Receber texto do campo OBS               │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│ 2. Pré-processamento (normalização)         │
│    - Lowercase                               │
│    - Remover acentos                         │
│    - Limpar espaços                          │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│ 3. Identificar tipo (ordem de prioridade):  │
│    a) BOLETO (bto:)                          │
│    b) TED (ted)                              │
│    c) TRANSFERÊNCIA (transf)                 │
│    d) PIX (pix)                              │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│ 4. Extrair dados conforme tipo identificado │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│ 5. Validar matematicamente (se aplicável)   │
│    - Boleto: Módulo 10/11                    │
│    - PIX: CPF/CNPJ/Telefone/Email/UUID       │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│ 6. Retornar estrutura normalizada ou erro   │
└─────────────────────────────────────────────┘
```

---

## 7. Exemplo de Estrutura de Saída

```python
{
    "tipo_pagamento": "PIX",  # ou "BOLETO", "TED", "TRANSFERENCIA"
    "validacao": {
        "valido": True,
        "mensagem": "Chave PIX CPF válida"
    },
    "dados": {
        "chave_pix": "12345678901",
        "tipo_chave": "CPF"
    }
}
```

---

## 8. Casos de Erro Comuns

| Situação | Tratamento |
|----------|------------|
| Trigger não encontrado | Retornar erro "Tipo de pagamento não identificado" |
| Linha digitável inválida | Retornar erro "Validação matemática falhou" com detalhes do campo |
| Campos obrigatórios ausentes | Retornar erro listando campos faltantes |
| CPF/CNPJ inválido em chave PIX | Validar matematicamente e rejeitar se inválido |
| Telefone PIX sem DDI | Aceitar formato local (85...)  e normalizar para +5585... |

---

**Data de Criação:** 27/11/2025  
**Versão:** 1.0
