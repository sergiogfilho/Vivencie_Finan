<#
.SYNOPSIS
    Script wrapper para executar o Capturador de Pessoas (Física e Jurídica) com inputs facilitados.
.DESCRIPTION
    Executa o script Python `src/capturador_pessoas.py` preenchendo automaticamente:
    - Modo visível: N (Headless/Oculto)
    - Execução paralela: S (Sim)
#>

$Host.UI.RawUI.WindowTitle = "Capturador Pessoas - Vivencie Finan"

# Limpar tela e mostrar cabeçalho
Clear-Host
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "      CAPTURADOR PESSOAS - ACADE ONE         " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# Mudar para o diretório do script para garantir que logs/arquivos sejam salvos no lugar certo
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

# Construir a lista de inputs que será enviada para o Python
# Sequência esperada pelo capturador_pessoas.py:
# 1. Modo visível? (s/n) -> 'n' (Headless)
# 2. Paralelo? (s/n)     -> 's' (Sim)
$inputList = @(
    "n",
    "s"
)

Write-Host "Iniciando capturador..." -ForegroundColor Yellow
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host "Configuração enviada:" -ForegroundColor Gray
Write-Host "  - Modo:       Headless/Oculto" -ForegroundColor Gray
Write-Host "  - Paralelo:   SIM (Física e Jurídica simultaneamente)" -ForegroundColor Gray
Write-Host "---------------------------------------------" -ForegroundColor DarkGray

# Executa o comando
# script alvo: src/capturador_pessoas.py
try {
    $scriptPath = Join-Path $PSScriptRoot "src\capturador_pessoas.py"
    if (-not (Test-Path $scriptPath)) {
        throw "O arquivo do script não foi encontrado em: $scriptPath"
    }
    
    # Executa enviando os inputs via pipe
    $inputList | & $pythonCmd $scriptPath
}
catch {
    Write-Host "Erro ao executar o script Python: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "Script finalizado." -ForegroundColor Cyan
# Pausa final para o usuário ver o resultado
Read-Host "Pressione ENTER para fechar..."
