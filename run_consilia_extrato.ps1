<#
.SYNOPSIS
    Script wrapper para executar a Conciliação de Extrato (Consilia).
.DESCRIPTION
    Executa o script Python `src/consilia_extrato.py` carregando o ambiente virtual
    e garantindo a codificação correta para o Windows.
#>

$Host.UI.RawUI.WindowTitle = "Conciliação de Extrato - Vivencie Finan"

# ==============================================================================
# CONFIGURAÇÃO DE ENCODING (CRÍTICO PARA WINDOWS)
# ==============================================================================
# Garante que o PowerShell e o Python falem a mesma língua (UTF-8)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Env:PYTHONUTF8 = "1"

# Limpar tela e mostrar cabeçalho
Clear-Host
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "      CONCILIAÇÃO EXTRATO - VIVENCIE FINAN   " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# Mudar para o diretório do script
if ($PSScriptRoot) {
    Set-Location $PSScriptRoot
}

# ==============================================================================
# DETECÇÃO DO PYTHON (VENV)
# ==============================================================================
$possibleVenvs = @(
    Join-Path $PSScriptRoot "venv\Scripts\python.exe"
    Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
)

$pythonCmd = $null

foreach ($path in $possibleVenvs) {
    if (Test-Path $path) {
        $pythonCmd = $path
        Write-Host "Ambiente virtual detectado: $pythonCmd" -ForegroundColor Gray
        break
    }
}

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
        Write-Host "ERRO: Python não encontrado." -ForegroundColor Red
        Read-Host "Pressione ENTER para sair..."
        exit 1
    }
}

# ==============================================================================
# EXECUÇÃO
# ==============================================================================
$scriptPath = Join-Path $PSScriptRoot "src\consilia_extrato.py"

if (-not (Test-Path $scriptPath)) {
    Write-Host "ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
    Read-Host "Pressione ENTER para sair..."
    exit 1
}

Write-Host "Iniciando processamento..." -ForegroundColor Yellow
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Script:   src\consilia_extrato.py" -ForegroundColor Gray
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

try {
    # Executa o script Python
    & $pythonCmd $scriptPath
    
    $exitCode = $LASTEXITCODE
    Write-Host ""
    if ($exitCode -eq 0) {
        Write-Host "Execução finalizada com sucesso." -ForegroundColor Cyan
    }
    else {
        Write-Host "Script finalizado com código de erro: $exitCode" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "Erro ao executar o script Python: $_" -ForegroundColor Red
}

Write-Host ""
Read-Host "Pressione ENTER para fechar..."