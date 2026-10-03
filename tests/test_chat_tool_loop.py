# tests/test_chat_tool_loop.py
# Pruebas para el bug donde el modelo alterna entre herramientas diferentes
# que devuelven resultados validos, pero nunca genera la respuesta final.
import json
import sys
from unittest.mock import MagicMock, patch


def test_chat_corta_loop_hasta_6_iteraciones_con_tools_validas_distintas(app_module):
    """Si el modelo alterna entre herramientas diferentes (p. ej. list_organizational_units,
    get_users) y TODAS devuelven resultados validos (no null/error), pero despues de 6
    iteraciones full_response sigue vacio, api_chat debe cortar el loop en vez de
    repetir hasta MAX_ITERATIONS.

    Regression para bug 2026-09-12: el modelo Qwen 3.x alterna entre tools MCP AD
    que funcionan correctamente, pero nunca llega a la iteracion sin tool calls
    donde generaria la respuesta final. El resultado es una respuesta vacia (full_response="")
    despues de agotar MAX_ITERATIONS (30) en lugar de cortar pronto.
    """
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        # Simula un modelo que alterna herramientas diferentes, todas con exito.
        # Ninguna devuelve null/error, asi que iteraciones_sin_progreso NUNCA se incrementa.
        # Emite tools indefinidamente: el corte debe venir SOLO del limite de
        # iteraciones_con_tools_sin_respuesta (6), no del agotamiento del stream.
        tool_names = [
            "list_organizational_units",
            "get_users",
        ]
        calls["n"] += 1
        name = tool_names[calls["n"] % len(tool_names)]
        yield {"type": "tool_calls", "tool_calls": [
            {"id": f"call_{calls['n']}", "type": "function",
             "function": {
                 "name": name,
                 "arguments": '{"query": "q"}'
             }}
        ]}

    def _execute_tool(name, args):
        # Todas las herramientas devuelven resultados validos (NO null/error)
        return {"result": f"data from {name}"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.side_effect = _execute_tool
        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={
            "text": "listame usuarios",
            "conversation_id": "conv-1",
        })
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")

        # El loop debe cortarse tras 6 iteraciones (max_tool_iterations sin respuesta),
        # NO llegar a MAX_ITERATIONS (30).
        assert calls["n"] == 6, \
            f"Esperaba cortar el loop en 6 iteraciones con tools validas, hubo {calls['n']}"

        # Debe haber un evento done al final.
        assert '{"type": "done"' in body


def test_chat_acota_resultado_tool_con_linea_gigante_para_el_llm(app_module):
    """Regression bug 2026-09-24: un resultado de tool con una linea enorme
    (stdout de run_powershell con JSON gigante en una sola linea, ~500KB)
    pasaba ENTERO al prompt del LLM: el recorte por lineas (40 head + 20 tail)
    no acotaba caracteres y lines[-20:] duplicaba la linea cuando el resultado
    tenia <= 60 lineas. Resultado: prompt de 258K tokens -> 400 de llama.cpp
    "exceeds the available context size" en la conversacion 'equipos online'
    (scan de 192.0.2.0/24 con run_powershell). El contenido que recibe el
    LLM debe estar acotado por caracteres SIEMPRE.
    """
    import app as app_module_ref

    def _fake_memory_for_user(user):
        mc = MagicMock()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        return mc

    calls = {"n": 0}
    captured = {}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        call = calls["n"]
        calls["n"] += 1
        if call == 0:
            # El modelo decide escanear la subred via run_powershell
            yield {"type": "tool_calls", "raw": "<tool_call><function=run_powershell>",
                   "tool_calls": [
                       {"id": "call_1", "type": "function",
                        "function": {"name": "run_powershell",
                                     "arguments": '{"command": "scan 192.0.2.0/24"}'}}]}
        else:
            captured["llm_messages"] = [dict(m) for m in messages]
            yield {"type": "delta", "content": "Esta es la respuesta final con la tabla."}

    def _execute_tool(name, args):
        # stdout ENORME en una sola linea: el caso real que revento el contexto
        return {"stdout": "X" * 500_000, "stderr": "", "exit_code": 0, "elapsed_ms": 1234}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        app_module_ref.mcp_manager.execute_tool.side_effect = _execute_tool
        # La tool ya fue aprobada en esta conversacion (flujo real: el usuario
        # aprobo run_powershell la primera vez, despues se ejecuta directo)
        with app_module_ref._approval_conv_lock:
            app_module_ref._approved_tools_per_conv.setdefault("conv-1", set()).add("run_powershell")

        test_client = app_module_ref.app.test_client()
        resp = test_client.post("/api/chat", json={
            "text": "dame una tabla con los equipos online en 192.0.2.0/24",
            "conversation_id": "conv-1",
        })
        assert resp.status_code == 200
        body = b"".join(resp.response).decode("utf-8")
        assert '{"type": "done"' in body

        # El mensaje con el resultado de la tool que llega al LLM esta acotado
        # por caracteres: el caso real pasaba 1MB+ (prompt de 258K tokens -> 400)
        assert "llm_messages" in captured
        result_msg = captured["llm_messages"][-1]
        assert result_msg["role"] == "user"
        assert len(result_msg["content"]) < 5000
        assert "X" * 500_000 not in result_msg["content"]
