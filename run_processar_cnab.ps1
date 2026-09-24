<#
.SYNOPSIS
    Script wrapper para executar o Processador de Contas a Pagar (CNAB 240).
.DESCRIPTION
    Executa o script Python `src/processar_contas_pagar_cnab.py` passando os arquivos CSV necessários
    e redirecionando a saída para `remessas\resultado.txt`.
#>

$Host.UI.RawUI.WindowTitle = "Processador CNAB - Vivencie Finan"

# Limpar tela e mostrar cabeçalho
Clear-Host
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "      PROCESSADOR CNAB - VIVENCIE FINAN      " -ForegroundColor Cyan
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

# Caminhos dos arquivos de entrada e saída
# Baseado na estrutura solicitada:
# Inputs: arquivos_auxiliares/
# Output: remessas/resultado.txt

$inputFileContas = Join-Path $PSScriptRoot "arquivos_auxiliares\relatorio_contas_pagar.csv"
$inputFileBancos = Join-Path $PSScriptRoot "arquivos_auxiliares\bancos.csv"
$inputFilePessoas = Join-Path $PSScriptRoot "arquivos_auxiliares\pessoas_cadastradas.csv"

$outputFile = Join-Path $PSScriptRoot "remessas\resultado.txt"

# Verifica existência dos arquivos de entrada
$missingFiles = @()
if (-not (Test-Path $inputFileContas)) { $missingFiles += $inputFileContas }
if (-not (Test-Path $inputFileBancos)) { $missingFiles += $inputFileBancos }
if (-not (Test-Path $inputFilePessoas)) { $missingFiles += $inputFilePessoas }

if ($missingFiles.Count -gt 0) {
    Write-Host "ERRO: Os seguintes arquivos de entrada não foram encontrados:" -ForegroundColor Red
    foreach ($file in $missingFiles) {
        Write-Host "  - $file" -ForegroundColor Red
    }
    Read-Host "Pressione ENTER para sair..."
    exit 1
}

# Garante que o diretório de saída existe
$outputDir = Split-Path $outputFile
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

Write-Host "Iniciando processamento CNAB..." -ForegroundColor Yellow
Write-Host "---------------------------------------------" -ForegroundColor DarkGray
Write-Host "Entradas:" -ForegroundColor Gray
Write-Host "  - Contas:  arquivos_auxiliares\relatorio_contas_pagar.csv" -ForegroundColor Gray
Write-Host "  - Bancos:  arquivos_auxiliares\bancos.csv" -ForegroundColor Gray
Write-Host "  - Pessoas: arquivos_auxiliares\pessoas_cadastradas.csv" -ForegroundColor Gray
Write-Host "Saída:" -ForegroundColor Gray
Write-Host "  - Log:     remessas\resultado.txt" -ForegroundColor Gray
Write-Host "---------------------------------------------" -ForegroundColor DarkGray

# Executa o comando
# script alvo: src/processar_contas_pagar_cnab.py
# Configurar encoding do Console e do Python para UTF-8 (Fix Definitivo de Unicode)
# Isso garante que o PowerShell e o Python falem a mesma língua (UTF-8) e evita erros com emojis/símbolos.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Env:PYTHONUTF8 = "1"

# Executa o comando
# script alvo: src/processar_contas_pagar_cnab.py
try {
    $scriptPath = Join-Path $PSScriptRoot "src\processar_contas_pagar_cnab.py"
    if (-not (Test-Path $scriptPath)) {
        throw "O arquivo do script não foi encontrado em: $scriptPath"
    }
    
    # Executa redirecionando stdout para o arquivo
    # Usando cmd.exe /c para garantir o comportamento de redirecionamento '>' nativo se preferir,
    # Mas em PowerShell ' | Out-File' é mais idiomático e controla encoding.
    # Vou usar call operator '&' e pipe para Out-File para garantir encoding UTF8 limpo.
    
    & $pythonCmd $scriptPath $inputFileContas $inputFileBancos $inputFilePessoas | Out-File -FilePath $outputFile -Encoding utf8
    
    Write-Host "Processamento finalizado." -ForegroundColor Cyan
    Write-Host "Verifique o arquivo de saída: $outputFile" -ForegroundColor Cyan
}
catch {
    Write-Host "Erro ao executar o script Python: $_" -ForegroundColor Red
}

Write-Host ""
# Pausa final para o usuário ver o resultado
Read-Host "Pressione ENTER para fechar..."
