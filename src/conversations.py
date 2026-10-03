# src/conversations.py
# Blueprint de conversaciones: listado, creacion, detalle (con roles), metas
# (cache con TTL), borrado y renombrado. Funciones auxiliares para construir
# mensajes del chat y filtrar tool calls del historial.
#
# Se separa de app.py para mantener los modulos por debajo del limite de 600
# lineas. Para evitar el import circular app <-> conversations, este modulo NO
# importa app: recibe via set_memory_client_provider() la funcion que resuelve
# el MemoryClient segun la sesion (definida en app.py).
import html as html_module
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

conversations_bp = Blueprint("conversations", __name__, url_prefix="/api")

_memory_client_provider = None
_config_provider = None


def set_memory_client_provider(provider) -> None:
    """Inyecta el proveedor de MemoryClient definido en app.py.

    El provider es la funcion _get_memory_client de app.py, que resuelve el
    cliente segun la sesion (usuario aislado o cliente global en desarrollo).
    """
    global _memory_client_provider
    _memory_client_provider = provider


def set_config_provider(provider) -> None:
    """Inyecta el proveedor de config definido en app.py.

    El provider es la variable config de app.py (instancia de AppConfig).
    """
    global _config_provider
    _config_provider = provider


def _get_config():
    """Obtiene la instancia de config de app.py."""
    if _config_provider is None:
        raise RuntimeError("config provider no configurado")
    return _config_provider()


def _get_memory_client():
    if _memory_client_provider is None:
        raise RuntimeError("memory client provider no configurado")
    return _memory_client_provider()


# Cache de las memorias meta (titulos renombrados) de las conversaciones.
# Evita repetir las N peticiones get_history por conversacion en cada carga de
# la lista, que saturaban llama-memory (429 Too Many Requests) al haber muchas
# conversaciones. TTL corto porque los titulos rara vez cambian.
META_CACHE_TTL = 60  # segundos
# Workers maximos al resolver metas en paralelo. Se mantiene bajo (3) para no
# exceder el rate limit de llama-memory cuando se llena el cache.
META_MAX_WORKERS = 3

# Cache de metas: conversation_id -> (timestamp, metas). Protegido con lock
# porque api_conversations_meta se ejecuta en hilos de Flask.
_meta_cache: dict[str, tuple[float, list]] = {}
_meta_cache_lock = threading.Lock()

# Maximo de mensajes del historial reenviados al LLM como contexto. Se mantienen
# los mas recientes (los que aportan contexto util) para no exceder la ventana
# de contexto del modelo en conversaciones largas. Aumentado a 100 para mejorar
# la retencion de contexto en conversaciones largas (bug: segunda pregunta sin
# memoria de la primera).
MAX_HISTORY_MESSAGES = 100


# Limite de caracteres para resultados de tool calls en historial.
# Se truncana a este tamano para ahorrar tokens en el prompt del LLM.
TOOL_CALL_RESULT_LIMIT = 500

# Limite de caracteres para resultados de tool calls EN VIVO (los del
# historial usan TOOL_CALL_RESULT_LIMIT). Se mantiene un resumen con las
# primeras lineas y un indicador de longitud total.
LLM_RESULT_LIMIT = 2000


def truncate_tool_result_for_llm(result_text: str, limit: int = LLM_RESULT_LIMIT) -> str:
    """Trunca un resultado de tool call para el prompt del LLM.

    Mantiene las primeras 40 lineas y las ultimas 20 con un indicador de las
    omitidas, y despues acota POR CARACTERES. El recorte por lineas solo no
    basta: un resultado con lineas enormes (p.ej. stdout de run_powershell con
    un JSON gigante en una sola linea) pasaria entero, y lines[-20:] lo
    duplicaria cuando hay <= 60 lineas, reventando la ventana de contexto
    (bug 2026-09-24: 400 de llama.cpp "exceeds the available context size"
    con prompt de 258K tokens en la conversacion 'equipos online'). La salida
    siempre esta acotada a limit caracteres (+3 del sufijo). Funcion pura y
    testeable.
    """
    if not result_text or len(result_text) <= limit:
        return result_text
    lines = result_text.splitlines()
    if len(lines) > 60:
        head_tail = lines[:40] + [f"\n...[{len(lines) - 60} lineas mas truncadas...]\n"] + lines[-20:]
        out = "\n".join(head_tail)
    else:
        out = result_text
    if len(out) > limit:
        out = out[:limit] + "..."
    return out


def _is_tool_call_content(content: str) -> bool:
    """Determina si el contenido es una llamada a herramienta.

    Las tool calls se reconocen por:
    - JSON con campo "tool_calls" (formato nativo de llama.cpp)
    - Texto plano que empieza con [Resultado de (resultado de tool call)
    """
    if not content or not isinstance(content, str):
        return False
    # Verificar formato JSON con tool_calls
    if content.strip().startswith("{"):
        try:
            data = json.loads(content)
            if "tool_calls" in data:
                return True
        except json.JSONDecodeError:
            pass
    # Verificar resultado de tool call en texto plano
    if content.startswith("[Resultado de "):
        return True
    return False


def _truncate_tool_result(content: str) -> str:
    """Trunca un resultado de tool call a TOOL_CALL_RESULT_LIMIT caracteres.

    Mantiene las primeras lineas y un indicador de que fue truncado.
    Esto permite al LLM tener contexto de lo que hicieron las herramientas
    sin ocupar demasiado espacio en el prompt.
    """
    if len(content) <= TOOL_CALL_RESULT_LIMIT:
        return content
    lines = content.splitlines()
    # Mantener primeras 10 lineas + indicador + ultimas 5 lineas
    truncated_lines = lines[:10] + [f"\n...[{len(lines) - 15} lineas mas truncadas...]"] + lines[-5:]
    truncated = "\n".join(truncated_lines)
    if len(truncated) > TOOL_CALL_RESULT_LIMIT:
        truncated = truncated[:TOOL_CALL_RESULT_LIMIT] + "..."
    return truncated


# Años absurdos: el servidor AD MCP puede devolver FILETIME corruptos que el
# ldap3/ldap3 decodifica como fechas imposibles (por ejemplo un datetime con
# año 9999, 2486159 o 5851). Si esas fechas llegan al prompt, el LLM las
# "interpreta" de forma erronea y contamina toda la respuesta con calculos
# absurdos. Este patron captura fechas con año fuera del rango plausible
# (ano 1..2100) y dias de inactividad gigantes/corruptos, para neutralizarlas
# antes de inyectarlas al contexto del LLM (defensa de segundo nivel, la
# primera esta en el propio servidor MCP AD).
_ABSURD_YEAR_MAX = 2100  # Anos <= esto se consideran validos
_ABSURD_DATE_RE = re.compile(
    r"(?i)\b("
    # Cualquier numero de 4+ digitos (ano o valor corrupto)
    r"\d{4,}"
    r")\b"
)
_ABSURD_DAYS_RE = re.compile(
    r"(\b\d{5,}\s*días?\b|\b\d+[.,]?\d*\s*(?:[mM]illones?\s+)?[dD]ías?\b)"
)


def _neutralize_absurd_year(match: re.Match) -> str:
    """Callback de re.sub: reemplaza SOLO anos con valor > _ABSURD_YEAR_MAX.

    La distincion es por VALOR numerico, no por cantidad de digitos: "2026"
    (ano real) se conserva; "2486159", "5851", "9999" (FILETIME corrupto del
    AD MCP) se neutralizan a 1601/01/01. Un regex que cuente digitos ("28?...")
    tambien pilla fechas legitimas tipo 2026 (que es el ano actual), con
    falsos positivos. Testeable de forma pura.
    """
    try:
        value = int(match.group(0))
        if value > _ABSURD_YEAR_MAX:
            return "1601/01/01"
        return match.group(0)  # ano plausible: se conserva
    except (ValueError, TypeError):
        return match.group(0)


def sanitize_absurd_dates(text: str) -> str:
    """Neutraliza fechas/dias absurdos en texto de resultados de herramientas.

    Reemplaza anos literalmente imposibles (FILETIME corrupto del MCP AD: maximo
    64-bit -> ano 2486159/5851/9999) y conteos de dias gigantes/corruptos por
    marcadores seguros, para que el LLM no calcule sobre valores sin sentido.
    Los anos plausibles (<= _ABSURD_YEAR_MAX, p.ej. 2026) SI se conservan: este
    sanitizer neutraliza solo fechas corruptas, NO fechas reales. Si el texto
    no se puede procesar, se devuelve igual. Funcion pura y testeable.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        out = _ABSURD_DAYS_RE.sub("N nunca", text)
        if _ABSURD_DATE_RE.search(out):
            out = _ABSURD_DATE_RE.sub(_neutralize_absurd_year, out)
        return out
    except Exception:
        return text


def persist_chat(mem_client, conversation_id: str | None, user_text: str, assistant_response: str, save_user: bool = True) -> None:
    """Persiste la pregunta y la respuesta en la conversacion (con tags de rol).

    Los fallos se registran con logging para poder diagnosticarlos, en lugar de
    tragar las excepciones silenciosamente.

    save_user controla si el mensaje del usuario se guarda. En un chat normal
    (save_user=True) se guardan ambos. En un "resend" (save_user=False) el
    mensaje del usuario ya esta en memoria (se reenvia el mismo texto) asi que
    no se duplica; solo se guarda la nueva respuesta del asistente.
    """
    if save_user and conversation_id:
        try:
            mem_client.save_memory(user_text, conversation_id, tags=["role:user"])
        except Exception as e:
            current_app.logger.warning("Fallo al guardar pregunta en %s: %s", conversation_id, e)
    if conversation_id and assistant_response:
        try:
            mem_client.save_memory(assistant_response, conversation_id, tags=["role:assistant"])
        except Exception as e:
            current_app.logger.warning("Fallo al guardar respuesta en %s: %s", conversation_id, e)


def build_chat_messages(mem_client, conversation_id: str | None, text: str, user: str | None = None, tools: list | None = None, resources: list | None = None) -> list:
    """Construye la lista de mensajes para el LLM.

    Incluye el system prompt, el historial de la conversacion (limitado a
    MAX_HISTORY_MESSAGES, manteniendo los mas recientes) y el mensaje actual
    del usuario. Los marcadores de adjuntos [adjunto:...] se resuelven a su
    contenido (unico punto de resolucion: mensajes nuevos, resend e historial).
    Si falla la carga del historial, continua solo con system+user.

    Con tools, el system prompt se ampla con el bloque XML de herramientas
    (format_tools_prompt): las tools NO viajan en el payload del LLM (la grammar
    PEG de llama.cpp rompe con el thinking de Qwen 3.x), asi que el modelo debe
    conocerlas via prompt y generar  XML en texto plano.
    
    Los resources se incluyen al final del system prompt con su URI y descripcion.
    
    Las tool calls se truncan en lugar de excluirse: se envia al LLM un resumen
    truncado de los resultados de las herramientas para mantener contexto sin
    ocupar demasiado espacio en la ventana de contexto.
    """
    cfg = _get_config()
    
    if user is None:
        # Identidad canonica, no `cfg.memory_user` tal cual: `user` decide el
        # directorio de adjuntos y aqui se comparaba con la cadena que venia de
        # la sesion, que es la misma persona escrita de otra forma.
        from user_identity import canonical_user
        user = canonical_user(cfg.memory_user) or "default"
    
    # Importar aqui para evitar import circular
    from llm_client import format_tools_prompt
    from attachments import resolve_attachments
    
    system_content = cfg.system_prompt
    if tools or resources:
        # Los prompts por servidor MCP vienen de config.json (campo mcp_prompts);
        # format_tools_prompt agrupa las tools por servidor y usa el prompt
        # especifico de cada uno (o el default si no esta definido).
        system_content += "\n\n" + format_tools_prompt(tools or [], cfg.mcp_prompts, resources)
    
    messages = [{"role": "system", "content": system_content}]
    if conversation_id:
        try:
            mem = mem_client.get_history(conversation_id)
            history = mem.get("memories", [])[-MAX_HISTORY_MESSAGES:]
            for m in history:
                role = _llm_role(_memory_role(m))
                content = m.get("text", "")
                # Truncar tool calls del historial en lugar de excluirlos:
                # las llamadas a herramienta (JSON nativo con "tool_calls" o
                # texto [Resultado de...]) se truncan para mantener contexto
                # sin ocupar demasiado espacio en la ventana de contexto.
                if _is_tool_call_content(content):
                    content = _truncate_tool_result(content)
                content = sanitize_absurd_dates(content)
                if role == "user":
                    content = resolve_attachments(content, user, Path(cfg.upload_dir))
                messages.append({"role": role, "content": content})
        except Exception as e:
            # Logging silencioso en tests donde cfg no tiene logger
            pass
    
    messages.append({"role": "user", "content": resolve_attachments(text, user, Path(cfg.upload_dir))})
    return messages


@conversations_bp.route("/conversations", methods=["GET"])
def api_list_conversations():
    try:
        result = _get_memory_client().list_conversations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@conversations_bp.route("/conversations", methods=["POST"])
def api_create_conversation():
    data = request.get_json() or {}
    name = data.get("name", "Nueva conversacion")
    try:
        result = _get_memory_client().create_conversation(name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _memory_role(memory: dict) -> str:
    """Determina el rol de una memoria segun su tag role:*.

    Devuelve "user" o "assistant" cuando la memoria es parte del dialogo,
    y "note" (nota neutral) cuando no tiene tag de rol, para que las memorias
    guardadas por el asistente como hechos/preferencias no se confundan con
    respuestas del bot al mostrar una conversacion.
    """
    tags = memory.get("tags") or []
    for tag in tags:
        if tag == "role:user":
            return "user"
        if tag == "role:assistant":
            return "assistant"
    return "note"


def _llm_role(memory_role: str) -> str:
    """Convierte el rol de una memoria a un rol valido para el LLM.

    Las notas (memorias sin tag) se incluyen como contenido del asistente,
    ya que el modelo OpenAI-compatible solo acepta system/user/assistant/tool.
    """
    return memory_role if memory_role in ("user", "assistant") else "assistant"


@conversations_bp.route("/conversations/<conv_id>", methods=["GET"])
def api_get_conversation(conv_id):
    try:
        result = _get_memory_client().get_history(conv_id)
        for m in result.get("memories", []):
            m["role"] = _memory_role(m)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@conversations_bp.route("/conversations/meta")
def api_conversations_meta():
    """Return metadata memories for given conversation IDs.

    Query param: ids=id1,id2,...
    Used by frontend to apply renamed titles stored as meta memories.

    Las consultas por conversacion se resuelven en paralelo (pocos workers,
    con retraso) y se cachean con TTL para no saturar el rate limit de
    llama-memory cuando hay muchas conversaciones (antes disparaba 429).
    """
    try:
        qs = request.args.get("ids", "")
        if not qs.strip():
            return jsonify({})
        ids = [x.strip() for x in qs.split(",") if x.strip()]
        mc = _get_memory_client()
        with ThreadPoolExecutor(max_workers=min(META_MAX_WORKERS, len(ids))) as pool:
            results = list(pool.map(lambda cid: _get_cached_meta(mc, cid), ids))
        out = {}
        for cid, metas in zip(ids, results):
            if metas:
                out[cid] = metas
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_cached_meta(mc, conversation_id: str) -> list:
    """Devuelve las metas de una conversacion, usando cache con TTL.

    Si la entrada no esta en cache (o expiro), la obtiene de llama-memory y la
    cachea. Se serializa el acceso con un lock para no duplicar peticiones
    cuando varios hilos piden la misma conversacion a la vez.
    """
    now = time.time()
    with _meta_cache_lock:
        cached = _meta_cache.get(conversation_id)
        if cached and now - cached[0] < META_CACHE_TTL:
            return cached[1]
    # Fuera del lock: la peticion a llama-memory puede tardar y bloquearia a otros hilos
    metas = _fetch_conversation_meta(mc, conversation_id)
    with _meta_cache_lock:
        _meta_cache[conversation_id] = (time.time(), metas)
    return metas


def _fetch_conversation_meta(mc, conversation_id: str) -> list:
    """Devuelve las memorias meta (conversation_name) de una conversacion.

    Si la consulta falla, registra el problema y devuelve lista vacia para no
    romper el resto de conversaciones.
    """
    try:
        hist = mc.get_history(conversation_id, limit=200)
        metas = []
        for m in (hist or {}).get("memories", []):
            tags = [t.lower() for t in (m.get("tags") or [])]
            if "meta" in tags and "conversation_name" in tags:
                metas.append(m)
        return metas
    except Exception as e:
        current_app.logger.warning("Fallo al obtener meta de conversacion %s: %s", conversation_id, e)
        return []


@conversations_bp.route("/conversations/<conv_id>", methods=["DELETE"])
def api_delete_conversation(conv_id):
    try:
        result = _get_memory_client().delete_conversation(conv_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@conversations_bp.route("/conversations/<conv_id>/rename", methods=["POST"])
def api_rename_conversation(conv_id):
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "Campo 'name' requerido"}), 400
    try:
        mc = _get_memory_client()
        result = mc.rename_conversation(conv_id, new_name)
        # Invalidar cache de metas para esta conversacion: el nuevo titulo
        # debe reflejarse en la siguiente peticion de /api/conversations/meta.
        with _meta_cache_lock:
            _meta_cache.pop(conv_id, None)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fmt_timestamp(value) -> str:
    """Formatea una fecha ISO o timestamp numerico a dd/mm/yyyy hh:mm.

    Devuelve cadena vacia si el valor no se puede parsear.
    """
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


def _build_export_html(conversation_name: str, blocks: list, user: str = "") -> str:
    """Construye la pagina HTML de exportacion con CSS print embebido.

    blocks: lista de tuplas (role, text, created_at) filtradas a user/assistant.
    La pagina es autocontenida (sin dependencias externas) y lista para
    imprimir/guardar como PDF desde el dialogo de impresion del navegador.
    """
    rows = []
    for role, text, created in blocks:
        label = "Usuario" if role == "user" else "Asistente"
        ts = _fmt_timestamp(created)
        ts_html = f'<span class="ts">{html_module.escape(ts)}</span>' if ts else ""
        safe = html_module.escape(text).replace("\n", "<br>")
        rows.append(
            f'<div class="msg msg-{role}">'
            f'<div class="msg-label">{label}{ts_html}</div>'
            f'<div class="msg-body">{safe}</div>'
            f'</div>'
        )
    body = "\n".join(rows) if rows else (
        "<p class='empty'>Esta conversacion no tiene mensajes.</p>"
    )
    title = html_module.escape(conversation_name or "Conversacion")
    user_safe = html_module.escape(user or "")
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  body {{ font-family: "Segoe UI", Roboto, Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 24px; color: #1a1a1a; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #333; padding-bottom: 8px; margin-bottom: 6px; }}
  .subtitle {{ color: #666; font-size: 13px; margin-bottom: 24px; }}
  .msg {{ margin-bottom: 16px; }}
  .msg-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #888; margin-bottom: 4px; }}
  .msg-label .ts {{ float: right; text-transform: none; letter-spacing: 0; color: #aaa; }}
  .msg-body {{ font-size: 14px; line-height: 1.6; word-wrap: break-word; }}
  .msg-user .msg-body {{ background: #e3f2fd; border-radius: 8px; padding: 10px 14px; }}
  .msg-assistant .msg-body {{ background: #f5f5f5; border-radius: 8px; padding: 10px 14px; }}
  .empty {{ color: #888; font-style: italic; text-align: center; padding: 40px 0; }}
  .footer {{ margin-top: 30px; padding-top: 12px; border-top: 1px solid #ddd; font-size: 11px; color: #999; text-align: center; }}
  @media print {{
    body {{ padding: 0; max-width: 100%; }}
    .msg {{ break-inside: avoid; }}
    h1 {{ font-size: 18px; }}
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">Exportado el {date_str}{f" por {user_safe}" if user_safe else ""}</div>
{body}
<div class="footer">Generado por Voice-WebUI</div>
<script>
  window.addEventListener("load", function () {{
    setTimeout(function () {{ window.print(); }}, 400);
  }});
</script>
</body>
</html>"""


@conversations_bp.route("/conversations/<conv_id>/export")
def api_export_conversation(conv_id):
    """Exporta una conversacion como HTML listo para imprimir/guardar PDF.

    Devuelve una pagina HTML autocontenida (CSS embebido) con el dialogo
    completo: mensajes de usuario y asistente en orden cronologico. El
    frontend la abre en una ventana nueva y la pagina dispara window.print()
    automaticamente al cargar, para que el usuario la guarde como PDF desde
    el dialogo de impresion del navegador.
    """
    try:
        mc = _get_memory_client()
        result = mc.get_history(conv_id)
        memories = result.get("memories", [])

        # Titulo de la conversacion: usa el meta renombrado si existe
        name = "Conversacion"
        for m in _fetch_conversation_meta(mc, conv_id):
            text = m.get("text", "")
            if text.startswith("conversation_name="):
                name = text.split("=", 1)[1].strip() or name
                break

        blocks = []
        for m in memories:
            role = _memory_role(m)
            if role not in ("user", "assistant"):
                continue  # Saltar notas/metadatos neutrales
            blocks.append((role, m.get("text", ""), m.get("created_at")))

        user = getattr(mc, "user", "") or ""
        html_doc = _build_export_html(name, blocks, user)
        return Response(html_doc, mimetype="text/html")
    except Exception as e:
        return jsonify({"error": str(e)}), 500