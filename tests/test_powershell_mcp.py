# tests/test_powershell_mcp.py
# Unit tests del server MCP local "powershell" (mcp_servers/powershell/server.py).
# Se testean las impl puras (run_powershell_impl / run_python_impl) sin levantar
# el transporte MCP. Comandos reales triviales: echo, exit codes, timeout.
import subprocess
import sys
from pathlib import Path

import pytest

POWERSHELL_DIR = Path(__file__).resolve().parent.parent / "mcp_servers" / "powershell"
sys.path.insert(0, str(POWERSHELL_DIR))

import server  # noqa: E402
from mcp_clients import MCPClient  # noqa: E402


def test_server_arranca_sin_user_site_y_expone_tools():
    """Regresion bug 2026-09-18 (MCP powershell con punto rojo en la UI).

    El servicio Flask corre como LocalSystem, cuyo user-site no tiene `mcp`;
    por eso resuelve el `mcp 2.x` del env llama (que renombro FastMCP a
    MCPServer). Al ejecutar el server sin el user-site (flag -s) el proceso
    debe arrancar y exponer sus tools, no morir importando
    `mcp.server.fastmcp` (que solo existe en mcp 1.x).
    Se verifican las tools NOMINALES (no un conteo duro): el catalogo crece
    con cada feature (run_powershell_admin, run_disk_analysis), y un assert
    de numero exacto se quedaria obsoleto en cada commit.
    """
    path = POWERSHELL_DIR / "server.py"
    client = MCPClient(
        name="powershell",
        command=sys.executable,
        args=["-s", str(path)],
    )
    try:
        assert client.start() is True
        tool_names = {t.get("name") for t in client.tools}
        assert {"run_powershell", "run_python"} <= tool_names
    finally:
        client.stop()


def test_run_powershell_echo():
    result = server.run_powershell_impl("Write-Output 'hola'", timeout=30)
    assert result["exit_code"] == 0
    assert "hola" in result["stdout"]


def test_run_powershell_exit_code():
    result = server.run_powershell_impl("exit 3", timeout=30)
    assert result["exit_code"] == 3


def test_run_python_echo():
    result = server.run_python_impl("print(1+1)", timeout=30)
    assert result["exit_code"] == 0
    assert "2" in result["stdout"]


def test_run_powershell_timeout_mata_proceso():
    result = server.run_powershell_impl("Start-Sleep -Seconds 10", timeout=2)
    assert result["exit_code"] == -1
    assert "Timeout" in result["stderr"]


# --- CLIXML en stderr (bug 2026-09-26) -------------------------------------
#
# Con stderr redirigido a un pipe, el ConsoleHost de PowerShell 5.1 serializa
# el stream de error y el de progreso como CLIXML en vez de texto. El LLM
# recibia un bloque XML con escapes _x000D__x000A_ que no puede leer, pese a
# que el server promete en su docstring "lee stderr y corrige el script".


def test_run_powershell_stderr_vacio_si_no_hay_error():
    """Sin errores, stderr llega VACIO, no con el bloque de progreso CLIXML.

    El registro "Preparing modules for first use." salia en TODAS las llamadas
    (tambien en un simple Write-Output) porque el host no puede pintar la barra
    de progreso en un pipe: la serializa a stderr.
    """
    result = server.run_powershell_impl("Write-Output 'hola'", timeout=30)
    assert result["exit_code"] == 0
    assert result["stderr"] == ""


def test_run_powershell_errores_en_texto_plano():
    """Los errores de PowerShell deben llegar como texto legible, no como XML.

    Este es el caso que importa de verdad: si el script falla, el LLM tiene que
    poder leer POR QUE para corregirlo. Con CLIXML no puede.
    """
    result = server.run_powershell_impl(
        "Get-Item 'C:\\noexiste\\archivo.txt'", timeout=30
    )
    assert "CLIXML" not in result["stderr"]
    assert "_x000D__x000A_" not in result["stderr"]
    assert "Cannot find path" in result["stderr"]


def test_run_powershell_error_de_sintaxis_es_legible():
    """Un script con sintaxis rota tambien debe dar un mensaje legible.

    Aqui el fallo ocurre al parsear, antes de que la redireccion del error
    llegue a aplicarse, asi que el mensaje llega por el stderr del proceso.
    """
    result = server.run_powershell_impl("if ($true { Write-Output 'roto'", timeout=30)
    assert "CLIXML" not in result["stderr"]
    assert result["stderr"].strip() != ""
    assert "Unexpected token" in result["stderr"]


def test_run_powershell_script_con_llaves_no_se_parte():
    """Un script con llaves debe ejecutarse igual (no envolverlo en un
    ScriptBlock { } interpolado, que lo parte: bug ya visto en admin_runner)."""
    result = server.run_powershell_impl(
        "if ($true) {\n  Write-Output 'dentro'\n}\nWrite-Output 'fuera'", timeout=30
    )
    assert result["exit_code"] == 0
    assert "dentro" in result["stdout"]
    assert "fuera" in result["stdout"]
    assert result["stderr"] == ""


def test_run_powershell_error_no_rompe_stdout():
    """stdout y stderr van por separado: un error no debe tragarse la salida."""
    result = server.run_powershell_impl(
        "Write-Output 'antes'\nGet-Item 'C:\\noexiste\\archivo.txt'\nWrite-Output 'despues'",
        timeout=30,
    )
    assert "antes" in result["stdout"]
    assert "despues" in result["stdout"]
    assert "Cannot find path" in result["stderr"]


def test_run_powershell_no_filtra_la_ruta_del_temporal():
    """Los mensajes de PowerShell citan la ruta del .ps1 que se acaba de crear.

    Al LLM esa ruta no le aporta nada, le confunde (no existe ningun script con
    ese nombre) y de paso expone el nombre de usuario del equipo en cada error.
    Se sustituye por un marcador estable, conservando el numero de linea para
    que pueda localizar la sentencia.
    """
    result = server.run_powershell_impl("Get-Item 'C:\\noexiste\\archivo.txt'", timeout=30)
    assert "psrun_" not in result["stderr"]
    assert "AppData" not in result["stderr"]
    assert "<script>:1 char:1" in result["stderr"]
    assert "Cannot find path" in result["stderr"]


# --- has_error: exit_code 0 no significa exito (2026-09-26) -------------------
#
# Un cmdlet con error NO terminante (Get-Item sobre una ruta inexistente) deja
# stderr lleno y exit_code 0. El modelo veia "exit_code: 0" y daba el comando
# por bueno ("la calculadora se ha abierto correctamente") sin mirar stderr.


def test_run_powershell_has_error_true_aunque_el_exit_code_sea_cero():
    result = server.run_powershell_impl("Get-Item 'C:\\noexiste\\archivo.txt'", timeout=30)
    assert result["exit_code"] == 0, "el caso que motiva el test: exit_code miente"
    assert result["has_error"] is True


def test_run_powershell_has_error_false_cuando_todo_va_bien():
    result = server.run_powershell_impl("Write-Output 'hola'", timeout=30)
    assert result["exit_code"] == 0
    assert result["has_error"] is False


def test_run_python_tambien_devuelve_has_error():
    result = server.run_python_impl("import sys; sys.stderr.write('aviso\\n')", timeout=30)
    assert result["exit_code"] == 0
    assert result["has_error"] is True


def test_has_error_es_siempre_booleano_y_esta_presente():
    """Toda tool de ejecucion debe incluirlo: el LLM decide con esa bandera."""
    for caso in ("Write-Output 'ok'", "Get-Item 'C:\\noexiste\\a.txt'", "exit 4"):
        r = server.run_powershell_impl(caso, timeout=30)
        assert isinstance(r["has_error"], bool), caso
        assert r["has_error"] == bool(r["stderr"].strip()), caso


def test_el_docstring_de_las_tools_explica_has_error():
    """El docstring se convierte en la descripcion de la tool en el prompt. Si
    no explica la bandera, el modelo no tiene por que mirar stderr."""
    for doc in (server.run_powershell.__doc__, server.run_python.__doc__,
                server.run_powershell_admin.__doc__):
        assert "has_error" in doc, doc


# --- run_desktop_app (2026-09-26) -------------------------------------------
#
# El servicio VoiceWebUI corre como servicio NSSM -> LocalSystem -> sesion 0.
# Medido desde SYSTEM: Start-Process calc devuelve "OK, sin error" y no abre
# NINGUN proceso. Ninguna ventana puede mostrarse en el escritorio desde la
# sesion 0, y la calculadora es ademas una app Store por usuario, imposible
# desde el perfil de SYSTEM. La via que si funciona es el Task Scheduler con
# LogonType Interactive: verificado, CalculatorApp arranca en la sesion 1.


def test_run_desktop_app_rechacha_target_vacio():
    r = server.run_desktop_app_impl("", timeout=10)
    assert r["has_error"] is True
    assert "target" in r["stderr"]
    assert r["elapsed_ms"] < 5000, "no debe lanzar nada para un error de uso"


def test_run_desktop_app_rechacha_ejecutable_inexistente():
    r = server.run_desktop_app_impl("no_existe_este_programa_xyz", timeout=20)
    assert r["has_error"] is True
    assert "no encuentro" in r["stderr"].lower()


def test_run_desktop_app_verifica_el_proceso_antes_de_dar_exito():
    """El requisito de fondo: verificar el EFECTO, no el comando. Start-Process
    devuelve 0 sin abrir nada, asi que devolver exito por codigo de salida es
    repetir el bug que el usuario topo."""
    r = server.run_desktop_app_impl("calc", timeout=25)
    # No exigimos que se abra de verdad (depende de que haya sesion activa), pero
    # si el tool dice exito tiene que haber visto el proceso en la sesion del
    # usuario, y si no lo ha visto tiene que decirlo con has_error true.
    if r["has_error"] is False:
        assert "nuevo_proceso" in r["stdout"]
        assert "NINGUNO" not in r["stdout"], "exito sin ver el proceso: falso positivo"
    else:
        assert "sesion" in r["stdout"] or "sesion" in r["stderr"]


def test_run_desktop_app_informa_de_la_sesion_del_usuario():
    """El usuario interactivo se descubre del owner de explorer.exe: el tool no
    puede hardcodear '<usuario>' ni asumir que hay sesion activa."""
    r = server.run_desktop_app_impl("calc", timeout=25)
    salida = r["stdout"] + r["stderr"]
    assert "sesion" in salida.lower()


def test_el_docstring_de_run_desktop_app_explica_la_sesion_0():
    """El docstring va al prompt: si no explica por que existe esta tool, el
    modelo la ignora y sigue usando Start-Process."""
    doc = (server.run_desktop_app.__doc__ or "").lower()
    assert "sesion 0" in doc or "sesión 0" in doc
    assert "start-process" in doc
    assert "has_error" in doc


# --- run_disk_analysis (2026-09-26) -----------------------------------------
#
# El docstring prometia "CIM/WMI ... sin recorrer recursivamente cada archivo".
# Mentia: el script hacia Get-ChildItem -Recurse por cada carpeta. Medido en
# C:\Users: 122 GB en 43 s. Ademas max_depth se aceptaba y se ignoraba en
# silencio, asi que un LLM que pide profundidad 1 se comia la unidad entera.


def _tamano(stdout: str) -> dict:
    """Parsea el CSV 'nombre<TAB>bytes<TAB>humano' que emite la tool."""
    filas = {}
    for linea in stdout.strip().splitlines():
        partes = linea.split("\t")
        if len(partes) == 3 and partes[1].isdigit():
            filas[partes[0]] = int(partes[1])
    return filas


def test_run_disk_analysis_rechaza_max_depth_menor_de_uno():
    """Antes se aceptaba y se ignoraba. Con 0 el script generaba un depth sin
    sentido en silencio; ahora debe explicar el rango valido."""
    r = server.run_disk_analysis(drive="C:", max_depth=0, timeout=30)
    assert r["has_error"] is True
    assert "max_depth" in r["stderr"]
    assert r["elapsed_ms"] < 5000, "no debe lanzar PowerShell para un error de uso"


def test_run_disk_analysis_rechaza_drive_vacio():
    r = server.run_disk_analysis(drive="   ", timeout=30)
    assert r["has_error"] is True
    assert "drive" in r["stderr"]


def test_run_disk_analysis_calcula_tamanos_exactos(tmp_path):
    """.NET con stack coincide con Get-ChildItem al byte (verificado en
    C:\\Users: 122.295.318.128 vs 122.295.312.925, 5 KB de archivos que cambian
    durante la medicion). Este test fija el total en un arbol congelado."""
    (tmp_path / "datos.bin").write_bytes(b"x" * 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.bin").write_bytes(b"y" * 2000)
    (sub / "b.bin").write_bytes(b"z" * 500)

    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, timeout=60)
    assert r["has_error"] is False, r["stderr"]
    filas = _tamano(r["stdout"])
    assert filas["sub"] == 2500
    assert filas[".archivos_raiz"] == 1000


def test_run_disk_analysis_usa_ruta_relativa_y_no_el_nombre_suelto(tmp_path):
    """Dos carpetas homonimas colapsarian en la misma clave si la salida fuera el
    nombre suelto (Split-Path -Leaf). Con max_depth=2, p1\\hoja y p2\\hoja salen
    las dos: son claves distintas y cada fila se puede localizar sin ambiguedad."""
    for padre in ("p1", "p2"):
        (tmp_path / padre / "hoja").mkdir(parents=True)
    (tmp_path / "p1" / "hoja" / "x.bin").write_bytes(b"x" * 700)
    (tmp_path / "p2" / "hoja" / "y.bin").write_bytes(b"y" * 300)

    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=2, timeout=60)
    assert r["has_error"] is False, r["stderr"]
    filas = _tamano(r["stdout"])
    assert filas["p1\\hoja"] == 700, filas
    assert filas["p2\\hoja"] == 300, filas
    assert "hoja" not in filas, "el nombre suelto colapsaria las dos en una clave"


def test_run_disk_analysis_max_depth_acota_las_carpetas_listadas(tmp_path):
    """max_depth=1 solo debe listar las carpetas de primer nivel. Antes el
    parametro no hacia nada y por eso 'D:' agotaba los 300 s de timeout."""
    nivel1 = tmp_path / "nivel1"
    nivel1.mkdir()
    (nivel1 / "a.bin").write_bytes(b"x" * 100)
    nivel2 = nivel1 / "nivel2"
    nivel2.mkdir()
    (nivel2 / "b.bin").write_bytes(b"y" * 200)

    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, timeout=60)
    filas = _tamano(r["stdout"])
    assert "nivel1" in filas
    assert "nivel2" not in filas, "max_depth=1 no debe listar carpetas de segundo nivel"
    assert filas["nivel1"] == 300, "el tamano de nivel1 si cubre su subarbol"


def test_run_disk_analysis_no_sigue_junctions_circulares(tmp_path):
    """El directorio Temp\\pytest-of-<usuario>\\pytest-current de pytest
    es un junction que apunta a su propio padre. Un recorrido que lo siga se
    cuelga (medido: 343 s y 221 GB, casi el doble, siguiendo el ciclo)."""
    raiz = tmp_path / "raiz"
    real = raiz / "real"
    real.mkdir(parents=True)
    (real / "dato.bin").write_bytes(b"x" * 4096)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(real / "loop"), str(raiz)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"no se pudo crear el junction: {proc.stderr.strip()}")

    r = server.run_disk_analysis(drive=str(raiz), max_depth=1, timeout=90)
    assert r["has_error"] is False, r["stderr"]
    filas = _tamano(r["stdout"])
    assert filas["real"] == 4096, "el junction no debe sumar sus propios archivos"


def test_run_disk_analysis_ruta_inexistente_es_error_de_verdad(tmp_path):
    """Regresion 2026-09-26: el script de disco escribia el error con
    Write-Output (stdout) y salia con codigo 1. Como has_error se deriva de
    stderr, el tool devolvia has_error false con un mensaje de error en stdout:
    exactamente el falso positivo que has_error deberia evitar."""
    r = server.run_disk_analysis(drive=str(tmp_path / "no_existe"), max_depth=1, timeout=60)
    assert r["has_error"] is True
    assert "no existe" in r["stderr"]
    assert r["stdout"] == ""


def test_run_disk_analysis_camino_correcto_no_deja_basura_en_stderr(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.bin").write_bytes(b"x" * 10)
    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, timeout=60)
    assert r["stderr"] == ""
    assert r["has_error"] is False


def test_el_docstring_de_run_disk_analysis_no_menta_sobre_cim():
    """El docstring va al prompt del LLM. Afirmar un metodo que no se usa hace
    que el modelo pidametricas que la tool no da."""
    doc = (server.run_disk_analysis.__doc__ or "").lower()
    assert "cim" not in doc
    assert "wmi" not in doc
    assert "has_error" in doc


# --- top_n para run_disk_analysis (2026-09-26) -------------------------------
# El LLM recibe todo el stdout del tool. Sin top_n, una unidad grande devuelve
# miles de filas de carpetas y el modelo no sabe filtrar. top_n limita a las N
# mas grandes.


def test_run_disk_analysis_top_n_0_sin_limite(tmp_path):
    """top_n=0 (default) = sin limite: devuelve todas las carpetas."""
    for i in range(5):
        (tmp_path / f"dir{i}").mkdir()
        (tmp_path / f"dir{i}" / "f.bin").write_bytes(b"x" * (100 + i * 10))
    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, top_n=0, timeout=60)
    assert r["has_error"] is False
    # Debe haber 5 carpetas + 1 total
    folder_lines = [l for l in r["stdout"].splitlines() if l and not l.startswith("total")]
    assert len(folder_lines) == 5


def test_run_disk_analysis_top_n_limita_a_n(tmp_path):
    """top_n=2 devuelve solo las 2 carpetas mas grandes, ordenadas descendente."""
    for i in range(5):
        (tmp_path / f"dir{i}").mkdir()
        (tmp_path / f"dir{i}" / "f.bin").write_bytes(b"x" * (100 + i * 10))
    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, top_n=2, timeout=60)
    assert r["has_error"] is False
    folder_lines = [l for l in r["stdout"].splitlines() if l and not l.startswith("total")]
    assert len(folder_lines) == 2
    # Orden descendente por tamano (columna 1 = bytes)
    sizes = [int(l.split("\t")[1]) for l in folder_lines]
    assert sizes == sorted(sizes, reverse=True)


def test_run_disk_analysis_top_n_invalido_es_error(tmp_path):
    """top_n negativo o no entero produce has_error true."""
    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, top_n=-1, timeout=60)
    assert r["has_error"] is True
    assert "top_n" in r["stderr"]


def test_run_disk_analysis_top_n_mayor_que_resultados_devuelve_todo(tmp_path):
    """top_n=100 con solo 3 carpetas devuelve las 3."""
    for i in range(3):
        (tmp_path / f"dir{i}").mkdir()
        (tmp_path / f"dir{i}" / "f.bin").write_bytes(b"x" * 50)
    r = server.run_disk_analysis(drive=str(tmp_path), max_depth=1, top_n=100, timeout=60)
    assert r["has_error"] is False
    folder_lines = [l for l in r["stdout"].splitlines() if l and not l.startswith("total")]
    assert len(folder_lines) == 3