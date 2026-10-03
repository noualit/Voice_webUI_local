# mcp_servers/powershell/server.py
"""Server MCP local "powershell": ejecuta scripts PowerShell/Python en el
equipo del usuario (Windows 11, PowerShell 5.1).

El modelo Qwen 3.6 asumia Linux (python3, /tmp/) y fallaba en Windows con
'Exit code: 9009' (bug 2026-09-15). Este MCP le da una via FIABLE de ejecutar
comandos: el prompt de format_tools_prompt le indica el entorno y las tools
devuelven stdout/stderr/exit_code claros para que corrija sin loops.

run_python usa sys.executable: el server se lanza con el python del env llama
(ver mcp-config.json), asi que 'python' es el entorno correcto sin rutas
hardcodeadas.
"""
import os
import re
import subprocess
import sys
import time
from typing import Any

# Directorio raiz del server (mcp_servers/powershell/) para importar los modulos
# hermanos (ps_files y, opcionalmente, admin_runner)
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from ps_files import make_result, new_temp_file, remove_quietly  # noqa: E402

try:
    from admin_runner import run_elevated
except ImportError:
    run_elevated = None


def _create_mcp_server(name: str):
    """Crea el server MCP compatible con mcp 1.x y 2.x.

    mcp 1.x expone `FastMCP` en `mcp.server.fastmcp`. En mcp 2.x ese modulo se
    elimino y la clase se renombro a `MCPServer` (`mcp.server.mcpserver`), pero
    la API que usamos (`@server.tool()` y `server.run()`, stdio por defecto) es
    la misma.

    Importar FastMCP de forma directa rompia el server cuando el proceso padre
    resolvia mcp 2.x: p.ej. el servicio Flask corre como LocalSystem, cuyo
    user-site no tiene mcp, por lo que cae al mcp 2.x del env llama y el
    subproceso moria al importar (bug 2026-09-18: el MCP powershell aparecia
    offline con punto rojo en la UI).
    """
    try:
        from mcp.server.fastmcp import FastMCP
        return FastMCP(name)
    except ModuleNotFoundError as e:
        # Solo se hace fallback si falta precisamente mcp.server.fastmcp
        # (mcp 2.x). Si falla una dependencia distinta, se propaga.
        if e.name != "mcp.server.fastmcp":
            raise
        from mcp.server.mcpserver import MCPServer
        return MCPServer(name)


mcp = _create_mcp_server("powershell")


def _sin_ruta_del_temporal(text: str, script_path: str) -> str:
    """Sustituye la ruta del .ps1 temporal por un marcador estable.

    PowerShell la incluye en cada mensaje de error ("At <ruta>:2 char:1"). Al
    LLM esa ruta le confunde y le filtra el nombre de usuario del equipo."""
    return text.replace(script_path, "<script>").replace(
        os.path.basename(script_path), "<script>"
    )


def run_powershell_impl(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script PowerShell y devuelve {stdout, stderr, exit_code,
    elapsed_ms}.

    Por que un .ps1 temporal con -File y NO -EncodedCommand: en modo
    -EncodedCommand PowerShell trata la entrada como protocolo Automation
    CLIXML, no como texto del usuario, y serializa a stderr tanto el stream de
    error como el de progreso como XML con escapes _x000D__x000A_. El LLM
    recibia ese bloque y no podia leer ni "Cannot find path" ni el ruido de
    "Preparing modules for first use." (bug 2026-09-26). Con -File ambos
    streams llegan en texto plano y el progreso no ensucia la salida.

    El script va en un archivo propio, nunca interpolado en un ScriptBlock:
    interpolarlo dentro de { ... } lo parte en cuanto trae una llave (bug ya
    visto en admin_runner)."""
    start = time.time()
    script_path = new_temp_file(script)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            # PowerShell 5.1 escribe stdout/stderr en UTF-8. Sin fijarlo aqui se
            # decodifica con la codicion del locale y los acentos salen rotos.
            encoding="utf-8",
            errors="replace",
        )
        return make_result(
            _sin_ruta_del_temporal(proc.stdout, script_path),
            _sin_ruta_del_temporal(proc.stderr, script_path),
            proc.returncode,
            int((time.time() - start) * 1000),
        )
    except subprocess.TimeoutExpired as e:
        return make_result(
            e.stdout or "",
            f"Timeout: el comando excedio {timeout}s y se cancelo.",
            -1,
            int((time.time() - start) * 1000),
        )
    finally:
        remove_quietly(script_path)


def run_python_impl(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script Python (env llama, via stdin) y devuelve la misma
    estructura que run_powershell_impl."""
    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            # El script escribe su propia salida; PowerShell no interviene, asi
            # que la codificacion la fija el propio script (run_python_opt).
            encoding="utf-8",
            errors="replace",
        )
        return make_result(
            proc.stdout, proc.stderr, proc.returncode,
            int((time.time() - start) * 1000),
        )
    except subprocess.TimeoutExpired as e:
        return make_result(
            e.stdout or "",
            f"Timeout: el comando excedio {timeout}s y se cancelo.",
            -1,
            int((time.time() - start) * 1000),
        )


@mcp.tool()
def run_powershell(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script PowerShell en el equipo local del usuario (Windows,
    PowerShell 5.1). Devuelve stdout, stderr, exit_code, has_error y elapsed_ms.

    has_error es la bandera de fallo: ponla a true si el comando NO hizo lo que
    pedias. NO deduzcas el exito de exit_code, porque un error no terminante
    (p.ej. Get-Item sobre una ruta inexistente) devuelve exit_code 0. Si
    has_error es true, lee stderr, corrige el script y vuelve a intentarlo."""
    return run_powershell_impl(script, timeout)


@mcp.tool()
def run_python(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script Python (entorno llama) en el equipo local del
    usuario. Devuelve stdout, stderr, exit_code, has_error y elapsed_ms.

    has_error es true si el script escribio en stderr. NO deduzcas el exito de
    exit_code: un script puede terminar con 0 tras avisar de un problema."""
    return run_python_impl(script, timeout)


@mcp.tool()
def run_powershell_admin(script: str, timeout: int = 60, computer: str = "localhost") -> dict[str, Any]:
    """Ejecuta un script PowerShell con credenciales de administrador.

    Usa ADMIN_USER y ADMIN_PASSWORD del entorno (.env). Para tareas que
    necesitan privilegios: gestion de servicios, registros, drivers,
    espacio en disco de otro equipo, etc.

    Args:
        script: Script PowerShell a ejecutar.
        timeout: Tiempo maximo en segundos antes de cancelar.
        computer: Host destino. Por defecto "localhost" (este equipo).
            Para otro equipo hay que indicarlo aqui (p.ej. "192.0.2.50")
            y el host debe estar en ADMIN_ALLOWED_HOSTS; si no, se rechaza
            sin intentar la conexion.

    Devuelve stdout, stderr, exit_code, has_error y elapsed_ms. has_error es
    la bandera de fallo: un exit_code 0 NO significa exito si has_error es true.
    """
    if run_elevated is None:
        return {
            "stdout": "",
            "stderr": "admin_runner no disponible (modulo no instalado).",
            "exit_code": -1,
            "has_error": True,
            "elapsed_ms": 0,
        }
    return run_elevated(script, timeout, computer)


# --- run_desktop_app --------------------------------------------------------
#
# Por que existe: la app corre como servicio NSSM -> LocalSystem -> sesion 0, y
# en la sesion 0 no hay escritorio. Medido desde SYSTEM: 'Start-Process calc'
# devuelve exito y no abre NINGUN proceso. Ademas la calculadora es una app
# Store por usuario, y desde el perfil de SYSTEM no existe. La via que si
# funciona es el Task Scheduler con LogonType Interactive: verificado, el proceso
# arranca en la sesion 1 del usuario.
#
# Criterio de exito: que aparezca un proceso NUEVO en la sesion del usuario, no
# el codigo de salida. Por eso el script compara PIDs antes/despues en vez de
# nombres: el shim calc.exe no es el proceso final, ese es CalculatorApp.
_DESKTOP_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = '__TARGET__'
$argstr = '__ARGS__'
$maxWait = __WAIT__

# --- usuario de la sesion interactiva ---------------------------------------
# No se puede hardcodear: el owner sale del explorer.exe de una sesion != 0.
$usuario = $null
$sesion = $null
foreach ($p in (Get-Process -Name explorer -ErrorAction SilentlyContinue | Sort-Object SessionId)) {
    if ($p.SessionId -eq 0) { continue }
    try {
        $o = Invoke-CimMethod -InputObject (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)") -MethodName GetOwner
        if ($o.ReturnValue -eq 0 -and $o.User) { $usuario = "$($o.Domain)\$($o.User)"; $sesion = $p.SessionId; break }
    } catch { }
}
if (-not $usuario) {
    [Console]::Error.WriteLine('No hay ninguna sesion de usuario activa (no se encontro explorer.exe fuera de la sesion 0). Si el equipo esta en la pantalla de bloqueo o sin sesion iniciada, no hay a quien mostrarle una ventana.')
    exit 1
}

# --- resolver el ejecutable --------------------------------------------------
$exe = $null
if (Test-Path -LiteralPath $target) { $exe = (Resolve-Path -LiteralPath $target).Path }
else {
    $cmd = Get-Command $target -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $exe = $cmd.Source }
}
if (-not $exe) {
    [Console]::Error.WriteLine("No encuentro '$target' ni como ruta de archivo ni en el PATH.")
    exit 1
}

# --- foto de procesos antes de lanzar ----------------------------------------
$antes = @{}
foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) { $antes[$p.Id] = $true }

$taskName = 'VoiceWebUI_app_' + [guid]::NewGuid().ToString('N').Substring(0, 12)
try {
    # -Argument no acepta cadena vacia (ParameterArgumentValidationError), asi
    # que solo se pasa si hay argumentos.
    if ($argstr) { $action = New-ScheduledTaskAction -Execute $exe -Argument $argstr }
    else { $action = New-ScheduledTaskAction -Execute $exe }
    $principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName

    $deadline = (Get-Date).AddSeconds($maxWait)
    $nuevos = @()
    do {
        Start-Sleep -Milliseconds 700
        $nuevos = @()
        foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
            if (-not $antes.ContainsKey($p.Id) -and $p.SessionId -eq $sesion) { $nuevos += $p }
        }
    } while ($nuevos.Count -eq 0 -and (Get-Date) -lt $deadline)

    $resultado = 0
    try { $resultado = (Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult } catch { }

    "usuario`t$usuario"
    "sesion`t$sesion"
    "ejecutable`t$exe"
    "last_task_result`t$resultado"
    "esperado_s`t$maxWait"
    if ($nuevos.Count -gt 0) {
        # Sin MainWindowHandle: desde SYSTEM devuelve 0 para procesos de otro
        # usuario, asi que el campo siempre seria False y el modelo lo leeria
        # como "no se abrio ventana". El proceso nuevo en la sesion del usuario
        # es la evidencia suficiente.
        foreach ($p in $nuevos) { "nuevo_proceso`t$($p.ProcessName)`tPID=$($p.Id)" }
        # Puede haber procesos ajenos (el navegador del usuario, p.ej., si
        # arranca en ese momento). El exito es que haya AL MENOS uno.
        "resumen`t$($nuevos.Count) proceso(s) nuevo(s) en la sesion $sesion"
    } else {
        "nuevo_proceso`tNINGUNO"
        [Console]::Error.WriteLine("La tarea se lanzo (last_task_result=$resultado) pero en $maxWait s no aparecio ningun proceso nuevo en la sesion $sesion. Puede que la aplicacion ya estuviera abierta, o que no admita este tipo de lanze. NO le digas al usuario que se ha abierto: no hay evidencia de ello.")
    }
} finally {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
"""


def run_desktop_app_impl(target: str, args: str = "", timeout: int = 20) -> dict[str, Any]:
    """Lanza una app de escritorio y comprueba que de verdad se ha abierto."""
    if not (target or "").strip():
        return make_result("", "El parametro 'target' esta vacio. Indica el programa a "
                                 "abrir ('calc', 'notepad') o la ruta de un archivo.", -1, 0)
    script = (_DESKTOP_SCRIPT
              .replace("__TARGET__", target.strip().replace("'", "''"))
              .replace("__ARGS__", (args or "").replace("'", "''"))
              .replace("__WAIT__", str(int(timeout))))
    return run_powershell_impl(script, timeout + 20)


@mcp.tool()
def run_desktop_app(target: str, args: str = "", timeout: int = 20) -> dict[str, Any]:
    """Abre una aplicacion de escritorio en la pantalla del usuario y VERIFICA
    que se ha abierto de verdad.

    Usa esta tool, NO Start-Process de run_powershell. Esta app corre como
    servicio de Windows en la sesion 0, donde no hay escritorio: medido,
    'Start-Process calc' devuelve exito y no abre NINGUN proceso, porque las
    ventanas de la sesion 0 no se ven y la calculadora es ademas una app Store
    por usuario. Aqui la app se lanza con un token interactivo, en la sesion
    del usuario de verdad.

    Args:
        target: Programa a abrir ('calc', 'notepad', 'mspaint') o ruta de un
            archivo, que se abre con su programa asociado.
        args: Argumentos para el programa (opcional).
        timeout: Segundos que espera a que aparezca el proceso.

    Devuelve stdout, stderr, exit_code, has_error y elapsed_ms. Solo se considera
    exito si se ha visto al menos un proceso NUEVO en la sesion del usuario. Ojo:
    en la lista pueden aparecer procesos ajenos que hayan arrancado de paso (el
    navegador del usuario, por ejemplo); el exito es que haya uno, no que sean
    todos el programa pedido. Si has_error es true, la app NO se ha abierto: no
    se lo digas al usuario como hecho. Ojo tambien con Start-Process para otras
    acciones visibles (abrir un archivo, mostrar un messagebox): devuelven exito
    igual y pueden no hacer nada."""
    return run_desktop_app_impl(target, args, timeout)


# Recorrido del arbol con .NET en vez de Get-ChildItem -Recurse.
#
# Por que no CIM/WMI: ni CIM ni Get-CimInstance exponen el tamano de una carpeta
# de forma utilizable, asi que cualquier tool que lo prometa esta MIDiendo.
# Por que .NET y no Get-ChildItem: sobre C:\Users (122 GB) tardo 43 s con
# Get-ChildItem y 28 s con este recorrido, y da el mismo total al byte
# (122.295.312.925 vs 122.295.318.128; los 5 KB de diferencia son archivos que
# cambian mientras se mide).
#
# Los reparse points (junction/symlink) se saltan: el directorio
# Temp\pytest-of-<usuario>\pytest-current de pytest apunta a su propio padre, y
# seguirlo recorre el subarbol repetidas veces (medido: 343 s y 221 GB, casi el
# doble).
#
# El template va aparte del f-string a proposito: un f-string obliga a doblar
# cada llave del script y hace el cambiooderso a revisar. Los valores viajan por
# marcadores sustituidos, con el escapado del string single-quoted de PowerShell.
_DISK_SCRIPT = r"""
$ErrorActionPreference = 'Continue'
$root = '__DRIVE__'
$maxDepth = __MAXDEPTH__
$topN = __TOPN__

function Format-Size {
    param([long]$bytes)
    if ($bytes -ge 1TB) { '{0:N2} TB' -f ($bytes / 1TB) }
    elseif ($bytes -ge 1GB) { '{0:N2} GB' -f ($bytes / 1GB) }
    elseif ($bytes -ge 1MB) { '{0:N2} MB' -f ($bytes / 1MB) }
    elseif ($bytes -ge 1KB) { '{0:N2} KB' -f ($bytes / 1KB) }
    else { "$bytes Bytes" }
}

# Tamano total del subarbol de $Path, sin seguir junctions.
function Get-Size {
    param([string]$Path)
    [long]$total = 0
    $stack = New-Object System.Collections.Stack
    $stack.Push($Path)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try {
            $di = New-Object System.IO.DirectoryInfo $dir
            if ($di.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            foreach ($f in $di.EnumerateFiles()) { try { $total += $f.Length } catch { } }
            foreach ($sub in $di.EnumerateDirectories()) { $stack.Push($sub.FullName) }
        } catch { }
    }
    return $total
}

# Carpetas de nivel 1..Depth bajo $Path (en anchura, sin junctions).
# Dos colas en paralelo (ruta y nivel) en vez de una de pares: al meter un array
# en un metodo .NET que toma 'object', PowerShell 5.1 lo desenvuelve y guarda solo
# el primer elemento, con lo que el nivel se pierde y max_depth no acota nada.
function Get-LevelDirs {
    param([string]$Path, [int]$Depth)
    $out = New-Object System.Collections.ArrayList
    $qPath = New-Object System.Collections.Queue
    $qLevel = New-Object System.Collections.Queue
    $qPath.Enqueue($Path); $qLevel.Enqueue(0)
    while ($qPath.Count -gt 0) {
        $dir = $qPath.Dequeue()
        $level = $qLevel.Dequeue()
        if ($level -ge $Depth) { continue }
        try {
            $di = New-Object System.IO.DirectoryInfo $dir
            foreach ($sub in $di.EnumerateDirectories()) {
                if (-not ($sub.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    [void]$out.Add($sub.FullName)
                    $qPath.Enqueue($sub.FullName); $qLevel.Enqueue($level + 1)
                }
            }
        } catch { }
    }
    return $out
}

try {
    $rootInfo = New-Object System.IO.DirectoryInfo $root
    if (-not $rootInfo.Exists) {
        # A stderr, no a stdout: has_error se deriva de stderr, y un error
        # escrito en stdout saldia con has_error false (falso positivo).
        [Console]::Error.WriteLine("Error: la ruta '$root' no existe.")
        exit 1
    }
} catch {
    [Console]::Error.WriteLine("Error: no se puede leer la ruta '$root'.")
    exit 1
}

# Los archivos sueltos de la raiz no pertenecen a ninguna carpeta listada: sin
# esta linea el total no cuadra con lo que ocupa la unidad.
[long]$loose = 0
foreach ($f in $rootInfo.EnumerateFiles()) { try { $loose += $f.Length } catch { } }
if ($loose -gt 0) { ".archivos_raiz`t$loose`t$(Format-Size $loose)" }

[long]$total = $loose
# Ruta relativa a la raiz y no el nombre suelto: con max_depth > 1 dos carpetas
# homonimas de distinto nivel colapsarian en la misma clave del CSV.
$base = $root.TrimEnd('\') + '\'

# Recoger todas las carpetas con su tamano, ordenar por tamano descendente y
# devolver solo las top_n (0 = sin limite). Esto evita que el LLM reciba miles
# de filas cuando se analiza una unidad entera.
$allFolders = @()
foreach ($dir in (Get-LevelDirs -Path $root -Depth $maxDepth)) {
    $size = Get-Size -Path $dir
    $total += $size
    $allFolders += [PSCustomObject]@{
        RelPath = $dir.Substring($base.Length)
        Size = $size
        SizeHuman = Format-Size $size
    }
}
if ($topN -gt 0) {
    $allFolders = $allFolders | Sort-Object Size -Descending | Select-Object -First $topN
}
foreach ($f in $allFolders) {
    "{0}`t{1}`t{2}" -f $f.RelPath, $f.Size, $f.SizeHuman
}
"total`t$total`t$(Format-Size $total)"
"""


@mcp.tool()
def run_disk_analysis(drive: str = "D:", max_depth: int = 1, top_n: int = 0, timeout: int = 300) -> dict[str, Any]:
    """Cuanto ocupa cada carpeta de una unidad o ruta, ordenado por tamano real.

    Recorre el arbol con .NET desde la version de PowerShell y salta los
    junctions, asi que no se cuelga con los enlaces circulares de AppData y no
    cuenta dos veces los mismos archivos.

    Args:
        drive: Letra de unidad ("D") o ruta completa ("D:\\\\models"). Con "D"
            analiza la unidad entera; con una ruta, solo esa carpeta.
        max_depth: Cuantos niveles de carpetas listar. 1 = solo las carpetas
            directas de la raiz (imprescindible para unidades grandes: listar
            toda la unidad puede tardar minutos). El tamano de cada carpeta
            listada cubre siempre su subarbol completo.
        top_n: Limitar a las N carpetas mas grandes (0 = sin limite).
            Recomendado: 10-20 para que el LLM no reciba miles de filas.
        timeout: Tiempo maximo en segundos antes de cancelar.

    Returns:
        Dict con stdout, stderr, exit_code, has_error y elapsed_ms. La salida es
        CSV: Folder, SizeBytes, SizeHuman, mas una fila 'total'. Los archivos
        sueltos de la raiz aparecen como '.archivos_raiz'. Si has_error es true,
        no des el resultado por bueno aunque exit_code sea 0.
    """
    destino = (drive or "").strip()
    if not destino:
        return make_result("", "El parametro 'drive' esta vacio. Indica una letra "
                                 "de unidad ('D:') o una ruta ('D:\\models').", -1, 0)
    if re.fullmatch(r"[A-Za-z]:?", destino):
        raiz = destino.rstrip(":") + ":\\"
    else:
        raiz = destino
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        return make_result("", f"max_depth debe ser un entero >= 1 (recibido: {max_depth!r}). "
                                 "1 = solo las carpetas de primer nivel.", -1, 0)
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 0:
        return make_result("", f"top_n debe ser un entero >= 0 (recibido: {top_n!r}). "
                                 "0 = sin limite.", -1, 0)

    script = (_DISK_SCRIPT
              .replace("__DRIVE__", raiz.replace("'", "''"))
              .replace("__MAXDEPTH__", str(max_depth))
              .replace("__TOPN__", str(top_n)))
    return run_powershell_impl(script, timeout)


if __name__ == "__main__":
    mcp.run()