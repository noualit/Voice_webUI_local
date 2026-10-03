import json
import re

# Tags de apertura de tool calls XML que Qwen 3.x genera como texto plano en el
# stream. Se usan para detectar el inicio de un tool call; tambien cuando el tag
# llega DIVIDIDO entre deltas por la tokenizacion BPE (bug 2026-09-13: el modelo
# emitio "<tool_code>memoria.create_conversation(...)</tool_code>" y la app lo
# mostro como texto crudo porque ningun delta contenia el tag completo).
_TOOL_TAG_OPENERS = ("<tool_call>", "<tool_calls>", "<tool name=", "<tool_code>", "<tools>", "<name>")

# Nombres de herramientas conocidos para validar el formato standalone
# <name>...</name><arguments>...</arguments>. Solo se reconocen como tool
# calls los nombres que estan en esta lista (bug 2026-09-13: el parser
# standalone interpretaba texto normal como herramientas: "Saludo y
# bienvenida a Nordin", "tool_call").
_KNOWN_TOOL_NAMES = frozenset({
    # Tools de memoria (memoria_tools.py)
    "create_conversation",
    "list_conversations",
    "get_conversation_history",
    "search_memories",
    "save_memory",
    "delete_conversation",
    # Tools comunes de MCP (dia, filesystem, git, AD, etc.)
    "get_time",
    "get_timezone",
    "web_search",
    "web_fetch",
    "run_command",
    "list_users",
    "get_user",
    "list_groups",
    "list_organizational_units",
    "list_files",
    "read_file",
    "write_file",
    "delete_file",
    "git_status",
    "git_log",
    "git_diff",
    "git_branch",
    "git_commit",
    "calculator",
    "python_sandbox",
    "get_system_info",
    "run_powershell_admin",
})

def is_known_tool_name(name: str, whitelist) -> bool:
    """True si name (o su base tras el punto, p. ej. 'dia.get_time' ->
    'get_time') esta en la whitelist de tools conocidas.

    La whitelist puede ser estatica (_KNOWN_TOOL_NAMES) o dinamica (las
    tools reales disponibles, pasadas por app.py como known_tool_names).
    """
    base = name.rsplit(".", 1)[-1] if "." in name else name
    return name in whitelist or base in whitelist

def parse_xml_tool_calls(text: str, known_names: set | None = None) -> list | None:
    """Parsea tool calls en formato XML de Qwen (texto plano).

    Qwen 3.x genera las llamadas a herramientas como texto plano:

        <tool_call>
        <function=nombre>
        <parameter=arg1>
        valor
        </parameter>
        </function>
        </tool_call>

    llama.cpp normalmente convierte ese XML a delta.tool_calls JSON, pero
    cuando el XML llega incompleto o malformado lo emite como contenido de
    texto. Este parser es el fallback para no depender de esa conversion.

    known_names: whitelist de tools reales disponibles (dinamica, desde
    app.py). Si es None, se usa la whitelist estatica _KNOWN_TOOL_NAMES.
    TODO nombre de tool extraido se valida contra la whitelist: si el modelo
    alucina nombres (mcp_memory_search_memories, query, limit, mcp_) la
    call se descarta y no se ejecuta nada (bug 2026-09-14: 'Herramienta
    tool_call no encontrada').

    Devuelve lista de tool calls (formato OpenAI) o None si no hay XML.
    """
    whitelist = known_names if known_names is not None else _KNOWN_TOOL_NAMES
    if "<tool_call>" not in text and "<tool_calls>" not in text and "<tool name=" not in text and "<tool_code>" not in text and "<tools>" not in text and "<name>" not in text:
        return None
    calls = []
    # Formato 7 (bug 2026-09-12): el modelo a veces ESCRIBE CODIGO para
    # llamar a la tool:
    #   <tools>
    #   <tool_code>
    #   print(dia.get_time())
    #   </tool_code>
    #   </tools>
    # SOLO se ejecuta el patron seguro (llamada directa con kwargs
    # literales). Codigo complejo (multi-statement, posicionales, variables,
    # expresiones) NO genera tool_call: no se puede mapear a args MCP de
    # forma fiable y evaluar codigo del modelo es un riesgo.
    for match in re.finditer(r"<tool_code>(.*?)</tool_code>", text, re.DOTALL):
        code = match.group(1).strip()
        # Quitar print(...) envolvente si existe
        m = re.fullmatch(r"print\s*\((.*)\)", code, re.DOTALL)
        if m:
            code = m.group(1).strip()
        call_match = re.fullmatch(
            r"([a-zA-Z_][\w.]*)\s*\(\s*(.*?)\s*\)", code, re.DOTALL
        )
        if not call_match:
            continue  # codigo complejo: no ejecutar
        name = call_match.group(1).strip()
        args_str = call_match.group(2).strip()
        args = {}
        if args_str:
            ok = True
            for kv in args_str.split(","):
                if "=" not in kv:
                    ok = False
                    break
                k, v = kv.split("=", 1)
                k = k.strip()
                v = v.strip()
                try:
                    args[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    if (v.startswith('"') and v.endswith('"')) or (
                        v.startswith("'") and v.endswith("'")
                    ):
                        args[k] = v[1:-1]
                    else:
                        ok = False
                        break
            if not ok:
                continue
        calls.append({
            "id": f"call_xml_{len(calls) + 1}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    # Formato 6 (bug 2026-09-12): MCP canonico con atributo name y JSON como
    # contenido, con o sin wrapper <tools>:
    #   <tools>
    #     <tool name="dia.get_time">
    #       { "timezone": "Europe/Madrid" }
    #     </tool>
    #   </tools>
    # El modelo Qwen 3.6 lo genero al preguntarle "que dia es hoy". No usa
    # </tool_calls> ni <parameters>: el nombre va en el atributo y el JSON
    # es el contenido del elemento <tool>.
    for match in re.finditer(
        r'<tool\s+name="([^"]+)"\s*>(.*?)</tool>', text, re.DOTALL
    ):
        name = match.group(1).strip()
        args = {}
        content = match.group(2).strip()
        if content:
            try:
                args = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                # Escaneo incremental por si el JSON viene con texto alrededor
                for i in range(1, len(content) + 1):
                    try:
                        args = json.loads(content[:i])
                        break
                    except (json.JSONDecodeError, TypeError):
                        continue
        calls.append({
            "id": f"call_xml_{len(calls) + 1}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    # OJO: el modelo Qwen 3.6 cierra a veces con </tool_calls> (con 's',
    # bug 2026-09-12) y el parser solo buscaba </tool_call>: el XML nunca
    # se parseaba y salia como texto. El regex acepta ambos cierres.
    # Tambien acepta la APERTURA con 's' (<tool_calls>, bug 2026-09-13):
    # el modelo alterna <tool_call> y <tool_calls>.
    for match in re.finditer(r"<tool_calls?>(.*?)</tool_calls?>", text, re.DOTALL):
        block = match.group(1)
        name = None
        args = {}

        # 1) Formato clasico (prompt de la app):
        #    <tool_call><function=name>...<parameter=arg>valor</parameter>
        #    </function></tool_call>
        func_match = re.search(
            r"<function=([^>]+)>(.*?)</function>", block, re.DOTALL
        )
        if func_match:
            name = func_match.group(1).strip()
            for pm in re.finditer(
                r"<parameter=([^>]+)>(.*?)</parameter>", func_match.group(2), re.DOTALL
            ):
                arg_name = pm.group(1).strip()
                arg_value = pm.group(2).strip()
                # Intentar interpretar el valor como JSON (numeros, bools,
                # dicts, listas); si falla, dejarlo como string.
                try:
                    args[arg_name] = json.loads(arg_value)
                except (json.JSONDecodeError, TypeError):
                    args[arg_name] = arg_value
        elif re.search(r"<function>(.*?)</function>", block, re.DOTALL):
            # 1b) Formato estandar XML (bug 2026-09-15): <function>get_time
            #     </function> SIN '='. El nombre es el contenido del tag.
            #     Qwen 3.6 lo genera cuando el prompt no muestra el formato
            #     exacto (adivina); ni el parser ni llama.cpp lo reconocian
            #     -> tool calls invisibles, respuestas vacias/loops.
            name = re.search(
                r"<function>(.*?)</function>", block, re.DOTALL
            ).group(1).strip()
        else:
            # 2) JSON-in-XML (bug 2026-09-12): el modelo Qwen 3.6 a veces
            #    genera <tool_call>{"name": "...", arguments: {...}}
            #    </tool_calls> en lugar de <function=...>. Extraer name y
            #    arguments con regex (el "arguments:" sin comillas no es
            #    JSON valido, por eso no se usa json.loads sobre el bloque
            #    entero).
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', block)
            if name_match:
                name = name_match.group(1).strip()
                args = {}
                # El bloque puede ser JSON valido completo ({"name": ...,
                # "arguments": {...}} con comillas en arguments) o JSON
                # invalido (arguments: sin comillas, bug 2026-09-12). Primero
                # json.loads del bloque; si falla, escaneo incremental.
                try:
                    parsed = json.loads(block.strip())
                    if isinstance(parsed, dict) and isinstance(
                        parsed.get("arguments"), dict
                    ):
                        args = parsed["arguments"]
                except (json.JSONDecodeError, TypeError):
                    pass
                if not args:
                    # Fallback: escanear el JSON de arguments incrementalmente
                    # (mismo enfoque que _parse_peg_native_call): acepta
                    # "arguments": con o sin comillas; el objeto puede
                    # terminar antes del '}' exterior del bloque, asi que se
                    # prueba cada posicion hasta que json.loads acepta.
                    args_match = re.search(r'"?arguments"?\s*:\s*', block)
                    if args_match:
                        json_str = block[args_match.end():]
                        for i in range(1, len(json_str) + 1):
                            try:
                                args = json.loads(json_str[:i])
                                break
                            except (json.JSONDecodeError, TypeError):
                                continue
            else:
                # 5) Formato nativo de Qwen 3.6 (bug 2026-09-12):
                #    <tool_call>call_tool("get_system_info", {})</tool_call>
                #    <tool_calls></tool_calls>. Nombre y args dentro de
                #    call_tool(...), no en XML.
                call_tool_match = re.match(
                    r'call_tool\(\s*"([^"]+)"\s*,\s*(.+)\)\s*$', block, re.DOTALL
                )
                if call_tool_match:
                    name = call_tool_match.group(1).strip()
                    args_str = call_tool_match.group(2).strip()
                    # Escaneo incremental: el JSON puede terminar antes del
                    # ')' final (mismo enfoque que _parse_peg_native_call).
                    for i in range(1, len(args_str) + 1):
                        try:
                            args = json.loads(args_str[:i])
                            break
                        except (json.JSONDecodeError, TypeError):
                            continue
                else:
                    # 3) Formato MCP canonico de Qwen:
                    #    <tool_call><name>get_time</name><parameters>{...}
                    #    </parameters></tool_call>
                    name_match = re.search(r"<name>(.*?)</name>", block, re.DOTALL)
                    if name_match:
                        name = name_match.group(1).strip()
                    else:
                        # 4) Nombre como contenido (bug 2026-09-12):
                        #    <tool_call>dia.get_time</tool_call><parameters>
                        #    {"timezone": ...}</parameters></function_calls>
                        name = block.strip()
                    if not name:
                        continue
                    # El nombre como contenido del bloque es texto libre:
                    # validar que sea una tool conocida (o nombre base tras
                    # el punto). Sin esto, cualquier contenido del bloque se
                    # convertia en tool name (bug 2026-09-14:
                    # <tool_call><arguments></arguments></tool_call> generaba
                    # una call con nombre '<arguments></arguments>').
                    if not is_known_tool_name(name, whitelist):
                        continue

        # Args desde <parameters> o <arguments>...</arguments>: dentro del
        # bloque o, en el formato 4, DESPUES del cierre del tool_call.
        # El modelo Qwen 3.x usa <arguments> (JSON directo) en lugar de
        # <parameters> (elementos <parameter> anidados). Soportar ambos.
        if not args:
            params_match = re.search(
                r"<arguments>(.*?)</arguments>", block, re.DOTALL
            )
            if not params_match:
                params_match = re.search(
                    r"<parameters>(.*?)</parameters>", block, re.DOTALL
                )
            if not params_match:
                params_match = re.search(
                    r"<arguments>(.*?)</arguments>", text[match.end():], re.DOTALL
                )
            if not params_match:
                params_match = re.search(
                    r"<parameters>(.*?)</parameters>", text[match.end():], re.DOTALL
                )
            if params_match:
                inner = params_match.group(1).strip()
                if not inner:
                    args = {}
                else:
                    try:
                        args = json.loads(inner)
                    except (json.JSONDecodeError, TypeError):
                        # XML anidado (bug 2026-09-15): el modelo genera
                        # <arguments><timezone>Europe/Madrid</timezone>
                        # </arguments> en lugar de JSON. Cada hijo se
                        # convierte en clave/valor; <timezone></timezone>
                        # (vacio) -> {"timezone": ""}.
                        xml_args = {}
                        for am in re.finditer(
                            r"<([a-zA-Z_][\w.]*)>(.*?)</\1>", inner, re.DOTALL
                        ):
                            xml_args[am.group(1)] = am.group(2).strip()
                        if xml_args:
                            args = xml_args
                        else:
                            # Escaneo incremental por si el JSON viene con
                            # texto alrededor (mismo enfoque que
                            # _parse_peg_native_call).
                            for i in range(1, len(inner) + 1):
                                try:
                                    args = json.loads(inner[:i])
                                    break
                                except (json.JSONDecodeError, TypeError):
                                    continue

        calls.append({
            "id": f"call_xml_{len(calls) + 1}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })

    # Formato minimalista (bug 2026-09-12): <tools><tool_name></tool_name>
    # </tools> sin parametros. El modelo Qwen 3.6 lo genera cuando la tool
    # no requiere argumentos (p.ej. get_time sin timezone especificado).
    # SOLO se reconocen como tools los nombres en la whitelist (mismo
    # criterio que el formato standalone): tags estructurales como
    # <tool_call>, <name> o <arguments> (con o sin cierre correcto) NO son
    # tools y no deben generar calls fantasma (bug 2026-09-14: XML
    # malformado con <tool_call> anidado en <tools> producia 'Herramienta
    # tool_call no encontrada').
    if not calls and "<tools>" in text:
        wrapper_tags = {
            "tools", "tool_calls", "tool_call", "tool_code",
            "tool", "function", "name", "arguments",
        }
        for match in re.finditer(r"<([a-zA-Z_][\w.]*)\b[^>]*>", text):
            tag_name = match.group(1)
            if tag_name.lower() in wrapper_tags:
                continue
            # Validar contra la whitelist: el nombre (o su base tras el
            # punto, p.ej. "dia.get_time" -> "get_time") debe ser una tool
            # conocida. Texto o tags estructurales no son tools.
            if not is_known_tool_name(tag_name, whitelist):
                continue
            calls.append({
                "id": f"call_xml_{len(calls) + 1}",
                "type": "function",
                "function": {"name": tag_name, "arguments": "{}"},
            })

    # Formato standalone <name>...</name><arguments>JSON</arguments> sin
    # wrapper (<tool_calls>, <tools>, etc). El modelo Qwen 3.x lo genera
    # cuando no hay wrapper en el stream. SOLO se reconocen nombres que
    # estan en la lista de herramientas conocidas (bug 2026-09-13: el parser
    # interpretaba texto normal como herramientas: "Saludo y bienvenida a
    # Nordin", "tool_call").
    if not calls:
        # Patrón estricto: nombre de herramienta solo alfanumerico + _ + .
        # Esto filtra automaticamente texto como "Saludo y bienvenida a Nordin"
        name_pattern = re.compile(r"<name>([a-zA-Z_][\w.]*?)</name>", re.DOTALL)
        name_matches = list(name_pattern.finditer(text))
        for name_match in name_matches:
            name = name_match.group(1).strip()
            # Validar: nombre debe estar en la lista de herramientas conocidas.
            # El modelo a veces prefija el servidor (p.ej. "dia.get_time"),
            # asi que tambien se verifica la parte tras el ultimo punto.
            if not is_known_tool_name(name, whitelist):
                continue
            # Buscar <arguments> DESPUES del <name> encontrado
            args_match = re.search(
                r"<arguments>(.*?)</arguments>",
                text[name_match.end():],
                re.DOTALL,
            )
            if args_match:
                try:
                    args = json.loads(args_match.group(1).strip())
                except (json.JSONDecodeError, TypeError):
                    args = {}
                calls.append({
                    "id": f"call_xml_{len(calls) + 1}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                })

    # Red de seguridad final (bug 2026-09-14): descartar CUALQUIER call cuyo
    # nombre no este en la whitelist, venga del formato que venga (function=,
    # JSON-in-XML, tool name=, tool_code, etc). El modelo alucina nombres
    # con prefijos inventados (mcp_memory_search_memories, mcp_, query,
    # limit) que no son tools reales: ejecutarlos solo produce 'Herramienta
    # X no encontrada' y descarrila la conversacion.
    if calls:
        calls = [
            c for c in calls
            if is_known_tool_name(c["function"]["name"], whitelist)
        ]
    return calls or None

def parse_peg_native_call(text: str, known_names: set | None = None) -> list | None:
    """Parsea tool calls en formato peg-native de llama.cpp (texto plano).

    Con chat_format peg-native (Qwen 3.x), llama.cpp genera las llamadas a
    herramientas como texto plano:

        [call:diamcp://context/dia.get_timezone|{"params":{"timezone":"Europa/Madrid"}}]

    Formato: [call:URI|JSON]
    - URI: identifica la tool; el nombre es el ultimo segmento tras la barra
      (p. ej. 'dia.get_timezone' — el modelo prefija el servidor MCP).
    - JSON: argumentos; si tiene clave 'params', los argumentos reales estan
      ahi (formato del proxy MCP de llama.cpp), si no, el JSON completo.

    known_names: whitelist dinamica (tools reales). Si es None, se usa
    _KNOWN_TOOL_NAMES. Los nombres que no esten en la whitelist se
    descartan (bug 2026-09-14: el modelo alucino mcp_memory_search_memories
    en este formato).

    Devuelve lista de tool calls (formato OpenAI) o None si no hay ninguna
    call completa. El JSON puede contener corchetes internos (arrays), por
    eso se escanea probando cada ']' hasta que el JSON parsea (bug
    2026-09-12: el modelo generaba las calls en este formato y la app las
    mostraba como texto).
    """
    whitelist = known_names if known_names is not None else _KNOWN_TOOL_NAMES
    if "[call:" not in text:
        return None
    calls = []
    pos = 0
    while True:
        start = text.find("[call:", pos)
        if start == -1:
            break
        pipe = text.find("|", start)
        if pipe == -1:
            break
        uri = text[start + 6:pipe].strip()
        json_start = pipe + 1
        parsed = None
        end = -1
        for i in range(json_start, len(text)):
            if text[i] != "]":
                continue
            try:
                parsed = json.loads(text[json_start:i])
                end = i
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            break  # JSON incompleto o invalido: no hay call completa
        name = uri.rstrip("/").split("/")[-1].strip()
        if isinstance(parsed, dict) and "params" in parsed:
            args = parsed["params"]
        else:
            args = parsed
        if not isinstance(args, dict):
            args = {}
        calls.append({
            "id": f"call_peg_{len(calls) + 1}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
        pos = end + 1
    if calls:
        calls = [
            c for c in calls
            if is_known_tool_name(c["function"]["name"], whitelist)
        ]
    return calls or None

def find_peg_native_call(text: str, known_names: set | None = None) -> tuple | None:
    """Busca la PRIMERA [call:URI|JSON] completa en text.

    known_names: whitelist dinamica (tools reales). Si es None, se usa
    _KNOWN_TOOL_NAMES. Calls con nombre fuera de la whitelist se descartan
    (bug 2026-09-14).

    Devuelve (tool_calls, raw, end_index) donde raw es el texto exacto del
    call (para la continuacion en texto plano) y end_index la posicion justo
    despues del ']' de cierre. None si no hay una call completa.
    """
    whitelist = known_names if known_names is not None else _KNOWN_TOOL_NAMES
    if "[call:" not in text:
        return None
    start = text.find("[call:")
    pipe = text.find("|", start)
    if pipe == -1:
        return None
    uri = text[start + 6:pipe].strip()
    json_start = pipe + 1
    for i in range(json_start, len(text)):
        if text[i] != "]":
            continue
        try:
            parsed = json.loads(text[json_start:i])
        except json.JSONDecodeError:
            continue
        name = uri.rstrip("/").split("/")[-1].strip()
        if isinstance(parsed, dict) and "params" in parsed:
            args = parsed["params"]
        else:
            args = parsed
        if not isinstance(args, dict):
            args = {}
        raw = text[start:i + 1]
        if not is_known_tool_name(name, whitelist):
            # Call fantasma (bug 2026-09-14): seguir buscando en el resto
            # del texto por si hay una call valida despues.
            start = i + 1
            pipe = text.find("|", start)
            if pipe == -1:
                return None
            uri = text[start + 6:pipe].strip()
            json_start = pipe + 1
            continue
        calls = [{
            "id": "call_peg_1",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]
        return calls, raw, i + 1
    return None

def flush_peg_call(peg_buffer: str, known_names: set | None = None) -> tuple[list, str]:
    """Si el buffer [call:...] ya tiene una call completa, devuelve los
    eventos a emitir (tool_calls con raw + posible delta sobrante) y el
    buffer reiniciado. Si aun no esta completa, devuelve ([], buffer).
    known_names: whitelist dinamica (tools reales); None -> estatica."""
    found = find_peg_native_call(peg_buffer, known_names=known_names)
    if not found:
        return [], peg_buffer
    calls, raw, end = found
    events = [{"type": "tool_calls", "raw": raw, "tool_calls": calls}]
    rest = peg_buffer[end:]
    if rest.strip():
        events.append({"type": "delta", "content": rest})
    return events, ""
