# memory_client.py
import json
import time

import requests


class MemoryClient:
    """Cliente HTTP para llama-memory (servicio MCP en puerto 9001)."""

    # Reintentos ante 429 Too Many Requests con backoff exponencial
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 0.5  # segundos

    def __init__(self, base_url: str, user: str = "default", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.timeout = timeout

    def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        payload = {
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        headers = {"X-User": self.user, "Content-Type": "application/json"}
        url = f"{self.base_url}/mcp"
        for attempt in range(self.MAX_RETRIES + 1):
            r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            if r.status_code == 429 and attempt < self.MAX_RETRIES:
                # Espera con backoff exponencial: 0.5s, 1s, 2s...
                time.sleep(self.RETRY_BASE_DELAY * (2 ** attempt))
                continue
            r.raise_for_status()
            break
        data = r.json()
        # Extraer el text del primer content (formato MCP)
        if "result" in data and isinstance(data["result"], dict):
            data = data["result"]
        if "content" in data and isinstance(data["content"], list):
            texts = []
            for item in data["content"]:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
            if len(texts) == 1:
                try:
                    return json.loads(texts[0])
                except json.JSONDecodeError:
                    return {"text": texts[0]}
            return {"texts": texts}
        return data

    def create_conversation(self, name: str) -> dict:
        return self._call_tool("create_conversation", {"name": name})

    def list_tools(self) -> list:
        """Consulta la lista de tools de memoria disponibles en llama-memory.

        Usa el metodo MCP "tools/list" que devuelve las definiciones completas
        (name, description, inputSchema). Es la fuente de verdad de las tools de
        memoria, por lo que no se duplican en este proyecto.
        """
        payload = {"method": "tools/list", "params": {}}
        headers = {"X-User": self.user, "Content-Type": "application/json"}
        r = requests.post(f"{self.base_url}/mcp", json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data.get("tools", [])

    def list_conversations(self, limit: int = 50) -> dict:
        return self._call_tool("list_conversations", {"limit": limit})

    def get_history(self, conversation_id: str, limit: int = 50) -> dict:
        return self._call_tool("get_conversation_history", {
            "conversation_id": conversation_id,
            "limit": limit,
        })

    def search_memories(self, query: str, limit: int = 10,
                        mode: str | None = None) -> dict:
        """Busca memorias. `mode` solo se manda si el LLM lo pide.

        El inputSchema de llama-memory declara mode con enum
        hybrid/vector/lexical. Si no llega, no se envia la clave: el default lo
        decide llama-memory (hybrid), y mandar "mode": null deja el parametro
        declarado y sin valor, que es peor que no mandarlo.
        """
        args: dict = {"query": query, "limit": limit}
        if mode:
            args["mode"] = mode
        return self._call_tool("search_memories", args)

    def save_memory(self, text: str, conversation_id: str | None = None, tags: list | None = None) -> dict:
        args = {"text": text}
        if conversation_id:
            args["conversation_id"] = conversation_id
        if tags:
            args["tags"] = tags
        return self._call_tool("save_memory", args)

    def rename_conversation(self, conversation_id: str, name: str) -> dict:
        """Persist new conversation name as a metadata memory."""
        meta_text = f"conversation_name={name}"
        return self.save_memory(
            text=meta_text,
            conversation_id=conversation_id,
            tags=["meta", "conversation_name"],
        )

    def delete_conversation(self, conversation_id: str) -> dict:
        return self._call_tool("delete_conversation", {"conversation_id": conversation_id})

    def health(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.json() if r.status_code == 200 else {"status": "error"}
        except requests.RequestException:
            return {"status": "unreachable"}
