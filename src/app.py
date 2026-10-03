# app.py
import atexit
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context, session

from attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MARKER_RE,
    resolve_attachments,
    save_upload,
)
from config import AppConfig
from conversations import (
    _llm_role,
    _memory_role,
    build_chat_messages,
    conversations_bp,
    MAX_HISTORY_MESSAGES,
    persist_chat,
    sanitize_absurd_dates,
    set_config_provider,
    truncate_tool_result_for_llm,
    set_memory_client_provider,
)

# Alias backward-compatible para tests que esperan _persist_chat en app.
_persist_chat = persist_chat

# Alias backward-compatible para tests que esperan _build_chat_messages en app.
_build_chat_messages = build_chat_messages

from llm_client import LLMClient, format_tools_prompt
from memory_client import MemoryClient
from mcp_manager import MCPToolManager
from memory_tools import get_memory_tools, execute_memory_tool, _is_memory_tool
from user_identity import canonical_user, same_identity

# Patron regex para eliminar XML de tool calls del contenido delta.
# El modelo Qwen genera tool calls en formato XML como parte del texto:
#   <name>save_memory</name><arguments>{"key": "val"}</arguments>
# Estos bloques se eliminan antes de enviar al frontend SSE para que el
# usuario no vea el XML crudo (bug: respuestas con XML visible).
_TOOL_XML_RE = None


def _get_tool_xml_re():
    """Compila y cachea el patron regex para XML de tool calls."""
    global _TOOL_XML_RE
    if _TOOL_XML_RE is None:
        import re
        # Eliminar bloques <name>...</name><arguments>...</arguments>
        # re.DOTALL para que . coincida con saltos de linea en arguments
        _TOOL_XML_RE = re.compile(
            r"<name>[^<]*</name><arguments>.*?</arguments>",
            re.DOTALL,
        )
    return _TOOL_XML_RE


def strip_tool_xml_content(content: str) -> str:
    """Elimina patrones XML de tool calls del contenido de un delta.

    El modelo Qwen genera tool calls en formato XML como parte del texto:
      <name>save_memory</name><arguments>{"key": "val"}</arguments>

    Esta funcion elimina esos bloques del contenido antes de enviarlo al
    frontend SSE, para que el usuario no vea el XML crudo.

    El texto normal sin XML pasa intacto. XML malformado (sin cerrar) no
    se elimina por seguridad.
    """
    pattern = _get_tool_xml_re()
    return pattern.sub("", content)


# Directorio raiz del proyecto (padre de src/) para resolver rutas de forma
# independiente del directorio de trabajo actual (cwd).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = Flask(__name__, static_folder=str(PROJECT_ROOT / "static"), static_url_path="")

# Configuracion global (se inicializa en _init_globals)
config = None
llm_client = None
memory_client = None
mcp_manager = None
whisper_model = None

MAX_ITERATIONS = 10

# Flags de cancelacion por conversation_id. Se usan para parar la respuesta
# del modelo desde el frontend en cualquier momento via POST /api/chat/cancel.
_cancel_flags: dict[str, bool] = {}
_cancel_lock = threading.Lock()

# Tools que requieren aprobacion explicita del usuario antes de ejecutarse.
# run_command ejecuta comandos arbitrarios en el servidor (riesgo de seguridad:
# el modelo puede alucinar comandos, bug 2026-09-07). Ampliable por el usuario.
# run_powershell/run_python (MCP local "powershell", 2026-09-15) ejecutan
# comandos en el equipo del usuario: misma politica de aprobacion.
APPROVAL_REQUIRED_TOOLS = {"run_command", "run_powershell", "run_python", "run_powershell_admin"}

# Tiempo maximo (segundos) que el stream espera la decision del usuario antes
# de bloquear la tool por defecto (fail-safe): sin respuesta, no se ejecuta.
APPROVAL_TIMEOUT = 60

# Solicitudes de aprobacion pendientes por approval_id. El stream de /api/chat
# registra un threading.Event y espera; el frontend responde via
# POST /api/chat/approve (o el timeout bloquea la tool).
_approval_events: dict[str, threading.Event] = {}
_approval_results: dict[str, bool] = {}
_approval_lock = threading.Lock()

# Tools peligrosas aprobadas por el usuario, agrupadas por conversation_id
# (aprobacion UNA vez por conversacion, 2026-09-15): la primera llamada a una
# tool de APPROVAL_REQUIRED_TOOLS pide permiso; si se aprueba, el resto de la
# conversacion ejecuta sin volver a preguntar. Una conversacion nueva empieza
# sin aprobaciones (su id no esta en el dict).
_approved_tools_per_conv: dict[str, set[str]] = {}
_approval_conv_lock = threading.Lock()


def _set_cancel(conversation_id: str, flag: bool) -> None:
    with _cancel_lock:
        if flag:
            _cancel_flags[conversation_id] = True
        else:
            _cancel_flags.pop(conversation_id, None)


def _is_cancelled(conversation_id: str) -> bool:
    with _cancel_lock:
        return bool(_cancel_flags.get(conversation_id))


def _tool_exists(name: str) -> bool:
    """Comprueba si name es una tool conocida (MCP o memoria)."""
    all_tools = getattr(mcp_manager, "all_tools", None)
    if isinstance(all_tools, list):
        for t in all_tools:
            if t.get("name") == name:
                return True
    return _is_memory_tool(name)


def _resolve_tool_name(tool_name: str) -> str:
    """Resuelve el nombre de tool generado por el modelo al nombre real de la app.

    El modelo (formato peg-native de llama.cpp, bug 2026-09-12) a veces prefija
    el nombre con el servidor MCP (p. ej. 'dia.get_timezone' porque el system
    prompt agrupa por '## dia'). Si el nombre completo no existe en el manager,
    se usa la parte tras el ultimo punto ('get_timezone'); si esa tampoco existe,
    execute_tool devolvera 'no encontrada' (mismo comportamiento seguro).
    """
    if _tool_exists(tool_name):
        return tool_name
    if "." in tool_name:
        return tool_name.rsplit(".", 1)[-1]
    return tool_name

# Maximo de mensajes del historial reenviados al LLM como contexto. Se mantienen
# los mas recientes (los que aportan contexto util) para no exceder la ventana
# de contexto del modelo en conversaciones largas.
# Definido en conversations.py para evitar import circular.


def _init_globals():
    global config, llm_client, memory_client, mcp_manager, whisper_model
    config = AppConfig()
    set_config_provider(lambda: config)  # Inyectar config en conversations.py
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-local-secret")
    llm_client = LLMClient(config.llama_url, config.model, config.llm_timeout)
    memory_client = MemoryClient(config.memory_url, _memory_identity())
    mcp_manager = MCPToolManager(config.mcp_config_path)
    mcp_manager.start_all()
    # Whisper NO se carga en el arranque (carga perezosa en _get_whisper_model).
    # Asi la app levanta sin reservar RAM para whisper hasta que se necesite
    # transcripcion. Se deja el global a None.
    whisper_model = None


# Lock para serializar la carga perezosa de whisper. Flask corre api_transcribe
# en hilos; sin lock dos peticiones simultaneas podrian cargar el modelo dos
# veces (o leer whisper_model a medio inicializar).
_whisper_lock = threading.Lock()


def _get_whisper_model():
    """Devuelve el modelo whisper, cargandolo perezosamente (lazy) la primera vez.

    Se carga en CPU (device="cpu") por decision deliberada: el audio va bien asi
    y whisper no compite por la GPU con llama.cpp (Qwen 3.6-35B).

    AVISO sobre la VRAM: la premisa de "no hay sitio en la GPU" es FALSA en esta
    maquina, y se ha medido. nvidia-smi no sirve para decidirlo en Windows porque
    usa el driver model WDDM, donde memory.used incluye asignaciones del
    compositor de escritorio que NO bloquean las reservas de CUDA; el propio
    nvidia-smi lo admite con "Used GPU Memory: Not available in WDDM driver
    model". Con torch.cuda.mem_get_info hay ~14 GB libres, whisper small carga en
    CUDA y quedan ~7 GB libres. Si algun dia se quiere pasar a GPU, el cambio es
    device="cpu" -> device="cuda": no hay que competir por la memoria.
    """
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    with _whisper_lock:
        # Doble comprobacion: otro hilo pudo cargarlo mientras esperabamos el lock
        if whisper_model is not None:
            return whisper_model
        import whisper
        whisper_model = whisper.load_model(config.whisper_model, device="cpu")
        return whisper_model


def _memory_identity() -> str:
    """Identidad canonica (Windows/AD) que gobierna el namespace de memoria.

    La fija la configuracion, no el texto que teclea el usuario en el login: ese
    texto libre era la clave del namespace y partia los historiales en dos
    (2026-10-01: 9 memorias bajo el nombre corto, 1 bajo la misma cuenta con
    dominio). Ver src/user_identity.py.
    """
    return canonical_user(config.memory_user) or "default"


def _current_user() -> str | None:
    """Devuelve el usuario de la sesion, o None si no ha iniciado sesion."""
    return session.get("user")


def _memory_for_user(user: str) -> MemoryClient:
    """Crea un MemoryClient aislado por usuario (header X-User)."""
    return MemoryClient(config.memory_url, canonical_user(user))


def _get_memory_client() -> MemoryClient:
    """Devuelve un MemoryClient para el usuario de la sesion.

    Si hay sesion, se crea un cliente por usuario (aislamiento). Si no,
    se usa el cliente global (identidad configurada) para compatibilidad en
    desarrollo sin login.

    Ambos caminos apuntan a la misma identidad canonica: el login solo acepta la
    cuenta configurada y la guarda canonizada, asi que no hay un segundo
    namespace.
    """
    user = _current_user()
    if user:
        return _memory_for_user(user)
    return memory_client


# Registro del blueprint de conversaciones en tiempo de import (Flask no admite
# register_blueprint despues de la primera request). Se inyecta el proveedor de
# MemoryClient para evitar el import circular app <-> conversations.
set_memory_client_provider(_get_memory_client)
app.register_blueprint(conversations_bp, url_prefix="/api")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Nombre de usuario requerido"}), 400
    # El nombre tecleado tiene que ser la cuenta configurada (la real de
    # Windows/AD). Antes se aceptaba cualquier texto y ese texto era la clave del
    # namespace de memoria: no habia aislamiento real, porque teclear el nombre
    # de otro daba acceso a sus memorias, y la misma persona acababa con dos
    # historiales segun como escribiera su nombre. Se aceptan las formas de esa
    # cuenta (corto, con dominio, mayusculas) y se guarda la canonica.
    if not same_identity(name, config.memory_user):
        return jsonify({
            "error": "Usuario incorrecto. La sesion solo admite la cuenta "
                     "configurada en este equipo."
        }), 401
    # Sin session.permanent la cookie es de sesion: el navegador la borra al
    # cerrar y el usuario vuelve al overlay de login, que intercepta todos los
    # clics y deja el boton de microfono inalcanzable. Con permanent, Flask
    # anade Expires y la sesion dura PERMANENT_SESSION_LIFETIME (31 dias por
    # defecto, con renovacion en cada peticion).
    session.permanent = True
    session["user"] = _memory_identity()
    return jsonify({"user": session["user"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("user", None)
    return jsonify({"status": "ok"})


@app.route("/api/me", methods=["GET"])
def api_me():
    return jsonify({"user": _current_user()})


@app.route("/")
def index():
    return send_from_directory(str(PROJECT_ROOT / "static"), "index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global llm_client
    if request.method == "GET":
        return jsonify({
            "system_prompt": config.system_prompt,
            "llama_url": config.llama_url,
            "model": config.model,
            "whisper_model": config.whisper_model,
            "memory_url": config.memory_url,
        })
    data = request.get_json() or {}
    if "system_prompt" in data:
        config.system_prompt = data["system_prompt"]
    if "llama_url" in data:
        config.llama_url = data["llama_url"]
    if "model" in data:
        config.model = data["model"]
    config.save()
    llm_client = LLMClient(config.llama_url, config.model, config.llm_timeout)
    return jsonify({"status": "ok"})


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No se recibio audio"}), 400
    audio = request.files["audio"]
    tmp_path = None
    try:
        # Carga perezosa de whisper: solo se carga en memoria (CPU) la primera
        # vez que se transcribe, no al arrancar la app.
        model = _get_whisper_model()
        ext = os.path.splitext(audio.filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            audio.save(tmp.name)
            tmp_path = tmp.name
        result = model.transcribe(
            tmp_path,
            language="es",
            fp16=False,
            initial_prompt="Esto es una transcripcion en espanol:",
        )
        text = result["text"].strip()
        if not text:
            return jsonify({"error": "No se detecto voz"}), 400
        return jsonify({"transcription": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Sube un archivo adjunto a la conversacion (documento como texto).

    Guarda el archivo en uploads/<usuario>/, extrae el texto y devuelve la
    metadata {file_id, name, chars, truncated}. El contenido no viaja al
    frontend; el backend lo re-lee al resolver los marcadores en /api/chat.
    """
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "No se recibio archivo"}), 400
    # Identidad canonica y no el texto tecleado: el directorio de subidas no
    # puede depender de como escribio su nombre el usuario en cada sesion.
    user = _current_user() or _memory_identity()
    try:
        result = save_upload(file, user, Path(config.upload_dir))
    except ValueError as e:
        msg = str(e)
        status = 413 if "demasiado grande" in msg else 400
        return jsonify({"error": msg}), status
    return jsonify(result)


@app.route("/api/mcp-status")
def api_mcp_status():
    return jsonify(mcp_manager.get_status())


@app.route("/api/mcp/add", methods=["POST"])
def api_mcp_add():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    transport = (data.get("transport") or "stdio").lower()
    if not name:
        return jsonify({"error": "name es requerido"}), 400
    if transport in ("http", "sse"):
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url es requerida para transport http/sse"}), 400
        try:
            ok = mcp_manager.add_http_server(
                name,
                url,
                headers=data.get("headers") or {},
                timeout=int(data.get("timeout", 30)),
            )
            return jsonify({"status": "ok" if ok else "failed"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    # stdio (subprocess)
    command = data.get("command", "").strip()
    args = data.get("args", [])
    env = data.get("env") or {}
    if not command:
        return jsonify({"error": "name y command son requeridos"}), 400
    try:
        ok = mcp_manager.add_server(name, command, args, env)
        return jsonify({"status": "ok" if ok else "failed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp/remove", methods=["POST"])
def api_mcp_remove():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name es requerido"}), 400
    try:
        mcp_manager.remove_server(name)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    conversation_id = data.get("conversation_id")
    # Flag "resend": el usuario pide regenerar la respuesta a la ultima pregunta
    # del usuario (boton reload, estilo WebUI de llama-server). Cuando es true,
    # el mensaje del usuario ya esta en memoria (se reenvia el mismo texto) asi
    # que NO se guarda de nuevo; solo se guarda la nueva respuesta del asistente.
    resend = bool(data.get("resend", False))
    if not text:
        return jsonify({"error": "Falta el texto"}), 400
    if len(MARKER_RE.findall(text)) > MAX_ATTACHMENTS_PER_MESSAGE:
        return jsonify({"error": f"Maximo {MAX_ATTACHMENTS_PER_MESSAGE} adjuntos por mensaje"}), 400

    # Limpia cualquier cancelacion previa para esta conversacion
    _set_cancel(conversation_id, False)

    def generate():
        mem_client = _get_memory_client()
        user = _current_user() or _memory_identity()
        # Combina las tools de los MCPs con las tools de memoria (obtenidas de
        # llama-memory via MemoryClient.list_tools). Asi el modelo puede usar
        # create_conversation, save_memory, search_memories, etc. como tools
        # reales en lugar de incrustarlas solo en el system_prompt.
        tools = mcp_manager.get_openai_tools() + get_memory_tools(mem_client)

        # Whitelist dinamica de tools REALES (MCP + memoria): se pasa a
        # chat_stream para que el parser de tool calls descarte nombres
        # alucinados (mcp_memory_search_memories, query, limit, mcp_) que no
        # corresponden a ninguna tool disponible (bug 2026-09-14: 'Herramienta
        # X no encontrada' en la segunda pregunta).
        known_tool_names = {t["function"]["name"] for t in tools}
        
        # Obtener recursos de los servidores MCP HTTP
        resources = []
        for name, client in mcp_manager.clients.items():
            if hasattr(client, 'list_resources'):
                try:
                    client_resources = client.list_resources()
                    if client_resources:
                        resources.extend(client_resources)
                except Exception:
                    pass  # Ignorar errores de recursos, no criticos
        
        messages = build_chat_messages(mem_client, conversation_id, text, user, tools=tools, resources=resources)
        iteration = 0
        full_response = ""
        # Registro de las tools llamadas en la iteracion anterior, para detectar
        # loops donde el modelo repite la misma tool sin progreso (p. ej. cuando
        # un MCP devuelve null/error y el modelo reintenta una y otra vez).
        prev_tool_keys: list[tuple[str, str]] = []
        # Contador de iteraciones consecutivas donde las tools devolvieron
        # null/error (sin progreso). Cubre el caso real donde el modelo alterna
        # tools distintas (p. ej. full-web-search -> get-web-search-summaries)
        # pero todas fallan, porque ahi prev_tool_keys nunca son identicos.
        iteraciones_sin_progreso = 0
        # Contador de iteraciones donde el modelo hizo tool calls pero no ha
        # generado una respuesta final (full_response sigue vacio). Se usa para
        # cortar loops donde el modelo alterna entre herramientas diferentes
        # que SI devuelven resultados validos, pero nunca llega a la iteracion
        # sin tool calls donde generaria la respuesta final. (bug 2026-09-12)
        iteraciones_con_tools_sin_respuesta = 0
        start_time = time.time()
        last_metrics_yield = start_time
        # Acumuladores de tokens reales (del bloque "usage" que llama.cpp envia
        # en cada iteracion del stream gracias a stream_options.include_usage).
        # En un chat con tools hay varias iteraciones de chat_stream; se suman
        # los completion_tokens de todas para el total real consumido.
        total_completion_tokens = 0
        total_prompt_tokens = 0

        while iteration < MAX_ITERATIONS:
            iteration += 1
            called_tools = False
            current_tool_keys: list[tuple[str, str]] = []
            # Una iteracion "sin progreso" es aquella donde ninguna tool devolvio
            # un resultado util (todas null o con error). Se usa para cortar loops
            # de reintento cuando el MCP esta saturado.
            iteracion_con_progreso = True
            # Buffer del texto generado en ESTA iteracion. Solo se acumula en
            # full_response si la iteracion termina SIN tool calls. Si termina
            # con tools, ese texto es preambulo del modelo (Qwen 3 a veces
            # responde y LUEGO llama save_memory/create_conversation por el
            # system_prompt) y se descarta: la siguiente iteracion genera la
            # respuesta final, y acumular ambos duplicaria el texto (bug
            # 2026-09-07: respuestas con 30K+ chars de basura + respuesta real).
            iteration_text = ""
            try:
                # IMPORTANTE (2026-09-30): las tools viajan EN el payload del LLM
                # (JSON) con tool_choice="auto", igual que llama.cpp lo hace nativamente.
                # Esto permite que el modelo decida cuando usar herramientas sin
                # depender del parsing XML del system prompt. El formato es:
                # {"type": "function", "function": {"name": "...", "parameters": {...}}}
                # y tool_choice="auto" para que el modelo elija cuando usarlas.
                for event in llm_client.chat_stream(messages, tools, known_tool_names=known_tool_names):
                    # Permite parar la respuesta desde el frontend en cualquier momento
                    if _is_cancelled(conversation_id):
                        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                        return

                    if event["type"] == "delta":
                        iteration_text += event["content"]
                        # Filtrar XML de tool calls antes de enviar al frontend.
                        # El modelo Qwen emite tool calls en formato XML como
                        # parte del texto; el usuario no debe ver esto.
                        clean_content = strip_tool_xml_content(event["content"])
                        yield f"data: {json.dumps({'type': 'delta', 'content': clean_content})}\n\n"
                        # Envia metricas aproximadas de velocidad cada ~200ms
                        now = time.time()
                        if now - last_metrics_yield >= 0.2:
                            elapsed = max(now - start_time, 1e-6)
                            yield f"data: {json.dumps({'type': 'metrics', 'elapsed_ms': int((now - start_time) * 1000), 'chars': len(full_response) + len(iteration_text)})}\n\n"
                            last_metrics_yield = now
                    elif event["type"] == "usage":
                        # Acumular tokens reales del bloque "usage" de esta iteracion
                        usage = event.get("usage", {})
                        total_completion_tokens += usage.get("completion_tokens", 0)
                        total_prompt_tokens += usage.get("prompt_tokens", 0)
                    elif event["type"] == "tool_calls":
                        called_tools = True
                        # Flujo XML (Qwen sin payload tools): la continuacion debe
                        # ser texto plano (assistant content = XML crudo + resultado
                        # como user). Enviar role:tool/tool_calls nativos crashea
                        # llama.cpp sin payload["tools"] (ChunkedEncodingError,
                        # bug 2026-09-08).
                        raw_xml = event.get("raw", "")
                        if raw_xml:
                            assistant_msg = {"role": "assistant", "content": raw_xml}
                        else:
                            assistant_msg = {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": event["tool_calls"],
                            }
                        messages.append(assistant_msg)
                        for tc in event["tool_calls"]:
                            if tc.get("type") == "function":
                                func = tc["function"]
                                # El modelo puede prefijar el servidor MCP al
                                # nombre (p. ej. 'dia.get_timezone'); resolverlo
                                # al nombre real de la app (bug 2026-09-12).
                                tool_name = _resolve_tool_name(func["name"])
                                try:
                                    args = json.loads(func["arguments"])
                                except json.JSONDecodeError:
                                    args = {}
                                # Defensa final (bug 2026-09-14): si la tool no
                                # existe (MCP o memoria), NO ejecutarla. El
                                # parser de chat_stream ya filtra la mayoria de
                                # nombres alucinados; esto cubre cualquier caso
                                # que lo esquive. Ejecutar tools inexistentes
                                # solo producia 'Herramienta X no encontrada' y
                                # descarrilaba la conversacion.
                                if not _tool_exists(tool_name):
                                    current_tool_keys.append((tool_name, json.dumps(args, sort_keys=True)))
                                    messages.append({
                                        "role": "user",
                                        "content": (
                                            f"[Resultado de {tool_name}]: Error: la herramienta "
                                            f"'{tool_name}' no existe. Usa solo las herramientas "
                                            "disponibles en el system prompt."
                                        ),
                                    })
                                    iteracion_con_progreso = False
                                    continue
                                current_tool_keys.append((tool_name, json.dumps(args, sort_keys=True)))
                                if _is_memory_tool(tool_name):
                                    result = execute_memory_tool(mem_client, tool_name, args)
                                elif tool_name in APPROVAL_REQUIRED_TOOLS:
                                    # Tool peligrosa: pedir aprobacion explicita al
                                    # usuario ANTES de ejecutar. El evento SSE
                                    # approval_request llega al frontend, que muestra
                                    # un modal y responde via POST /api/chat/approve.
                                    # Si nadie responde en APPROVAL_TIMEOUT segundos, la
                                    # tool se bloquea (fail-safe): nunca se ejecuta sin
                                    # consentimiento (bug 2026-09-07: run_command
                                    # alucinado sin control del usuario).
                                    # Aprobacion UNA vez por conversacion (2026-09-15):
                                    # si el usuario ya aprobo esta tool en esta
                                    # conversacion, se ejecuta directamente.
                                    with _approval_conv_lock:
                                        already_approved = tool_name in _approved_tools_per_conv.get(conversation_id, set())
                                    if already_approved:
                                        result = mcp_manager.execute_tool(tool_name, args)
                                    else:
                                        approval_id = f"ap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{tool_name}"
                                        with _approval_lock:
                                            _approval_events[approval_id] = threading.Event()
                                            # None = aun sin respuesta (pendiente). False = el
                                            # usuario la RECHAZO explicitamente. True = aprobada.
                                            _approval_results[approval_id] = None
                                        yield f"data: {json.dumps({'type': 'approval_request', 'approval_id': approval_id, 'tool': tool_name, 'arguments': args})}\n\n"
                                        # Bug 2026-09-18 (loop reportado por el usuario):
                                        # si el permiso expiraba, se hacia pop(_approval_*)
                                        # de forma SILENCIOSA -> el frontend no cerraba el modal
                                        # y si el usuario aprobaba tarde (approval_id ya
                                        # eliminado) POST /api/chat/approve daba 404 -> el
                                        # modelo reintentaba pidiendo permiso (loop infinito,
                                        # "Tool repetida sin progreso... cortando loop").
                                        # Fix: al expirar SIN respuesta (still None), emitir
                                        # approval_expired para que el frontend cierre el modal
                                        # y avise que el permiso caduco.
                                        _approval_events[approval_id].wait(timeout=APPROVAL_TIMEOUT)
                                        with _approval_lock:
                                            approval_result = _approval_results.pop(approval_id, None)
                                            _approval_events.pop(approval_id, None)
                                        if approval_result is None:
                                            yield f"data: {json.dumps({'type': 'approval_expired', 'approval_id': approval_id, 'tool': tool_name})}\n\n"
                                            result = {"error": f"Tool '{tool_name}' bloqueada: permiso caducado (no aprobada)"}
                                        elif approval_result:
                                            with _approval_conv_lock:
                                                _approved_tools_per_conv.setdefault(conversation_id, set()).add(tool_name)
                                            result = mcp_manager.execute_tool(tool_name, args)
                                        else:
                                            result = {"error": f"Tool '{tool_name}' bloqueada por el usuario (no aprobada)"}
                                else:
                                    try:
                                        result = mcp_manager.execute_tool(tool_name, args)
                                    except Exception as e:
                                        app.logger.error("Error ejecutando tool %s: %s", tool_name, e)
                                        result = {"error": f"Error ejecutando '{tool_name}': {e}"}
                                # Resultado sin progreso: null o dict con error.
                                # Si TODAS las tools de la iteracion fallan asi,
                                # el modelo esta en un loop de reintento.
                                # Convertir None a error descriptivo: el modelo recibe
                                # "null" como texto y no entiende que fallo, reintenta
                                # la misma tool en un loop infinito. Con error claro,
                                # el modelo intenta otra tool o responde directamente.
                                if result is None:
                                    result = {"error": f"La herramienta '{tool_name}' no devolvio resultado (posible error de conexion con el servidor MCP)"}
                                    iteracion_con_progreso = False
                                elif isinstance(result, dict) and result.get("error"):
                                    iteracion_con_progreso = False
                                result_text = json.dumps(result, ensure_ascii=False, indent=2)
                                # Defensa de segundo nivel: neutralizar fechas/dias
                                # absurdos (FILETIME corrupto del MCP AD decodificado
                                # como ano 9999, 2486159 o dias de 5851) antes de
                                # inyectarlos al prompt del LLM. La primera defensa
                                # esta en el propio servidor MCP AD (base.py).
                                result_text = sanitize_absurd_dates(result_text)
                                # Truncar resultados largos para el LLM: reducir
                                # el prompt tokens y mejorar tiempos de respuesta.
                                # Se mantiene un resumen con las primeras lineas y
                                # un indicador de longitud total. Acotado SIEMPRE
                                # por caracteres (bug 2026-09-24: resultados con
                                # lineas enormes reventaban el contexto -> 400 de
                                # llama.cpp "exceeds the available context size").
                                result_text_llm = truncate_tool_result_for_llm(result_text)
                                if raw_xml:
                                    # Continuacion en texto plano (flujo XML)
                                    messages.append({
                                        "role": "user",
                                        "content": f"[Resultado de {tool_name}]: {result_text_llm}",
                                    })
                                else:
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc.get("id", ""),
                                        "content": result_text_llm,
                                    })
                                args_text = json.dumps(args, ensure_ascii=False)
                                yield f"data: {json.dumps({'type': 'tool', 'name': tool_name, 'arguments': args_text, 'result': result_text})}\n\n"
                        break
                if not called_tools:
                    # Iteracion final sin tool calls: este es el texto que debe
                    # persistir. Las iteraciones anteriores con tools quedaron
                    # descartadas (iteration_text no se acumulo).
                    full_response += iteration_text
                    break
                # Si el modelo repite exactamente las mismas tools (nombre + args)
                # que en la iteracion anterior, esta en un loop sin progreso:
                # cortar para no agotar MAX_ITERATIONS con reintentos inutiles.
                if current_tool_keys == prev_tool_keys:
                    app.logger.warning("Tool call repetida sin progreso: %s. Cortando loop.", current_tool_keys)
                    break
                prev_tool_keys = current_tool_keys
                # Tambien cortar si las tools devuelven null/error en varias
                # iteraciones consecutivas, aunque sean tools distintas (el modelo
                # alterna busquedas que fallan). Evita agotar MAX_ITERATIONS.
                if iteracion_con_progreso:
                    iteraciones_sin_progreso = 0
                else:
                    iteraciones_sin_progreso += 1
                    if iteraciones_sin_progreso >= 2:
                        app.logger.warning(
                            "Tools sin progreso %d iteraciones seguidas (null/error). Cortando loop.",
                            iteraciones_sin_progreso,
                        )
                        break
                # Tambien cortar si el modelo hace tool calls en todas las
                # iteraciones y full_response sigue vacio: indica que el modelo
                # alterna entre herramientas diferentes (que funcionan) pero nunca
                # genera la respuesta final. Maximo 6 iteraciones sin respuesta.
                if called_tools and not full_response:
                    iteraciones_con_tools_sin_respuesta += 1
                    if iteraciones_con_tools_sin_respuesta >= 6:
                        app.logger.warning(
                            "Modelo hizo %d iteraciones con tools pero no genero respuesta final. Cortando loop.",
                            iteraciones_con_tools_sin_respuesta,
                        )
                        break
                else:
                    iteraciones_con_tools_sin_respuesta = 0
            except Exception as e:
                # En error, conservar el texto parcial de la iteracion actual
                # para no perder contenido que el usuario ya vio en el stream.
                full_response += iteration_text
                app.logger.warning("Error en streaming del LLM: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break

        persist_chat(mem_client, conversation_id, text, full_response, save_user=not resend)

        elapsed_ms = int((time.time() - start_time) * 1000)
        done_payload = {
            "type": "done",
            "response": full_response,
            "elapsed_ms": elapsed_ms,
            "tokens": total_completion_tokens,
            "prompt_tokens": total_prompt_tokens,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/chat/cancel", methods=["POST"])
def api_chat_cancel():
    """Permite parar la respuesta del modelo en cualquier momento.

    Marca la conversacion como cancelada; el stream de /api/chat la detectara
    y cortara la generacion de forma limpia.
    """
    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return jsonify({"error": "conversation_id requerido"}), 400
    _set_cancel(conversation_id, True)
    return jsonify({"status": "ok"})


@app.route("/api/chat/approve", methods=["POST"])
def api_chat_approve():
    """Responde a una solicitud de aprobacion de tool peligrosa (run_command).

    El stream de /api/chat emite approval_request y espera la decision; este
    endpoint desbloquea la espera con la respuesta del usuario. Si el
    approval_id no existe (ya expiro por timeout o se respondio antes),
    devuelve 404.
    """
    data = request.get_json(silent=True) or {}
    approval_id = data.get("approval_id")
    approved = bool(data.get("approved", False))
    if not approval_id:
        return jsonify({"error": "approval_id requerido"}), 400
    with _approval_lock:
        evt = _approval_events.get(approval_id)
        if evt is None:
            return jsonify({"error": "Aprobacion no encontrada o expirada"}), 404
        _approval_results[approval_id] = approved
    evt.set()
    return jsonify({"status": "ok", "approved": approved})


@app.route("/api/health")
def api_health():
    memory_health = memory_client.health() if memory_client else {"status": "not_ready"}
    return jsonify({
        "memory": memory_health,
        "mcp_tools": len(mcp_manager.all_tools) if mcp_manager else 0,
    })


@atexit.register
def cleanup():
    if mcp_manager:
        mcp_manager.stop_all()


if __name__ == "__main__":
    _init_globals()
    port = int(os.environ.get("PORT", "5000"))
    # use_reloader=False: el reloader de Flask duplica el proceso y relanza
    # los MCP servers (incompatible con subprocess MCP). El debug mode sigue
    # disponible via FLASK_DEBUG=1 sin el reinicio automatico.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    
    # Silenciar logs de acceso Werkzeug (GET/POST cada peticion HTTP)
    # Solo mostrar errores reales (WARNING+)
    import logging
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.WARNING)
    
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True, use_reloader=False)
