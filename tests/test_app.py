# tests/test_app.py
# Tests de endpoints generales, autenticacion y chat.
# Los tests de conversaciones estan en tests/test_app_conversations.py.
import io
import json
import sys
import threading
import time
from unittest.mock import patch, MagicMock


def test_index_serves_html(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/")
    # Aun no existe static/index.html, pero el endpoint debe responder
    assert resp.status_code in (200, 404)


def test_init_globals_no_carga_whisper_perezoso(app_module):
    """Con carga perezosa, _init_globals NO debe cargar whisper al arrancar."""
    import app as app_module_ref

    mock_whisper = MagicMock()
    with patch.dict(sys.modules, {"whisper": mock_whisper}), \
         patch.object(app_module_ref, "AppConfig", return_value=app_module_ref.config), \
         patch.object(app_module_ref, "LLMClient", return_value=app_module_ref.llm_client), \
         patch.object(app_module_ref, "MemoryClient", return_value=app_module_ref.memory_client), \
         patch.object(app_module_ref, "MCPToolManager") as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_mcp_cls.return_value = mock_mcp
        app_module_ref._init_globals()

        # En carga perezosa no se toca whisper en el arranque
        mock_whisper.load_model.assert_not_called()
        assert app_module_ref.whisper_model is None


def test_transcribe_carga_whisper_perezoso(app_module):
    """api_transcribe debe cargar whisper bajo demanda (lazy) en CPU la primera
    vez que se llama, y luego reutilizar el modelo cargado."""
    import app as app_module_ref

    mock_whisper = MagicMock()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "Hola mundo"}
    mock_whisper.load_model.return_value = mock_model

    # Reset del global por si otro test lo dejo cargado
    app_module_ref.whisper_model = None

    with patch.dict(sys.modules, {"whisper": mock_whisper}):
        test_client = app_module_ref.app.test_client()
        data = {"audio": (io.BytesIO(b"fakeaudio"), "rec.webm")}
        resp = test_client.post("/api/transcribe", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert resp.get_json()["transcription"] == "Hola mundo"
    # whisper.load_model se llamo con device="cpu"
    mock_whisper.load_model.assert_called_once_with(
        app_module_ref.config.whisper_model, device="cpu"
    )
    # El modelo quedo cacheado en el global
    assert app_module_ref.whisper_model is mock_model
    # Se transcribio con el modelo cacheado
    mock_model.transcribe.assert_called_once()


def test_transcribe_reutiliza_whisper_ya_cargado(app_module):
    """Si whisper ya esta cargado, api_transcribe NO debe volver a cargarlo."""
    import app as app_module_ref

    mock_whisper = MagicMock()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "Ya cargado"}
    app_module_ref.whisper_model = mock_model

    with patch.dict(sys.modules, {"whisper": mock_whisper}):
        test_client = app_module_ref.app.test_client()
        data = {"audio": (io.BytesIO(b"fakeaudio"), "rec.webm")}
        resp = test_client.post("/api/transcribe", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert resp.get_json()["transcription"] == "Ya cargado"
    # No se debe volver a llamar load_model
    mock_whisper.load_model.assert_not_called()
    # Y no se debe sobrescribir el modelo ya cargado
    assert app_module_ref.whisper_model is mock_model


def test_get_config(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "system_prompt" in data
    assert "llama_url" in data


def test_post_config_updates(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/config", json={"system_prompt": "Nuevo"})
    assert resp.status_code == 200
    resp2 = test_client.get("/api/config")
    data = resp2.get_json()
    assert data["system_prompt"] == "Nuevo"


def test_transcribe(app_module):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "Hola mundo"}
    app_module.whisper_model = mock_model
    test_client = app_module.app.test_client()
    data = {"audio": (io.BytesIO(b"fakeaudio"), "rec.webm")}
    resp = test_client.post("/api/transcribe", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.get_json()["transcription"] == "Hola mundo"


def test_mcp_status(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/api/mcp-status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "servers" in data
    assert "total_tools" in data


def test_api_mcp_add_http(app_module):
    """POST /api/mcp/add con transport http debe delegar en add_http_server
    (url/headers/timeout) y no exigir command."""
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/mcp/add", json={
        "name": "httpsrv",
        "transport": "http",
        "url": "http://localhost:8111/mcp",
        "headers": {"Authorization": "Bearer x"},
        "timeout": 15,
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    app_module.mcp_manager.add_http_server.assert_called_once_with(
        "httpsrv",
        "http://localhost:8111/mcp",
        headers={"Authorization": "Bearer x"},
        timeout=15,
    )


def test_api_mcp_add_http_sin_url_devuelve_400(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/mcp/add", json={"name": "x", "transport": "http"})
    assert resp.status_code == 400


def test_api_mcp_add_stdio_requiere_command(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/mcp/add", json={"name": "x", "transport": "stdio"})
    assert resp.status_code == 400


def test_login_sets_user(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/login", json={"name": "ana"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["user"] == "ana"


def test_login_requires_name(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/login", json={})
    assert resp.status_code == 400


def test_me_returns_user(app_module):
    test_client = app_module.app.test_client()
    test_client.post("/api/login", json={"name": "ana"})
    resp = test_client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json()["user"] == "ana"


def test_me_returns_null_when_not_logged(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json()["user"] is None


def test_logout_clears_user(app_module):
    test_client = app_module.app.test_client()
    test_client.post("/api/login", json={"name": "ana"})
    resp = test_client.post("/api/logout")
    assert resp.status_code == 200
    me = test_client.get("/api/me").get_json()
    assert me["user"] is None


def test_login_emite_cookie_con_caducidad(app_module):
    """La cookie de sesion debe llevar Expires.

    Sin Expires la cookie es de sesion: el navegador la borra al cerrar y el
    usuario vuelve al overlay de login, que intercepta todos los clics y deja
    el boton de microfono inalcanzable.
    """
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/login", json={"name": "ana"})
    cookies = resp.headers.getlist("Set-Cookie")
    session_cookie = next((c for c in cookies if c.startswith("session=")), None)
    assert session_cookie is not None, "no se emitio la cookie de sesion"
    assert "Expires=" in session_cookie, f"la cookie no lleva caducidad: {session_cookie}"


def test_sesion_marcada_como_permanente(app_module):
    """Tras el login, la sesion debe marcarse como permanente.

    Cubre la capa de Flask (session.permanent), mientras que
    test_login_emite_cookie_con_caducidad cubre la capa del navegador.
    """
    test_client = app_module.app.test_client()
    test_client.post("/api/login", json={"name": "ana"})
    with test_client.session_transaction() as sess:
        assert sess.permanent is True


def test_chat_guarda_pregunta_y_respuesta_con_tags(app_module):
    """Al hacer chat, se guarda la pregunta con tag role:user y la respuesta con role:assistant."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    # Mock del streaming del LLM: produce un delta y termina
    def _fake_chat_stream(messages, tools, known_tool_names=None):
        yield {"type": "delta", "content": "Respuesta del asistente"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        # Consumir el stream completo
        resp = test_client.post("/api/chat", json={"text": "Mi pregunta", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)  # drenar el stream

        # Verificar que se guardaron 2 memorias con tags correctos
        call_args_list = created["mc"].save_memory.call_args_list
        assert len(call_args_list) == 2, f"Se esperaban 2 save_memory, hay {len(call_args_list)}"
        # Primera: la pregunta del usuario
        user_call = call_args_list[0]
        assert user_call[0][0] == "Mi pregunta"
        assert user_call[0][1] == "conv-1"  # conversation_id posicional
        assert "role:user" in user_call[1]["tags"]
        # Segunda: la respuesta del asistente
        asst_call = call_args_list[1]
        assert asst_call[0][0] == "Respuesta del asistente"
        assert asst_call[0][1] == "conv-1"
        assert "role:assistant" in asst_call[1]["tags"]


def test_chat_reenvia_contexto_con_roles_correctos(app_module):
    """Al reenviar contexto al modelo, cada memoria se usa con su role correcto."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "Pregunta previa", "tags": ["role:user"]},
                {"text": "Respuesta previa", "tags": ["role:assistant"]},
                {"text": "Sin role", "tags": None},
            ]
        }
        created["mc"] = mc
        return mc

    captured_messages = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        captured_messages["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post("/api/chat", json={"text": "Nueva pregunta", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

        msgs = captured_messages["messages"]
        # system + pregunta previa (user) + respuesta previa (assistant) + sin role (assistant) + pregunta nueva
        roles = [m["role"] for m in msgs]
        assert roles[0] == "system"
        assert roles[1] == "user"       # pregunta previa
        assert roles[2] == "assistant"  # respuesta previa
        assert roles[3] == "assistant"  # sin role -> default assistant
        assert roles[-1] == "user"      # pregunta nueva
        assert msgs[1]["content"] == "Pregunta previa"
        assert msgs[2]["content"] == "Respuesta previa"


def test_chat_reenvia_nota_como_assistant_al_llm(app_module):
    """Las notas (memorias sin tag) se reenvian al LLM como assistant, no como 'note'."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "Nota sin tag", "tags": []},
                {"text": "Pregunta", "tags": ["role:user"]},
            ]
        }
        created["mc"] = mc
        return mc

    captured = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post("/api/chat", json={"text": "Nueva", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

        msgs = captured["messages"]
        # system + nota (assistant) + pregunta previa (user) + pregunta nueva (user)
        roles = [m["role"] for m in msgs]
        assert roles[0] == "system"
        # La nota sin tag se envia como assistant
        assert any(m["role"] == "assistant" and m["content"] == "Nota sin tag" for m in msgs)
        assert roles[-1] == "user"


def test_chat_evento_tool_incluye_argumentos(app_module):
    """El evento tool del stream SSE incluye name, arguments y result."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Solo la primera iteracion emite tool_call; las siguientes ya no,
        # simulando que el modelo recibe el resultado y responde.
        if calls["n"] == 0:
            calls["n"] += 1
            yield {"type": "tool_calls", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "web_search", "arguments": '{"query": "llama"}'}}
            ]}
        else:
            yield {"type": "delta", "content": "Resultado final"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.return_value = {"results": ["ok"]}
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "busca", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # Localizar el evento tool
        tool_event = None
        for line in body.splitlines():
            if line.startswith("data: "):
                evt = json.loads(line[6:])
                if evt.get("type") == "tool":
                    tool_event = evt
                    break
        assert tool_event is not None
        assert tool_event["name"] == "web_search"
        assert "query" in tool_event["arguments"]
        assert tool_event["arguments"] == '{"query": "llama"}'
        assert "ok" in tool_event["result"]

        # Verificar que la herramienta se ejecuto con los argumentos parseados
        app_module_ref.mcp_manager.execute_tool.assert_called_once_with("web_search", {"query": "llama"})


def test_chat_continuacion_xml_usa_texto_plano(app_module):
    """Con XML raw (flujo Qwen sin payload tools), la continuacion usa texto plano:
    assistant content = XML crudo y resultado como user (NUNCA role:tool, que
    crashea llama.cpp sin tools en payload). Regresion bug 2026-09-08:
    ChunkedEncodingError en la iteracion 2 al enviar role:tool."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}
    xml = "<tool_call>\n<function=web_search>\n<parameter=query>\nllama\n</parameter>\n</function>\n</tool_call>"

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "tool_calls", "raw": xml, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "web_search", "arguments": '{"query": "llama"}'}}
            ]}
        else:
            yield {"type": "delta", "content": "Resultado final"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.return_value = {"results": ["ok"]}
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "busca", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # El segundo mensaje del chat (iteracion 2) debe contener el resultado
        # como user con texto plano, no role:tool
        second_call_msgs = app_module_ref.llm_client.chat_stream.call_args_list[1][0][0]
        # assistant con el XML crudo en content
        assistant = second_call_msgs[-2]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == xml
        assert "tool_calls" not in assistant, "No debe usar tool_calls nativos con XML"
        # resultado como user, no role:tool
        result_msg = second_call_msgs[-1]
        assert result_msg["role"] == "user", f"Debe ser user, got {result_msg.get('role')}"
        assert "web_search" in result_msg["content"]
        assert "ok" in result_msg["content"]


def test_chat_ejecuta_tool_peg_native_con_prefijo_servidor(app_module):
    """Cuando el modelo genera la tool call en formato peg-native ([call:...]),
    api_chat debe ejecutarla: normalizar el nombre 'dia.get_timezone' a la tool
    real 'get_timezone' (el modelo prefija el servidor MCP del system prompt) y
    continuar con texto plano (bug 2026-09-12: el modelo genera [call:...] y la
    app no la reconocia, mostrandola como texto)."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}
    raw = '[call:diamcp://context/dia.get_timezone|{"params":{"timezone":"Europa/Madrid"}}]'

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "tool_calls", "raw": raw, "tool_calls": [
                {"id": "call_peg_1", "type": "function",
                 "function": {"name": "dia.get_timezone", "arguments": '{"timezone": "Europa/Madrid"}'}}
            ]}
        else:
            yield {"type": "delta", "content": "La zona horaria es Europa/Madrid"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.return_value = {"timezone": "Europa/Madrid"}
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "zona horaria", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # La tool se ejecuto con el nombre normalizado (sin prefijo de servidor)
        app_module_ref.mcp_manager.execute_tool.assert_called_once_with(
            "get_timezone", {"timezone": "Europa/Madrid"}
        )

        # Continuacion en texto plano: assistant content = [call:...] crudo,
        # resultado como user (NUNCA role:tool, bug 2026-09-08)
        second_call_msgs = app_module_ref.llm_client.chat_stream.call_args_list[1][0][0]
        assistant = second_call_msgs[-2]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == raw
        assert "tool_calls" not in assistant, "No debe usar tool_calls nativos con raw"
        result_msg = second_call_msgs[-1]
        assert result_msg["role"] == "user", f"Debe ser user, got {result_msg.get('role')}"
        assert "get_timezone" in result_msg["content"]

        # Respuesta final presente
        assert "La zona horaria es Europa/Madrid" in body


def test_chat_corta_loop_cuando_la_misma_tool_se_repite(app_module):
    """Si el modelo llama la misma tool con los mismos argumentos dos veces
    seguidas, api_chat corta el loop en vez de repetir hasta MAX_ITERATIONS."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Simula un modelo en loop: siempre devuelve la misma tool call
        calls["n"] += 1
        yield {"type": "tool_calls", "tool_calls": [
            {"id": f"call_{calls['n']}", "type": "function",
             "function": {"name": "web_search", "arguments": '{"query": "llama"}'}}
        ]}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.return_value = {"results": ["ok"]}
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "busca", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # El loop debe cortarse: la misma tool web_search se repite -> 2 llamadas
        # a chat_stream (la primera emite la tool, la segunda la repite y se corta),
        # NO las MAX_ITERATIONS (5) que habria sin la proteccion.
        assert calls["n"] == 2, f"Esperaba 2 llamadas a chat_stream, hubo {calls['n']}"

        # Debe haber un evento done al final (respuesta vacia porque el modelo no respondio)
        assert '{"type": "done"' in body


def test_chat_ejecuta_tool_de_memoria_en_lugar_de_mcp(app_module):
    """Si el modelo llama una tool de memoria (p. ej. save_memory), api_chat la
    ejecuta via execute_memory_tool (memory_client) y NO via mcp_manager."""
    import app as app_module_ref

    # Configurar el memory_client global con list_tools para que get_memory_tools
    # devuelva la definicion de save_memory, y con save_memory para el resultado.
    app_module_ref.memory_client.list_tools.return_value = [
        {"name": "save_memory", "description": "Guarda memoria",
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    ]
    app_module_ref.memory_client.save_memory.return_value = {"ok": True}

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Invariante cambiada el 2026-10-01 con la decision del tool calling
        # nativo (commit d8a4c1c): las tools viajan en el payload como JSON
        # OpenAI, que es lo que llama.cpp procesa de forma nativa, en vez de
        # depender solo del parsing XML del system prompt.
        #
        # Lo que este test protege de verdad es otro: que la tool de memoria se
        # ejecute por memory_client y NO por mcp_manager. Eso se sigue
        # comprobando abajo.
        #
        # NOTA: hoy las tools estan en los DOS sitios (payload JSON nativo +
        # descripcion XML en el system prompt). El payload es lo que dispara la
        # llamada; el XML queda como descripcion. El bug del 2026-09-12
        # ("tools en ambos formato confunden al modelo") esta pendiente de
        # verificarse con el camino nativo: si se confirma, el XML sobra.
        assert tools is not None, "las tools deben viajar en el payload (JSON nativo)"
        nombres = {t["function"]["name"] for t in tools}
        assert "save_memory" in nombres, f"save_memory debe ir en el payload: {nombres}"
        assert all(t["type"] == "function" for t in tools), "las tools van con type=function"
        if calls["n"] == 0:
            calls["n"] += 1
            yield {"type": "tool_calls", "raw": "<tool_call>\n<function=save_memory>\n<parameter=text>\ndato importante\n</parameter>\n</function>\n</tool_call>", "tool_calls": [
                {"id": "call_mem", "type": "function",
                 "function": {"name": "save_memory", "arguments": '{"text": "dato importante"}'}}
            ]}
        else:
            yield {"type": "delta", "content": "Guardado"}

    with patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "recuerda esto", "conversation_id": "conv-1"})
        assert resp.status_code == 200

        # La tool de memoria se ejecuto en el memory_client, no en mcp_manager
        app_module_ref.memory_client.save_memory.assert_called_once_with("dato importante", None, None)
        app_module_ref.mcp_manager.execute_tool.assert_not_called()


def test_chat_corta_loop_cuando_tools_devuelven_null_o_error(app_module):
    """Si el modelo llama tools distintas (alternando busquedas) pero todas
    devuelven null o error (sin progreso), api_chat corta el loop en vez de
    llegar a MAX_ITERATIONS. Cubre el caso real donde web-search devuelve null
    y el modelo reintenta con otras queries/tools."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Simula un modelo en loop que alterna tools de busqueda distintas
        # (full-web-search, get-web-search-summaries) con queries distintas.
        calls["n"] += 1
        tool_names = ["full-web-search", "get-web-search-summaries"]
        name = tool_names[calls["n"] % 2]
        yield {"type": "tool_calls", "tool_calls": [
            {"id": f"call_{calls['n']}", "type": "function",
             "function": {"name": name, "arguments": f'{{"query": "q{calls["n"]}"}}'}}
        ]}

    def _execute_tool(name, args):
        # Todas las tools devuelven null (saturacion del MCP web-search)
        return None

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.side_effect = _execute_tool
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "busca noticias", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # El loop debe cortarse pronto: tras 2 iteraciones sin progreso (tools
        # que devuelven null), NO llegar a MAX_ITERATIONS (5).
        assert calls["n"] <= 3, f"Esperaba cortar el loop en <=3 iteraciones, hubo {calls['n']}"

        # Debe haber un evento done al final
        assert '{"type": "done"' in body


def test_chat_cancel_marca_y_chat_limpia_flag(app_module):
    """POST /api/chat/cancel marca la conversacion como cancelada (via
    _set_cancel) y al iniciar un chat la flag se limpia (via _set_cancel False).
    """
    import app as app_module_ref

    test_client = app_module_ref.app.test_client()
    resp = test_client.post("/api/chat/cancel", json={"conversation_id": "conv-1"})
    assert resp.status_code == 200
    assert app_module_ref._is_cancelled("conv-1") is True

    # Al iniciar un chat para esa conversacion, la flag se limpia
    with patch.object(app_module_ref.llm_client, "chat_stream",
                      return_value=iter([{"type": "delta", "content": "ok"}])):
        resp2 = test_client.post("/api/chat", json={"text": "hola", "conversation_id": "conv-1"})
        assert resp2.status_code == 200
    assert app_module_ref._is_cancelled("conv-1") is False


def test_chat_resend_no_guarda_pregunta_pero_si_respuesta(app_module):
    """resend=true: el mensaje del usuario NO se guarda (ya esta en memoria)
    pero la respuesta del asistente SI se guarda con role:assistant."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        yield {"type": "delta", "content": "Nueva respuesta"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post("/api/chat", json={"text": "Pregunta", "conversation_id": "conv-1", "resend": True})
        assert resp.status_code == 200
        list(resp.response)

        # Solo 1 save_memory (la respuesta), NO 2 (no guarda la pregunta)
        call_args_list = created["mc"].save_memory.call_args_list
        assert len(call_args_list) == 1, f"Esperaba 1 save_memory, hubo {len(call_args_list)}"
        # La unica llamada es la respuesta con role:assistant
        assert call_args_list[0][0][0] == "Nueva respuesta"
        assert "role:assistant" in call_args_list[0][1]["tags"]


def test_chat_resend_sin_text_devuelve_400(app_module):
    """resend=true sin texto es tan invalido como resend=false sin texto."""
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/chat", json={"text": "", "conversation_id": "conv-1", "resend": True})
    assert resp.status_code == 400


def test_chat_descarta_texto_de_iteraciones_con_tools(app_module):
    """Si el modelo genera texto + tool calls en una iteracion y la respuesta
    final en la siguiente (p. ej. responde y luego llama save_memory como pide
    el system_prompt), full_response del evento done SOLO debe contener la
    respuesta final. El texto pre-tool es preambulo y no debe duplicarse."""
    import app as app_module_ref
    calls = {"n": 0}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        # save_memory se ejecuta como tool real en la iteracion 1; su resultado
        # debe ser serializable para emitirse en el evento tool del stream.
        mc.save_memory.return_value = {"ok": True, "memory_id": "m1"}
        return mc

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Iteracion 1: el modelo genera texto PRE-tool (p. ej. una respuesta
        # degenerada) y LUEGO llama save_memory (flujo obligatorio del
        # system_prompt). Este texto no debe acumularse en full_response.
        if calls["n"] == 0:
            calls["n"] += 1
            yield {"type": "delta", "content": "Texto pre-tool degenerado"}
            yield {"type": "tool_calls", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "save_memory", "arguments": '{"text": "x"}'}}
            ]}
        # Iteracion 2: respuesta final (la unica que debe persistir)
        else:
            yield {"type": "delta", "content": "Respuesta final correcta"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        # Login para que _get_memory_client use _memory_for_user (mock)
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post("/api/chat", json={"text": "pregunta", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        done_response = None
        deltas_vistos = []
        for line in body.splitlines():
            if line.startswith("data: "):
                evt = json.loads(line[6:])
                if evt.get("type") == "delta":
                    deltas_vistos.append(evt.get("content", ""))
                elif evt.get("type") == "done":
                    done_response = evt.get("response", "")

        # El texto pre-tool NO debe estar en la respuesta final
        assert done_response == "Respuesta final correcta", \
            f"full_response no debe incluir texto pre-tool: {done_response!r}"
        # El texto pre-tool sigue emitiendose en el streaming (deltas), pero
        # no se persiste en el done
        assert "".join(deltas_vistos) == "Texto pre-tool degeneradoRespuesta final correcta"


def test_chat_run_command_pide_aprobacion_y_ejecuta_al_aprobar(app_module):
    """run_command (tool peligrosa) requiere aprobacion explicita: el stream
    emite approval_request, espera la decision de POST /api/chat/approve, y
    solo con approved=true ejecuta la tool (bug 2026-09-07: el modelo alucino
    run_command sin control del usuario)."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Iteracion 1: el modelo llama run_command (requiere aprobacion)
        if calls["n"] == 0:
            calls["n"] += 1
            yield {"type": "delta", "content": "Voy a ejecutarlo."}
            yield {"type": "tool_calls", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "run_command", "arguments": json.dumps({"command": "dir"})}}
            ]}
        # Iteracion 2: respuesta final tras ver el resultado de la tool
        else:
            yield {"type": "delta", "content": "Listo."}

    captured = {"executed": []}

    def _fake_execute_tool(name, args):
        captured["executed"].append((name, args))
        return {"output": "ok"}

    old_timeout = app_module_ref.APPROVAL_TIMEOUT
    app_module_ref.APPROVAL_TIMEOUT = 2  # fail-fast si el test falla antes de aprobar
    try:
        # Importante: el join del thread debe hacerse DENTRO del with patch.
        # El generador del stream se ejecuta en el thread y continua tras el
        # evt.set() del approve; si los patches se restauran antes (join en el
        # finally), execute_tool ya no esta patcheado y el test es flaky.
        with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
             patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream), \
             patch.object(app_module_ref.mcp_manager, "execute_tool", side_effect=_fake_execute_tool):
            result = {}

            def _run_chat():
                tc = app_module_ref.app.test_client()
                tc.post("/api/login", json={"name": "ana"})
                resp = tc.post("/api/chat", json={"text": "Ejecuta dir", "conversation_id": "conv-1"})
                result["chunks"] = list(resp.response)

            t = threading.Thread(target=_run_chat)
            t.start()

            # Esperar a que se registre la aprobacion pendiente (el stream espera)
            deadline = time.time() + 5
            approval_id = None
            while time.time() < deadline:
                with app_module_ref._approval_lock:
                    if app_module_ref._approval_events:
                        approval_id = next(iter(app_module_ref._approval_events))
                        break
                time.sleep(0.05)
            assert approval_id is not None, "No se emitio approval_request"

            # Aprobar desde el hilo principal
            tc2 = app_module_ref.app.test_client()
            resp_approve = tc2.post("/api/chat/approve", json={"approval_id": approval_id, "approved": True})
            assert resp_approve.status_code == 200
            t.join(timeout=10)
            assert not t.is_alive(), "El chat no termino tras aprobar"
    finally:
        app_module_ref.APPROVAL_TIMEOUT = old_timeout

    assert captured["executed"] == [("run_command", {"command": "dir"})], \
        f"execute_tool debe llamarse con run_command al aprobar: {captured['executed']}"

    events = []
    for chunk in result["chunks"]:
        for line in chunk.decode("utf-8").splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert "approval_request" in types, "El stream debe emitir approval_request"
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response"] == "Listo."


def test_chat_run_command_bloquea_si_no_se_aprueba(app_module):
    """Fail-safe: si el usuario no aprueba (timeout), la tool NO se ejecuta y
    el modelo recibe un resultado de bloqueo."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        yield {"type": "delta", "content": "Voy a ejecutarlo."}
        yield {"type": "tool_calls", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "run_command", "arguments": json.dumps({"command": "dir"})}}
        ]}
        yield {"type": "delta", "content": "No pude."}

    captured = {"executed": []}

    def _fake_execute_tool(name, args):
        captured["executed"].append((name, args))
        return {"output": "ok"}

    old_timeout = app_module_ref.APPROVAL_TIMEOUT
    app_module_ref.APPROVAL_TIMEOUT = 0.3  # fail-fast en el test
    try:
        with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
             patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream), \
             patch.object(app_module_ref.mcp_manager, "execute_tool", side_effect=_fake_execute_tool):
            result = {}

            def _run_chat():
                tc = app_module_ref.app.test_client()
                tc.post("/api/login", json={"name": "ana"})
                resp = tc.post("/api/chat", json={"text": "Ejecuta dir", "conversation_id": "conv-1"})
                result["chunks"] = list(resp.response)

            t = threading.Thread(target=_run_chat)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive(), "El chat no termino tras el timeout de aprobacion"

            assert captured["executed"] == [], \
                f"La tool NO debe ejecutarse sin aprobacion: {captured['executed']}"

            events = []
            for chunk in result["chunks"]:
                for line in chunk.decode("utf-8").splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
            tool_evts = [e for e in events if e["type"] == "tool"]
            assert tool_evts, "Debe haber un evento tool con el resultado de bloqueo"
            assert "bloqueada" in tool_evts[0].get("result", "").lower(), \
                f"El resultado debe indicar bloqueo: {tool_evts[0]!r}"
    finally:
        app_module_ref.APPROVAL_TIMEOUT = old_timeout


def test_api_chat_approve_con_id_inexistente_devuelve_404(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/chat/approve", json={"approval_id": "no-existe", "approved": True})
    assert resp.status_code == 404


def test_chat_no_ejecuta_tool_inexistente(app_module):
    """Bug 2026-09-14: el modelo alucina tools que no existen (mcp_memory_
    search_memories, query, limit). La app NO debe ejecutarlas: debe registrar
    el rechazo y devolver un resultado de error claro al modelo, sin llamar a
    execute_tool."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        if calls["n"] == 0:
            calls["n"] += 1
            yield {"type": "tool_calls", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "query", "arguments": "{}"}}
            ]}
        else:
            yield {"type": "delta", "content": "No tengo esa herramienta."}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={"text": "busca", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # La tool inexistente NO debe llegar a execute_tool
        app_module_ref.mcp_manager.execute_tool.assert_not_called()

        # El stream debe terminar con una respuesta del asistente
        assert "No tengo esa herramienta." in body
def test_chat_aprobacion_una_vez_por_conversacion(app_module):
    """La aprobacion de tools peligrosas (run_powershell) se pide UNA vez por
    conversacion: primera llamada -> modal; aprobada -> el resto de la misma
    conversacion ejecuta sin modal. Otra conversacion vuelve a pedir permiso
    (2026-09-15)."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            yield {"type": "tool_calls", "tool_calls": [
                {"id": f"call_{calls['n']}", "type": "function",
                 "function": {"name": "run_powershell",
                              "arguments": json.dumps({"script": "Get-Date"})}}
            ]}
        else:
            yield {"type": "delta", "content": f"respuesta {calls['n']}"}

    executed = []

    def _fake_execute_tool(name, args):
        executed.append((name, args))
        return {"output": "ok"}

    old_timeout = app_module_ref.APPROVAL_TIMEOUT
    app_module_ref.APPROVAL_TIMEOUT = 2  # fail-fast

    def _wait_approval_id():
        deadline = time.time() + 5
        while time.time() < deadline:
            with app_module_ref._approval_lock:
                if app_module_ref._approval_events:
                    return next(iter(app_module_ref._approval_events))
            time.sleep(0.05)
        raise AssertionError("No se emitio approval_request")

    try:
        # Importante: el join de los threads DENTRO del with patch (ver
        # test_chat_run_command_pide_aprobacion_y_ejecuta_al_aprobar).
        with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
             patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream), \
             patch.object(app_module_ref.mcp_manager, "execute_tool", side_effect=_fake_execute_tool):
            result = {}

            def _run_chat(conversation_id, text):
                tc = app_module_ref.app.test_client()
                tc.post("/api/login", json={"name": "ana"})
                resp = tc.post("/api/chat", json={"text": text, "conversation_id": conversation_id})
                result[conversation_id] = b"".join(resp.response).decode("utf-8")

            # 1a. Primera llamada (conv-apr-1): modal de aprobacion
            t1 = threading.Thread(target=_run_chat, args=("conv-apr-1", "hora"))
            t1.start()
            approval_id = _wait_approval_id()
            tc2 = app_module_ref.app.test_client()
            resp_approve = tc2.post("/api/chat/approve", json={"approval_id": approval_id, "approved": True})
            assert resp_approve.status_code == 200
            t1.join(timeout=10)
            assert not t1.is_alive(), "El chat no termino tras aprobar"
            assert ("run_powershell", {"script": "Get-Date"}) in executed
            assert "approval_request" in result["conv-apr-1"]

            # 1b. Misma conversacion (conv-apr-1): sin modal, ejecuta directo
            executed.clear()
            t2 = threading.Thread(target=_run_chat, args=("conv-apr-1", "hora otra vez"))
            t2.start()
            t2.join(timeout=10)
            assert not t2.is_alive(), "El chat no termino sin modal"
            assert "approval_request" not in result["conv-apr-1"]
            assert ("run_powershell", {"script": "Get-Date"}) in executed

            # 1c. Conversacion distinta (conv-apr-2): modal otra vez; sin
            # aprobar, la tool NO se ejecuta (bloqueada por timeout)
            executed.clear()
            t3 = threading.Thread(target=_run_chat, args=("conv-apr-2", "hora"))
            t3.start()
            approval_id2 = _wait_approval_id()
            # NO se aprueba: el stream espera APPROVAL_TIMEOUT y bloquea la tool
            t3.join(timeout=10)
            assert not t3.is_alive(), "El chat no termino tras bloquear la tool"
            assert "approval_request" in result["conv-apr-2"]
            assert executed == []
    finally:
        app_module_ref.APPROVAL_TIMEOUT = old_timeout


def test_chat_approval_expira_y_emite_approval_expired(app_module):
    """Bug 2026-09-18 (reportado por el usuario): apruebo y no ejecuta.
    Cuando APPROVAL_TIMEOUT expira, el backend hace pop(_approval_events) de
    forma SILENCIOSA: el frontend no recibe ningun evento SSE, el modal queda
    abierto pidiendo permiso, y si el usuario aprueba tarde (sobre un
    approval_id ya eliminado por el timeout), POST /api/chat/approve devuelve
    404 -> la tool nunca ejecuta y el modelo reintenta pidiendo permiso de
    nuevo (loop que reproduce el usuario).

    Requisito (fix): al expirar, el stream SSE debe emitir un evento
    'approval_expired' que incluya el approval_id, para que el frontend cierre
    el modal y avise al usuario que el permiso caduco."""
    import app as app_module_ref

    old_timeout = app_module_ref.APPROVAL_TIMEOUT
    app_module_ref.APPROVAL_TIMEOUT = 0.3  # FAIL-FAST: expira durante el test

    captured = {"executed": [], "asked": []}
    chunk_data = []

    def _fake_memory_for_user(user, memory_client=None):
        # Mismo scaffolding que los tests de aprobacion: nunca conecta a :9001
        m = MagicMock()
        m.get_memory_tools.return_value = []
        return m

    def _fake_execute_tool(name, args):
        captured["executed"].append((name, args))
        return {"output": "ok"}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # El modelo intenta usar run_powershell (requiere aprobacion) y NO lo
        # aprueba: el stream debe esperar APPROVAL_TIMEOUT y luego emitir
        # approval_expired (hoy no lo emite -> RED)
        captured["asked"].append(True)
        yield {"type": "tool_calls", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "run_powershell",
                          "arguments": json.dumps({"script": "Get-Process"})}}
        ]}

    def _run_chat():
        tc = app_module_ref.app.test_client()
        tc.post("/api/login", json={"name": "ana"})
        resp = tc.post("/api/chat", json={"text": "Ejecuta ps", "conversation_id": "conv-1"})
        chunk_data.extend(resp.response)

    try:
        with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
                patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream), \
                patch.object(app_module_ref.mcp_manager, "execute_tool", side_effect=_fake_execute_tool):
            t = threading.Thread(target=_run_chat)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive(), "El chat no termino tras el timeout de aprobacion"

        events = []
        for chunk in chunk_data:
            for line in chunk.decode("utf-8").splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        expired_evts = [e for e in events if e.get("type") == "approval_expired"]
        assert expired_evts, \
            "El stream debe emitir approval_expired al expirar el permiso (bug 2026-09-18)"
        evt = expired_evts[0]
        assert evt.get("approval_id"), f"approval_expired debe incluir approval_id: {evt!r}"
        assert captured["executed"] == [], \
            "Sin aprobacion la tool NO debe ejecutarse"
    finally:
        app_module_ref.APPROVAL_TIMEOUT = old_timeout
