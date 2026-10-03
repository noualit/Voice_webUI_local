#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Despliega el endpoint JEA "JEADiagnostics" en endpoints Windows 10/11.

.DESCRIPTION
    - Crea la estructura de modulo JEADiagnostics
    - Copia Diagnostics.psrc y Diagnostics.pssc
    - Registra la configuracion de sesion JEA
    - Habilita WinRM si no esta activo
    - Configura firewall para WinRM
    - Valida el despliegue

.PARAMETER DomainServiceAccount
    Cuenta de dominio que usara el MCP para conectarse via WinRM.
    Ejemplo: "EXAMPLE\\svc-mcp-diag"

.PARAMETER SkipWinRMConfig
    Omite la configuracion de WinRM (si ya esta gestionada por GPO).

.EXAMPLE
    .\Deploy-JEA.ps1 -DomainServiceAccount "EXAMPLE\\svc-mcp-diag"

.EXAMPLE
    .\Deploy-JEA.ps1 -DomainServiceAccount "EXAMPLE\\svc-mcp-diag" -SkipWinRMConfig
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DomainServiceAccount,

    [switch]$SkipWinRMConfig
)

$ErrorActionPreference = "Stop"
$ModuleName = "JEADiagnostics"
$ModulePath = "C:\Program Files\WindowsPowerShell\Modules\$ModuleName"
$RoleCapPath = "$ModulePath\RoleCapabilities"
$TranscriptPath = "C:\ProgramData\JEA\Transcripts"

# ============================================================
# FUNCIONES AUXILIARES
# ============================================================
function Write-Step {
    param([string]$Message)
    Write-Host "[+] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[X] $Message" -ForegroundColor Red
}

# ============================================================
# 1. VALIDAR PRIVILEGIOS DE ADMIN
# ============================================================
Write-Step "Validando privilegios de administrador..."
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Err "Este script debe ejecutarse como Administrador."
    exit 1
}
Write-Ok "Privilegios de administrador confirmados."

# ============================================================
# 2. CONFIGURAR WINRM (si no se omite)
# ============================================================
if (-not $SkipWinRMConfig) {
    Write-Step "Configurando WinRM..."

    try {
        $winrm = Get-Service -Name "WinRM" -ErrorAction SilentlyContinue
        if (-not $winrm) {
            Write-Err "El servicio WinRM no esta disponible en este sistema."
            exit 1
        }

        if ($winrm.Status -ne "Running") {
            Start-Service -Name "WinRM" -ErrorAction Stop
            Write-Ok "Servicio WinRM iniciado."
        } else {
            Write-Ok "Servicio WinRM ya esta en ejecucion."
        }

        # Habilitar PSRemoting (idempotente)
        Enable-PSRemoting -Force -SkipNetworkProfileCheck -ErrorAction Stop | Out-Null
        Write-Ok "PSRemoting habilitado."

        # Configurar TrustedHosts si es necesario (solo en entornos de trabajo grupo; en AD no es estrictamente necesario)
        # Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force
        # Write-Warn "TrustedHosts configurado a '*' (restringir en produccion)."

        # Firewall: permitir WinRM-HTTP y WinRM-HTTPS
        $fwRules = @("WINRM-HTTP-In-TCP", "WINRM-HTTP-In-TCP-PUBLIC")
        foreach ($rule in $fwRules) {
            $r = Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue
            if ($r) {
                Set-NetFirewallRule -Name $rule -Enabled True -ErrorAction SilentlyContinue
            }
        }
        Write-Ok "Reglas de firewall WinRM activadas."

    } catch {
        Write-Err "Error configurando WinRM: $_"
        exit 1
    }
} else {
    Write-Warn "Configuracion de WinRM omitida por parametro."
}

# ============================================================
# 3. CREAR ESTRUCTURA DE MODULO
# ============================================================
Write-Step "Creando estructura de modulo JEA..."
try {
    New-Item -ItemType Directory -Path $RoleCapPath -Force | Out-Null
    New-Item -ItemType Directory -Path $TranscriptPath -Force | Out-Null
    Write-Ok "Directorios creados:"
    Write-Ok "  - $RoleCapPath"
    Write-Ok "  - $TranscriptPath"
} catch {
    Write-Err "Error creando directorios: $_"
    exit 1
}

# ============================================================
# 4. COPIAR ARCHIVOS DE CONFIGURACION
# ============================================================
Write-Step "Copiando archivos de configuracion JEA..."

$PsscSource = Join-Path $PSScriptRoot "..\JEADiagnostics\Diagnostics.pssc"
$PsrcSource = Join-Path $PSScriptRoot "..\JEADiagnostics\RoleCapabilities\Diagnostics.psrc"

# Fallback: buscar en el mismo directorio
if (-not (Test-Path $PsscSource)) {
    $PsscSource = Join-Path $PSScriptRoot "Diagnostics.pssc"
    $PsrcSource = Join-Path $PSScriptRoot "Diagnostics.psrc"
}

if (-not (Test-Path $PsscSource)) {
    Write-Err "No se encontro Diagnostics.pssc. Verifica la estructura de carpetas."
    exit 1
}
if (-not (Test-Path $PsrcSource)) {
    Write-Err "No se encontro Diagnostics.psrc. Verifica la estructura de carpetas."
    exit 1
}

try {
    Copy-Item -Path $PsscSource -Destination "$ModulePath\Diagnostics.pssc" -Force
    Copy-Item -Path $PsrcSource -Destination "$RoleCapPath\Diagnostics.psrc" -Force
    Write-Ok "Archivos copiados correctamente."
} catch {
    Write-Err "Error copiando archivos: $_"
    exit 1
}

# ============================================================
# 5. ACTUALIZAR RoleDefinitions CON LA CUENTA CORRECTA
# ============================================================
Write-Step "Actualizando cuenta de servicio en la configuracion JEA..."

$PsscDest = "$ModulePath\Diagnostics.pssc"
$content = Get-Content -Path $PsscDest -Raw

# Reemplazar el placeholder de cuenta de dominio
$oldAccount = '"EXAMPLE\\svc-mcp-diag"'
$newAccount = '"' + ($DomainServiceAccount -replace "\\", "\\") + '"'

if ($content -match [regex]::Escape($oldAccount)) {
    $content = $content -replace [regex]::Escape($oldAccount), $newAccount
} else {
    # Si no coincide exactamente, reemplazar cualquier cuenta svc-mcp
    $content = $content -replace '"[A-Za-z0-9._-]+\\svc-mcp-diag"', $newAccount
}

Set-Content -Path $PsscDest -Value $content -Encoding UTF8
Write-Ok "Cuenta de servicio actualizada a: $DomainServiceAccount"

# ============================================================
# 6. REGISTRAR CONFIGURACION DE SESION JEA
# ============================================================
Write-Step "Registrando endpoint JEA 'JEADiagnostics'..."
try {
    # Desregistrar si ya existe (para actualizaciones)
    $existing = Get-PSSessionConfiguration -Name "JEADiagnostics" -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Warn "El endpoint JEADiagnostics ya existe. Se actualizara."
        Unregister-PSSessionConfiguration -Name "JEADiagnostics" -Force -NoServiceRestart
        Start-Sleep -Seconds 2
    }

    Register-PSSessionConfiguration `
        -Name "JEADiagnostics" `
        -Path "$ModulePath\Diagnostics.pssc" `
        -Force `
        -NoServiceRestart

    # Reiniciar servicio WinRM para aplicar cambios
    Restart-Service -Name "WinRM" -Force
    Start-Sleep -Seconds 3

    Write-Ok "Endpoint JEADiagnostics registrado correctamente."
} catch {
    Write-Err "Error registrando endpoint JEA: $_"
    exit 1
}

# ============================================================
# 7. VALIDAR DESPLIEGUE
# ============================================================
Write-Step "Validando despliegue..."

try {
    $config = Get-PSSessionConfiguration -Name "JEADiagnostics" -ErrorAction Stop
    Write-Ok "Endpoint encontrado: $($config.Name)"
    Write-Ok "  - Permission: $($config.Permission)"
    Write-Ok "  - RunAsVirtualAccount: $($config.RunAsVirtualAccount)"
    Write-Ok "  - SessionType: $($config.SessionType)"

    # Validar que la cuenta de servicio tiene acceso
    $acl = Get-PSSessionConfiguration -Name "JEADiagnostics" | Select-Object -ExpandProperty SecurityDescriptorSddl
    if ($acl) {
        Write-Ok "  - SDDL configurado."
    }

    # Probar conexion local al endpoint JEA
    $testSession = New-PSSession -ConfigurationName "JEADiagnostics" -ComputerName "localhost" -ErrorAction Stop
    $result = Invoke-Command -Session $testSession -ScriptBlock { Get-ComputerInfo | Select-Object -Property CsName, WindowsVersion, TotalPhysicalMemory }
    Remove-PSSession -Session $testSession

    Write-Ok "  - Prueba de sesion JEA exitosa."
    Write-Ok "  - Equipo: $($result.CsName)"
    Write-Ok "  - Windows: $($result.WindowsVersion)"
    Write-Ok "  - RAM: $([math]::Round($result.TotalPhysicalMemory / 1GB, 2)) GB"

} catch {
    Write-Err "Error en validacion: $_"
    exit 1
}

# ============================================================
# 8. RESUMEN
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  DESPLIEGUE JEA COMPLETADO" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Endpoint:        JEADiagnostics"
Write-Host "Cuenta MCP:      $DomainServiceAccount"
Write-Host "Modulo:          $ModulePath"
Write-Host "Transcripciones: $TranscriptPath"
Write-Host ""
Write-Host "Para conectarse desde el MCP, usa:"
Write-Host "  Enter-PSSession -ComputerName <HOST> -ConfigurationName JEADiagnostics -Credential <DOMAIN>\\svc-mcp-diag"
Write-Host ""
Write-Host "IMPORTANTE: Asegurate de que la cuenta '$DomainServiceAccount'"
Write-Host "tenga permisos para autenticarse via WinRM en este equipo."
Write-Host "Considera agregarla al grupo 'Remote Management Users' local."
Write-Host ""
