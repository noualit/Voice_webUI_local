# memory_tools.py
"""Tools de memoria expuestas al LLM.

Las definiciones (name, description, inputSchema) NO se duplican aqui: se
obtienen de llama-memory (proyecto d:/python/llama-memory) via MemoryClient
.list_tools() -> method MCP "tools/list", que es la fuente de verdad. Aqui solo
se convierten al formato OpenAI function que espera el LLM y se despacha su
ejecucion a los metodos de MemoryClient.
"""
from typing import Any


def get_memory_tools(memory_client) -> list[dict]:
    """Devuelve las tools de memoria en formato OpenAI function.

    Consulta las definiciones a llama-memory (via MemoryClient.list_tools) y
    las convierte al formato {"type": "function", "function": {...}} que el
    LLM espera, igual que MCPToolManager.get_openai_tools para los MCPs.
    """
    tools = []
    for t in memory_client.list_tools():
        tools.append({
            "type": "function",
            "function": {
                "name": t.get("name", "unknown"),
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            },
            # Servidor virtual "memoria": format_tools_prompt agrupa las tools por
            # servidor y las de memoria tienen su propio bloque de instrucciones.
            "_mcp_server": "memoria",
        })
    return tools


def _is_memory_tool(name: str) -> bool:
    """Indica si el nombre de tool corresponde a una tool de memoria."""
    return name in {
        "create_conversation",
        "list_conversations",
        "get_conversation_history",
        "search_memories",
        "save_memory",
        "delete_conversation",
    }


def execute_memory_tool(memory_client, tool_name: str, arguments: dict) -> Any:
    """Despacha la ejecucion de una tool de memoria al metodo de MemoryClient.

    Devuelve la misma estructura que devuelven los metodos de MemoryClient
    (dict). Si la tool no es de memoria o no existe, devuelve un dict de error
    para que el LLM lo procese sin reventar el stream.
    """
    if not _is_memory_tool(tool_name):
        return {"error": f"Tool de memoria no reconocida: {tool_name}"}

    if tool_name == "create_conversation":
        return memory_client.create_conversation(arguments.get("name", ""))
    if tool_name == "list_conversations":
        return memory_client.list_conversations(arguments.get("limit", 50))
    if tool_name == "get_conversation_history":
        return memory_client.get_history(
            arguments.get("conversation_id", ""),
            arguments.get("limit", 50),
        )
    if tool_name == "search_memories":
        # mode (hybrid/vector/lexical) lo anuncia el inputSchema que viene de
        # llama-memory. Si se descarta aqui, el LLM cree que esta filtrando y no
        # puede notar que el resultado es de otro tipo de busqueda.
        mode = arguments.get("mode")
        if mode:
            return memory_client.search_memories(
                arguments.get("query", ""),
                arguments.get("limit", 10),
                mode=mode,
            )
        return memory_client.search_memories(
            arguments.get("query", ""),
            arguments.get("limit", 10),
        )
    if tool_name == "save_memory":
        return memory_client.save_memory(
            arguments.get("text", ""),
            arguments.get("conversation_id"),
            arguments.get("tags"),
        )
    if tool_name == "delete_conversation":
        return memory_client.delete_conversation(arguments.get("conversation_id", ""))
    return {"error": f"Tool de memoria no reconocida: {tool_name}"}