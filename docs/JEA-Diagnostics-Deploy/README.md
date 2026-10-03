# JEA Diagnostics — Despliegue Masivo para Windows 10/11

## Estructura del paquete

```
JEA-Diagnostics-Deploy/
├── JEADiagnostics/
│   ├── Diagnostics.pssc              # Configuracion de sesion JEA
│   └── RoleCapabilities/
│       └── Diagnostics.psrc          # Capacidades de rol (comandos permitidos)
└── Scripts/
    ├── Deploy-JEA.ps1                # Script de despliegue
    └── Test-JEA.ps1                  # Script de validacion
```

---

## Pre-requisitos

1. **Cuenta de servicio de dominio** para el MCP:
   ```powershell
   # En un Controlador de Dominio
   New-ADUser -Name "svc-mcp-diag" `
       -SamAccountName "svc-mcp-diag" `
       -UserPrincipalName "svc-mcp-diag@ejemplo.local" `
       -AccountPassword (Read-Host -AsSecureString "Contraseña") `
       -Enabled $true `
       -PasswordNeverExpires $true
   ```

2. **WinRM habilitado** en los endpoints (el script de despliegue lo configura si no esta activo).

3. **Permisos de administrador local** para ejecutar el script de despliegue.

---

## Metodo 1: Despliegue via GPO (Startup Script)

### Paso 1: Preparar el paquete

1. Copia la carpeta `JEA-Diagnostics-Deploy` a una **carpeta compartida de red** accesible por todos los equipos:
   ```
   \\ejemplo.local\SYSVOL\ejemplo.local\JEA-Diagnostics
   ```

2. Edita `JEADiagnostics\Diagnostics.pssc` y reemplaza `EXAMPLE\\svc-mcp-diag` por tu cuenta real.

### Paso 2: Crear la GPO

1. Abre **Group Policy Management** (`gpmc.msc`).
2. Crea una nueva GPO: `JEA Diagnostics Deployment`.
3. Vincula la GPO a la OU que contiene tus equipos Windows 10/11.
4. Edita la GPO:

#### Computer Configuration > Policies > Windows Settings > Scripts (Startup/Shutdown)
- Ve a **Startup** > **PowerShell Scripts** > **Add**
- Script: `\\ejemplo.local\SYSVOL\ejemplo.local\JEA-Diagnostics\Scripts\Deploy-JEA.ps1`
- Parameters: `-DomainServiceAccount "EXAMPLE\\svc-mcp-diag"`

#### Computer Configuration > Policies > Administrative Templates > System > Group Policy
- Configura **Configure scripts policy processing** > **Enabled** > **Allow processing across a slow network connection**.

### Paso 3: Forzar actualizacion

```powershell
# En un endpoint de prueba
gpupdate /force
# Reiniciar para ejecutar el startup script
Restart-Computer
```

### Paso 4: Validar

```powershell
# En el endpoint despues del reinicio
. \\ejemplo.local\SYSVOL\ejemplo.local\JEA-Diagnostics\Scripts\Test-JEA.ps1
```

---

## Metodo 2: Despliegue via Intune (Proactive Remediation)

### Detection Script

```powershell
# detection.ps1
$ErrorActionPreference = "SilentlyContinue"
$config = Get-PSSessionConfiguration -Name "JEADiagnostics"
if (-not $config) { exit 1 }
if ($config.SessionType -ne "RestrictedRemoteServer") { exit 1 }
exit 0
```

### Remediation Script

```powershell
# remediation.ps1
$ErrorActionPreference = "Stop"
$scriptPath = "$env:TEMP\Deploy-JEA.ps1"
Invoke-WebRequest -Uri "https://tu-servidor/Deploy-JEA.ps1" -OutFile $scriptPath
& $scriptPath -DomainServiceAccount "EXAMPLE\\svc-mcp-diag" -SkipWinRMConfig
```

### Configuracion en Intune

1. Ve a **Endpoint security > Proactive remediations**.
2. Crea un paquete:
   - **Name**: `JEA Diagnostics Deployment`
   - **Detection script**: Sube `detection.ps1`
   - **Remediation script**: Sube `remediation.ps1`
   - **Run this script using the logged-on credentials**: **No**
   - **Enforce script signature check**: **No** (o firma los scripts si tienes PKI)
   - **Run script in 64-bit PowerShell**: **Yes**
3. Asignalo a un grupo de dispositivos Windows 10/11.

---

## Metodo 3: Despliegue manual (uno a uno)

```powershell
# Copiar el paquete al equipo
Copy-Item -Path "\\servidor\JEA-Diagnostics" -Destination "C:\Temp" -Recurse

# Ejecutar como Administrador
C:\Temp\JEA-Diagnostics\Scripts\Deploy-JEA.ps1 -DomainServiceAccount "EXAMPLE\\svc-mcp-diag"
```

---

## Configuracion del MCP (powershell-mcp)

Una vez desplegado, configura `powershell-mcp` para usar el endpoint JEA:

```json
{
  "mcpServers": {
    "powershell": {
      "command": "node",
      "args": ["C:\ruta\a\powershell-mcp\dist\index.js"]
    }
  }
}
```

### Prompts de ejemplo

> *"Conectate a PC-EQUIPO-01 via WinRM usando el endpoint JEADiagnostics con la cuenta svc-mcp-diag. Muestra los ultimos 20 errores del System log, el uso de disco y los 5 procesos con mas CPU."*

> *"En WS-ADMIN01 via JEADiagnostics, verifica que servicios criticos (Spooler, wuauserv, WinRM) esten corriendo y muestra la tabla de rutas de red."*

> *"Diagnostica PC-EQUIPO-02: usa Get-ComputerInfo para ver la version de Windows, RAM total y ultima fecha de instalacion. Luego revisa si hay eventos criticos en Application en las ultimas 24 horas."*

---

## Seguridad y auditoria

### Transcripciones

Todas las sesiones JEA se transcriben automaticamente en:
```
C:\ProgramData\JEA\Transcripts\
```

Cada archivo incluye:
- Fecha/hora de inicio y fin
- Comandos ejecutados
- Output generado
- Identidad del usuario (la cuenta virtual)

### Permisos de la cuenta de servicio

La cuenta `svc-mcp-diag` NO necesita ser administrador local. Solo necesita:

1. **Autenticacion WinRM**: Agregala al grupo local **Remote Management Users**:
   ```powershell
   Add-LocalGroupMember -Group "Remote Management Users" -Member "EXAMPLE\\svc-mcp-diag"
   ```

2. **Lectura de logs**: Ya cubierto por el endpoint JEA (Virtual Account).

3. **Lectura de contadores**: Ya cubierto por el endpoint JEA.

> **IMPORTANTE**: No agregues `svc-mcp-diag` al grupo **Administradores** local. El proposito de JEA es evitar exactamente eso.

### Firewall

El script de despliegue habilita las reglas de firewall para WinRM. Si usas GPO para firewall, asegurate de permitir:
- **WINRM-HTTP-In-TCP** (puerto 5985)
- **WINRM-HTTPS-In-TCP** (puerto 5986, opcional)

---

## Troubleshooting

### "Access is denied" al conectar

```powershell
# Verificar que la cuenta tiene permisos WinRM
Get-PSSessionConfiguration -Name "JEADiagnostics" | Format-List Permission

# Verificar membresia en Remote Management Users
Get-LocalGroupMember -Group "Remote Management Users"
```

### "The term 'Get-WinEvent' is not recognized"

```powershell
# Verificar que el modulo esta en ModulesToImport del .psrc
Get-Content "C:\Program Files\WindowsPowerShell\Modules\JEADiagnostics\RoleCapabilities\Diagnostics.psrc"
```

### Transcripciones vacias

```powershell
# Verificar permisos del directorio
Get-Acl "C:\ProgramData\JEA\Transcripts" | Format-List

# La cuenta virtual necesita permisos de escritura
icacls "C:\ProgramData\JEA\Transcripts" /grant "NT VIRTUAL SERVICE\VirtualAccounts:*(OI)(CI)(M)"
```

### WinRM no responde

```powershell
# Verificar estado
Test-WSMan -ComputerName localhost

# Reconfigurar
Enable-PSRemoting -Force
Set-Item WSMan:\localhost\Service\AllowRemoteAccess -Value $true
```

---

## Comandos permitidos (resumen)

| Categoria | Comandos |
|---|---|
| **Sistema** | Get-ComputerInfo, Get-HotFix, Get-Date, Get-Uptime |
| **Procesos** | Get-Process, Get-Counter |
| **Servicios** | Get-Service (solo lectura) |
| **Logs** | Get-EventLog, Get-WinEvent |
| **Red** | Get-NetAdapter, Get-NetIPAddress, Test-Connection, Resolve-DnsName, Test-NetConnection |
| **Almacenamiento** | Get-Disk, Get-Volume, Get-Partition |
| **WMI/CIM** | Get-WmiObject, Get-CimInstance |
| **Archivos** | Get-ChildItem, Get-Content, Get-Item, Test-Path (solo lectura) |

**Comandos PROHIBIDOS (bloqueados por JEA):**
- Stop-Service, Start-Service, Restart-Service
- Remove-Item, New-Item, Set-Item
- Set-ExecutionPolicy
- New-LocalUser, Add-LocalGroupMember
- Invoke-Expression, Start-Process
- Cualquier cmdlet con verbos Set, New, Remove, Stop, Start, Restart, Disable, Enable

---

## Licencia

Este paquete es de uso interno. Adapta las cuentas de dominio y rutas a tu entorno antes del despliegue.
