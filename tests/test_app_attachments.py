# tests/test_app_attachments.py
# Tests de adjuntos: endpoint /api/upload (metadata, limites) y resolucion de
# marcadores [adjunto:...] en los mensajes del chat (nuevo, historial y resend).
import io
from unittest.mock import patch, MagicMock


def test_upload_sin_archivo_400(app_module):
    resp = app_module.app.test_client().post(
        "/api/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_extension_no_permitida_400(app_module):
    test_client = app_module.app.test_client()
    data = {"file": (io.BytesIO(b"x"), "datos.bin")}
    resp = test_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_ok_devuelve_metadata(app_module):
    import app as app_module_ref
    test_client = app_module_ref.app.test_client()
    data = {"file": (io.BytesIO("Hola archivo".encode("utf-8")), "nota.txt")}
    resp = test_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == "nota.txt"
    assert body["chars"] == len("Hola archivo")
    assert body["file_id"] and len(body["file_id"]) == 12
    assert body["truncated"] is False


def test_upload_mas_de_max_upload_bytes_413(app_module):
    """Un archivo que supera MAX_UPLOAD_BYTES se rechaza con 413 (no 400)."""
    from attachments import MAX_UPLOAD_BYTES
    test_client = app_module.app.test_client()
    data = {"file": (io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)), "grande.txt")}
    resp = test_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 413


def test_chat_resuelve_marcadores_y_guarda_referencia(app_module):
    """Con adjuntos, el LLM recibe el contenido resuelto y la memoria guarda
    la referencia (marcador), no el contenido."""
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    def _fake_memory_for_user(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    captured = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    # Subir un archivo real al upload_dir temporal del fixture
    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat",
            json={"text": f"Resume\n{marker}", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

    msgs = captured["messages"]
    user_msg = msgs[-1]
    assert user_msg["role"] == "user"
    assert "contenido del adjunto" in user_msg["content"]
    assert marker not in user_msg["content"]
    # La memoria guarda el texto crudo con el marcador (referencia), no el contenido
    saved = created["mc"].save_memory.call_args_list[0][0][0]
    assert marker in saved
    assert "contenido del adjunto" not in saved


def test_chat_resuelve_marcadores_en_historial(app_module):
    """Los marcadores en mensajes user del historial tambien se resuelven."""
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"

    def _fake_memory_for_user_hist(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": f"Pregunta previa\n{marker}", "tags": ["role:user"]},
            ]
        }
        created["mc"] = mc
        return mc

    captured = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    with patch.object(app_module_ref, "_memory_for_user",
                      side_effect=_fake_memory_for_user_hist), \
         patch.object(app_module_ref.llm_client, "chat_stream",
                      side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat", json={"text": "Nueva", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

    hist_user = captured["messages"][1]
    assert hist_user["role"] == "user"
    assert "contenido del adjunto" in hist_user["content"]


def test_chat_mas_de_5_adjuntos_400(app_module):
    import app as app_module_ref
    markers = "\n".join(f"[adjunto:{i:012x}|f{i}.txt]" for i in range(6))
    resp = app_module_ref.app.test_client().post(
        "/api/chat", json={"text": f"hola\n{markers}", "conversation_id": "conv-1"})
    assert resp.status_code == 400


def test_chat_con_5_adjuntos_acepta(app_module):
    """Exactamente MAX_ATTACHMENTS_PER_MESSAGE marcadores NO debe rechazarse
    (el limite es superar el maximo, no llegar a el)."""
    import app as app_module_ref
    created = {}

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        yield {"type": "delta", "content": "ok"}

    markers = "\n".join(f"[adjunto:{i:012x}|f{i}.txt]" for i in range(5))
    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat", json={"text": f"hola\n{markers}", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)


def test_chat_resend_con_adjuntos_resuelve_y_no_guarda_pregunta(app_module):
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    def _fake_memory_for_user(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"
    captured = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "Nueva respuesta"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat",
            json={"text": f"Resume\n{marker}", "conversation_id": "conv-1", "resend": True})
        assert resp.status_code == 200
        list(resp.response)

    # El LLM recibe el contenido resuelto
    assert "contenido del adjunto" in captured["messages"][-1]["content"]
    # Solo 1 save_memory (la respuesta), no la pregunta (resend)
    assert len(created["mc"].save_memory.call_args_list) == 1