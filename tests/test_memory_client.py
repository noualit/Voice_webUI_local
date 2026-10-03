# tests/test_memory_client.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_create_conversation():
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"conversation_id": "abc-123", "name": "Test"}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response) as mock_post:
        result = client.create_conversation("Mi tema")
        assert result["conversation_id"] == "abc-123"
        # Verify payload
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://localhost:9001/mcp"
        payload = call_args[1]["json"]
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "create_conversation"
        assert payload["params"]["arguments"]["name"] == "Mi tema"


def test_list_conversations():
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"conversations": [{"id": "x", "name": "A"}]}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = client.list_conversations()
        assert len(result["conversations"]) == 1
        assert result["conversations"][0]["name"] == "A"


def test_get_history():
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response =MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"memories": [{"text": "hola", "tags": null}]}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = client.get_history("conv-1")
        assert len(result["memories"]) == 1
        assert result["memories"][0]["text"] == "hola"


def test_delete_conversation():
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"deleted": true}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response) as mock_post:
        result = client.delete_conversation("abc-123")
        assert result["deleted"] is True
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://localhost:9001/mcp"
        payload = call_args[1]["json"]
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "delete_conversation"
        assert payload["params"]["arguments"]["conversation_id"] == "abc-123"
        # Verificar header X-User
        assert call_args[1]["headers"]["X-User"] == "testuser"


def test_response_parsing_extracts_content_text():
    """Si la respuesta es JSON-RPC envuelto, extraer el text del primer content."""
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    # Simular respuesta JSON-RPC con content array
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "result": {
            "content": [{"type": "text", "text": '{"ok": true}'}]
        }
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = client._call_tool("list_conversations", {})
        assert result == {"ok": True}


def test_retry_429_hasta_exito():
    """Ante 429 Too Many Requests, reintenta hasta obtener exito."""
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")

    def _make_response(status_code):
        r = MagicMock()
        r.status_code = status_code
        if status_code == 429:
            r.raise_for_status.side_effect = Exception("429 Too Many Requests")
            r.json.return_value = {"error": "rate limited"}
        else:
            r.raise_for_status = MagicMock()
            r.json.return_value = {
                "content": [{"type": "text", "text": '{"ok": true}'}]
            }
        return r

    # 429, 429, 200 -> tras 2 reintentos exito
    with patch("requests.post", side_effect=[_make_response(429), _make_response(429), _make_response(200)]) as mock_post, \
         patch("time.sleep", return_value=None) as mock_sleep:
        result = client._call_tool("list_conversations", {})
        assert result == {"ok": True}
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2


def test_retry_429_agota_reintentos():
    """Si todos los intentos devuelven 429, se propaga la ultima excepcion."""
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")

    def _make_429():
        r = MagicMock()
        r.status_code = 429
        r.raise_for_status.side_effect = Exception("429 Too Many Requests")
        r.json.return_value = {"error": "rate limited"}
        return r

    with patch("requests.post", side_effect=[_make_429(), _make_429(), _make_429(), _make_429()]) as mock_post, \
         patch("time.sleep", return_value=None):
        try:
            client._call_tool("list_conversations", {})
            assert False, "deberia haber lanzado excepcion"
        except Exception as e:
            assert "429" in str(e)
        assert mock_post.call_count == 4


def test_search_memories_manda_el_mode_al_servicio():
    """mode tiene que viajar en el payload hasta llama-memory.

    Aqui es donde se perdia: el dispatcher lo reenviaba pero search_memories
    armaba {"query", "limit"} a pelo, asi que el servicio recibia la llamada sin
    mode y aplicaba su default (hybrid) en silencio.
    """
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"memories": []}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response) as mock_post:
        client.search_memories("python", 5, mode="vector")
        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["arguments"]["mode"] == "vector"
        assert payload["params"]["arguments"]["limit"] == 5


def test_search_memories_no_manda_mode_si_no_se_pide():
    """Sin mode, la clave no viaja: el default lo decide llama-memory."""
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"memories": []}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response) as mock_post:
        client.search_memories("python")
        payload = mock_post.call_args[1]["json"]
        assert "mode" not in payload["params"]["arguments"]


def test_list_tools_devuelve_tools_de_memoria():
    """list_tools() consulta tools/list en llama-memory y devuelve las tools MCP."""
    from memory_client import MemoryClient
    client = MemoryClient("http://localhost:9001", "testuser")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "tools": [
            {"name": "create_conversation", "description": "Crea conversacion",
             "inputSchema": {"type": "object", "properties": {}, "required": ["name"]}},
            {"name": "save_memory", "description": "Guarda memoria",
             "inputSchema": {"type": "object", "properties": {}, "required": ["text"]}},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response) as mock_post:
        result = client.list_tools()
        assert len(result) == 2
        assert result[0]["name"] == "create_conversation"
        assert result[1]["name"] == "save_memory"
        # Verificar payload: method tools/list
        payload = mock_post.call_args[1]["json"]
        assert payload["method"] == "tools/list"
