# tests/test_powershell_admin.py
# Unit tests de mcp_servers/powershell/admin_runner.py
#
# Contexto (bug 2026-09-26): run_elevated no podia alcanzar ningun equipo
# remoto. Motivos reales encontrados leyendo el codigo:
#   1. New-PSSession usaba -ComputerName localhost fijo (no habia parametro
#      de host), asi que era imposible apuntar a otro equipo.
#   2. Se leia ADMIN_PASSWORD y NUNCA se usaba: la sesion iba con
#      -Authentication Negotiate (identidad del proceso Flask = LocalSystem),
#      no con las credenciales declaradas. El docstring afirmaba lo contrario.
# Estos tests fijan el comportamiento correcto sin tocar la red: se verifica
# el SCRIPT que se generaria y las guardas, no la ejecucion remota real.
#
# Las IP de los allowlist y de las subredes son de los rangos de documentacion
# de RFC 5737 (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24). No es
# cosmetico: una IP privada en un test dice que subred tiene la gente que lo
# escribio. La aritmetica CIDR que se comprueba aqui es identica con estos
# rangos, porque lo que importa es que dos /24 distintos no se solapen.
import sys
from pathlib import Path

import pytest

POWERSHELL_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "powershell"
sys.path.insert(0, str(POWERSHELL_DIR))

import admin_runner  # noqa: E402


@pytest.fixture(autouse=True)
def _credenciales_fijas(monkeypatch):
    """Cada test parte de credenciales y allowlist conocidas."""
    monkeypatch.setenv("ADMIN_USER", "DOMINIO\\administrador")
    monkeypatch.setenv("ADMIN_PASSWORD", "secreto-de-prueba")
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "localhost,192.0.2.50")


# --- Guards -----------------------------------------------------------------

def test_sin_credenciales_devuelve_error_claro(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    r = admin_runner.run_elevated("Get-Date")
    assert r["exit_code"] == -1
    assert "ADMIN_USER" in r["stderr"]


def test_host_fuera_de_allowlist_se_rechaza_sin_ejecutar():
    """El LLM tiene credenciales de admin de dominio: sin allowlist podria
    escanear la red con ellas. El rechazo debe ser ANTES de tocar la red."""
    r = admin_runner.run_elevated("Get-Date", computer="203.0.113.9")
    assert r["exit_code"] == -1
    assert "203.0.113.9" in r["stderr"]
    assert "ADMIN_ALLOWED_HOSTS" in r["stderr"]


def test_allowlist_vacia_por_defecto_solo_permite_localhost(monkeypatch):
    """Default seguro: sin ADMIN_ALLOWED_HOSTS solo localhost."""
    monkeypatch.delenv("ADMIN_ALLOWED_HOSTS", raising=False)
    r = admin_runner.run_elevated("Get-Date", computer="192.0.2.50")
    assert r["exit_code"] == -1
    assert "ADMIN_ALLOWED_HOSTS" in r["stderr"]


def test_allowlist_no_es_sensible_a_mayusculas_ni_espacios(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "  192.0.2.50 ,  203.0.113.10 ")
    assert admin_runner._allowed_hosts() == {"192.0.2.50", "203.0.113.10"}


# --- Subredes CIDR en la allowlist -----------------------------------------
# ADMIN_ALLOWED_HOSTS es un lista de Host objects, no un string: acepta tanto
# IPs sueltas como redes en notacion CIDR. Antes solo comparaba por igualdad
# exacta, asi que "192.0.2.0/24" solo habria admitido un equipo llamado
# literalmente "192.0.2.0/24" y ninguna subred se podia autorizar.

def test_ip_exacta_sigue_siendo_valida(monkeypatch):
    """El modo anterior no puede romperse: IPs sueltas y 'localhost'."""
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "localhost,192.0.2.50")
    assert admin_runner._host_is_allowed("192.0.2.50")
    assert admin_runner._host_is_allowed("localhost")
    assert not admin_runner._host_is_allowed("192.0.2.51")


def test_host_dentro_de_la_subred_se_permite(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "192.0.2.0/24")
    for host in ("192.0.2.1", "192.0.2.40", "192.0.2.168", "192.0.2.255"):
        assert admin_runner._host_is_allowed(host), host


def test_host_fuera_de_la_subred_se_rechaza(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "192.0.2.0/24")
    for host in ("198.51.100.40", "203.0.113.5", "198.51.100.0"):
        assert not admin_runner._host_is_allowed(host), host


def test_la_comparacion_es_por_red_y_no_por_prefijo_de_texto(monkeypatch):
    """'198.51.100.0/24' NO puede admitir 192.0.2.x. Un fallo aqui seria
    abrir la red entera por un simple startswith."""
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "198.51.100.0/24")
    assert admin_runner._host_is_allowed("198.51.100.77")
    assert not admin_runner._host_is_allowed("192.0.2.77")


def test_se_pueden_combinar_ips_y_subredes(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "localhost,192.0.2.0/24,198.51.100.0/24")
    assert admin_runner._host_is_allowed("localhost")
    assert admin_runner._host_is_allowed("192.0.2.40")
    assert admin_runner._host_is_allowed("198.51.100.4")
    assert not admin_runner._host_is_allowed("203.0.113.4")


def test_cidr_mal_formado_no_revienta_el_proceso(monkeypatch):
    """Un typo en la allowlist no puede tumbar la herramienta: se trata como
    literal y se sigue operando con el resto."""
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "192.0.2.0/banana,localhost")
    assert admin_runner._host_is_allowed("localhost")
    assert not admin_runner._host_is_allowed("192.0.2.4")


def test_un_hostname_no_se_deduce_de_una_subred(monkeypatch):
    """Las subredes son de IPs: un nombre no se resuelve ni se acepta."""
    monkeypatch.setenv("ADMIN_ALLOWED_HOSTS", "192.0.2.0/24")
    assert not admin_runner._host_is_allowed("servidor-cualquiera")
    assert not admin_runner._host_is_allowed("")


def test_env_de_usuario_debe_usar_comillas_simples(tmp_path):
    """Trampa real encontrada el 2026-09-26: python-dotenv interpreta escapes
    DENTRO de comillas dobles. "DOMINIO\\administrador" convertia \\a en BEL
    (0x07), el usuario real era 'DOMINIO\\x07dministrador' y WinRM respondia
    "Acesso negado". Este test fija la convencion del .env para que nadie
    vuelva a poner comillas dobles.
    """
    from dotenv import dotenv_values

    p = tmp_path / ".env"
    p.write_text(
        'DOBLE="DOMINIO\\administrador"\n'
        "SIMPLE='DOMINIO\\administrador'\n",
        encoding="utf-8",
    )
    v = dotenv_values(p)
    assert v["DOBLE"] != "DOMINIO\\administrador", (
        "python-dotenv deja de escapar; revisa si la convencion sigue siendo necesaria"
    )
    assert v["SIMPLE"] == "DOMINIO\\administrador"


# --- Generacion del script: la parte que estaba mal ------------------------

def test_script_usa_el_host_indicado():
    s = admin_runner.build_session_script(
        "192.0.2.50", "C:\\tmp\\out.txt", "C:\\tmp\\err.txt", "C:\\tmp\\user.ps1"
    )
    assert "-ComputerName '192.0.2.50'" in s


def test_script_crea_pscredential_con_las_credenciales_de_entorno(monkeypatch):
    """Este es EL bug: antes se leia ADMIN_PASSWORD y se descartaba."""
    s = admin_runner.build_session_script(
        "192.0.2.50", "C:\\tmp\\out.txt", "C:\\tmp\\err.txt", "C:\\tmp\\user.ps1"
    )
    # Los comentarios del wrapper explican por que no se usa el cmdlet; hay que
    # mirarlos aparte para no confundir la documentacion con el codigo.
    code = "\n".join(l for l in s.splitlines() if not l.strip().startswith("#"))
    # SecureString/PSCredential via .NET, no ConvertTo-SecureString: ese cmdlet
    # requiere Microsoft.PowerShell.Security, que no carga con PSModulePath raro.
    assert "System.Security.SecureString" in code
    assert "ConvertTo-SecureString" not in code
    assert "secreto-de-prueba" in s
    assert "DOMINIO\\administrador" in s
    # Y la sesion debe usar -Credential (no Negotiate sin credenciales)
    assert "-Credential" in s


def test_script_usa_invoke_command_filepath_y_no_interpola_braces():
    """El script del usuario va en un archivo aparte y se ejecuta con
    -FilePath: si se interpolase dentro de un ScriptBlock{ }, una llave del
    script usuario cerraria el bloque y corromperia el wrapper."""
    user = "if ($true) { Write-Output 'ok' } }"
    s = admin_runner.build_session_script(
        "192.0.2.50", "C:\\tmp\\out.txt", "C:\\tmp\\err.txt", "C:\\tmp\\user.ps1"
    )
    assert "-FilePath 'C:\\tmp\\user.ps1'" in s
    # Ni el contenido del usuario ni sus llaves sueltas tocan el wrapper
    assert user not in s
    assert "} }" not in s


def test_script_escapa_comillas_en_rutas():
    """Rutas con comilla simple romperian el wrapper de PowerShell."""
    s = admin_runner.build_session_script(
        "192.0.2.50", "C:\\it's\\out.txt", "C:\\it's\\err.txt", "C:\\it's\\user.ps1"
    )
    assert "it''s" in s
