# tests/test_app_conversations.py
# Tests de endpoints de conversaciones (listado, crear, ver, borrar, renombrar,
# metadatos, cache de metas y helpers de chat/persistencia).
import json
from unittest.mock import patch, MagicMock


def test_list_conversations(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/api/conversations")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "conversations" in data


def test_create_conversation(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.post("/api/conversations", json={"name": "Test"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["conversation_id"] == "123"


def test_get_conversation(app_module):
    test_client = app_module.app.test_client()
    resp = test_client.get("/api/conversations/abc")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "memories" in data


def test_list_conversations_uses_session_user(app_module):
    """Cuando hay sesion, list_conversations usa un MemoryClient con ese usuario."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.list_conversations.return_value = {"conversations": [{"id": "1", "name": "x"}]}
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations")
        assert resp.status_code == 200
        assert created["mc"].user == "ana"


def test_delete_conversation(app_module):
    """DELETE /api/conversations/<id> llama a delete_conversation con el usuario de sesion."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.delete_conversation.return_value = {"deleted": True}
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.delete("/api/conversations/abc-123")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] is True
        created["mc"].delete_conversation.assert_called_once_with("abc-123")
        assert created["mc"].user == "ana"


def test_get_conversation_devuelve_role(app_module):
    """GET /api/conversations/<id> debe devolver el role de cada memoria."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "Hola", "tags": ["role:user"], "created_at": None},
                {"text": "Respuesta", "tags": ["role:assistant"], "created_at": None},
            ]
        }
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1")
        assert resp.status_code == 200
        data = resp.get_json()
        mems = data["memories"]
        assert mems[0]["role"] == "user"
        assert mems[1]["role"] == "assistant"
        assert mems[0]["text"] == "Hola"


def test_memory_role_default_nota_sin_tag(app_module):
    """Memorias sin tag de rol se clasifican como 'note' (nota neutral), no como respuesta del bot."""
    import conversations as conversations_ref
    assert conversations_ref._memory_role({"text": "x", "tags": []}) == "note"
    assert conversations_ref._memory_role({"text": "x", "tags": None}) == "note"
    assert conversations_ref._memory_role({"text": "x", "tags": ["otro"]}) == "note"
    assert conversations_ref._memory_role({"text": "x", "tags": ["role:user"]}) == "user"
    assert conversations_ref._memory_role({"text": "x", "tags": ["role:assistant"]}) == "assistant"


def test_get_conversation_memoria_sin_tag_es_nota(app_module):
    """GET /api/conversations/<id> devuelve role 'note' para memorias sin tag de rol."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "Pregunta", "tags": ["role:user"], "created_at": None},
                {"text": "Sin tag", "tags": [], "created_at": None},
            ]
        }
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1")
        assert resp.status_code == 200
        mems = resp.get_json()["memories"]
        assert mems[0]["role"] == "user"
        assert mems[1]["role"] == "note"


def test_rename_conversation_uses_memory_client(app_module):
    """POST /api/conversations/<id>/rename debe delegar en memory_client.rename_conversation."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.rename_conversation.return_value = {"ok": True, "name": "Nuevo titulo"}
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/conversations/conv-1/rename",
            json={"name": "Nuevo titulo"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["name"] == "Nuevo titulo"

        # Verificar que se llamó al cliente con id y nuevo nombre
        created["mc"].rename_conversation.assert_called_once()
        call_args = created["mc"].rename_conversation.call_args[0]
        assert call_args == ("conv-1", "Nuevo titulo")


def test_rename_conversation_requires_name(app_module):
    """Rename debe fallar si no se envía campo 'name'."""
    import app as app_module_ref

    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/conversations/conv-1/rename",
            json={},
        )
        # Debe devolver 400 (o al menos no llamar a rename)
        assert resp.status_code in (400, 422)
        if "mc" in created:
            assert not created["mc"].rename_conversation.called


def test_conversations_meta_devuelve_metas_renombradas(app_module):
    """GET /api/conversations/meta devuelve las memorias meta (conversation_name) por id."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user

        def _fake_get_history(cid, limit=50):
            if cid == "conv-1":
                return {"memories": [
                    {"text": "Hola", "tags": ["role:user"]},
                    {"text": "conversation_name=Nuevo titulo", "tags": ["meta", "conversation_name"]},
                ]}
            if cid == "conv-2":
                return {"memories": [{"text": "sin meta", "tags": []}]}
            return {"memories": []}

        mc.get_history.side_effect = _fake_get_history
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/meta?ids=conv-1,conv-2")
        assert resp.status_code == 200
        data = resp.get_json()
        # conv-1 tiene el meta, conv-2 no
        assert "conv-1" in data
        assert len(data["conv-1"]) == 1
        assert data["conv-1"][0]["text"] == "conversation_name=Nuevo titulo"
        assert "conv-2" not in data


def test_conversations_meta_sin_ids_devuelve_vacio(app_module):
    """GET /api/conversations/meta sin ids devuelve objeto vacio."""
    import app as app_module_ref

    with patch.object(app_module_ref, "_memory_for_user"):
        test_client = app_module_ref.app.test_client()
        resp = test_client.get("/api/conversations/meta")
        assert resp.status_code == 200
        assert resp.get_json() == {}


def test_conversations_meta_usa_cache_evita_repeticiones(app_module):
    """La segunda llamada a /api/conversations/meta usa cache y no repite get_history."""
    import app as app_module_ref
    get_history_calls = {"n": 0}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user

        def _fake_get_history(cid, limit=50):
            get_history_calls["n"] += 1
            return {"memories": [
                {"text": "conversation_name=Titulo", "tags": ["meta", "conversation_name"]},
            ]}

        mc.get_history.side_effect = _fake_get_history
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})

        # Primera llamada: obtiene de llama-memory (N peticiones)
        resp1 = test_client.get("/api/conversations/meta?ids=conv-1,conv-2")
        assert resp1.status_code == 200
        assert get_history_calls["n"] == 2

        # Segunda llamada inmediata: usa cache, no repite get_history
        resp2 = test_client.get("/api/conversations/meta?ids=conv-1,conv-2")
        assert resp2.status_code == 200
        assert get_history_calls["n"] == 2  # sin peticiones nuevas

        # Simular expiracion del TTL alterando el timestamp del cache
        import conversations as conversations_ref
        with conversations_ref._meta_cache_lock:
            for cid in ("conv-1", "conv-2"):
                ts, metas = conversations_ref._meta_cache[cid]
                conversations_ref._meta_cache[cid] = (ts - conversations_ref.META_CACHE_TTL - 1, metas)

        # Tras expirar el TTL, vuelve a consultar llama-memory
        resp3 = test_client.get("/api/conversations/meta?ids=conv-1,conv-2")
        assert resp3.status_code == 200
        assert get_history_calls["n"] == 4  # 2 nuevas tras expirar


def test_build_chat_messages_limita_historial(app_module):
    """_build_chat_messages capa el historial a los MAX_HISTORY_MESSAGES mas recientes."""
    import app as app_module_ref
    n = app_module_ref.MAX_HISTORY_MESSAGES + 5
    mems = [{"text": f"mensaje {i}", "tags": ["role:user" if i % 2 == 0 else "role:assistant"]} for i in range(n)]
    mc = MagicMock()
    mc.get_history.return_value = {"memories": mems}

    # Sin conversation_id: solo system + user
    msgs = app_module_ref._build_chat_messages(mc, None, "hola")
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "hola"}
    assert len(msgs) == 2

    # Con conversation_id: system + ultimos MAX_HISTORY_MESSAGES + user
    msgs2 = app_module_ref._build_chat_messages(mc, "conv-1", "hola")
    assert msgs2[0]["role"] == "system"
    assert msgs2[-1] == {"role": "user", "content": "hola"}
    hist = msgs2[1:-1]
    assert len(hist) == app_module_ref.MAX_HISTORY_MESSAGES
    # Los mas recientes son los que tienen el indice mas alto
    assert hist[-1]["content"] == f"mensaje {n - 1}"


def test_build_chat_messages_canoniza_el_usario_para_los_adjuntos(app_module):
    """Sin sesion, los adjuntos van al directorio de la identidad canonica.

    Antes se pasaba `cfg.memory_user` tal cual ("ana@EJEMPLO") mientras la
    sesion usaba "noualit": las subidas de una sesion y las del global caian en
    carpetas distintas y el mensaje con la imagen no se resolvia nunca.
    """
    import app as app_module_ref
    import attachments as attachments_ref
    from unittest.mock import patch

    app_module_ref.config.memory_user = "ana@EJEMPLO"
    mc = MagicMock()

    with patch.object(attachments_ref, "resolve_attachments",
                      side_effect=lambda content, user, base: f"{user}|{content}"):
        msgs = app_module_ref._build_chat_messages(mc, None, "hola")

    assert msgs[-1]["content"] == "ana@ejemplo|hola", (
        "los adjuntos se resuelven contra el usuario sin canonizar: cada forma "
        "de escribir el nombre crea su propia carpeta de subidas"
    )


def test_build_chat_messages_sin_historial_falla_continua(app_module):
    """Si falla la carga del historial, _build_chat_messages continua con system+user."""
    import app as app_module_ref
    mc = MagicMock()
    mc.get_history.side_effect = Exception("boom")
    msgs = app_module_ref._build_chat_messages(mc, "conv-1", "hola")
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "hola"}
    assert len(msgs) == 2


def test_persist_chat_guarda_solo_con_conversation_id(app_module):
    """_persist_chat guarda pregunta y respuesta solo si hay conversation_id."""
    import app as app_module_ref
    mc = MagicMock()
    app_module_ref._persist_chat(mc, None, "pregunta", "respuesta")
    mc.save_memory.assert_not_called()

    app_module_ref._persist_chat(mc, "conv-1", "pregunta", "respuesta")
    assert mc.save_memory.call_count == 2
    # pregunta con role:user, respuesta con role:assistant
    call_args_list = mc.save_memory.call_args_list
    assert call_args_list[0][1]["tags"] == ["role:user"]
    assert call_args_list[1][1]["tags"] == ["role:assistant"]


def test_persist_chat_respuesta_vacia_no_guarda_respuesta(app_module):
    """_persist_chat no guarda la respuesta si esta vacia."""
    import app as app_module_ref
    mc = MagicMock()
    app_module_ref._persist_chat(mc, "conv-1", "pregunta", "")
    assert mc.save_memory.call_count == 1
    assert mc.save_memory.call_args_list[0][1]["tags"] == ["role:user"]


def test_build_chat_messages_incluye_tools_en_system_prompt(app_module):
    """_build_chat_messages con tools inyecta el bloque de tools en el system prompt.

    Regresion del bug 2026-09-07: las tools se construian y se pasaban a
    chat_stream pero NUNCA llegaban al modelo (payload tools comentado y
    system prompt sin lista de tools) -> el modelo no sabia de las tools de
    AD y confabulaba puertos (8814) o declinaba ("no tengo acceso a BD").
    """
    import app as app_module_ref
    mc = MagicMock()
    tools = [{
        "type": "function",
        "function": {
            "name": "list_users",
            "description": "List users in Active Directory",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    msgs = app_module_ref._build_chat_messages(mc, None, "hola", tools=tools)
    assert msgs[0]["role"] == "system"
    assert "## default" in msgs[0]["content"]
    assert "list_users" in msgs[0]["content"]
    assert "List users in Active Directory" in msgs[0]["content"]
    # El prompt original se conserva
    assert app_module_ref.config.system_prompt in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "hola"}


def test_build_chat_messages_sin_tools_no_inyecta_bloque(app_module):
    """Sin tools, el system prompt queda intacto (comportamiento previo)."""
    import app as app_module_ref
    mc = MagicMock()
    msgs = app_module_ref._build_chat_messages(mc, None, "hola")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == app_module_ref.config.system_prompt
    assert "<tool_call>" not in msgs[0]["content"]


def test_export_conversation_devuelve_html(app_module):
    """GET /api/conversations/<id>/export devuelve HTML con los mensajes."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "Hola", "tags": ["role:user"], "created_at": None},
                {"text": "Respuesta del asistente", "tags": ["role:assistant"], "created_at": None},
                {"text": "Nota neutral", "tags": [], "created_at": None},
            ]
        }
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1/export")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        html = resp.get_data(as_text=True)
        # Contiene los mensajes de dialogo
        assert "Hola" in html
        assert "Respuesta del asistente" in html
        # La nota neutral NO se exporta
        assert "Nota neutral" not in html
        # Etiquetas de rol
        assert "Usuario" in html
        assert "Asistente" in html


def test_export_conversation_usa_titulo_renombrado(app_module):
    """El export usa el titulo renombrado (meta conversation_name) si existe."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user

        def _fake_get_history(cid, limit=50):
            return {"memories": [
                {"text": "Hola", "tags": ["role:user"], "created_at": None},
                {"text": "conversation_name=Mi titulo", "tags": ["meta", "conversation_name"]},
            ]}

        mc.get_history.side_effect = _fake_get_history
        created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1/export")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<h1>Mi titulo</h1>" in html


def test_export_conversacion_vacia_muestra_aviso(app_module):
    """Conversacion sin mensajes muestra el aviso de vacia en el export."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1/export")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "no tiene mensajes" in html


def test_export_conversation_escapar_html(app_module):
    """El contenido se escapa para evitar inyeccion HTML en el export."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": "<script>alert('xss')</script>", "tags": ["role:user"], "created_at": None},
            ]
        }
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1/export")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html


def test_export_conversation_error_devuelve_500(app_module):
    """Si falla get_history, el endpoint devuelve 500 con JSON de error."""
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.side_effect = Exception("boom")
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.get("/api/conversations/conv-1/export")
        assert resp.status_code == 500
        assert "error" in resp.get_json()


class TestSanitizeAbsurdDates:
    """Tests de sanitize_absurd_dates: defensa de segundo nivel contra fechas
    absurdas/corruptas que trae el servidor MCP AD (FILETIME decodificado de
    forma erronea como ano 2486159, 5851 o 9999 con dias de 2486159/5851).
    Son los valores REALES observados en get_inactive_users de ActiveDirectoryMCP.
    """

    def test_fecha_ano_2486159_se_neutraliza(self):
        import conversations as conversations_ref
        entrada = "30/Julio/2486159-7 días"
        salida = conversations_ref.sanitize_absurd_dates(entrada)
        assert "2486159" not in salida
        assert "-7 días" not in salida

    def test_fecha_ano_5851_se_neutraliza(self):
        import conversations as conversations_ref
        assert "5851" not in conversations_ref.sanitize_absurd_dates("año 5851 de inactividad")

    def test_dias_2486159_se_neutralizan(self):
        import conversations as conversations_ref
        assert "2486159" not in conversations_ref.sanitize_absurd_dates("2486159 días")

    def test_dias_5851_se_neutralizan(self):
        import conversations as conversations_ref
        assert "5851" not in conversations_ref.sanitize_absurd_dates("5851 días")

    def test_fecha_valida_se_mantiene(self):
        import conversations as conversations_ref
        # El sanitizer NO convierte formatos de fecha, solo neutraliza absurdos
        # Un ano valido (2026) se conserva tal cual
        assert "2026" in conversations_ref.sanitize_absurd_dates("30/Julio/2026")

    def test_texto_normal_se_mantiene_igual(self):
        import conversations as conversations_ref
        assert conversations_ref.sanitize_absurd_dates("Hola, ¿qué tal?") == "Hola, ¿qué tal?"

    def test_valor_vacio_o_no_texto_devuelve_igual(self):
        import conversations as conversations_ref
        assert conversations_ref.sanitize_absurd_dates("") == ""
        assert conversations_ref.sanitize_absurd_dates(None) is None


class TestTruncateToolResultForLLM:
    """Tests de truncate_tool_result_for_llm: acota SIEMPRE por caracteres el
    resultado de una tool antes de inyectarlo al prompt del LLM.

    Regression bug 2026-09-24: el recorte anterior era solo por LINEAS (40 head
    + 20 tail) sin re-verificar la longitud en caracteres. Un resultado con
    lineas enormes (stdout de run_powershell con un JSON gigante en una sola
    linea, ~500KB) pasaba ENTERO al prompt, y ademas lines[-20:] lo duplicaba
    cuando el resultado tenia <= 60 lineas. Resultado: prompt de 258K tokens ->
    400 de llama.cpp "exceeds the available context size" en la conversacion
    'equipos online' (scan de 192.0.2.0/24 con run_powershell).
    """

    def test_resultado_corto_se_mantiene_igual(self):
        import conversations as conversations_ref
        assert conversations_ref.truncate_tool_result_for_llm("hola") == "hola"

    def test_resultado_vacio_devuelve_vacio(self):
        import conversations as conversations_ref
        assert conversations_ref.truncate_tool_result_for_llm("") == ""
        assert conversations_ref.truncate_tool_result_for_llm(None) is None

    def test_resultado_multilinea_largo_mantiene_head_tail_y_aviso(self):
        import conversations as conversations_ref
        # Lineas de ~22 chars: head(40) + aviso + tail(20) caben en el limite
        text = "\n".join(f"linea {i} " + "x" * 15 for i in range(200))
        out = conversations_ref.truncate_tool_result_for_llm(text)
        assert out.startswith("linea 0")
        assert "linea 199" in out  # la cola se preserva
        assert "lineas mas truncadas" in out
        assert len(out) <= conversations_ref.LLM_RESULT_LIMIT + 3

    def test_linea_gigante_se_acota_por_caracteres(self):
        """El caso real del bug: stdout enorme en UNA sola linea (~500KB)."""
        import conversations as conversations_ref
        text = "X" * 500_000
        out = conversations_ref.truncate_tool_result_for_llm(text)
        # Acotada por caracteres: se conserva el inicio + sufijo, NUNCA entera
        assert len(out) <= conversations_ref.LLM_RESULT_LIMIT + 3
        assert out == "X" * conversations_ref.LLM_RESULT_LIMIT + "..."
        assert "X" * 500_000 not in out

    def test_pocas_lineas_gigantes_se_acotan_por_caracteres(self):
        """Menos de 60 lineas pero cada una enorme: el recorte por lineas no
        basta, el corte final por caracteres debe acotar."""
        import conversations as conversations_ref
        text = "\n".join("Y" * 100_000 for _ in range(30))
        out = conversations_ref.truncate_tool_result_for_llm(text)
        assert len(out) <= conversations_ref.LLM_RESULT_LIMIT + 3

    def test_resultado_justo_al_limite_no_se_trunca(self):
        import conversations as conversations_ref
        text = "z" * conversations_ref.LLM_RESULT_LIMIT
        assert conversations_ref.truncate_tool_result_for_llm(text) == text