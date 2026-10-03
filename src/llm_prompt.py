import json

from collections import defaultdict

_REASONING_MARKERS = set(">")

MAX_PROMPT_TOKENS = 49152

def estimate_prompt_tokens(messages: list, tools: list | None = None) -> int:
    """Estima los tokens del prompt sin tokenizador local.

    Suma los chars del JSON serializado de messages + tools y divide por 3
    (conservador: el JSON real tokeniza ~3.88 chars/token, medido en el bug
    2026-09-24 con 258K tokens ~= 1MB). Sobre-estimar es seguro: el guard
    corta ANTES de que llama.cpp rechace con 400.

    Herramientas incluidas porque viajan en el payload JSON y tambien consumen
    contexto (el system prompt de texto de la app con 90 tools son ~7000
    tokens). Funcion pura, testeable.
    """
    if not messages and not tools:
        return 0
    try:
        text = json.dumps({"messages": messages, "tools": tools or []}, ensure_ascii=False)
    except (TypeError, ValueError):
        return 0
    return len(text) // 3


class PromptTooLargeError(ValueError):
    """El prompt estimado supera MAX_PROMPT_TOKENS.

    Se lanza ANTES de llamar a llama.cpp (defensa en profundidad): en lugar de
    recibir el 400 opaco "exceeds the available context size" y perder la
    conversacion, el usuario recibe un error claro con el limite y la sugerencia
    de iniciar una conversacion nueva.
    """


def _filter_reasoning_markers(text: str) -> str:
    """Elimina marcadores de razonamiento que se filtran al campo content.

    Qwen 3.x con --reasoning on a veces emite marcadores de blockquote ('>') en
    el campo content del delta, cuando en realidad pertenecen al reasoning_content.
    Estos marcadores NO deben llegar al usuario.

    Se eliminan TODAS las ocurrencias de '>' del texto, no solo las lineas
    completas, porque el marcador puede aparecer en cualquier posicion (p. ej.
    "texto > mas texto"). El '>' no tiene significado valido en respuestas normales
    del asistente.
    """
    if not text:
        return text
    return "".join(c for c in text if c not in _REASONING_MARKERS)


def format_tools_prompt(tools: list[dict], mcp_prompts: dict[str, str] | None = None, resources: list[dict] | None = None) -> str:
    """Formatea las tools y resources en texto plano para inyectarlas en el system prompt.

    Las tools se agrupan por servidor MCP (atributo _mcp_server). Para cada
    grupo se genera un bloque con:
    - El prompt de instrucciones del servidor (de mcp_prompts o default)
    - La lista de herramientas con nombre, descripcion y schema
    
    Los resources se incluyen al final del prompt con su URI y descripcion.

    Esto es necesario porque llama.cpp rompe con el thinking de Qwen 3.x cuando
    las tools viajan en el payload JSON; al inyectarlas en el prompt como texto
    plano, el modelo las conoce y genera los XML de tool calling correctamente.
    """
    blocks = []
    
    # Incluir resources si existen
    if resources:
        blocks.append("## Recursos MCP\n\nRecursos disponibles para consulta:\n")
        for res in resources:
            uri = res.get("uri", "unknown")
            name = res.get("name", "")
            desc = res.get("description", "")
            block = f"- **{uri}**: {name or desc}\n"
            blocks.append(block)
        blocks.append("")  # Espacio en blanco entre secciones
    
    if not tools:
        return "\n\n".join(blocks) if blocks else ""

    # Agrupar tools por servidor
    grouped = defaultdict(list)
    for tool in tools:
        server = tool.get("_mcp_server", "default")
        grouped[server].append(tool)

    for server, server_tools in sorted(grouped.items()):
        # Obtener el prompt del servidor o usar default
        prompt = (mcp_prompts or {}).get(server, f"Instrucciones para las herramientas de {server}:")
        # Instrucciones de entorno para el MCP local "powershell"
        # (2026-09-15): el modelo asumia Linux (python3, /tmp/) y fallaba
        # en Windows con 'Exit code: 9009', entrando en loops sin respuesta.
        if server == "powershell":
            prompt += (
                "\n  IMPORTANT: Ejecutas en el equipo local del usuario — "
                "Windows 11, PowerShell 5.1. El nombre EXACTO de la herramienta "
                "es run_powershell (scripts PowerShell) y run_python (Python). "
                "NUNCA uses 'powershell' como nombre de funcion en <function=...>: "
                "la funcion debe ser el nombre de la herramienta (run_powershell), "
                "no el nombre del servidor. Para escanear equipos online usa "
                "Test-Connection o 'ping -n 1 -w 400' en paralelo. NUNCA uses "
                "'python3' ni rutas '/tmp/' (entorno Windows). Si un comando "
                "falla, lee stderr y corrige."
                "\n  ANALISIS DE UN EQUIPO CONCRETO DE LA RED (ping, puertos, "
                "conexiones): usa run_powershell con Test-NetConnection "
                "(-ComputerName <IP> -Port <puerto>) para comprobar puertos y "
                "Get-NetTCPConnection (-RemoteAddress <IP>) para conexiones "
                "activas hacia/desde ese equipo. Estos comandos se ejecutan "
                "DESDE el equipo local y no requieren credenciales de la maquina "
                "remota. NUNCA inventes resultados de ping, puertos ni "
                "conexiones: si la tool falla o no puedes ejecutarla, di "
                "claramente 'No tengo acceso a esa maquina' con el error real."
                "\n  COMO SABER SI UN COMANDO FALLO (no te confundas): estas "
                "tools devuelven stdout, stderr, exit_code, has_error y "
                "elapsed_ms. La bandera de fallo es has_error, NO exit_code: un "
                "error no terminante (Get-Item sobre una ruta inexistente, "
                "Get-Service de un servicio que no existe) devuelve exit_code 0 "
                "con stderr lleno. Regla: si has_error es true, el comando NO "
                "hizo lo que pedias, aunque exit_code sea 0. No afirmes al "
                "usuario que algo esta hecho sin comprobar has_error, y si no "
                "puedes verificarlo, dilo con la duda en vez de darlo por bueno."
                "\n  ABRIR UNA APLICACION DE ESCRITORIO (calculadora, notepad, "
                "paint, un archivo con su programa): usa run_desktop_app, NUNCA "
                "Start-Process dentro de run_powershell. Esta aplicacion corre "
                "como servicio de Windows, en la sesion 0, donde no hay "
                "escritorio: Start-Process devuelve exit_code 0 y no abre NINGUN "
                "proceso, sin dar ningun error. run_desktop_app lanza la app en "
                "la sesion del usuario de verdad y comprueba que el proceso "
                "aparece. Lo mismo vale para el resto de acciones visibles "
                "(abrir un archivo, mostrar una ventana): se ejecutan sin "
                "error aunque no hagan nada visible. Regla general: si la "
                "accion es visible para el usuario, exijele una verificacion "
                "en el resultado. Si run_desktop_app devuelve has_error true, "
                "la app NO se ha abierto y no puedes decírselo como hecho."
            )
        blocks.append(f"## {server}\n\n{prompt}\n")

        for tool in server_tools:
            func = tool.get("function", {})
            name = func.get("name", "unknown")
            desc = func.get("description", "")
            params = func.get("parameters", {})

            # Formatear el schema de parametros
            params_str = _format_parameters(params)

            block = f"- **{name}**: {desc}\n"
            if params_str:
                block += f"  Parameters:\n{params_str}"
            
            # Instrucciones especiales para herramientas AD con filter_criteria
            if server == "AD" and "filter_criteria" in (params.get("properties", {}) if params else {}):
                block += '\n  IMPORTANT: Si el usuario pide usuarios por fecha (ultimo mes, ultima semana, etc.), usa `filter_criteria` con un filtro LDAP como: `(&(objectClass=user)(whenCreated>=20250812000000Z))`'
            
            blocks.append(block)

    # Formato exacto de llamada (bug 2026-09-15): sin este ejemplo, Qwen 3.6
    # ADIVINA el formato y genera <function>get_time</function> (sin '='), que
    # ni el parser ni llama.cpp reconocen -> tool calls invisibles, respuestas
    # vacias y loops. El modelo debe ver el formato EXACTO en cada turno.
    #
    # Bug 2026-09-24: el ejemplo con placeholders (nombre_de_la_herramienta,
    # arg1) hacia que Qwen 3.6 confundiera el nombre del servidor MCP ('dia')
    # con el nombre de la tool y emitiera <function=dia> en ~50% de los
    # intentos (verificado con el prompt exacto de la app, 6979 tokens). El
    # parser descarta 'dia' (whitelist) -> tool call perdida -> respuesta
    # vacia. El ejemplo debe usar una tool REAL del prompt para que el modelo
    # imite el formato correcto (verificado 3/3 con get_time + timezone).
    example_name, example_param = _pick_format_example(grouped)
    example = (
        "\nFORMATO DE LLAMADA A HERRAMIENTAS (usa SIEMPRE este XML exacto, "
        "nunca JSON, markdown ni <tool_code>):\n"
        "<tool_call>\n"
        f"<function={example_name}>\n"
    )
    if example_param:
        example += (
            f"<parameter={example_param}>\n"
            "valor\n"
            "</parameter>\n"
        )
    example += (
        "</function>\n"
        "</tool_call>\n"
        "IMPORTANTE: el nombre en <function=...> es el nombre EXACTO de la "
        "herramienta de la lista (p. ej. get_time). NUNCA uses el nombre del "
        "servidor MCP (p. ej. dia) ni combinaciones con prefijo "
        "(dia.get_time, dia-get_time): el parser solo acepta el nombre exacto."
    )
    blocks.append(example)

    return "\n\n".join(blocks)


def _pick_format_example(grouped: dict[str, list[dict]]) -> tuple[str, str | None]:
    """Elige la tool de ejemplo para el bloque FORMATO DE LLAMADA A
    HERRAMIENTAS.

    Prefiere get_time si existe (verificado 3/3 con el prompt exacto de la
    app: el modelo imita el formato correcto). Si no existe, usa la primera
    tool real del primer servidor para no inducir llamadas a una tool
    inexistente. Devuelve (nombre_tool, primer_parametro | None).
    """
    all_tools = [t for server_tools in grouped.values() for t in server_tools]
    for tool in all_tools:
        func = tool.get("function", {})
        if func.get("name") == "get_time":
            props = (func.get("parameters", {}) or {}).get("properties", {}) or {}
            return "get_time", next(iter(props), None)
    tool = all_tools[0]
    func = tool.get("function", {})
    props = (func.get("parameters", {}) or {}).get("properties", {}) or {}
    return func.get("name", "unknown"), next(iter(props), None)


def _format_parameters(params: dict, indent: str = "    ") -> str:
    """Formatea el schema de parametros de una tool como texto legible."""
    if not params or not isinstance(params, dict):
        return ""

    properties = params.get("properties", {})
    required = params.get("required", [])
    if not properties:
        return ""

    lines = []
    for prop_name, prop_def in properties.items():
        prop_type = prop_def.get("type", "string")
        prop_desc = prop_def.get("description", "")
        req_marker = " (required)" if prop_name in required else ""
        lines.append(f"{indent}- `{prop_name}` ({prop_type}){req_marker}: {prop_desc}")

        # Manejar enums si existen
        enum_vals = prop_def.get("enum", [])
        if enum_vals:
            lines.append(f"{indent}  Options: {', '.join(str(v) for v in enum_vals)}")

        # Manejar nested objects
        if prop_type == "object" and "properties" in prop_def:
            nested = _format_parameters(prop_def, indent + "    ")
            if nested:
                lines.append(f"{indent}  Nested properties:")
                lines.append(nested)

    return "\n".join(lines)

