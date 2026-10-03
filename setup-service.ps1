# setup-service.ps1
# Crea el servicio Windows VoiceWebUI con NSSM.
# Ejecutar como administrador.
#
# Por que todo es parametrizable: la version anterior traia la raiz del
# proyecto y el perfil de usuario de quien la escribio escritas a fuego. Eso
# no era solo una exposicion: el servicio no arrancaba en ningun otro equipo,
# porque el PATH apuntaba a un perfil de usuario que ahi no existe.
#
#   .\setup-service.ps1
#   .\setup-service.ps1 -ProjectRoot D:\apps\Voice-WebUI -Python C:\Python\python.exe
#   .\setup-service.ps1 -DryRun     # solo imprime los comandos nssm, no ejecuta

[CmdletBinding()]
param(
    # Raiz del repositorio. Por defecto, el directorio donde vive este script,
    # para que el servicio apunte al codigo que hay junto a el.
    [string]$ProjectRoot = "",

    # Interprete de Python del entorno conda con las dependencias del proyecto.
    # Por defecto, el entorno conda activo; si no hay ninguno, el python del PATH.
    [string]$Python = "",

    [string]$ServiceName = "VoiceWebUI",

    # Imprime los comandos en vez de ejecutarlos. Para revisar el PATH
    # resultante sin tocar el servicio.
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot no llega evaluado dentro del default de param() en PS 5.1
# cuando el script se lanza con -File, asi que se resuelve aqui y no ahi.
if (-not $ProjectRoot) { $ProjectRoot = $PSScriptRoot }
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
if (-not $ProjectRoot) {
    Write-Error "No se pudo determinar la raiz del proyecto. Indicala con -ProjectRoot."
}

$APP = Join-Path $ProjectRoot "src\app.py"
$WORKDIR = $ProjectRoot
$LOGS = Join-Path $ProjectRoot "logs"

if (-not $Python) {
    if ($env:CONDA_PREFIX) {
        $Python = Join-Path $env:CONDA_PREFIX "python.exe"
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}

# El PATH del servicio se construye por partes y se filtra por existencia.
# Un servicio cuyo PATH menciona directorios inexistentes arranca igual, pero
# cualquier herramienta que se resuelva por PATH (node, git) falla sin aviso.
$pathParts = @()

# Directorios del entorno Python: el del interprete, sus Scripts y las DLLs
# de las librerias nativas (llama.cpp, whisper).
if ($Python) {
    $pyDir = Split-Path -Parent $Python
    $pathParts += $pyDir
    $pathParts += (Join-Path $pyDir "Scripts")
    $pathParts += (Join-Path $pyDir "Library\bin")
}

# npm global del usuario que instala (los servidores MCP stdio llegan por ahi).
# Con APPDATA en vez de una ruta literal: es lo mismo en cada maquina y no
# revela el nombre de nadie.
$npmGlobal = Join-Path $env:APPDATA "npm"
if ($npmGlobal) { $pathParts += $npmGlobal }

foreach ($comun in @(
    "C:\Program Files\nodejs",
    "C:\Program Files\Git\cmd",
    "C:\Windows\system32",
    "C:\Windows",
    "C:\Windows\System32\Wbem",
    "C:\Windows\System32\WindowsPowerShell\v1.0"
)) {
    $pathParts += $comun
}

$pathServidor = ($pathParts | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique) -join ";"

function Invoke-Nssm {
    param([Parameter(ValueFromRemainingArguments = $true)]$Argumentos)
    Write-Host "nssm $($Argumentos -join ' ')" -ForegroundColor DarkGray
    if ($DryRun) { return }
    & nssm @Argumentos
}

# --- Comprobaciones previas -------------------------------------------------

if (-not $Python) {
    Write-Error "No encuentro Python. Indicalo con -Python, o activa antes el entorno conda con las dependencias del proyecto."
}
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Python no encontrado en $Python"
}
if (-not (Test-Path -LiteralPath $APP)) {
    Write-Error "No encuentro src\app.py bajo $ProjectRoot. Indica la raiz correcta con -ProjectRoot."
}

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssm -and -not $DryRun) {
    Write-Host "NSSM no encontrado. Instalando via winget..." -ForegroundColor Yellow
    winget install --id NSSM.NSSM --source winget --accept-package-agreements --accept-source-agreements --silent
    $env:Path += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"
}

if (-not (Test-Path -LiteralPath $LOGS)) {
    New-Item -ItemType Directory -Path $LOGS | Out-Null
}

# --- Creacion del servicio --------------------------------------------------

Write-Host "Servicio:  $ServiceName" -ForegroundColor Cyan
Write-Host "Proyecto:  $WORKDIR"
Write-Host "Python:    $Python"
Write-Host "Logs:      $LOGS"
Write-Host "PATH:      $pathServidor"
Write-Host ""

Invoke-Nssm install $ServiceName $Python
Invoke-Nssm set $ServiceName AppParameters $APP
Invoke-Nssm set $ServiceName AppDirectory $WORKDIR
Invoke-Nssm set $ServiceName AppEnvironmentExtra "PATH=$pathServidor"
Invoke-Nssm set $ServiceName AppStdout "$LOGS\service.log"
Invoke-Nssm set $ServiceName AppStderr "$LOGS\service.log"
Invoke-Nssm set $ServiceName AppRestartDelay 60
Invoke-Nssm set $ServiceName Start SERVICE_AUTO_START
Invoke-Nssm set $ServiceName Description "Voice-WebUI Flask application with MCP tools, Whisper STT, and Qwen LLM"
Invoke-Nssm set $ServiceName AppStopMethodThreads 30000
Invoke-Nssm set $ServiceName AppStopMethodWindow 30000

if (-not $DryRun) {
    nssm start $ServiceName
    Write-Host "Servicio $ServiceName creado y corriendo." -ForegroundColor Green
} else {
    Write-Host "DryRun: no se ha ejecutado nada." -ForegroundColor Yellow
}

Write-Host "Comandos utiles:"
Write-Host "  nssm status $ServiceName"
Write-Host "  nssm restart $ServiceName"
Write-Host "  nssm stop $ServiceName"
Write-Host "  nssm remove $ServiceName confirm"
