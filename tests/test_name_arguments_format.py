# tests/test_name_arguments_format.py
"""Test TDD para el bug: el modelo genera <name>/<arguments> y el parser no lo reconoce.

El modelo Qwen 3.x genera tool calls en formato:
  <name>search_memories</name><arguments>{"query": "...", "limit": 5}</arguments>

El parser actual solo busca <parameters>, no <arguments>. Esto causa que:
1. El parser devuelva None (no detecta tool call)
2. El XML se emita como texto delta al frontend
3. El frontend muestre el XML crudo como si fueran herramientas separadas
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestNameArgumentsFormat:
    """El parser debe reconocer el formato <name>...</name><arguments>JSON</arguments>."""

    def test_parser_reconoce_name_arguments_sin_wrapper(self):
        """Formato standalone: <name>...</name><arguments>...</arguments> sin wrapper."""
        from llm_client import LLMClient

        text = '<name>search_memories</name><arguments>{"query": "nombre", "limit": 5}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None, (
            "El parser debe reconocer <name>/<arguments> standalone"
        )
        assert len(result) == 1
        assert result[0]["function"]["name"] == "search_memories"
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {"query": "nombre", "limit": 5}

    def test_parser_reconoce_name_arguments_con_tool_calls_wrapper(self):
        """Formato con wrapper <tool_calls>: el modelo a veces envuelve en tags."""
        from llm_client import LLMClient

        text = '<tool_calls><name>save_memory</name><arguments>{"text": "Hola"}</arguments></tool_calls>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert result[0]["function"]["name"] == "save_memory"
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {"text": "Hola"}

    def test_parser_reconoce_name_arguments_create_conversation(self):
        """create_conversation con nombre vacio (bug reportado)."""
        from llm_client import LLMClient

        text = '<name>create_conversation</name><arguments>{"name": "Test", "title": "Reunion"}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert result[0]["function"]["name"] == "create_conversation"
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {"name": "Test", "title": "Reunion"}

    def test_parser_multiple_name_arguments(self):
        """Multiple tool calls en el mismo texto."""
        from llm_client import LLMClient

        text = (
            '<name>search_memories</name><arguments>{"query": "reunion"}</arguments>'
            '<name>get_time</name><arguments>{}</arguments>'
        )
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert len(result) == 2
        assert result[0]["function"]["name"] == "search_memories"
        assert result[1]["function"]["name"] == "get_time"

    def test_parser_name_arguments_con_args_vacios(self):
        """Tool sin argumentos: <arguments>{}</arguments>."""
        from llm_client import LLMClient

        text = '<name>get_time</name><arguments>{}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert result[0]["function"]["name"] == "get_time"
        args = json.loads(result[0]["function"]["arguments"])
        assert args == {}

    def test_parser_name_arguments_args_multiline(self):
        """Arguments con JSON multilinia."""
        from llm_client import LLMClient

        text = '''<name>save_memory</name>
<arguments>
{
  "text": "El usuario prefiere espanol",
  "conversation_id": "abc-123",
  "tags": ["preferencia", "idioma"]
}
</arguments>'''
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert result[0]["function"]["name"] == "save_memory"
        args = json.loads(result[0]["function"]["arguments"])
        assert args["text"] == "El usuario prefiere espanol"
        assert args["tags"] == ["preferencia", "idioma"]

    def test_parser_no_confunde_name_tag_normal(self):
        """El parser no debe interpretar <name> dentro de texto normal como tool call."""
        from llm_client import LLMClient

        # Texto normal que contiene la palabra "name" pero sin formato tool call
        text = 'El nombre del usuario es Juan.'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is None, (
            "Texto normal sin <name>...</name><arguments>...</arguments> no debe parsearse como tool call"
        )

    def test_parser_name_sin_arguments_no_falla(self):
        """<name> sin <arguments> no debe causar error, solo ignorarse."""
        from llm_client import LLMClient

        text = '<name>get_time</name>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        # Puede ser None o una tool call vacia; lo importante es que NO falle
        # y no interprete "get_time" como herramienta standalone

    def test_parser_filtra_nombre_desconocido(self):
        """Nombres de herramientas desconocidos NO deben interpretarse como tool calls.

        El parser standalone debe filtrar nombres que no estan en la lista de
        herramientas conocidas (bug 2026-09-13: "Saludo y bienvenida a Nordin"
        y "tool_call" se interpretaban erroneamente como herramientas).
        """
        from llm_client import LLMClient

        # Nombre con espacios (claramente no es una herramienta)
        text = '<name>Saludo y bienvenida a Nordin</name><arguments>{}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        assert result is None, (
            f'Nombre con espacios no debe parsearse: {result}'
        )

        # Nombre genérico no conocido
        text = '<name>tool_call</name><arguments>{}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        assert result is None, (
            f'"tool_call" no es una herramienta conocida: {result}'
        )

    def test_parser_filtra_nombre_no_alfanumerico(self):
        """Nombres con caracteres invalidos (espacios, simbolos) no se parsean."""
        from llm_client import LLMClient

        # El regex del parser standalone solo acepta [a-zA-Z_][\w.]*
        # "Hola Mundo" tiene espacio → no coincide con el regex
        text = '<name>Hola Mundo</name><arguments>{"text": "test"}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        assert result is None, (
            f'Nombre con espacios no debe coincidir con el regex: {result}'
        )

    def test_parser_reconoce_nombre_con_prefijo_servidor(self):
        """Nombres con prefijo de servidor (dia.get_time) deben reconocerse."""
        from llm_client import LLMClient

        # El modelo a veces prefija el servidor: "dia.get_time"
        # El parser debe reconocerlo porque "get_time" esta en la lista
        text = '<name>dia.get_time</name><arguments>{}</arguments>'
        result = LLMClient._parse_xml_tool_calls(text)
        
        assert result is not None
        assert result[0]["function"]["name"] == "dia.get_time"
