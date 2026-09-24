# RESUMO DOS ELEMENTOS DESCOBERTOS - ACADE ONE

## Baseado na análise do arquivo HTML fornecido

### 1. Login (Página inicial)
- **Campo Usuário**: `id="login"`
- **Campo Senha**: `id="senha"`
- **Botão Acessar**: `button.btn.btn-primary`

### 2. Menu Sidebar (Após login)
- **Menu Relatório**: XPath = `//span[text()='Relatório']/..`
  - Expandir menu clicando no elemento pai do span
  
- **Submenu Contas à Pagar**: `a[href*='rlContaPagar']`
  - Link que contém "rlContaPagar" no href

### 3. Formulário de Relatório
#### Campos obrigatórios:

**Tipo de Relatório** (select2):
- ID: `cdTipoRelatorio`
- Valor para "Contas à Pagar": `A`
- JavaScript: `$('#cdTipoRelatorio').val('A').trigger('change');`

**Empreendimento** (select2 múltiplo):
- Name: `cdEmpreendimento[]`
- Valor para "Todos": ` '' ` (string vazia)
- JavaScript: `$('select[name="cdEmpreendimento[]"]').val('').trigger('change');`

**Centro de Custo** (select2 múltiplo):
- Name: `cdCentroCusto[]`
- Valor para "Todos": `''` (string vazia)
- JavaScript: `$('select[name="cdCentroCusto[]"]').val('').trigger('change');`

**Data Inicial**:
- ID: `dtInicial`
- Tipo: input text com classe `dtpicker maskData`
- Formato: DD/MM/YYYY

**Data Final**:
- ID: `dtFinal`
- Tipo: input text com classe `dtpicker maskData`
- Formato: DD/MM/YYYY

**Tipo de Saída** (radio buttons):
- PDF: `id="tipoRelatorio"` value="pdf"
- Excel: `id="tipoRelatorioExcel"` value="xls"
- **Visualizar**: `id="tipoRelatorioVisualizar"` value="vis" ✅ (USAR ESTE)
- JavaScript: `document.getElementById('tipoRelatorioVisualizar').click();`

**Botão Gerar**:
- ID: `btnSalvar`
- Texto: "Gerar"
- JavaScript: `document.getElementById('btnSalvar').click();`

### 4. Tabela de Resultados
- Seletor genérico: `table`, `.table`
- Headers em: `thead th` ou primeira linha `tr td`
- Dados em: `tr td`

## Observações Importantes

1. **Select2**: Os campos de seleção usam Select2, então precisa usar jQuery/JavaScript para alterar valores
2. **Datepicker**: Pode abrir um calendário e bloquear outros elementos - fechar antes de clicar em Gerar
3. **Ordem de preenchimento**:
   - Tipo de Relatório
   - Empreendimento  
   - Centro de Custo
   - Datas
   - Tipo de Saída (Visualizar)
   - Botão Gerar

## Comandos JavaScript Úteis

```javascript
// Configurar formulário completo
$('#cdTipoRelatorio').val('A').trigger('change');
$('select[name="cdEmpreendimento[]"]').val('').trigger('change');
$('select[name="cdCentroCusto[]"]').val('').trigger('change');
document.getElementById('tipoRelatorioVisualizar').click();

// Fechar calendários
$('.datepicker').hide();
$('.datepicker-dropdown').hide();
document.activeElement.blur();

// Clicar no botão Gerar
document.getElementById('btnSalvar').click();
```

## URL do Relatório
`https://martins.acadeone.com.br/acade/finan/rlContaPagar/gerar`