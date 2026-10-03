# tests/test_tool_xml_visible.py
"""Test TDD para el bug: el XML crudo de tool calls aparece visible en el chat.

Cuando el modelo llama a una tool (save_memory, create_conversation, etc.) en
formato XML, el backend envia el XML como delta SSE al frontend. El frontend
lo acumula en el contenido del mensaje assistant y el usuario ve algo como:

    <name>save_memory</name>
    <arguments>{"text": "...", ...}</arguments>

Esto es ilegible. El XML debe ser filtrado del stream SSE.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestToolXmlNotVisible:
    """El XML de tool calls NO debe aparecer en el contenido del mensaje."""

    def test_xml_tool_call_strippeado_del_delta(self):
        """La funcion strip_tool_xml_content elimina XML de tool calls."""
        from app import strip_tool_xml_content

        xml_content = '<name>save_memory</name><arguments>{"text": "El usuario se llama Nordin."}</arguments>'
        result = strip_tool_xml_content(xml_content)
        assert result == "", (
            f"Se esperaba string vacio, got {result!r}"
        )

    def test_texto_sin_xml_no_se_modifica(self):
        """El texto normal sin XML debe pasar intacto."""
        from app import strip_tool_xml_content

        normal_text = "El usuario se llama Nordin."
        result = strip_tool_xml_content(normal_text)
        assert result == normal_text

    def test_texto_mas_xml_se_strippea_solo_xml(self):
        """Texto antes/despues del XML se conserva, solo se elimina el XML."""
        from app import strip_tool_xml_content

        mixed = "Hola, voy a guardar eso. <name>save_memory</name><arguments>{}</arguments> Listo."
        result = strip_tool_xml_content(mixed)
        expected = "Hola, voy a guardar eso.  Listo."
        assert result == expected

    def test_multiple_tool_calls_en_un_delta(self):
        """Varias llamadas a tools en un solo delta se eliminan todas."""
        from app import strip_tool_xml_content

        multi = (
            '<name>save_memory</name><arguments>{"key": "val"}</arguments>'
            '<name>create_conversation</name><arguments>{"title": "Test"}</arguments>'
        )
        result = strip_tool_xml_content(multi)
        assert result == ""

    def test_xml_incompleto_no_se_elimina(self):
        """XML mal formado (sin cerrar) no debe eliminarse por seguridad."""
        from app import strip_tool_xml_content

        broken = '<name>save_memory</name><arguments>{"key": "val"'
        result = strip_tool_xml_content(broken)
        assert result == broken

    def test_llm_stream_no_envia_xml_al_frontend(self):
        """Simula el flujo completo: modelo genera tool call XML -> SSE -> frontend.

        El frontend NO debe recibir el XML como delta. El mensaje final del
        assistant debe contener solo el texto de respuesta, sin XML.
        """
        from app import strip_tool_xml_content

        # Simular eventos del LLM (como los que devuelve chat_stream)
        llm_events = [
            {"type": "delta", "content": "Voy a guardar eso."},
            {
                "type": "delta",
                "content": '<name>save_memory</name><arguments>{"text": "Nordin"}</arguments>',
            },
            {"type": "tool_calls", "tool_calls": [{"type": "function", "function": {"name": "save_memory", "arguments": '{"text": "Nordin"}'}}]},
            {"type": "delta", "content": "Guardado correctamente."},
            {"type": "done", "response": "Guardado correctamente."},
        ]

        # Simular el procesamiento del frontend (chatStream.js)
        messages = []
        current_bot_idx = None

        def ensure_assistant():
            nonlocal current_bot_idx
            if current_bot_idx is None:
                messages.append({"role": "assistant", "content": ""})
                current_bot_idx = len(messages) - 1
            return current_bot_idx

        delta_buffer = ""

        for event in llm_events:
            if event["type"] == "delta":
                # AQUI: el backend debe strippear el XML ANTES de enviar al SSE
                clean_content = strip_tool_xml_content(event["content"])
                delta_buffer += clean_content
            elif event["type"] == "tool_calls":
                # El frontend limpia el contenido del assistant
                bot_idx = ensure_assistant()
                messages[bot_idx]["content"] = ""
            elif event["type"] == "done":
                bot_idx = ensure_assistant()
                messages[bot_idx]["content"] = event["response"]

        # Verificar: el mensaje assistant NO contiene XML
        assistant_content = messages[-1]["content"]
        assert "<name>" not in assistant_content, (
            f"El mensaje assistant contiene XML de tool call: {assistant_content!r}"
        )
        assert "<arguments>" not in assistant_content, (
            f"El mensaje assistant contiene tags arguments: {assistant_content!r}"
        )
        assert assistant_content == "Guardado correctamente.", (
            f"Se esperaba solo la respuesta final, got {assistant_content!r}"
        )
