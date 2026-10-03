# tests/test_publicar_sin_secretos.py
# Guardas de publicacion: el codigo que se entrega no puede contener datos que
# identifiquen la maquina o la red donde se desarrollo.
#
# Por que existe: el 2026-09-05 se hizo una revision que arrastro las IPs
# locales de los ficheros trackeados y la limpieza no se sostuvo. Volvieron a
# colarse en comentarios, en tests y en el script de instalacion del servicio.
# Un informe puntual no previene nada; esto si.
#
# Decisiones de diseno que importan:
#
# 1. Las reglas se expresan por PATRON, no por lista de valores prohibidos.
#    Una lista de strings prohibidos tendria que contener las cadenas
#    prohibidas, asi que el propio test seria la fuga que dice evitar.
#    "Ninguna IP de RFC1918" no necesita saber cual era la IP real.
#
# 2. Solo se vigila la superficie de CODIGO (src, static, mcp_servers, tests y
#    la raiz). `docs/sesiones/` es el diario de trabajo interno y contiene
#    datos de la red por naturaleza: no es un descuido, es su contenido. Que
#    docs salgan al repo publico se decide al construir la rama, no aqui.
#
# 3. Se admiten los rangos de documentacion de RFC 5737 (192.0.2.0/24,
#    198.51.100.0/24, 203.0.113.0/24) y loopback. Los ejemplos de CIDR que
#    explican que dos subredes contiguas no se solapan son correctos y no dicen
#    nada de ninguna red real, pero un test no puede distinguirlos de una
#    topologia verdadera: se cambian a rangos de documentacion.
import ipaddress
import re
from pathlib import Path

# Raices cuyo contenido se entrega con el codigo.
_SUPERVISADO = ("src", "static", "mcp_servers", "tests")

# Ficheros de la raiz que tambien se publican.
_SUPERVISADOS_RAIZ = (
    ".env.example",
    "config.example.json",
    "setup-service.ps1",
    "README.md",
    "AGENTS.md",
    "LOCAL_RULES.md",
)

# Extensiones que se leen como texto. Los binarios (png, pdf) quedan fuera:
# un grep no los revisa y solo darian falsos negativos silenciosos.
_EXT_TEXTO = {".py", ".js", ".html", ".css", ".json", ".md", ".ps1", ".txt", ".example", ""}

# Nombres de usuario Windows que son placeholders, no personas.
_PERFILES_PERMITIDOS = {"usuario", "user", "tester", "nombre", "<user>", "<usuario>"}

# Tokens de dominio que son placeholders, no organizaciones.
_DOMINIOS_PERMITIDOS = {"dominio", "ejemplo", "example", "contoso", "miempresa",
                        "mi-dominio", "otrodom"}

_RE_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_RE_PERFIL_WINDOWS = re.compile(r"[A-Za-z]:\\Users\\([^\\\s\"'/$]+)", re.IGNORECASE)
_RE_DOMINIO_LOCAL = re.compile(r"\b([a-z0-9][a-z0-9-]{2,})\.local\b", re.IGNORECASE)

# Cuenta con dominio, en las dos formas que aparecen en codigo: UPN
# (usuario@DOMINIO, tipico de Active Directory) y DOMINIO\usuario (la que
# espera Autenticacion Negotiate).
#
# El token de dominio exige un punto a proposito. Sin esa exigencia el patron
# casa con cualquier ruta de Windows (`C:\WINDOWS\system32`), con variables de
# PowerShell (`$LOGS\service.log`) y con los escapes `\n` de los literales de
# Python, y un guard que se queja de eso es un guard que nadie va a escuchar.
_RE_UPN = re.compile(r"\b([a-z0-9][a-z0-9._-]*)@([A-Z][A-Z0-9]{2,}(?:\.[A-Z0-9]+)?)\b")
_RE_CUENTA_DOMINIO = re.compile(r"\b([A-Z][A-Z0-9]{2,}\.[A-Za-z0-9]+)\\([a-z][a-z0-9_-]{2,})\b")
_RE_TOKEN_GITHUB = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")
# esquema://usuario:clave@host. Una URI de conexion con credenciales dentro es
# una credencial filtrada, y pasa desapercibida en revision porque no parece
# un secreto: parece una URL.
_RE_URI_CON_CLAVE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@", re.IGNORECASE)

_RE_NOMBRE_SECRETO = re.compile(r"(?i)(password|secret|token|api_?key|contrasena)")

_RAIZ = Path(__file__).resolve().parent.parent


def _ficheros_de_codigo():
    """Rutas relativas al repo de todo el texto que se entrega."""
    encontrados = []
    for sub in _SUPERVISADO:
        base = _RAIZ / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in _EXT_TEXTO:
                encontrados.append(p.relative_to(_RAIZ).as_posix())
    for nombre in _SUPERVISADOS_RAIZ:
        if (_RAIZ / nombre).is_file():
            encontrados.append(nombre)
    return encontrados


def _lineas_sospechosas(patron, predicado, ficheros=None):
    """[(ruta, linea, texto)] donde el patron casa y el predicado marca el
    hallazgo como ilecito. El patron puede ser amplio (cualquier IP) y la
    regla de negocio vive en el predicado."""
    hallazgos = []
    for rel in (ficheros if ficheros is not None else _ficheros_de_codigo()):
        texto = (_RAIZ / rel).read_text(encoding="utf-8", errors="replace")
        for n, linea in enumerate(texto.splitlines(), start=1):
            for m in patron.finditer(linea):
                if predicado(m):
                    fragmentos = linea.strip()
                    if len(fragmentos) > 120:
                        fragmentos = fragmentos[:120]
                    hallazgos.append(f"{rel}:{n}: {fragmentos}")
    return hallazgos


def _es_ipv4_valida(m):
    return all(int(o) <= 255 for o in m.groups())


def _es_ip_publicable(direccion):
    """Loopback, enlace local, unspecified y rangos de documentacion: todo lo
    demas (RFC1918 sobre todo) identifica una red real."""
    ip = ipaddress.ip_address(direccion)
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return True
    return any(ip in ipaddress.ip_network(n) for n in
               ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


# ---------------------------------------------------------------------------
# 1. Direcciones IP
# ---------------------------------------------------------------------------
def test_no_ips_de_red_privada_en_el_codigo_que_se_entrega():
    """Ninguna IP de RFC1918 (10/8, 172.16/12, 192.168/16) en el codigo.

    Una IP privada en un comentario o en un test no parece un secreto, pero
    publicada dibuja donde vive cada servicio (LLM, memoria, servidores).
    Los rangos de documentacion de RFC 5737 existen justo para esto.
    """
    def _privada(m):
        return _es_ipv4_valida(m) and not _es_ip_publicable(m.group(0))

    hallados = _lineas_sospechosas(_RE_IPV4, _privada)
    assert not hallados, (
        "IPs de red privada en el codigo entregado. Usa localhost, un "
        "placeholder entre <>, o un rango de documentacion RFC 5737 "
        "(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24):\n  "
        + "\n  ".join(hallados)
    )


def test_los_ficheros_de_ejemplo_no_apuntan_a_ninguna_ip_de_red():
    """`config.example.json` y `.env.example` son los dos ficheros que alguien
    copia tal cual. Si apuntan a una IP real, el despliegue de quien copia
    acaba hablando con la maquina de otro."""
    hallados = []
    for nombre in (".env.example", "config.example.json"):
        texto = (_RAIZ / nombre).read_text(encoding="utf-8")
        for m in _RE_IPV4.finditer(texto):
            if _es_ipv4_valida(m) and not _es_ip_publicable(m.group(0)):
                hallados.append(f"{nombre}: {m.group(0)}")
    assert not hallados, (
        "los ficheros de ejemplo apuntan a IPs de red reales: " + ", ".join(hallados)
    )


# ---------------------------------------------------------------------------
# 2. Rutas del sistema de ficheros
# ---------------------------------------------------------------------------
def test_no_rutas_de_perfil_de_usuario_windows_reales():
    """`C:\\Users\\<alguien>` es la huella del equipo que desarrollo el
    proyecto. En `setup-service.ps1` ademas es un bug: el servicio no arranca
    en otra maquina porque el PATH apunta a un perfil que no existe."""
    def _real(m):
        return m.group(1).lower() not in _PERFILES_PERMITIDOS

    hallados = _lineas_sospechosas(_RE_PERFIL_WINDOWS, _real)
    assert not hallados, (
        "rutas de perfil de usuario de Windows en el codigo. Usa "
        "$env:APPDATA / %APPDATA% o un placeholder:\n  " + "\n  ".join(hallados)
    )


# ---------------------------------------------------------------------------
# 3. Dominios y cuentas de dominio
# ---------------------------------------------------------------------------
def test_no_dominios_ldap_ni_cuentas_de_dominio_reales():
    """El dominio de Active Directory y la forma DOMINIO\\usuario aparecen en
    el codigo y en los tests. Identifican la organizacion y dan el formato
    exacto con el que hay que escribir una cuenta para Autenticacion
    Negotiate."""
    def _dominio_real(m):
        return m.group(1).lower() not in _DOMINIOS_PERMITIDOS

    def _upn_real(m):
        """En un UPN el grupo 1 es el usuario y el 2 el dominio: al reves que
        en DOMINIO\\usuario, y mezclar los dos hace que el propio test se
        queje de los placeholders que el permitir."""
        return (m.group(2).lower() not in _DOMINIOS_PERMITIDOS
                and m.group(1).lower() not in _PERFILES_PERMITIDOS)

    def _cuenta_real(m):
        return (m.group(1).lower() not in _DOMINIOS_PERMITIDOS
                and m.group(2).lower() not in _PERFILES_PERMITIDOS)

    hallados = _lineas_sospechosas(_RE_DOMINIO_LOCAL, _dominio_real)
    hallados += _lineas_sospechosas(_RE_UPN, _upn_real)
    hallados += _lineas_sospechosas(_RE_CUENTA_DOMINIO, _cuenta_real)
    assert not hallados, (
        "dominios o cuentas de dominio reales en el codigo entregado:\n  "
        + "\n  ".join(hallados)
    )


# ---------------------------------------------------------------------------
# 4. Credenciales
# ---------------------------------------------------------------------------
def test_no_tokens_de_github_en_el_codigo():
    """Un PAT de GitHub embebido en un remoto o en un doc permite escribir en
    el repo con los permisos del dueno. Si alguna vez se escribio, hay que
    revocarlo: quitar el fichero despues no basta."""
    hallados = _lineas_sospechosas(_RE_TOKEN_GITHUB, lambda m: True)
    assert not hallados, (
        "tokens de GitHub en el codigo entregado. Revocalos aunque los "
        "quites del fichero:\n  " + "\n  ".join(hallados)
    )


def test_no_credenciales_dentro_de_una_uri_de_conexion():
    """La clave va por entorno, no dentro de la URL."""
    def _con_clave(m):
        return m.group(1).lower() not in {
            "changeme", "change-me", "password", "secret", "x", "clave",
        }

    hallados = _lineas_sospechosas(_RE_URI_CON_CLAVE, _con_clave)
    assert not hallados, (
        "credenciales dentro de una URI en el codigo entregado:\n  "
        + "\n  ".join(hallados)
    )


def test_el_ejemplo_de_entorno_no_trae_secretos_con_valor():
    """`.env.example` se copia a `.env` y de ahi lo lee el proceso. Un valor
    real en el ejemplo acaba en produccion sin que nadie lo escribiera."""
    texto = (_RAIZ / ".env.example").read_text(encoding="utf-8")
    vacios_ok = {"", "change-me-in-production", "cambia-esta-clave"}
    hallados = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        if linea.strip().startswith("#") or "=" not in linea:
            continue
        nombre, _, valor = linea.partition("=")
        if not _RE_NOMBRE_SECRETO.search(nombre):
            continue
        if valor.strip().strip("'\"") not in vacios_ok:
            hallados.append(f".env.example:{n}: {nombre.strip()}")
    assert not hallados, (
        "`.env.example` trae secretos con valor. Deja la variable vacia:\n  "
        + "\n  ".join(hallados)
    )
