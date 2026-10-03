# mcp_servers/powershell/ps_files.py
# Archivos temporales para ejecutar scripts PowerShell.
#
# Vive aparte, y no dentro de admin_runner, porque lo necesitan el server MCP
# (run_powershell_impl) y el propio admin_runner. server.py no puede importar
# admin_runner para esto: ese modulo importa dotenv, que no esta garantizado en
# el entorno del servicio (LocalSystem, sin user-site), y sin este modulo el
# server no podria ni escribir un script.
#
# Sin dependencias fuera de la stdlib a proposito.
import os
import tempfile

PREFIX = "psrun_"


def new_temp_file(content: str, suffix: str = ".ps1") -> str:
    """Escribe `content` en un temporal YA creado en disco y devuelve la ruta.

    Sustituye a tempfile.mktemp(), que solo devuelve un nombre sin crear el
    archivo: otro proceso podria insertar algo entre el nombre y el uso
    (TOCTOU), y estos archivos llevan scripts y contrasenas dentro.

    El contenido se escribe en UTF-8 CON BOM. Sin el BOM, PowerShell 5.1
    interpreta el .ps1 con la codicion ANSI del sistema y rompe los acentos.
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=PREFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\n") as f:
            f.write(content)
    except BaseException:
        remove_quietly(path)
        raise
    return path


def read_if_exists(path: str, encoding: str = "utf-8") -> str:
    """Lee el archivo, o devuelve "" si no esta. Nunca revienta: el destino
    puede no haberse creado si el script fallo antes de tocarlo."""
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    except OSError:
        return ""


def remove_quietly(path: str) -> None:
    """Borra el temporal sin propagar el fallo del borrado."""
    try:
        os.unlink(path)
    except OSError:
        pass


def make_result(stdout: str, stderr: str, exit_code: int, elapsed_ms: int) -> dict:
    """Construye el contrato de retorno de las tools de ejecucion.

    has_error NO es (exit_code != 0): un cmdlet con error no terminante
    (Get-Item sobre una ruta inexistente) deja exit_code 0 con stderr lleno, y
    el modelo leia ese 0 como exito. has_error se deriva de stderr, que es el
    unico sitio donde un error deja rastro.
    """
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "has_error": bool(stderr.strip()),
        "elapsed_ms": elapsed_ms,
    }
