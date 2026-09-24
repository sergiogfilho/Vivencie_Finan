<#
.SYNOPSIS
    Script Orquestrador - Menu principal para todos os scripts Vivencie Finan.
.DESCRIPTION
    Apresenta um menu interativo para executar:
    1. Automatizador Final (captura contas a pagar)
    2. Baixa de Contas Pagas
    3. Capturador de Pessoas
    4. Conciliação de Extrato
    5. Processador CNAB 240
    0. Sair
#>

$Host.UI.RawUI.WindowTitle = "Orquestrador - Vivencie Finan"

# ==============================================================================
# CONFIGURAÇÃO GLOBAL DE ENCODING (CRÍTICO PARA WINDOWS)
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Env:PYTHONUTF8 = "1"

# ==============================================================================
# VARIÁVEIS GLOBAIS
# ==============================================================================
$global:pythonCmd = $null
$global:baseDir = $PSScriptRoot

# ==============================================================================
# FUNÇÃO: Detectar Python
# ==============================================================================
function Find-Python {
    if ($global:pythonCmd) { return $true }
    
    $possibleVenvs = @(
        Join-Path $global:baseDir "venv\Scripts\python.exe"
        Join-Path $global:baseDir ".venv\Scripts\python.exe"
    )
    
    foreach ($path in $possibleVenvs) {
        if (Test-Path $path) {
            $global:pythonCmd = $path
            return $true
        }
    }
    
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $global:pythonCmd = "python"
        return $true
    }
    elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
        $global:pythonCmd = "python3"
        return $true
    }
    
    return $false
}

# ==============================================================================
# FUNÇÃO: Exibir Cabeçalho
# ==============================================================================
function Show-Header {
    param([string]$Titulo = "ORQUESTRADOR")
    
    Clear-Host
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║                                                           ║" -ForegroundColor Cyan
    Write-Host "║            VIVENCIE FINAN - $($Titulo.PadRight(24))       ║" -ForegroundColor Cyan
    Write-Host "║                                                           ║" -ForegroundColor Cyan
    Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

# ==============================================================================
# FUNÇÃO: Menu Principal
# ==============================================================================
function Show-Menu {
    Show-Header "MENU PRINCIPAL"
    
    Write-Host "  Selecione uma opção:" -ForegroundColor White
    Write-Host ""
    Write-Host "  [1] Automatizador Final       - Captura contas a pagar do sistema" -ForegroundColor Yellow
    Write-Host "  [2] Baixar Contas Pagas       - Processa baixas automáticas" -ForegroundColor Yellow
    Write-Host "  [3] Capturador de Pessoas     - Captura PF e PJ do sistema" -ForegroundColor Yellow
    Write-Host "  [4] Conciliação de Extrato    - Processa arquivos OFX" -ForegroundColor Yellow
    Write-Host "  [5] Processador CNAB 240      - Gera arquivos de remessa" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  [0] Sair" -ForegroundColor Red
    Write-Host ""
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    
    if ($global:pythonCmd) {
        Write-Host "  Python: $global:pythonCmd" -ForegroundColor DarkGray
    }
    
    Write-Host ""
    Write-Host -NoNewline "  Digite sua opção: " -ForegroundColor White
}

# ==============================================================================
# FUNÇÃO: Aguardar tecla para voltar ao menu
# ==============================================================================
function Wait-ForKey {
    Write-Host ""
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Pressione qualquer tecla para voltar ao menu..." -ForegroundColor Cyan
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# ==============================================================================
# SCRIPT 1: Automatizador Final
# ==============================================================================
function Run-Automatizador {
    Show-Header "AUTOMATIZADOR FINAL"
    
    $scriptPath = Join-Path $global:baseDir "src\automatizador_final.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Host "  ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
        Wait-ForKey
        return
    }
    
    # Solicitar Data Inicial
    Write-Host "  Digite a Data Inicial (DD/MM/AAAA) ou ENTER para HOJE: " -NoNewline -ForegroundColor White
    $dataInicial = Read-Host
    if ([string]::IsNullOrWhiteSpace($dataInicial)) {
        $dataInicial = ""
        Write-Host "    -> Usando data ATUAL" -ForegroundColor DarkGray
    }
    
    # Solicitar Data Final
    Write-Host "  Digite a Data Final (DD/MM/AAAA) ou ENTER para HOJE: " -NoNewline -ForegroundColor White
    $dataFinal = Read-Host
    if ([string]::IsNullOrWhiteSpace($dataFinal)) {
        $dataFinal = ""
        Write-Host "    -> Usando data ATUAL" -ForegroundColor DarkGray
    }
    
    Write-Host ""
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Configuração:" -ForegroundColor Gray
    Write-Host "    - Pagamentos: Abertos (Padrão)" -ForegroundColor Gray
    Write-Host "    - Formato:    CSV (Padrão)" -ForegroundColor Gray
    Write-Host "    - Modo:       Headless/Oculto (Padrão)" -ForegroundColor Gray
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Executando..." -ForegroundColor Yellow
    Write-Host ""
    
    # Inputs: Data inicial, Data final, Tipo pagamento, Formato, Modo
    $inputList = @($dataInicial, $dataFinal, "", "", "")
    
    try {
        $inputList | & $global:pythonCmd $scriptPath
    }
    catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    }
    
    Wait-ForKey
}

# ==============================================================================
# SCRIPT 2: Baixar Contas Pagas
# ==============================================================================
function Run-BaixarContas {
    Show-Header "BAIXA DE CONTAS"
    
    $scriptPath = Join-Path $global:baseDir "src\baixar_contas_pagas.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Host "  ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
        Wait-ForKey
        return
    }
    
    # Diretório de retorno
    $retornoDir = Join-Path $global:baseDir "retorno"
    if (-not (Test-Path $retornoDir)) {
        Write-Host "  Criando diretório de retorno..." -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $retornoDir -Force | Out-Null
    }
    
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Configuração:" -ForegroundColor Gray
    Write-Host "    - Modo:     Headless (Invisível)" -ForegroundColor Gray
    Write-Host "    - Workers:  3" -ForegroundColor Gray
    Write-Host "    - Retorno:  .\retorno" -ForegroundColor Gray
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Executando..." -ForegroundColor Yellow
    Write-Host ""
    
    try {
        & $global:pythonCmd $scriptPath --diretorio_retorno "$retornoDir" --workers 3
    }
    catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    }
    
    Wait-ForKey
}

# ==============================================================================
# SCRIPT 3: Capturador de Pessoas
# ==============================================================================
function Run-Capturador {
    Show-Header "CAPTURADOR PESSOAS"
    
    $scriptPath = Join-Path $global:baseDir "src\capturador_pessoas.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Host "  ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
        Wait-ForKey
        return
    }
    
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Configuração:" -ForegroundColor Gray
    Write-Host "    - Modo:     Headless/Oculto" -ForegroundColor Gray
    Write-Host "    - Paralelo: SIM (PF e PJ simultaneamente)" -ForegroundColor Gray
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Executando..." -ForegroundColor Yellow
    Write-Host ""
    
    # Inputs: Modo visível (n), Paralelo (s)
    $inputList = @("n", "s")
    
    try {
        $inputList | & $global:pythonCmd $scriptPath
    }
    catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    }
    
    Wait-ForKey
}

# ==============================================================================
# SCRIPT 4: Conciliação de Extrato
# ==============================================================================
function Run-Conciliacao {
    Show-Header "CONCILIAÇÃO EXTRATO"
    
    $scriptPath = Join-Path $global:baseDir "src\consilia_extrato.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Host "  ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
        Wait-ForKey
        return
    }
    
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Script: src\consilia_extrato.py" -ForegroundColor Gray
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Executando..." -ForegroundColor Yellow
    Write-Host ""
    
    try {
        & $global:pythonCmd $scriptPath
        
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host ""
            Write-Host "  Execução finalizada com sucesso." -ForegroundColor Green
        }
        else {
            Write-Host ""
            Write-Host "  Finalizado com código: $exitCode" -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    }
    
    Wait-ForKey
}

# ==============================================================================
# SCRIPT 5: Processador CNAB 240
# ==============================================================================
function Run-ProcessadorCNAB {
    Show-Header "PROCESSADOR CNAB 240"
    
    $scriptPath = Join-Path $global:baseDir "src\processar_contas_pagar_cnab.py"
    if (-not (Test-Path $scriptPath)) {
        Write-Host "  ERRO: Script não encontrado em: $scriptPath" -ForegroundColor Red
        Wait-ForKey
        return
    }
    
    # Caminhos dos arquivos
    $inputFileContas = Join-Path $global:baseDir "arquivos_auxiliares\relatorio_contas_pagar.csv"
    $inputFileBancos = Join-Path $global:baseDir "arquivos_auxiliares\bancos.csv"
    $inputFilePessoas = Join-Path $global:baseDir "arquivos_auxiliares\pessoas_cadastradas.csv"
    $outputFile = Join-Path $global:baseDir "remessas\resumo.txt"
    
    # Verifica arquivos de entrada
    $missingFiles = @()
    if (-not (Test-Path $inputFileContas)) { $missingFiles += "relatorio_contas_pagar.csv" }
    if (-not (Test-Path $inputFileBancos)) { $missingFiles += "bancos.csv" }
    if (-not (Test-Path $inputFilePessoas)) { $missingFiles += "pessoas_cadastradas.csv" }
    
    if ($missingFiles.Count -gt 0) {
        Write-Host "  ERRO: Arquivos não encontrados em arquivos_auxiliares\:" -ForegroundColor Red
        foreach ($file in $missingFiles) {
            Write-Host "    - $file" -ForegroundColor Red
        }
        Wait-ForKey
        return
    }
    
    # Garante diretório de saída
    $outputDir = Split-Path $outputFile
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    }
    
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Entradas (arquivos_auxiliares\):" -ForegroundColor Gray
    Write-Host "    - relatorio_contas_pagar.csv" -ForegroundColor Gray
    Write-Host "    - bancos.csv" -ForegroundColor Gray
    Write-Host "    - pessoas_cadastradas.csv" -ForegroundColor Gray
    Write-Host "  Saída:" -ForegroundColor Gray
    Write-Host "    - remessas\resumo.txt" -ForegroundColor Gray
    Write-Host "─────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Executando..." -ForegroundColor Yellow
    Write-Host ""
    
    try {
        # Executa e exibe no terminal, também salva em arquivo
        & $global:pythonCmd $scriptPath $inputFileContas $inputFileBancos $inputFilePessoas | Tee-Object -FilePath $outputFile
        
        Write-Host ""
        Write-Host "  Log salvo em: remessas\resumo.txt" -ForegroundColor Green
    }
    catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    }
    
    Wait-ForKey
}

# ==============================================================================
# LOOP PRINCIPAL
# ==============================================================================

# Mudar para o diretório do script
if ($PSScriptRoot) {
    Set-Location $PSScriptRoot
    $global:baseDir = $PSScriptRoot
}

# Detectar Python
if (-not (Find-Python)) {
    Clear-Host
    Write-Host ""
    Write-Host "  ERRO CRÍTICO: Python não encontrado!" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Verifique se o Python está instalado e disponível no PATH," -ForegroundColor Yellow
    Write-Host "  ou se existe um ambiente virtual (venv/.venv) no diretório." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Pressione ENTER para sair..."
    exit 1
}

# Loop do menu
$running = $true
while ($running) {
    Show-Menu
    $choice = Read-Host
    
    switch ($choice) {
        "1" { Run-Automatizador }
        "2" { Run-BaixarContas }
        "3" { Run-Capturador }
        "4" { Run-Conciliacao }
        "5" { Run-ProcessadorCNAB }
        "0" { 
            $running = $false
            Clear-Host
            Write-Host ""
            Write-Host "  Até logo!" -ForegroundColor Cyan
            Write-Host ""
        }
        default {
            Write-Host ""
            Write-Host "  Opção inválida! Tente novamente." -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
}
