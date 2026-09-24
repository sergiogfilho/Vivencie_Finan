<#
.SYNOPSIS
    Script wrapper para executar o Automatizador Final com inputs facilitados.
.DESCRIPTION
    Solicita as datas inicial e final ao usuário (aceitando ENTER para data atual)
    e executa o script Python preenchendo automaticamente as opções padrão:
    - Pagamentos: Abertos
    - Formato: CSV
    - Modo: Headless (Oculto)
#>

$Host.UI.RawUI.WindowTitle = "Automatizador Vivencie Finan"

# Limpar tela e mostrar cabeçalho
Clear-Host
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "      AUTOMATIZADOR VIVENCIE FINAN           " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# 1. Solicitar Data Inicial
Write-Host "Digite a Data Inicial (DD/MM/AAAA) ou pressione ENTER para HOJE:" -NoNewline
$dataInicial = Read-Host
if ([string]::IsNullOrWhiteSpace($dataInicial)) {
    $dataInicial = ""
    Write-Host "   -> Usando data ATUAL para data inicial" -ForegroundColor DarkGray
}

# 2. Solicitar Data Final
Write-Host "Digite a Data Final (DD/MM/AAAA)   ou pressione ENTER para HOJE:" -NoNewline
$dataFinal = Read-Host
if ([string]::IsNullOrWhiteSpace($dataFinal)) {
    $dataFinal = ""
    Write-Host "   -> Usando data ATUAL para data final" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Iniciando automatizador..." -ForegroundColor Yellow
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host "Configuração enviada:" -ForegroundColor Gray
Write-Host "  - Pagamentos: Abertos (Padrão)" -ForegroundColor Gray
Write-Host "  - Formato:    CSV (Padrão)" -ForegroundColor Gray
Write-Host "  - Modo:       Headless/Oculto (Padrão)" -ForegroundColor Gray
Write-Host "---------------------------------------------" -ForegroundColor DarkGray

# Mudar para o diretório do script para garantir que logs/arquivos sejam salvos no lugar certo
# $PSScriptRoot é o diretório onde este script .ps1 está salvo
if ($PSScriptRoot) {
    Set-Location $PSScriptRoot
}

# Tentar localizar o Python dentro do venv ou .venv
$possibleVenvs = @(
    Join-Path $PSScriptRoot "venv\Scripts\python.exe"
    Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
)

$pythonCmd = $null

foreach ($path in $possibleVenvs) {
    if (Test-Path $path) {
        $pythonCmd = $path
        Write-Host "Ambiente virtual detectado. Usando: $pythonCmd" -ForegroundColor Gray
        break
    }
}

# Construir a lista de inputs que será enviada para o Python
# A estrutura corresponde EXATAMENTE aos input() do script python:
# 1. Data inicial
# 2. Data final
# 3. Tipo pagamento (Enter = vazio = Padrão 'A')
# 4. Formato (Enter = vazio = Padrão 'csv')
# 5. Modo (Enter = vazio = Padrão 'n'/headless)
$inputList = @(
    $dataInicial,
    $dataFinal,
    "",
    "",
    ""
)

# Se não achou no venv, tenta no sistema
if (-not $pythonCmd) {
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pythonCmd = "python"
        Write-Host "Usando Python do sistema (PATH)." -ForegroundColor Gray
    }
    elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
        $pythonCmd = "python3"
        Write-Host "Usando Python do sistema (python3)." -ForegroundColor Gray
    }
    else {
        Write-Host "ERRO: Python não encontrado no PATH nem em ambientes virtuais (venv/.venv)." -ForegroundColor Red
        Read-Host "Pressione ENTER para sair..."
        exit 1
    }
}

# Executa o comando
# O parametro -Encoding UTF8 garante que caracteres especiais sejam tratados corretamente se necessário
try {
    $scriptPath = Join-Path $PSScriptRoot "src\automatizador_final.py"
    if (-not (Test-Path $scriptPath)) {
        throw "O arquivo do script não foi encontrado em: $scriptPath"
    }
    
    $inputList | & $pythonCmd $scriptPath
}
catch {
    Write-Host "Erro ao executar o script Python: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "Script finalizado." -ForegroundColor Cyan
# Pausa final para o usuário ver o resultado
Read-Host "Pressione ENTER para fechar..."
