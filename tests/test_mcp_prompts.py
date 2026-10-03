# tests/test_mcp_prompts.py
"""Tests para validar que los prompts de configuracion MCP son correctos."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_mcp_prompt_dia_no_es_diagramas():
    """El mcp_prompt de 'dia' NO debe referirse a diagramas."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    dia_prompt = data.get("mcp_prompts", {}).get("dia", "")

    assert dia_prompt, "mcp_prompts debe tener entrada para 'dia'"
    assert "diagrama" not in dia_prompt.lower(), (
        f"El prompt de 'dia' no debe referirse a diagramas. Valor actual:\n{dia_prompt}"
    )


def test_mcp_prompt_dia_incluye_categorias_reales():
    """El mcp_prompt de 'dia' debe mencionar categorias de herramientas reales."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    dia_prompt = data.get("mcp_prompts", {}).get("dia", "")

    # El prompt debe mencionar categorias de herramientas (no listar todas, ya que
    # el bloque <tools> las envia dinamicamente en cada turno)
    categorias_reales = ["web_search", "web_fetch", "comandos", "calcul"]
    encontradas = [c for c in categorias_reales if c in dia_prompt.lower()]

    assert len(encontradas) >= 2, (
        f"El prompt de 'dia' debe mencionar al menos 2 de {categorias_reales}. "
        f"Categorias encontradas: {encontradas}"
    )


def test_mcp_prompt_dia_incluye_get_time():
    """El mcp_prompt de 'dia' debe mencionar get_time como herramienta."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    dia_prompt = data.get("mcp_prompts", {}).get("dia", "")

    assert "get_time" in dia_prompt, (
        f"El prompt de 'dia' debe mencionar la herramienta get_time. Valor actual:\n{dia_prompt}"
    )


def test_system_prompt_incluye_instruccion_get_time_obligatoria():
    """El system_prompt debe instruir explicitamente usar get_time para fecha/hora."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    system_prompt = data.get("system_prompt", "")

    assert system_prompt, "El system_prompt no puede estar vacio"

    # Debe mencionar get_time explicitamente en el contexto de fecha/hora
    assert "get_time" in system_prompt, (
        f"El system prompt debe mencionar 'get_time'. Valor actual:\n{system_prompt[:500]}..."
    )

    # Debe tener una instruccion fuerte (obligatorio/sempre) sobre usar get_time para fecha/hora
    prompt_lower = system_prompt.lower()
    tiene_instruccion_obligatoria = any(
        keyword in prompt_lower for keyword in [
            "siempre usa get_time", "usa get_time", "oblgatorio", "nunca respondas sin"
        ]
    )

    assert tiene_instruccion_obligatoria, (
        f"El system prompt debe instruir usar get_time para fecha/hora con instruccion clara. Valor actual:\n{system_prompt[:500]}..."
    )


def test_mcp_prompts_no_tiene_servidores_desconocidos():
    """Los mcp_prompts deben solo tener servidores configurados en mcp-config.json."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))

    # Cargar mcp-config para comparar
    mcp_cfg_path = Path(data.get("mcp_config_path", ""))
    if not mcp_cfg_path.exists():
        pytest.skip("mcp-config.json no encontrado")

    with open(mcp_cfg_path, "r", encoding="utf-8") as f:
        mcp_config = json.load(f)

    configured_servers = set(mcp_config.get("mcpServers", {}).keys())
    prompt_servers = set(data.get("mcp_prompts", {}).keys())

    # Todos los servidores configurados deben tener un prompt (aunque sea default)
    missing_prompts = configured_servers - prompt_servers
    assert not missing_prompts, (
        f"Servidores MCP sin mcp_prompt definido: {missing_prompts}. "
        f"Prompt servers: {prompt_servers}"
    )


def test_format_tools_prompt_powershell_incluye_instrucciones_entorno():
    """El bloque del servidor "powershell" debe educar al modelo sobre el
    entorno (Windows/PowerShell) para que no genere comandos Linux (bug
    2026-09-15: python3 + /tmp -> exit 9009 -> loop sin respuesta)."""
    from llm_client import format_tools_prompt

    tools = [{
        "type": "function",
        "function": {"name": "run_powershell", "description": "Ejecuta un script PowerShell", "parameters": {}},
        "_mcp_server": "powershell",
    }]
    prompt = format_tools_prompt(tools, {})
    assert "## powershell" in prompt
    assert "Windows 11" in prompt
    assert "PowerShell 5.1" in prompt
    assert "Test-Connection" in prompt
    assert "python3" in prompt
    assert "/tmp/" in prompt
    # El modelo emitia <function=powershell> (nombre del servidor) en vez de
    # <function=run_powershell> (nombre de la tool) -> la app lo rechazaba y
    # el chat terminaba en respuesta vacia. El prompt debe fijar el nombre exacto.
    assert "run_powershell" in prompt
    assert "nunca uses 'powershell' como nombre de funcion" in prompt.lower()


def test_format_tools_prompt_powershell_incluye_analisis_equipo_remoto():
    """El bloque del servidor "powershell" debe educar al modelo sobre como
    analizar UN equipo concreto de la red (ping, puertos, conexiones) con
    run_powershell, y prohibir inventar resultados de red (bug 2026-09-23: el
    modelo fabrico un escaneo de 198.51.100.20 — ping, puertos 445/139,
    'acceso denegado' — sin llamar a ninguna tool, y admitio la simulacion al
    preguntarle por credenciales)."""
    from llm_client import format_tools_prompt

    tools = [{
        "type": "function",
        "function": {"name": "run_powershell", "description": "Ejecuta un script PowerShell", "parameters": {}},
        "_mcp_server": "powershell",
    }]
    prompt = format_tools_prompt(tools, {})
    # Analisis de un equipo concreto: puertos y conexiones activas.
    # Test-Connection solo cubre ping (ya existia para subnet scan); el caso
    # "analiza las conexiones del equipo X" requiere Test-NetConnection
    # (puertos) y Get-NetTCPConnection (conexiones), ejecutados desde el
    # equipo local SIN credenciales de la maquina remota.
    assert "Test-NetConnection" in prompt
    assert "Get-NetTCPConnection" in prompt
    # Anti-fabricacion: el modelo debe saber que NUNCA puede inventar datos de
    # red. Si no ejecuta la tool (o esta falla), debe decir que no tiene acceso.
    assert "no inventes" in prompt.lower() or "nunca inventes" in prompt.lower()
    assert "no tengo acceso" in prompt.lower()


def test_mcp_prompt_powershell_incluye_analisis_equipo_remoto():
    """El mcp_prompt de 'powershell' en config.json debe educar al modelo para
    analizar equipos remotos con run_powershell y prohibir inventar resultados
    de red (misma regla que el bloque de format_tools_prompt, bug 2026-09-23)."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    prompt = data.get("mcp_prompts", {}).get("powershell", "")

    assert prompt, "mcp_prompts debe tener entrada para 'powershell'"
    assert "Test-NetConnection" in prompt
    assert "Get-NetTCPConnection" in prompt
    assert "no inventes" in prompt.lower() or "nunca inventes" in prompt.lower()
    assert "no tengo acceso" in prompt.lower()


def test_el_prompt_avisa_de_que_exit_code_cero_no_es_exito():
    """Un cmdlet con error no terminante devuelve exit_code 0 con stderr lleno.

    Sin esta instruccion el modelo lee "exit_code: 0" y afirma exito sin mirar
    stderr (bug 2026-09-26: dio por buena la calculadora leyendo el bloque
    CLIXML de stderr como si fuera ruido).
    """
    from llm_client import format_tools_prompt

    tools = [{
        "type": "function",
        "function": {"name": "run_powershell", "description": "Ejecuta un script PowerShell", "parameters": {}},
        "_mcp_server": "powershell",
    }]
    prompt = format_tools_prompt(tools, {})
    assert "has_error" in prompt
    assert "exit_code" in prompt


def test_mcp_prompt_powershell_explica_has_error():
    """El mcp_prompt de 'powershell' en config.json debe llevar la misma regla:
    es el texto que la app inyecta delante del bloque de tools en cada turno."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    prompt = data.get("mcp_prompts", {}).get("powershell", "")

    assert "has_error" in prompt
    assert "exit_code" in prompt


def test_el_prompt_manda_usar_run_desktop_app():
    """El servicio corre en la sesion 0: Start-Process no abre nada y devuelve
    exito. Sin esta instruccion el LLM sigue usando Start-Process, ve exit_code 0
    y afirma al usuario que la calculadora se ha abierto (2026-09-26)."""
    from llm_client import format_tools_prompt

    tools = [{
        "type": "function",
        "function": {"name": "run_desktop_app", "description": "Abre una app", "parameters": {}},
        "_mcp_server": "powershell",
    }]
    prompt = format_tools_prompt(tools, {})
    assert "run_desktop_app" in prompt
    assert "Start-Process" in prompt or "start-process" in prompt.lower()


def test_mcp_prompt_powershell_manda_run_desktop_app():
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    prompt = data.get("mcp_prompts", {}).get("powershell", "")
    assert "run_desktop_app" in prompt


def test_system_prompt_incluye_anti_fabricacion_datos_red():
    """El system_prompt debe prohibir explicitamente inventar datos de red
    (ping, puertos, conexiones, procesos remotos), igual que LOCAL_RULES.md
    (anti-pattern critico, bug 2026-09-23: el modelo fabrico un escaneo de
    198.51.100.20 sin llamar a ninguna tool)."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    system_prompt = data.get("system_prompt", "")

    assert system_prompt, "El system_prompt no puede estar vacio"
    prompt_lower = system_prompt.lower()
    assert "no inventes" in prompt_lower or "nunca inventes" in prompt_lower
    assert "no tengo acceso" in prompt_lower
