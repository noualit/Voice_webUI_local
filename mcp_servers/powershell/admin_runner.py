# admin_runner.py
# Ejecuta scripts PowerShell, local o remoto, con credenciales de administrador.
#
# Uso tipico:
#   1. Configurar en .env:  ADMIN_USER, ADMIN_PASSWORD y ADMIN_ALLOWED_HOSTS
#   2. run_elevated("Get-Service | Where Status -eq 'Running'")
#   3. run_elevated("Get-PSDrive C", computer="192.0.2.50")
#
# Por que existe en vez de usar run_powershell: hay tareas que necesitan
# privilegios (servicios, registros, drivers). En LocalSystem ya los tiene,
# pero para maquinas remotas hace falta autenticacion explicita.
#
# Limitaciones honestas:
#   - WinRM debe estar habilitado y accesible en el equipo destino.
#   - El usuario de ADMIN_USER debe ser administrador en el destino.
#   - Si el disco del destino esta casi lleno, WinRM puede fallar al arrancar
#     su proceso host aunque la autenticacion sea correcta.

import ipaddress
import os
import subprocess
import sys
import time
from pathlib import Path

# ps_files va en el mismo directorio que este modulo; se anade al path porque el
# server MCP lo importa asi (ver server.py) y no como paquete.
_SERVER_DIR = str(Path(__file__).resolve().parent)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from dotenv import load_dotenv  # noqa: E402

from ps_files import make_result, new_temp_file, read_if_exists, remove_quietly  # noqa: E402

# Cargar .env del raiz del proyecto (padre de mcp_servers/powershell/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# Sin ADMIN_ALLOWED_HOSTS solo se permite el propio equipo. El LLM maneja
# estas credenciales, y una allowlist evita que las use para recorrer la red.
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")

_PS = "powershell"


def _allowed_hosts() -> set[str]:
    """Hosts autorizados, normalizados a minusculas y sin espacios.

    Cada entrada es un Host object: puede ser un nombre, una IP suelta
    ('192.0.2.50') o una red en notacion CIDR ('192.0.2.0/24').
    """
    raw = os.environ.get("ADMIN_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return hosts or set(DEFAULT_ALLOWED_HOSTS)


def _host_is_allowed(computer: str) -> bool:
    """Indica si `computer` esta cubierto por la allowlist.

    Compara contra cada entrada: coincidencia exacta para nombres e IPs
    sueltas, y pertenencia a la red para las que van en notacion CIDR.

    La comparacion de subredes usa `ipaddress` a proposito. Con startswith,
    '198.51.100.0/24' acabaria admitiendo 192.0.2.x y la allowlist no
    frenaria nada. Una entrada mal formada se trata como literal en vez de
    propagar la excepcion: un typo en el .env no puede tumbar la herramienta.
    """
    target = (computer or "").strip().lower()
    if not target:
        return False

    for entry in _allowed_hosts():
        if entry == target:
            return True
        if "/" not in entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
            address = ipaddress.ip_address(target)
        except ValueError:
            continue  # entrada o destino no son IPs validas
        if address.version == network.version and address in network:
            return True
    return False


def _ps_quote(value: str) -> str:
    """Escapa un valor para interpolarlo en un literal de PowerShell.

    Una comilla simple se duplica; es la unica proteccion necesaria dentro
    de '...' en PowerShell.
    """
    return str(value).replace("'", "''")


def _error(stderr: str, elapsed_ms: int = 0) -> dict:
    return make_result("", stderr, -1, elapsed_ms)


def build_session_script(
    computer: str,
    stdout_path: str,
    stderr_path: str,
    user_script_path: str | None = None,
) -> str:
    """Genera el wrapper PowerShell que abre la sesion remota y ejecuta el
    script del usuario.

    El script del usuario va en un ARCHIVO aparte y se invoca con
    `Invoke-Command -FilePath`. Interpolarlo dentro de `ScriptBlock { ... }`
    partia el wrapper en cuanto el script traia una llave (p.ej. un `if`).

    user_script_path solo tiene default para las pruebas; run_elevated
    siempre lo pasa.
    """
    admin_user = os.environ.get("ADMIN_USER", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if user_script_path is None:
        user_script_path = f"{stdout_path}.script.ps1"

    return f"""\
$stdout = '{_ps_quote(stdout_path)}'
$stderr = '{_ps_quote(stderr_path)}'
try {{
    # La credencial se construye con .NET y NO con ConvertTo-SecureString:
    # ese cmdlet vive en Microsoft.PowerShell.Security, que no carga si el
    # proceso se lanza con un PSModulePath heredado raro (p.ej. desde un
    # servicio o un env conda). SecureString y PSCredential estan en el
    # ensamblado base, siempre disponible.
    $pw = '{_ps_quote(admin_password)}'
    $sec = New-Object System.Security.SecureString
    foreach ($c in $pw.ToCharArray()) {{ $sec.AppendChar($c) }}
    $sec.MakeReadOnly()
    $cred = New-Object System.Management.Automation.PSCredential('{_ps_quote(admin_user)}', $sec)
    $pw = $null
    $s = New-PSSession -ComputerName '{_ps_quote(computer)}' -Credential $cred -Authentication Negotiate -ErrorAction Stop
    try {{
        $r = Invoke-Command -Session $s -FilePath '{_ps_quote(user_script_path)}' 2>&1
        $errs = @($r | Where-Object {{ $_ -is [System.Management.Automation.ErrorRecord] }})
        $oks = @($r | Where-Object {{ $_ -isnot [System.Management.Automation.ErrorRecord] }})
        if ($oks.Count) {{ $oks | Out-File -FilePath $stdout -Encoding utf8 }}
        if ($errs.Count) {{ $errs | Out-File -FilePath $stderr -Encoding utf8 }}
    }} finally {{
        Remove-PSSession $s -ErrorAction SilentlyContinue
    }}
    exit 0
}} catch {{
    $_.Exception.Message | Out-File -FilePath $stderr -Encoding utf8
    exit 1
}}
"""


def run_elevated(script: str, timeout: int = 60, computer: str = "localhost") -> dict:
    """Ejecuta un script PowerShell con las credenciales de ADMIN_USER /
    ADMIN_PASSWORD contra `computer` (remoto) o el propio equipo (local).

    Args:
        script: Script PowerShell a ejecutar.
        timeout: Segundos maximos antes de cancelar.
        computer: Host destino. Debe estar en ADMIN_ALLOWED_HOSTS.

    Returns:
        Dict con stdout, stderr, exit_code, has_error y elapsed_ms.
    """
    start = time.time()
    admin_user = os.environ.get("ADMIN_USER", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()

    if not admin_user or not admin_password:
        return _error("ADMIN_USER o ADMIN_PASSWORD no configurados en el entorno (ver .env).")

    computer = (computer or "localhost").strip()
    if not _host_is_allowed(computer):
        return _error(
            f"Host '{computer}' no autorizado. Anade la IP o su subred (p.ej. 192.0.2.0/24) "
            f"a ADMIN_ALLOWED_HOSTS en .env (permitidos: {', '.join(sorted(_allowed_hosts()))})."
        )

    stdout_path = new_temp_file("", suffix=".out.txt")
    stderr_path = new_temp_file("", suffix=".err.txt")
    user_path = new_temp_file(script, suffix=".user.ps1")
    wrapper_path = new_temp_file(
        build_session_script(computer, stdout_path, stderr_path, user_path)
    )

    try:
        proc = subprocess.run(
            [_PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", wrapper_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        stdout_content = read_if_exists(stdout_path)
        stderr_content = read_if_exists(stderr_path)
        # El stderr del proceso local se descarta a proposito: el wrapper solo
        # escribe en los temporales y su salida no es del script del usuario.
        return make_result(stdout_content, stderr_content, proc.returncode, elapsed_ms)
    except subprocess.TimeoutExpired as e:
        return make_result(
            e.stdout or "",
            f"Timeout: el comando excedio {timeout}s y se cancelo.",
            -1,
            int((time.time() - start) * 1000),
        )
    except OSError as e:
        return _error(
            f"Error ejecutando {_PS}: {e}. Verifica que Windows PowerShell este disponible.",
            int((time.time() - start) * 1000),
        )
    finally:
        for path in (wrapper_path, user_path, stdout_path, stderr_path):
            remove_quietly(path)
