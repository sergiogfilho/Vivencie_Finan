# 🎯 BAIXA AUTOMÁTICA DE CONTAS PAGAS - ACADE ONE

## 📋 Descrição

Este módulo automatiza o processo de baixa de títulos pagos no sistema ACADE, processando arquivos de retorno bancário no formato CNAB 240 (Sicoob).

## 🚀 Funcionalidades

✅ **Parser CNAB 240 Retorno**
- Lê arquivos .RET do Sicoob
- Identifica pagamentos confirmados (ocorrência `00`)
- Extrai dados necessários para baixa (documento, datas, valores, conta)
- Gera relatório de pagamentos não confirmados

✅ **Baixa Automática no ACADE**
- Login automático no sistema
- Navegação para Contas à Pagar
- Busca avançada por documento e vencimento
- Seleção automática da parcela
- Preenchimento do modal de pagamento
- Confirmação da baixa

✅ **Relatórios Detalhados**
- Arquivo JSON com pagamentos não confirmados
- Relatório completo de baixas (sucesso/erro)
- Logs detalhados de todo o processo

## 📦 Estrutura de Arquivos

```
src/
├── automatizador_final.py         # Classe principal (modificada)
├── baixar_contas_pagas.py         # Script CLI para baixa automática
└── ...

retorno/                            # Diretório de arquivos de retorno
├── Retorno-*.RET                  # Arquivos de retorno CNAB 240
├── nao_processados_*.json         # Pagamentos não confirmados
└── relatorio_baixas_*.json        # Relatório de processamento
```

## 🔧 Instalação

### Pré-requisitos

```bash
pip install selenium python-dotenv pandas
```

### Configuração

1. **Arquivo .env**: Configure as credenciais do ACADE

```bash
ACADE_USUARIO=seu_usuario
ACADE_SENHA=sua_senha
ACADE_BASE_URL=https://martins.acadeone.com.br
```

2. **Chrome/Chromium**: O Selenium Manager baixará automaticamente o ChromeDriver necessário

## 💻 Como Usar

### Método 1: Script Dedicado (Recomendado)

```bash
# Processar arquivos em ./retorno (modo visível - para debug)
python src/baixar_contas_pagas.py

# Processar em modo invisível (produção)
python src/baixar_contas_pagas.py --headless

# Especificar diretório customizado
python src/baixar_contas_pagas.py --diretorio_retorno /caminho/para/retornos --headless
```

### Método 2: Integração no Script Principal

```bash
# Adicionar parâmetro --baixar_contas_pagas ao automatizador_final.py
python src/automatizador_final.py --baixar_contas_pagas [--headless]
```

## 📊 Fluxo de Processamento

```
1. LEITURA DOS ARQUIVOS .RET
   ├─ Busca todos os arquivos no diretório
   ├─ Parse de cada arquivo CNAB 240
   └─ Filtra apenas ocorrência '00' (Crédito/Débito Efetivado)

2. IDENTIFICAÇÃO DOS DADOS
   Para cada pagamento confirmado:
   ├─ Seu Número (posição 74-93 do Segmento A)
   ├─ Data de Pagamento (posição 94-101)
   ├─ Data Real de Efetivação (posição 155-162)
   ├─ Agência e Conta (Header do Lote)
   └─ Nome do Favorecido

3. NAVEGAÇÃO NO ACADE
   ├─ Login automático
   ├─ Acesso: Financeiro > Contas à Pagar
   └─ Para cada título:
       ├─ Busca Avançada (Doc + Vencimento)
       ├─ Clicar em PAGAR
       ├─ Selecionar parcela pelo vencimento
       └─ Preencher modal:
           ├─ Conta Movimento (agência + conta)
           ├─ Forma: "Débito em Conta"
           ├─ Datas de Pagamento e Débito
           └─ Confirmar (Salvar)

4. RELATÓRIOS GERADOS
   ├─ nao_processados_YYYYMMDD_HHMMSS.json
   └─ relatorio_baixas_YYYYMMDD_HHMMSS.json
```

## 📄 Formato dos Relatórios

### 1. Pagamentos Não Confirmados (`nao_processados_*.json`)

```json
[
  {
    "seu_numero": "1234",
    "nome_favorecido": "FULANO DE TAL",
    "data_pagamento": "19122025",
    "valor_pagamento": "0000000000015125",
    "ocorrencias": ["BD", "PD"],
    "motivo": "Ocorrências: BD, PD",
    "agencia": "3357",
    "conta": "0000000055263"
  }
]
```

### 2. Relatório de Baixas (`relatorio_baixas_*.json`)

```json
{
  "data_processamento": "2025-12-20T10:30:00",
  "total_arquivos": 1,
  "total_confirmados": 10,
  "total_nao_confirmados": 2,
  "total_processados": 10,
  "total_sucesso": 8,
  "total_erro": 2,
  "detalhes": [
    {
      "sucesso": true,
      "seu_numero": "1234",
      "nome": "FULANO DE TAL",
      "data_pagamento": "19122025"
    },
    {
      "sucesso": false,
      "seu_numero": "5678",
      "nome": "CICLANO",
      "motivo": "Título não encontrado na busca"
    }
  ]
}
```

## 🔍 Códigos de Ocorrência CNAB (G059)

Apenas pagamentos com ocorrência **`00`** são processados:

| Código | Descrição | Ação |
|--------|-----------|------|
| `00` | Crédito ou Débito Efetivado | ✅ Processar baixa |
| `BD` | Inclusão Efetuada com Sucesso | ⚠️ Aguardar processamento |
| `PD` | Transação Pendente de Assinatura | ⚠️ Aguardar autorização |
| `BF` | Transação Rejeitada | ❌ Não processar |
| `AJ` | Tipo de Movimento Inválido | ❌ Erro CNAB |

## ⚙️ Configurações de Baixa

### Mapeamento de Campos

| Campo CNAB | Campo ACADE | Observação |
|------------|-------------|------------|
| Seu Número (pos 74-93) | Doc. (busca avançada) | Identificador único |
| Data Pagamento (pos 94-101) | Vencto (busca) | Formato DDMMAAAA → DD/MM/YYYY |
| Agência + Conta (Header Lote) | Conta Movimento | Formato: "AAAA CCCCC" |
| Data Real (pos 155-162) | Data Pagamento e Data Débito | Data efetiva |
| - | Forma de Pagamento | Fixo: "Débito em Conta" |

## 🛡️ Tratamento de Erros

O sistema registra e continua o processamento mesmo quando encontra erros:

- ❌ **Título não encontrado**: Registra no relatório e continua
- ❌ **Parcela não encontrada**: Registra motivo e continua
- ❌ **Erro ao preencher modal**: Captura exceção e continua
- ⚠️ **Timeout**: Aguarda elementos com timeout configurável

## 📝 Logs

O sistema gera logs detalhados em:
- **Console**: Informações principais e progresso
- **automatizador_final.log**: Log completo de debug

Níveis de log:
- 🔍 DEBUG: Detalhes de navegação e interações
- ℹ️ INFO: Progresso e sucessos
- ⚠️ WARNING: Pagamentos não confirmados
- ❌ ERROR: Erros de processamento

## 🚨 Resolução de Problemas

### Problema: "Nenhum arquivo .RET encontrado"
**Solução**: Verifique se os arquivos estão no diretório correto com extensão `.RET`

### Problema: "Título não encontrado na busca"
**Solução**: 
- Verifique se o "Seu Número" está correto no CNAB
- Confirme se o título existe no ACADE
- Verifique se a data de vencimento corresponde

### Problema: "Erro ao selecionar Conta Movimento"
**Solução**:
- Verifique se a conta existe no ACADE
- Formato esperado: "3357 55263" (agência + espaço + conta sem zeros à esquerda)

### Problema: "Timeout ao aguardar elementos"
**Solução**: Aumentar o timeout no construtor da classe:
```python
automatizador = AutomatizadorAcadeOneFINAL(headless=True, timeout=90)
```

## 🔐 Segurança

- ✅ Credenciais armazenadas em `.env` (não versionar!)
- ✅ Navegação com contexto de sessão
- ✅ Fechamento automático do navegador após processamento
- ✅ Logs não contêm senhas

## 📞 Suporte

Para problemas ou dúvidas:
1. Verifique os logs em `automatizador_final.log`
2. Consulte o relatório de baixas JSON gerado
3. Execute em modo visível (sem `--headless`) para debug

## 📅 Histórico de Versões

### v2.0.0 (20/12/2025)
- ✨ Nova funcionalidade: Baixa automática de contas pagas
- ✨ Parser CNAB 240 retorno completo
- ✨ Relatórios JSON detalhados
- ✨ Suporte a múltiplos arquivos .RET
- ✨ Tratamento robusto de erros

### v1.0.0
- Geração de relatórios de contas a pagar/pagas

---

**Desenvolvido para**: Sistema ACADE ONE  
**Padrão**: CNAB 240 - Sicoob  
**Última atualização**: 20 de dezembro de 2025
