<#
.SYNOPSIS
    Script wrapper para executar a Baixa de Contas Pagas (ACADE ONE).
.DESCRIPTION
    Executa o script Python `src/baixar_contas_pagas.py` no modo headless,
    com 3 workers paralelos, lendo do diretório .\retorno.
#>

$Host.UI.RawUI.WindowTitle = "Baixa de Contas - Vivencie Finan"

# ==============================================================================
# CONFIGURAÇÃO DE ENCODING (CRÍTICO PARA WINDOWS)
# ==============================================================================
# Garante que o PowerShell e o Python falem a mesma língua (UTF-8)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Env:PYTHONUTF8 = "1"

# Limpar tela e mostrar cabeçalho
Clear-Host
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "      BAIXA DE CONTAS - VIVENCIE FINAN       " -ForegroundColor Cyan
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
# CONFIGURAÇÃO DOS PARÂMETROS
# ==============================================================================
# Diretório de retorno (.\retorno)
$retornoDir = Join-Path $PSScriptRoot "retorno"
if (-not (Test-Path $retornoDir)) {
    Write-Host "Criando diretório de retorno: $retornoDir" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $retornoDir -Force | Out-Null
}

# Caminho do script Python
$scriptPath = Join-Path $PSScriptRoot "src\baixar_contas_pagas.py"

if (-not (Test-Path $scriptPath)) {
    Write-Host "ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
    Read-Host "Pressione ENTER para sair..."
    exit 1
}

# ==============================================================================
# EXECUÇÃO
# ==============================================================================
Write-Host "Iniciando processo de baixa..." -ForegroundColor Yellow
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Script:   src\baixar_contas_pagas.py" -ForegroundColor Gray
Write-Host "  Modo:     Headless (Invisível)" -ForegroundColor Gray
Write-Host "  Workers:  3" -ForegroundColor Gray
Write-Host "  Retorno:  .\retorno" -ForegroundColor Gray
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

try {
    # Chama o script python com os argumentos solicitados
    # --no_hide NÃO é passado, logo roda em headless (default do script é headless=True se flag omitida? 
    # Espere, o script diz: parser.add_argument('--no_hide', action='store_true', help='Executar com browser visível')
    # Logo, omitir --no_hide significa headless=True (headless = not args.no_hide). Correto.
    
    & $pythonCmd $scriptPath --diretorio_retorno "$retornoDir" --workers 3
    
    Write-Host ""
    Write-Host "Execução finalizada." -ForegroundColor Cyan
}
catch {
    Write-Host "Erro ao executar o script Python: $_" -ForegroundColor Red
}

Write-Host ""
Read-Host "Pressione ENTER para fechar..."
