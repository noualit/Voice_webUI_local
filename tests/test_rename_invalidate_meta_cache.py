# tests/test_rename_invalidate_meta_cache.py
# Test de regresion: renombrar una conversacion debe invalidar el cache
# de metadatos para que el nuevo nombre se refleje inmediatamente.
#
# Las fixtures `setup_app` y `app_module` viven en tests/conftest.py. Este
# archivo tenia copias locales que se quedaron sin actualizar (no declaraban la
# identidad de la maquina), y por eso el login con "ana" devolvia 401 y el rename
# acababa en 500: dos definiciones de la misma fixture son una forma garantizada
# de que se desincronicen.
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Asegurar que src esta en path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_rename_invalida_cache_meta(app_module):
    """POST /rename debe invalidar la cache de metas para que el nuevo titulo
    se refleje en la siguiente llamada a /api/conversations/meta.

    Este es el bug reportado: cambiar el titulo no hace nada porque el cache
    de metadatos (TTL 60s) sirve datos viejos despues del rename.
    """
    import app as app_module_ref
    import conversations as conversations_ref

    created = {}

    rename_done = {"flag": False}

    def _fake_memory_for_user(user):
        mc = created.get("mc")
        if mc is None:
            mc = MagicMock()
            mc.user = user
            mc.list_conversations.return_value = {"conversations": [{"id": "conv-1", "name": "Original"}]}

            def _get_history(cid, limit=50):
                if rename_done["flag"] and cid == "conv-1":
                    return {"memories": [
                        {"text": "conversation_name=Nuevo titulo", "tags": ["meta", "conversation_name"]},
                    ]}
                return {"memories": []}

            mc.get_history.side_effect = _get_history
            mc.rename_conversation.side_effect = lambda cid, name: rename_done.update({"flag": True}) or {"ok": True, "name": name}
            created["mc"] = mc
        return mc

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})

        # 1) Cargar conversaciones + metas (poblamos cache con datos vacios)
        resp_list = test_client.get("/api/conversations")
        assert resp_list.status_code == 200, f"list conv falló: {resp_list.get_json()}"
        resp_meta = test_client.get("/api/conversations/meta?ids=conv-1")
        assert resp_meta.status_code == 200
        meta_data = resp_meta.get_json()
        # conv-1 no tiene metas en cache (get_history devolvio vacio)
        assert "conv-1" not in meta_data

        # 2) Renombrar la conversacion
        resp_rename = test_client.post(
            "/api/conversations/conv-1/rename",
            json={"name": "Nuevo titulo"},
        )
        assert resp_rename.status_code == 200

        # 3) Volver a pedir metas: debe devolver el nuevo titulo, NO el cache viejo
        resp_meta2 = test_client.get("/api/conversations/meta?ids=conv-1")
        assert resp_meta2.status_code == 200
        meta_data2 = resp_meta2.get_json()
        assert "conv-1" in meta_data2
        assert len(meta_data2["conv-1"]) >= 1
        # Buscar el meta con el nuevo nombre
        names_found = [
            m["text"] for m in meta_data2["conv-1"]
            if m.get("text", "").startswith("conversation_name=")
        ]
        assert "conversation_name=Nuevo titulo" in names_found, (
            f"El nombre renombrado no aparecio en metas. Cache no invalidado. "
            f"Metas devueltas: {meta_data2}"
        )

        # Verificar que rename_conversation fue llamado
        created["mc"].rename_conversation.assert_called_once_with("conv-1", "Nuevo titulo")
