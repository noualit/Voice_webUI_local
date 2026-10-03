# tests/test_llama_tool_calling.py
"""Test de integracion TDD para el tool calling de llama.cpp (Qwen3).

Reproduce el bug diagnosticado: con --reasoning off + --presence-penalty alto,
el modelo responde en TEXTO hablando de lo que "haria" en vez de emitir un
tool_call. Con --reasoning on + presence-penalty 0.0, si emite tool_calls.

Es un test de integracion (no unitario): golpea el server vivo en LLAMA_URL.
Se salta automaticamente si el server no responde, para que la suite unitaria
siga siendo rapida y no dependa de la GPU.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# La URL se resuelve igual que la app (.env -> config.json) en vez de estar
# escrita aqui a fuego. Con el default fijo en el test, en cuanto la instancia
# de llama.cpp escuchaba en otra direccion el modulo se saltaba solo, en verde,
# y el tool calling dejaba de probarse sin que nadie se enterara. Importar
# config ejecuta ademas load_dotenv(), que es lo que trae LLAMA_URL de .env.
from config import AppConfig

LLAMA_URL = AppConfig().llama_url
MODEL = os.environ.get("LLAMA_MODEL", "qwen3.6-35b")
TIMEOUT = int(os.environ.get("LLAMA_TEST_TIMEOUT", "180"))

# Tool minima pero con un nombre que obliga al modelo a usarla (no a hablar de ella).
SIMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Devuelve la hora actual del sistema. USO OBLIGATORIO cuando el usuario pregunte por la hora.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _server_alive() -> bool:
    try:
        r = requests.get(LLAMA_URL.replace("/v1/chat/completions", "/v1/models"),
                         timeout=5)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_alive(),
    reason="Server llama.cpp no responde en LLAMA_URL (test de integracion, salto en suite unitaria).",
)


def _chat(messages, tools=None, stream=False):
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": stream,
        "max_tokens": 2048,
        "tools": tools,
        "tool_choice": "auto",
    }
    r = requests.post(LLAMA_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def test_qwen3_emite_tool_call_cuando_se_pide_una_herramienta():
    """Pregunta que requiere una herramienta -> el modelo debe devolver
    choices[0].message.tool_calls (no solo texto)."""
    data = _chat(
        messages=[{"role": "user", "content": "Usa la herramienta get_current_time para decirme la hora actual."}],
        tools=SIMPLE_TOOLS,
    )
    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls")
    assert tool_calls, (
        f"El modelo NO emito tool_calls; devolvio texto: {msg.get('content', '')[:200]!r}"
    )
    assert tool_calls[0]["function"]["name"] == "get_current_time"


def test_qwen3_no_habla_de_herramientas_cuando_no_las_usa():
    """Con una pregunta simple y sin herramientas relevantes, el modelo debe
    responder en texto (no alucinar un tool_call)."""
    data = _chat(
        messages=[{"role": "user", "content": "Di hola en espanol."}],
        tools=SIMPLE_TOOLS,
    )
    msg = data["choices"][0]["message"]
    assert not msg.get("tool_calls"), (
        f"El modelo emito un tool_call innecesario: {msg.get('tool_calls')}"
    )
    assert msg.get("content", "").strip()