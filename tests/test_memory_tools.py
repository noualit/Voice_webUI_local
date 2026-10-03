# tests/test_memory_tools.py
# Tests para src/memory_tools.py: tools de memoria obtenidas de llama-memory.
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_get_memory_tools_convierte_a_formato_openai():
    """get_memory_tools(mc) obtiene las tools de llama-memory (via list_tools)
    y las convierte a formato OpenAI function."""
    from memory_tools import get_memory_tools
    mc = MagicMock()
    mc.list_tools.return_value = [
        {"name": "create_conversation", "description": "Crea conversacion",
         "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "save_memory", "description": "Guarda memoria",
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    ]
    tools = get_memory_tools(mc)
    assert len(tools) == 2
    for t in tools:
        assert t["type"] == "function"
        assert "parameters" in t["function"]
    # Verifica que usa list_tools como fuente de verdad (no duplica)
    mc.list_tools.assert_called_once_with()
    # Nombre y descripcion se copian del servicio
    assert tools[0]["function"]["name"] == "create_conversation"
    assert tools[0]["function"]["description"] == "Crea conversacion"
    # El servidor se marca como "memoria" para agrupar el prompt por servidor
    assert tools[0]["_mcp_server"] == "memoria"


def test_get_memory_tools_convierte_inputSchema_a_parameters():
    """El inputSchema MCP se mapea a 'parameters' en formato OpenAI."""
    from memory_tools import get_memory_tools
    mc = MagicMock()
    mc.list_tools.return_value = [
        {"name": "search_memories", "description": "Busca",
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    ]
    tools = get_memory_tools(mc)
    params = tools[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert params["required"] == ["query"]


def test_execute_memory_tool_crea_conversacion():
    """execute_memory_tool despacha create_conversation al memory_client."""
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    mc.create_conversation.return_value = {"conversation_id": "abc"}
    result = execute_memory_tool(mc, "create_conversation", {"name": "Titulo"})
    assert result == {"conversation_id": "abc"}
    mc.create_conversation.assert_called_once_with("Titulo")


def test_execute_memory_tool_guarda_memoria():
    """execute_memory_tool despacha save_memory con text, conversation_id y tags."""
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    mc.save_memory.return_value = {"ok": True}
    result = execute_memory_tool(mc, "save_memory", {"text": "hola", "conversation_id": "c1", "tags": ["x"]})
    assert result == {"ok": True}
    mc.save_memory.assert_called_once_with("hola", "c1", ["x"])


def test_execute_memory_tool_busca_memorias():
    """execute_memory_tool despacha search_memories con query y limit."""
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    mc.search_memories.return_value = {"memories": []}
    result = execute_memory_tool(mc, "search_memories", {"query": "python", "limit": 5})
    assert result == {"memories": []}
    mc.search_memories.assert_called_once_with("python", 5)


def test_execute_memory_tool_reenvia_el_mode_de_busqueda():
    """El mode que el LLM envia tiene que llegar a llama-memory.

    El inputSchema de search_memories (viene de llama-memory, es la fuente de
    verdad) anuncia mode con enum hybrid/vector/lexical, asi que el modelo lo
    usa creyendo que filtra. execute_memory_tool lo descartaba en silencio: la
    busqueda salia siempre hibrida y el LLM recibia unos resultados que no
    correspondian a lo que habia pedido. Peor que no exponer el parametro, porque
    el modelo no tiene forma de notar que se ignoro.
    """
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    mc.search_memories.return_value = {"memories": []}
    execute_memory_tool(mc, "search_memories", {"query": "python", "mode": "lexical"})
    assert mc.search_memories.call_args.kwargs["mode"] == "lexical"


def test_execute_memory_tool_no_inventa_mode_si_el_modelo_no_lo_manda():
    """Sin mode en los argumentos, el cliente no debe enviar uno inventado.

    llama-memory ya decide (hybrid por defecto). Mandar un None explicito o un
    "hybrid" hardcodeado acopla este cliente al default del otro proyecto y
    oculta el valor real en los logs.
    """
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    mc.search_memories.return_value = {"memories": []}
    execute_memory_tool(mc, "search_memories", {"query": "python"})
    assert "mode" not in mc.search_memories.call_args.kwargs


def test_execute_memory_tool_tool_desconocida_devuelve_error():
    """execute_memory_tool devuelve error si la tool no existe."""
    from memory_tools import execute_memory_tool
    mc = MagicMock()
    result = execute_memory_tool(mc, "no_existe", {})
    assert "error" in result