#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Valida que el endpoint JEA este correctamente configurado.

.DESCRIPTION
    - Verifica que el endpoint JEADiagnostics exista
    - Lista los comandos visibles
    - Prueba una sesion local
    - Verifica transcripciones

.EXAMPLE
    .\Test-JEA.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  VALIDACION JEA DIAGNOSTICS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verificar endpoint
Write-Host "[1/5] Verificando endpoint JEADiagnostics..." -ForegroundColor Yellow
$config = Get-PSSessionConfiguration -Name "JEADiagnostics" -ErrorAction SilentlyContinue
if (-not $config) {
    Write-Host "[X] Endpoint JEADiagnostics NO encontrado." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Endpoint encontrado." -ForegroundColor Green
Write-Host "     Nombre: $($config.Name)"
Write-Host "     Tipo de sesion: $($config.SessionType)"
Write-Host "     Cuenta virtual: $($config.RunAsVirtualAccount)"
Write-Host ""

# 2. Verificar modulo
Write-Host "[2/5] Verificando modulo JEADiagnostics..." -ForegroundColor Yellow
$module = Get-Module -ListAvailable -Name "JEADiagnostics" -ErrorAction SilentlyContinue
if (-not $module) {
    Write-Host "[!] Modulo no encontrado en PSModulePath." -ForegroundColor Yellow
} else {
    Write-Host "[OK] Modulo encontrado en: $($module.ModuleBase)" -ForegroundColor Green
}
Write-Host ""

# 3. Probar sesion JEA
Write-Host "[3/5] Probando sesion JEA local..." -ForegroundColor Yellow
try {
    $session = New-PSSession -ConfigurationName "JEADiagnostics" -ComputerName "localhost" -ErrorAction Stop

    # Probar comandos permitidos
    $commands = Invoke-Command -Session $session -ScriptBlock {
        $results = @{}
        $results.ComputerInfo = Get-ComputerInfo | Select-Object -Property CsName, WindowsVersion, WindowsInstallDate
        $results.Processes = (Get-Process | Measure-Object).Count
        $results.Services = (Get-Service | Where-Object { $_.Status -eq 'Running' } | Measure-Object).Count
        $results.Disk = Get-Volume | Where-Object { $_.DriveLetter } | Select-Object DriveLetter, SizeRemaining, Size
        $results.Events = (Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3} -MaxEvents 5 -ErrorAction SilentlyContinue | Measure-Object).Count
        $results
    }
    Remove-PSSession -Session $session

    Write-Host "[OK] Sesion JEA funciona correctamente." -ForegroundColor Green
    Write-Host "     Equipo: $($commands.ComputerInfo.CsName)"
    Write-Host "     Windows: $($commands.ComputerInfo.WindowsVersion)"
    Write-Host "     Procesos: $($commands.Processes)"
    Write-Host "     Servicios running: $($commands.Services)"
    Write-Host "     Eventos criticos/error (System): $($commands.Events)"
    Write-Host "     Discos:"
    $commands.Disk | ForEach-Object {
        $pct = if ($_.Size -gt 0) { [math]::Round((($_.Size - $_.SizeRemaining) / $_.Size) * 100, 1) } else { 0 }
        Write-Host "       $($_.DriveLetter): - $pct% usado"
    }
} catch {
    Write-Host "[X] Error en sesion JEA: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

# 4. Verificar transcripciones
Write-Host "[4/5] Verificando directorio de transcripciones..." -ForegroundColor Yellow
$transcriptDir = "C:\ProgramData\JEA\Transcripts"
if (Test-Path $transcriptDir) {
    $files = Get-ChildItem -Path $transcriptDir -Filter "*.txt" -ErrorAction SilentlyContinue
    Write-Host "[OK] Directorio de transcripciones existe." -ForegroundColor Green
    Write-Host "     Archivos de transcripcion: $($files.Count)"
} else {
    Write-Host "[!] Directorio de transcripciones no existe aun." -ForegroundColor Yellow
}
Write-Host ""

# 5. Verificar comandos prohibidos
Write-Host "[5/5] Verificando restricciones de comandos..." -ForegroundColor Yellow
try {
    $session = New-PSSession -ConfigurationName "JEADiagnostics" -ComputerName "localhost" -ErrorAction Stop

    $blocked = @()
    $testCommands = @(
        @{ Cmd = "Stop-Service -Name Spooler"; Desc = "Stop-Service" }
        @{ Cmd = "Remove-Item C:\Windows\Temp\test.txt -ErrorAction SilentlyContinue"; Desc = "Remove-Item" }
        @{ Cmd = "Set-ExecutionPolicy Bypass -Force -ErrorAction SilentlyContinue"; Desc = "Set-ExecutionPolicy" }
        @{ Cmd = "New-LocalUser testuser -ErrorAction SilentlyContinue"; Desc = "New-LocalUser" }
    )

    foreach ($tc in $testCommands) {
        try {
            Invoke-Command -Session $session -ScriptBlock ([scriptblock]::Create($tc.Cmd)) -ErrorAction Stop | Out-Null
            $blocked += "[!] $($tc.Desc) NO fue bloqueado (ATENCION)"
        } catch {
            # Esperado: debe fallar
        }
    }
    Remove-PSSession -Session $session

    if ($blocked.Count -eq 0) {
        Write-Host "[OK] Todos los comandos destructivos fueron correctamente bloqueados." -ForegroundColor Green
    } else {
        foreach ($b in $blocked) { Write-Host $b -ForegroundColor Red }
    }
} catch {
    Write-Host "[X] Error verificando restricciones: $_" -ForegroundColor Red
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  VALIDACION COMPLETADA" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
