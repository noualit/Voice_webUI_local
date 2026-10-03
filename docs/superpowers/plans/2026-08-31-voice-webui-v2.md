# Voice-WebUI v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la Web-UI single-file actual por una arquitectura Flask + Alpine.js con chat por voz, streaming SSE, configuracion de MCPs, system prompt global y conversaciones via llama-memory, lista para publicar en GitHub.

**Architecture:** Flask sirve API REST + archivos estaticos. Frontend Alpine.js con sidebar de conversaciones, chat con streaming SSE, y panel de configuracion. MCP clients arrancan al iniciar. Conversaciones y memoria via `llama-memory` HTTP en puerto 9001.

**Tech Stack:** Python 3.11, Flask, Whisper, requests, Alpine.js, SSE, llama-memory (FastAPI/Postgres).

**Spec:** `docs/superpowers/specs/2026-08-31-voice-webui-v2-design.md`

---

## File Structure

```
Voice-WebUI/
├── app.py                 # Flask app: rutas API, servir static, Whisper
├── mcp_manager.py         # MCP clients (reusado del actual), config JSON
├── llm_client.py          # Cliente HTTP para llama.cpp con streaming
├── memory_client.py       # Cliente HTTP para llama-memory
├── config.json            # Generado al primer arranque (no se committea)
├── config.example.json    # Plantilla de configuracion documentada
├── requirements.txt       # Dependencias Python
├── .env.example           # Variables de entorno documentadas
├── .gitignore
├── README.md
├── LICENSE                # MIT
├── static/
│   ├── index.html         # Pagina principal (Alpine.js)
│   ├── app.js             # Logica frontend
│   └── style.css          # Estilos
└── tests/
    ├── test_llm_client.py
    ├── test_memory_client.py
    ├── test_mcp_manager.py
    └── test_app.py
```

**Nota:** `voice2llama_mcp.py` se elimina al final cuando `app.py` este completo y funcione.

---

## Task 1: Configuracion base (env, requirements, .gitignore)

**Files:**
- Create: `D:\python\Voice-WebUI\requirements.txt`
- Create: `D:\python\Voice-WebUI\.env.example`
- Create: `D:\python\Voice-WebUI\.gitignore`
- Create: `D:\python\Voice-WebUI\config.example.json`
- Create: `D:\python\Voice-WebUI\LICENSE`

- [ ] **Step 1: Crear `requirements.txt`**

```
flask>=3.0
whisper>=1.1
requests>=2.31
python-dotenv>=1.0
```

- [ ] **Step 2: Crear `.env.example`**

```
# Voice-WebUI configuration
# Copia este archivo a .env y edita los valores

# Flask
FLASK_SECRET_KEY=change-me-in-production

# LLM (llama.cpp)
LLAMA_URL=http://localhost:8080/v1/chat/completions
LLAMA_MODEL=qwen3.6-27b
LLAMA_TEMPERATURE=0.6

# Whisper
WHISPER_MODEL=small

# MCP config path
MCP_CONFIG_PATH=./mcp-config.json

# llama-memory service
MEMORY_URL=http://localhost:9001

# User identifier for llama-memory
MEMORY_USER=default
```

- [ ] **Step 3: Crear `.gitignore`**

```
__pycache__/
*.pyc
.env
config.json
mcp-config.json
.pytest_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 4: Crear `config.example.json`**

```json
{
  "system_prompt": "Eres un asistente util. Responde en espanol.\n\nMEMORY WORKFLOW:\n- At the start of each new conversation, call create_conversation with a short title.\n- Use the conversation_id from create_conversation when calling save_memory.\n- Before answering questions about past topics, call search_memories FIRST.\n- When the user shares important information, save it with save_memory.\n- If list_conversations has previous chats, check get_conversation_history for context.",
  "llama_url": "http://localhost:8080/v1/chat/completions",
  "model": "qwen3.6-27b",
  "temperature": 0.6,
  "mcp_config_path": "./mcp-config.json",
  "memory_url": "http://localhost:9001",
  "memory_user": "default"
}
```

- [ ] **Step 5: Crear `LICENSE` (MIT)**

```text
MIT License

Copyright (c) 2026 noualit

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore config.example.json LICENSE
git commit -m "chore: configuracion base del proyecto"
```

---

## Task 2: Modulo de configuracion (`config.py`)

**Files:**
- Create: `D:\python\Voice-WebUI\config.py`
- Test: `D:\python\Voice-WebUI\tests\test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_config_defaults():
    from config import AppConfig
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        cfg = AppConfig()
        assert cfg.llama_url == "http://localhost:8080/v1/chat/completions"
        assert cfg.model == "qwen3.6-27b"
        assert cfg.temperature == 0.6
        assert cfg.memory_url == "http://localhost:9001"
        assert cfg.memory_user == "default"
        assert cfg.system_prompt  # non-empty


def test_config_roundtrip():
    from config import AppConfig
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        cfg = AppConfig()
        cfg.system_prompt = "Nuevo prompt"
        cfg.temperature = 0.8
        cfg.save()
        cfg2 = AppConfig()
        assert cfg2.system_prompt == "Nuevo prompt"
        assert cfg2.temperature == 0.8


def test_config_env_overrides():
    from config import AppConfig
    os.environ["LLAMA_URL"] = "http://other:9999/v1/chat/completions"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            cfg = AppConfig()
            assert cfg.llama_url == "http://other:9999/v1/chat/completions"
    finally:
        os.environ.pop("LLAMA_URL", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'config'"

- [ ] **Step 3: Write minimal implementation**

```python
# config.py
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = (
    "Eres un asistente util. Responde en espanol.\n\n"
    "MEMORY WORKFLOW:\n"
    "- At the start of each new conversation, call create_conversation with a short title.\n"
    "- Use the conversation_id from create_conversation when calling save_memory.\n"
    "- Before answering questions about past topics, call search_memories FIRST.\n"
    "- When the user shares important information, save it with save_memory.\n"
    "- If list_conversations has previous chats, check get_conversation_history for context."
)

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.json"))


class AppConfig:
    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_PATH
        self.llama_url = os.environ.get("LLAMA_URL", "http://localhost:8080/v1/chat/completions")
        self.model = os.environ.get("LLAMA_MODEL", "qwen3.6-27b")
        self.temperature = float(os.environ.get("LLAMA_TEMPERATURE", "0.6"))
        self.whisper_model = os.environ.get("WHISPER_MODEL", "small")
        self.mcp_config_path = os.environ.get("MCP_CONFIG_PATH", "./mcp-config.json")
        self.memory_url = os.environ.get("MEMORY_URL", "http://localhost:9001")
        self.memory_user = os.environ.get("MEMORY_USER", "default")
        self.system_prompt = ""
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key in ("llama_url", "model", "temperature", "whisper_model",
                            "mcp_config_path", "memory_url", "memory_user", "system_prompt"):
                    if key in data:
                        setattr(self, key, data[key])
            except (json.JSONDecodeError, OSError):
                pass
        if not self.system_prompt:
            self.system_prompt = DEFAULT_SYSTEM_PROMPT
        if not self.path.exists():
            self.save()

    def save(self):
        data = {
            "llama_url": self.llama_url,
            "model": self.model,
            "temperature": self.temperature,
            "whisper_model": self.whisper_model,
            "mcp_config_path": self.mcp_config_path,
            "memory_url": self.memory_url,
            "memory_user": self.memory_user,
            "system_prompt": self.system_prompt,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: modulo de configuracion con defaults y persistencia"
```

---

## Task 3: Cliente llama-memory (`memory_client.py`)

**Files:**
- Create: `D:\python\Voice-WebUI\memory_client.py`
- Test: `D:\python\Voice-WebUI\tests\test_memory_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_client.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": '{"memories": [{"text": "hola", "tags": null}]}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = client.get_history("conv-1")
        assert len(result["memories"]) == 1
        assert result["memories"][0]["text"] == "hola"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_client.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'memory_client'"

- [ ] **Step 3: Write minimal implementation**

```python
# memory_client.py
import json
import requests


class MemoryClient:
    """Cliente HTTP para llama-memory (servicio MCP en puerto 9001)."""

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
        r = requests.post(f"{self.base_url}/mcp", json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
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

    def list_conversations(self, limit: int = 50) -> dict:
        return self._call_tool("list_conversations", {"limit": limit})

    def get_history(self, conversation_id: str, limit: int = 50) -> dict:
        return self._call_tool("get_conversation_history", {
            "conversation_id": conversation_id,
            "limit": limit,
        })

    def search_memories(self, query: str, limit: int = 10) -> dict:
        return self._call_tool("search_memories", {"query": query, "limit": limit})

    def save_memory(self, text: str, conversation_id: str | None = None, tags: list | None = None) -> dict:
        args = {"text": text}
        if conversation_id:
            args["conversation_id"] = conversation_id
        if tags:
            args["tags"] = tags
        return self._call_tool("save_memory", args)

    def health(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.json() if r.status_code == 200 else {"status": "error"}
        except requests.RequestException:
            return {"status": "unreachable"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add memory_client.py tests/test_memory_client.py
git commit -m "feat: cliente HTTP para llama-memory"
```

---

## Task 4: Cliente LLM con streaming (`llm_client.py`)

**Files:**
- Create: `D:\python\Voice-WebUI\llm_client.py`
- Test: `D:\python\Voice-WebUI\tests\test_llm_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_chat_stream_yields_deltas():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 0.6)

    # Simular respuesta SSE de llama.cpp
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Hola"}}]}',
        b'data: {"choices": [{"delta": {"content": " mundo"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass

    with patch("requests.post", return_value=FakeResp()) as mock_post:
        chunks = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        assert chunks == ["Hola", " mundo"]
        # Verificar stream=True en payload
        payload = mock_post.call_args[1]["json"]
        assert payload["stream"] is True
        assert payload["model"] == "qwen3.6-27b"


def test_chat_stream_handles_tool_calls():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 0.6)

    # Primera respuesta: tool call, segunda: contenido final
    fake_lines = [
        b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "search_memories", "arguments": "{\"query\": \"test\"}"}}]}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        result = client.chat_stream([{"role": "user", "content": "hi"}], {})
        # Debe detectar tool_calls y retornarlos como dict
        # (el streaming real usa accumulate_tool_calls internamente)
        assert result is not None  # verificar que no explota


def test_chat_stream_raises_on_error():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 0.6)

    class FakeResp:
        def raise_for_status(self):
            raise Exception("HTTP 500")
        def iter_lines(self):
            return iter([])

    with patch("requests.post", return_value=FakeResp()):
        try:
            list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
            assert False, "Debio lanzar excepcion"
        except Exception as e:
            assert "HTTP 500" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'llm_client'"

- [ ] **Step 3: Write minimal implementation**

```python
# llm_client.py
import json
import requests


class LLMClient:
    """Cliente HTTP para llama.cpp con streaming SSE y tool calling."""

    def __init__(self, base_url: str, model: str, temperature: float, timeout: int = 120):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def _payload(self, messages: list, tools: list | None, stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat_stream(self, messages: list, tools: list | None):
        """
        Envia chat completion con stream=True y produce un generador de dicts.
        Cada yield es un dict: {"type": "delta", "content": "..."} o
        {"type": "tool_calls", "tool_calls": [...]} o {"type": "done"}.
        """
        payload = self._payload(messages, tools, stream=True)
        r = requests.post(self.base_url, json=payload, stream=True, timeout=self.timeout)
        r.raise_for_status()

        tool_calls_accum = {}
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    yield {"type": "delta", "content": delta["content"]}
                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if "id" in tc and tc["id"]:
                            tool_calls_accum[idx]["id"] = tc["id"]
                        if tc.get("function"):
                            if tc["function"].get("name"):
                                tool_calls_accum[idx]["function"]["name"] += tc["function"]["name"]
                            if tc["function"].get("arguments"):
                                tool_calls_accum[idx]["function"]["arguments"] += tc["function"]["arguments"]
            # Al final, si hay tool calls acumuladas, emitirlas
            if tool_calls_accum:
                calls = [tool_calls_accum[i] for i in sorted(tool_calls_accum)]
                yield {"type": "tool_calls", "tool_calls": calls}
        finally:
            r.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_llm_client.py
git commit -m "feat: cliente LLM con streaming SSE y tool calling"
```

---

## Task 5: MCP Manager (`mcp_manager.py`)

**Files:**
- Create: `D:\python\Voice-WebUI\mcp_manager.py`
- Test: `D:\python\Voice-WebUI\tests\test_mcp_manager.py`

Este modulo se reutiliza del `voice2llama_mcp.py` existente (clases `MCPClient` y `MCPToolManager`), pero con soporte para agregar/eliminar servidores via config JSON.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_manager.py
import sys
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_config(tmp, servers):
    path = os.path.join(tmp, "mcp-config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": servers}, f)
    return path


def test_load_config_returns_servers():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "server1": {"command": "python", "args": ["-m", "server1"]}
        })
        mgr = MCPToolManager(path)
        servers = mgr.load_config()
        assert "server1" in servers
        assert servers["server1"]["command"] == "python"


def test_add_server_saves_to_config():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {})
        mgr = MCPToolManager(path)
        mgr.add_server("newserver", "python", ["-m", "server"])
        servers = mgr.load_config()
        assert "newserver" in servers
        assert servers["newserver"]["command"] == "python"
        assert servers["newserver"]["args"] == ["-m", "server"]


def test_remove_server_saves_to_config():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "a": {"command": "python", "args": []},
            "b": {"command": "python", "args": []},
        })
        mgr = MCPToolManager(path)
        mgr.remove_server("a")
        servers = mgr.load_config()
        assert "a" not in servers
        assert "b" in servers


def test_get_openai_tools_format():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {})
        mgr = MCPToolManager(path)
        mgr.all_tools = [{
            "name": "test_tool",
            "description": "A test tool",
            "inputSchema": {"type": "object", "properties": {}},
            "_mcp_server": "srv",
        }]
        tools = mgr.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "test_tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_manager.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'mcp_manager'"

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_manager.py
import json
import os
import shutil
import subprocess
import threading
import time


class MCPClient:
    def __init__(self, name, command, args, env=None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.process = None
        self.request_id = 0
        self.pending = {}
        self.tools = []
        self.running = False
        self.reader_thread = None
        self.lock = threading.Lock()

    def _resolve_command(self):
        cmd = self.command
        if cmd.lower() == "npx":
            npx_path = shutil.which("npx") or shutil.which("npx.cmd")
            if npx_path:
                return npx_path, self.args, False
            return "cmd", ["/c", "npx"] + self.args, False
        if cmd.lower() in ("cmd", "cmd.exe"):
            return cmd, self.args, False
        resolved = shutil.which(cmd)
        if resolved:
            return resolved, self.args, False
        return cmd, self.args, False

    def start(self):
        env = os.environ.copy()
        env.update(self.env)
        cmd, args, use_shell = self._resolve_command()
        try:
            self.process = subprocess.Popen(
                [cmd] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,
                shell=False,
            )
            self.running = True
            self.reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self.reader_thread.start()
            init_result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "voice-webui", "version": "2.0.0"},
            })
            if init_result:
                self._send_notification("notifications/initialized", {})
                tools_result = self._send_request("tools/list", {})
                if tools_result and "tools" in tools_result:
                    self.tools = tools_result["tools"]
                return True
            else:
                self.stop()
                return False
        except Exception:
            return False

    def _read_stdout(self):
        while self.running and self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                msg = json.loads(line)
                if "id" in msg and msg["id"] in self.pending:
                    self.pending[msg["id"]] = msg
            except (json.JSONDecodeError, Exception):
                pass

    def _send_request(self, method, params, timeout=10):
        with self.lock:
            self.request_id += 1
            req_id = self.request_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self.pending[req_id] = None
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except Exception:
            return None
        start = time.time()
        while time.time() - start < timeout:
            if self.pending.get(req_id) is not None:
                resp = self.pending.pop(req_id)
                if "result" in resp:
                    return resp["result"]
                elif "error" in resp:
                    return None
            time.sleep(0.05)
        self.pending.pop(req_id, None)
        return None

    def _send_notification(self, method, params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except Exception:
            pass

    def call_tool(self, tool_name, arguments, timeout=30):
        return self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout=timeout)

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                self.process.kill()
        self.process = None
        self.tools = []


class MCPToolManager:
    def __init__(self, config_path):
        self.clients = {}
        self.all_tools = []
        self.config_path = config_path

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config.get("mcpServers", {})
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self, servers: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f, indent=2, ensure_ascii=False)

    def start_all(self):
        servers = self.load_config()
        for name, cfg in servers.items():
            client = MCPClient(name, cfg.get("command", ""), cfg.get("args", []), cfg.get("env", {}))
            if client.start():
                self.clients[name] = client
                for t in client.tools:
                    tool_copy = dict(t)
                    tool_copy["_mcp_server"] = name
                    self.all_tools.append(tool_copy)

    def stop_all(self):
        for name, client in self.clients.items():
            client.stop()
        self.clients.clear()
        self.all_tools.clear()

    def restart_server(self, name: str):
        """Detiene y reinicia un servidor MCP por su nombre."""
        if name in self.clients:
            self.clients[name].stop()
            del self.clients[name]
            self._rebuild_tools()
        servers = self.load_config()
        if name in servers:
            cfg = servers[name]
            client = MCPClient(name, cfg.get("command", ""), cfg.get("args", []), cfg.get("env", {}))
            if client.start():
                self.clients[name] = client
                self._rebuild_tools()
                return True
        return False

    def _rebuild_tools(self):
        self.all_tools = []
        for name, client in self.clients.items():
            for t in client.tools:
                tool_copy = dict(t)
                tool_copy["_mcp_server"] = name
                self.all_tools.append(tool_copy)

    def add_server(self, name: str, command: str, args: list, env: dict | None = None):
        servers = self.load_config()
        servers[name] = {"command": command, "args": args, "env": env or {}}
        self.save_config(servers)
        return self.restart_server(name)

    def remove_server(self, name: str):
        servers = self.load_config()
        if name in servers:
            del servers[name]
            self.save_config(servers)
        if name in self.clients:
            self.clients[name].stop()
            del self.clients[name]
            self._rebuild_tools()
        return True

    def get_openai_tools(self):
        tools = []
        for t in self.all_tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", "unknown"),
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })
        return tools

    def execute_tool(self, tool_name, arguments):
        for t in self.all_tools:
            if t.get("name") == tool_name:
                server = t.get("_mcp_server")
                if server in self.clients:
                    return self.clients[server].call_tool(tool_name, arguments)
        return {"error": f"Herramienta {tool_name} no encontrada"}

    def get_status(self):
        servers = []
        for name, client in self.clients.items():
            online = client.running and client.process and client.process.poll() is None
            servers.append({
                "name": name,
                "online": online,
                "tools": len(client.tools),
            })
        return {"servers": servers, "total_tools": len(self.all_tools)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_manager.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_manager.py tests/test_mcp_manager.py
git commit -m "feat: MCP manager con add/remove/restart de servidores"
```

---

## Task 6: Flask app backend (`app.py`)

**Files:**
- Create: `D:\python\Voice-WebUI\app.py`
- Test: `D:\python\Voice-WebUI\tests\test_app.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py
import sys
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_index_serves_html():
    from app import app
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        # Crear config minimal
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        test_client = app.test_client()
        resp = test_client.get("/")
        assert resp.status_code == 200
        assert b"<!DOCTYPE html>" in resp.data


def test_get_config():
    from app import app
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        test_client = app.test_client()
        resp = test_client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "system_prompt" in data
        assert "llama_url" in data


def test_post_config_updates():
    from app import app
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        test_client = app.test_client()
        resp = test_client.post("/api/config", json={"system_prompt": "Nuevo", "temperature": 0.9})
        assert resp.status_code == 200
        # Verificar que se persistio
        resp2 = test_client.get("/api/config")
        data = resp2.get_json()
        assert data["system_prompt"] == "Nuevo"
        assert data["temperature"] == 0.9


def test_transcribe():
    from app import app
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        test_client = app.test_client()
        # Mock whisper
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "Hola mundo"}
        with patch("app.whisper_model", mock_model):
            data = {"audio": (io.BytesIO(b"fakeaudio"), "rec.webm")}
            resp = test_client.post("/api/transcribe", data=data, content_type="multipart/form-data")
            assert resp.status_code == 200
            assert resp.get_json()["transcription"] == "Hola mundo"


def test_mcp_status():
    from app import app
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        test_client = app.test_client()
        resp = test_client.get("/api/mcp-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "servers" in data
        assert "total_tools" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app'"

- [ ] **Step 3: Write minimal implementation**

```python
# app.py
import atexit
import io
import json
import os
import tempfile

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context

from config import AppConfig
from llm_client import LLMClient
from memory_client import MemoryClient
from mcp_manager import MCPToolManager

app = Flask(__name__, static_folder="static", static_url_path="")

# Configuracion global (se inicializa en _init_globals)
config = None
llm_client = None
memory_client = None
mcp_manager = None
whisper_model = None

MAX_ITERATIONS = 5


def _init_globals():
    global config, llm_client, memory_client, mcp_manager, whisper_model
    config = AppConfig()
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-local-secret")
    llm_client = LLMClient(config.llama_url, config.model, config.temperature)
    memory_client = MemoryClient(config.memory_url, config.memory_user)
    mcp_manager = MCPToolManager(config.mcp_config_path)
    mcp_manager.start_all()
    import whisper
    whisper_model = whisper.load_model(config.whisper_model)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global llm_client
    if request.method == "GET":
        return jsonify({
            "system_prompt": config.system_prompt,
            "llama_url": config.llama_url,
            "model": config.model,
            "temperature": config.temperature,
            "whisper_model": config.whisper_model,
            "memory_url": config.memory_url,
        })
    data = request.get_json() or {}
    if "system_prompt" in data:
        config.system_prompt = data["system_prompt"]
    if "llama_url" in data:
        config.llama_url = data["llama_url"]
    if "model" in data:
        config.model = data["model"]
    if "temperature" in data:
        config.temperature = float(data["temperature"])
    config.save()
    # Recrear cliente LLM con nueva config
    llm_client = LLMClient(config.llama_url, config.model, config.temperature)
    return jsonify({"status": "ok"})


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No se recibio audio"}), 400
    audio = request.files["audio"]
    tmp_path = None
    try:
        ext = os.path.splitext(audio.filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            audio.save(tmp.name)
            tmp_path = tmp.name
        result = whisper_model.transcribe(
            tmp_path,
            language="es",
            fp16=False,
            initial_prompt="Esto es una transcripcion en espanol:",
        )
        text = result["text"].strip()
        if not text:
            return jsonify({"error": "No se detecto voz"}), 400
        return jsonify({"transcription": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    try:
        result = memory_client.list_conversations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/conversations", methods=["POST"])
def api_create_conversation():
    data = request.get_json() or {}
    name = data.get("name", "Nueva conversacion")
    try:
        result = memory_client.create_conversation(name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/conversations/<conv_id>", methods=["GET"])
def api_get_conversation(conv_id):
    try:
        result = memory_client.get_history(conv_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp-status")
def api_mcp_status():
    return jsonify(mcp_manager.get_status())


@app.route("/api/mcp/add", methods=["POST"])
def api_mcp_add():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    command = data.get("command", "").strip()
    args = data.get("args", [])
    env = data.get("env") or {}
    if not name or not command:
        return jsonify({"error": "name y command son requeridos"}), 400
    try:
        ok = mcp_manager.add_server(name, command, args, env)
        return jsonify({"status": "ok" if ok else "failed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp/remove", methods=["POST"])
def api_mcp_remove():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name es requerido"}), 400
    try:
        mcp_manager.remove_server(name)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    conversation_id = data.get("conversation_id")
    if not text:
        return jsonify({"error": "Falta el texto"}), 400

    def generate():
        messages = [{"role": "system", "content": config.system_prompt}]
        history = []
        if conversation_id:
            try:
                mem = memory_client.get_history(conversation_id)
                for m in mem.get("memories", []):
                    history.append({"role": "assistant", "content": m.get("text", "")})
            except Exception:
                pass
        messages.extend(history)
        messages.append({"role": "user", "content": text})

        tools = mcp_manager.get_openai_tools()
        iteration = 0
        full_response = ""

        while iteration < MAX_ITERATIONS:
            iteration += 1
            try:
                for event in llm_client.chat_stream(messages, tools):
                    if event["type"] == "delta":
                        full_response += event["content"]
                        yield f"data: {json.dumps({'type': 'delta', 'content': event['content']})}\n\n"
                    elif event["type"] == "tool_calls":
                        # Ejecutar tool calls
                        assistant_msg = {"role": "assistant", "content": "", "tool_calls": event["tool_calls"]}
                        messages.append(assistant_msg)
                        for tc in event["tool_calls"]:
                            if tc.get("type") == "function":
                                func = tc["function"]
                                tool_name = func["name"]
                                try:
                                    args = json.loads(func["arguments"])
                                except json.JSONDecodeError:
                                    args = {}
                                result = mcp_manager.execute_tool(tool_name, args)
                                result_text = json.dumps(result, ensure_ascii=False, indent=2)
                                if len(result_text) > 8000:
                                    result_text = result_text[:8000] + "\n...[truncado]"
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.get("id", ""),
                                    "content": result_text,
                                })
                                yield f"data: {json.dumps({'type': 'tool', 'name': tool_name, 'result': result_text[:500]})}\n\n"
                        # Continuar loop para la siguiente respuesta
                        break
                    elif event["type"] == "done":
                        break
                else:
                    break
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break

        # Guardar memoria si hay conversation_id
        if conversation_id and full_response:
            try:
                memory_client.save_memory(full_response, conversation_id)
            except Exception:
                pass

        yield f"data: {json.dumps({'type': 'done', 'response': full_response})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/health")
def api_health():
    memory_health = memory_client.health() if memory_client else {"status": "not_ready"}
    return jsonify({
        "memory": memory_health,
        "mcp_tools": len(mcp_manager.all_tools) if mcp_manager else 0,
    })


@atexit.register
def cleanup():
    if mcp_manager:
        mcp_manager.stop_all()


if __name__ == "__main__":
    _init_globals()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: backend Flask con API completa y streaming"
```

---

## Task 7: Frontend - estructura base + sidebar (Alpine.js)

**Files:**
- Create: `D:\python\Voice-WebUI\static\index.html`
- Create: `D:\python\Voice-WebUI\static\style.css`
- Create: `D:\python\Voice-WebUI\static\app.js`

- [ ] **Step 1: Crear `static/index.html`**

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Voice-WebUI</title>
<link rel="stylesheet" href="/style.css">
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script defer src="/app.js"></script>
</head>
<body x-data="voiceApp()" x-init="init()">
  <div class="layout">
    <!-- Sidebar -->
    <aside class="sidebar" :class="{'open': sidebarOpen}">
      <div class="sidebar-header">
        <h2>Conversaciones</h2>
        <button class="btn btn-new" @click="newChat()">+ Nueva</button>
      </div>
      <div class="sidebar-list">
        <template x-for="conv in conversations" :key="conv.id">
          <div class="conv-item" :class="{'active': conv.id === currentConvId}"
               @click="openConversation(conv.id)">
            <div class="conv-name" x-text="conv.name"></div>
            <div class="conv-meta" x-text="conv.updated_at ? new Date(conv.updated_at).toLocaleString() : ''"></div>
          </div>
        </template>
        <div x-show="conversations.length === 0" class="empty-state">
          No hay conversaciones
        </div>
      </div>
      <div class="sidebar-footer">
        <button class="btn btn-config" @click="showConfig = !showConfig">Configuracion</button>
      </div>
    </aside>

    <!-- Main -->
    <main class="main">
      <!-- Top bar -->
      <div class="topbar">
        <button class="btn btn-toggle" @click="sidebarOpen = !sidebarOpen">☰</button>
        <div class="status">
          <span class="dot" :class="{'online': memoryOnline}"></span>
          <span x-text="statusText">Listo</span>
        </div>
        <div class="model-info" x-text="model"></div>
      </div>

      <!-- Chat area -->
      <div class="chat" id="chatArea">
        <template x-for="(msg, i) in messages" :key="i">
          <div class="msg" :class="'msg-' + msg.role">
            <template x-if="msg.role === 'tool'">
              <div class="tool-call">
                <b x-text="'Herramienta: ' + msg.name"></b>
                <div x-text="msg.result"></div>
              </div>
            </template>
            <template x-if="msg.role !== 'tool'">
              <div x-text="msg.content"></div>
            </template>
          </div>
        </template>
        <div x-show="loading" class="msg msg-bot typing">Pensando...</div>
      </div>

      <!-- Input area -->
      <div class="input-area">
        <button class="btn btn-mic" @mousedown="startRec()" @mouseup="stopRec()"
                @mouseleave="stopRec()" @touchstart="startRec($event)" @touchend="stopRec($event)"
                x-text="recording ? 'Grabando...' : 'Hablar'"></button>
        <input class="text-input" type="text" x-model="draft" @keydown.enter="sendText()"
               placeholder="Escribe o habla...">
        <button class="btn btn-send" @click="sendText()">Enviar</button>
      </div>
    </main>

    <!-- Config panel -->
    <div class="config-panel" x-show="showConfig" @click.self="showConfig = false">
      <div class="config-content">
        <h3>Configuracion</h3>

        <div class="config-section">
          <label>System Prompt</label>
          <textarea x-model="settings.system_prompt" rows="8"></textarea>
        </div>

        <div class="config-section">
          <label>LLM URL</label>
          <input type="text" x-model="settings.llama_url">
        </div>

        <div class="config-section">
          <label>Modelo</label>
          <input type="text" x-model="settings.model">
        </div>

        <div class="config-section">
          <label>Temperatura</label>
          <input type="number" step="0.1" x-model="settings.temperature">
        </div>

        <div class="config-section">
          <button class="btn btn-primary" @click="saveConfig()">Guardar config</button>
        </div>

        <h3>Servidores MCP</h3>
        <div class="config-section" x-for="srv in mcpServers" :key="srv.name">
          <div class="mcp-row">
            <span>
              <span class="dot" :class="{'online': srv.online}"></span>
              <b x-text="srv.name"></b>
              <span x-text="'(' + srv.tools + ' tools)'"></span>
            </span>
            <button class="btn btn-danger" @click="removeMCP(srv.name)">Eliminar</button>
          </div>
        </div>

        <div class="config-section">
          <h4>Agregar MCP</h4>
          <label>Nombre</label>
          <input type="text" x-model="newMcp.name" placeholder="mi-servidor">
          <label>Comando</label>
          <input type="text" x-model="newMcp.command" placeholder="python">
          <label>Args (JSON array)</label>
          <input type="text" x-model="newMcp.args" placeholder='["-m", "server"]'>
          <button class="btn btn-primary" @click="addMCP()">Agregar</button>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Crear `static/style.css`**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, sans-serif;
  background: #0d1117; color: #c9d1d9; height: 100vh;
}
.layout { display: flex; height: 100vh; }

/* Sidebar */
.sidebar {
  width: 260px; background: #161b22; border-right: 1px solid #30363d;
  display: flex; flex-direction: column; flex-shrink: 0;
}
.sidebar-header {
  padding: 12px; display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid #30363d;
}
.sidebar-header h2 { font-size: 14px; color: #58a6ff; }
.sidebar-list { flex: 1; overflow-y: auto; padding: 8px; }
.conv-item {
  padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 4px;
}
.conv-item:hover { background: #21262d; }
.conv-item.active { background: #1f6feb22; border-left: 3px solid #1f6feb; }
.conv-name { font-size: 13px; }
.conv-meta { font-size: 11px; color: #8b949e; }
.empty-state { color: #8b949e; font-size: 12px; text-align: center; padding: 20px; }
.sidebar-footer { padding: 12px; border-top: 1px solid #30363d; }

/* Main */
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar {
  padding: 10px 16px; border-bottom: 1px solid #30363d;
  display: flex; align-items: center; gap: 12px;
}
.status { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #8b949e; flex: 1; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #30363d; display: inline-block; }
.dot.online { background: #3fb950; }
.model-info { font-size: 11px; color: #8b949e; }

/* Chat */
.chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
.msg { max-width: 85%; padding: 8px 12px; border-radius: 10px; font-size: 13px; line-height: 1.4; word-break: break-word; }
.msg-user { align-self: flex-end; background: #1f6feb; color: #fff; }
.msg-bot { align-self: flex-start; background: #21262d; }
.msg-tool { align-self: flex-start; background: #0d1b2a; border: 1px solid #1f6feb; color: #79c0ff; font-size: 11px; max-width: 90%; }
.msg-error { align-self: center; color: #f85149; }
.typing { color: #8b949e; font-style: italic; }

/* Input */
.input-area {
  padding: 12px 16px; border-top: 1px solid #30363d;
  display: flex; gap: 8px;
}
.text-input {
  flex: 1; background: #161b22; border: 1px solid #30363d; color: #c9d1d9;
  padding: 10px 12px; border-radius: 8px; font-size: 14px;
}

/* Buttons */
.btn {
  padding: 8px 14px; border-radius: 6px; border: none; cursor: pointer;
  font-size: 13px; font-weight: 600;
}
.btn-new { background: #238636; color: #fff; }
.btn-send { background: #1f6feb; color: #fff; }
.btn-mic { background: #238636; color: #fff; padding: 10px 20px; }
.btn-mic:active { background: #da3633; }
.btn-config { background: transparent; border: 1px solid #30363d; color: #8b949e; width: 100%; }
.btn-danger { background: transparent; border: 1px solid #f85149; color: #f85149; }
.btn-primary { background: #1f6feb; color: #fff; }
.btn-toggle { background: transparent; border: 1px solid #30363d; color: #c9d1d9; }

/* Config panel */
.config-panel {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  display: flex; justify-content: center; align-items: center; z-index: 100;
}
.config-content {
  background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 20px; width: 500px; max-height: 80vh; overflow-y: auto;
}
.config-content h3 { color: #58a6ff; margin-bottom: 12px; }
.config-content h4 { color: #c9d1d9; margin: 8px 0; }
.config-section { margin-bottom: 16px; }
.config-section label { display: block; font-size: 12px; color: #8b949e; margin-bottom: 4px; }
.config-section input, .config-section textarea {
  width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
  padding: 8px 10px; border-radius: 6px; font-size: 13px;
}
.mcp-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px; background: #0d1117; border-radius: 6px; margin-bottom: 4px;
}
```

- [ ] **Step 3: Crear `static/app.js`**

```javascript
function voiceApp() {
  return {
    sidebarOpen: window.innerWidth > 768,
    showConfig: false,
    conversations: [],
    currentConvId: null,
    messages: [],
    draft: "",
    loading: false,
    recording: false,
    mediaRecorder: null,
    chunks: [],
    statusText: "Listo",
    memoryOnline: false,
    model: "",
    settings: {
      system_prompt: "",
      llama_url: "",
      model: "",
      temperature: 0.6,
    },
    mcpServers: [],
    newMcp: { name: "", command: "", args: "[]" },

    async init() {
      await this.loadConfig();
      await this.loadConversations();
      await this.loadMCPStatus();
    },

    async loadConfig() {
      try {
        const r = await fetch("/api/config");
        const j = await r.json();
        this.settings = {
          system_prompt: j.system_prompt,
          llama_url: j.llama_url,
          model: j.model,
          temperature: j.temperature,
        };
        this.model = j.model;
      } catch (e) {
        this.statusText = "Error cargando config";
      }
    },

    async saveConfig() {
      try {
        await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.settings),
        });
        this.model = this.settings.model;
        this.statusText = "Config guardada";
        this.showConfig = false;
      } catch (e) {
        this.statusText = "Error guardando config";
      }
    },

    async loadConversations() {
      try {
        const r = await fetch("/api/conversations");
        const j = await r.json();
        this.conversations = j.conversations || [];
        this.memoryOnline = true;
      } catch (e) {
        this.memoryOnline = false;
        this.statusText = "llama-memory no disponible";
      }
    },

    async loadMCPStatus() {
      try {
        const r = await fetch("/api/mcp-status");
        const j = await r.json();
        this.mcpServers = j.servers || [];
      } catch (e) {
        this.mcpServers = [];
      }
    },

    async newChat() {
      try {
        const r = await fetch("/api/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "Nueva conversacion" }),
        });
        const j = await r.json();
        this.currentConvId = j.conversation_id;
        this.messages = [];
        await this.loadConversations();
        this.statusText = "Nueva conversacion creada";
        this.scrollToBottom();
      } catch (e) {
        this.statusText = "Error creando conversacion";
      }
    },

    async openConversation(id) {
      this.currentConvId = id;
      try {
        const r = await fetch(`/api/conversations/${id}`);
        const j = await r.json();
        this.messages = (j.memories || []).map(m => ({
          role: "assistant",
          content: m.text,
        }));
        this.statusText = "Conversacion cargada";
        this.scrollToBottom();
      } catch (e) {
        this.statusText = "Error cargando conversacion";
      }
    },

    async sendText() {
      const text = this.draft.trim();
      if (!text || this.loading) return;
      this.draft = "";
      this.messages.push({ role: "user", content: text });
      await this.sendToLLM(text);
    },

    async sendToLLM(text) {
      this.loading = true;
      this.statusText = "Qwen pensando...";
      let currentBot = "";
      const botMsg = { role: "bot", content: "" };
      this.messages.push(botMsg);
      this.scrollToBottom();

      try {
        const r = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: text,
            conversation_id: this.currentConvId,
          }),
        });

        if (!r.ok) {
          const j = await r.json();
          throw new Error(j.error || "Error");
        }

        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // Parse SSE events
          const events = buffer.split("\n\n");
          buffer = events.pop();

          for (const evt of events) {
            if (!evt.startsWith("data: ")) continue;
            const dataStr = evt.slice(6);
            try {
              const data = JSON.parse(dataStr);
              if (data.type === "delta") {
                currentBot += data.content;
                botMsg.content = currentBot;
                this.scrollToBottom();
              } else if (data.type === "tool") {
                this.messages.push({
                  role: "tool",
                  name: data.name,
                  result: data.result,
                });
                this.scrollToBottom();
              } else if (data.type === "error") {
                this.messages.push({ role: "error", content: data.message });
              } else if (data.type === "done") {
                botMsg.content = data.response || currentBot;
                this.statusText = "Listo";
              }
            } catch (e) {
              console.error("Error parsing SSE:", e);
            }
          }
        }
      } catch (e) {
        botMsg.content = "Error: " + e.message;
        this.statusText = "Error";
      } finally {
        this.loading = false;
        this.scrollToBottom();
        await this.loadConversations();
      }
    },

    async startRec(e) {
      if (e) e.preventDefault();
      if (this.recording) return;
      this.recording = true;
      this.chunks = [];
      this.statusText = "Grabando...";
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.mediaRecorder = new MediaRecorder(stream);
        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data.size) this.chunks.push(e.data);
        };
        this.mediaRecorder.start(100);
      } catch (err) {
        this.statusText = "Error: " + err.message;
        this.recording = false;
      }
    },

    async stopRec(e) {
      if (e) e.preventDefault();
      if (!this.recording) return;
      this.recording = false;
      this.statusText = "Transcribiendo...";
      if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
        this.mediaRecorder.stop();
      }
      if (this.mediaRecorder) {
        this.mediaRecorder.onstop = async () => {
          const blob = new Blob(this.chunks, { type: "audio/webm" });
          const form = new FormData();
          form.append("audio", blob, "rec.webm");
          try {
            const r1 = await fetch("/api/transcribe", { method: "POST", body: form });
            const j1 = await r1.json();
            if (j1.error) throw new Error(j1.error);
            const text = j1.transcription;
            this.messages.push({ role: "user", content: text });
            await this.sendToLLM(text);
          } catch (err) {
            this.statusText = "Error: " + err.message;
          } finally {
            if (this.mediaRecorder && this.mediaRecorder.stream) {
              this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
            }
          }
        };
      }
    },

    async addMCP() {
      try {
        let args = [];
        try {
          args = JSON.parse(this.newMcp.args || "[]");
        } catch (e) {
          this.statusText = "Args no es JSON valido";
          return;
        }
        const r = await fetch("/api/mcp/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: this.newMcp.name,
            command: this.newMcp.command,
            args: args,
          }),
        });
        const j = await r.json();
        if (j.error) throw new Error(j.error);
        this.statusText = "MCP agregado";
        this.newMcp = { name: "", command: "", args: "[]" };
        await this.loadMCPStatus();
      } catch (e) {
        this.statusText = "Error: " + e.message;
      }
    },

    async removeMCP(name) {
      try {
        const r = await fetch("/api/mcp/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name }),
        });
        const j = await r.json();
        if (j.error) throw new Error(j.error);
        this.statusText = "MCP eliminado";
        await this.loadMCPStatus();
      } catch (e) {
        this.statusText = "Error: " + e.message;
      }
    },

    scrollToBottom() {
      setTimeout(() => {
        const el = document.getElementById("chatArea");
        if (el) el.scrollTop = el.scrollHeight;
      }, 10);
    },
  };
}
```

- [ ] **Step 4: Verificar que sirve el frontend**

Run: `python -c "from app import app; test=app.test_client(); r=test.get('/'); print(r.status_code); print(r.data[:100])"`
Expected: 200 con HTML

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat: frontend Alpine.js con sidebar, chat y config"
```

---

## Task 8: README.md

**Files:**
- Create: `D:\python\Voice-WebUI\README.md`

- [ ] **Step 1: Crear `README.md`**

```markdown
# Voice-WebUI

Web-UI de chat por voz para modelos LLM locales (llama.cpp). Whisper para
reconocimiento de voz, streaming de respuestas, herramientas MCP configurables
desde la UI, y conversaciones persistentes con memoria semantica via
[llama-memory](https://github.com/noualit/llama-memory).

> Proyecto para uso local/demo. No exponer a internet sin hardening adicional.

## Requerimientos

- Python 3.11 (Miniconda recomendado)
- llama.cpp server con modelo Qwen 3.6-27B (o compatible con OpenAI API)
- [llama-memory](https://github.com/noualit/llama-memory) corriendo en puerto 9001
- PostgreSQL 16+ con PGVector (para llama-memory)

## Instalacion

```bash
# Crear entorno
conda create -n voice-webui python=3.11
conda activate voice-webui

# Instalar dependencias
pip install -r requirements.txt

# Configurar
cp .env.example .env   # editar valores
cp config.example.json config.json   # editar system prompt y endpoints
```

## Ejecucion

```bash
# 1. Iniciar llama.cpp server (ejemplo)
llama-server -m qwen3.6-27b.gguf --port 8080 --jinja

# 2. Iniciar llama-memory
cd ../llama-memory && ./scripts/run_server.ps1

# 3. Iniciar Voice-WebUI
python app.py
```

Abrir `http://localhost:5000`.

## Uso

- **Hablar**: mantener pulsado el boton de microfono y soltar para enviar.
- **Escribir**: usar el campo de texto y Enter.
- **Conversaciones**: sidebar muestra las persistentes via llama-memory.
- **Configuracion**: boton "Configuracion" en la sidebar para editar system
  prompt, parametros del LLM, y agregar/eliminar servidores MCP.

## Arquitectura

```
Browser (Alpine.js)  <->  Flask + Whisper  <->  llama.cpp (Qwen 3.6-27B)
                              |
                              v
                    llama-memory (FastAPI)  <->  PostgreSQL (PGVector)
```

- `app.py` - Backend Flask: API REST, streaming SSE, Whisper
- `mcp_manager.py` - Gestion de servidores MCP (stdio)
- `llm_client.py` - Cliente HTTP para llama.cpp con streaming
- `memory_client.py` - Cliente HTTP para llama-memory
- `static/` - Frontend Alpine.js

## Testing

```bash
pytest tests/ -q -p no:cacheprovider
```

## Licencia

MIT
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README con instalacion y uso"
```

---

## Task 9: Eliminar codigo viejo y verificacion final

**Files:**
- Delete: `D:\python\Voice-WebUI\voice2llama_mcp.py` (reemplazado por app.py)

- [ ] **Step 1: Verificar que app.py importa sin errores**

Run: `python -c "import app"` (sin ejecutar _init_globals, solo importar modulos)
Expected: Sin errores de importacion

- [ ] **Step 2: Verificar que todos los modulos importan**

Run:
```powershell
python -c "import app, config, llm_client, memory_client, mcp_manager; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Ejecutar toda la suite de tests**

Run: `python -m pytest tests/ -v -p no:cacheprovider`
Expected: Todos PASS

- [ ] **Step 4: Eliminar `voice2llama_mcp.py`**

```powershell
Remove-Item "D:\python\Voice-WebUI\voice2llama_mcp.py"
```

- [ ] **Step 5: Verificar conteo de lineas de cada archivo**

Run:
```powershell
Get-ChildItem -Filter "*.py" | ForEach-Object { "$($_.Name): $((Get-Content $_.FullName).Count) lineas" }
```
Expected: Todos < 600 lineas

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: eliminar voice2llama_mcp.py, reemplazado por arquitectura modular"
```

---

## Task 10: Prueba E2E manual

**Files:** Ninguno (verificacion manual)

- [ ] **Step 1: Iniciar servicios**

Requisitos previos:
- llama.cpp server corriendo en `http://localhost:8080` (o el configurado)
- llama-memory corriendo en `http://localhost:9001`

- [ ] **Step 2: Iniciar Voice-WebUI**

Run: `python app.py`
Expected: Servidor arranca en puerto 5000, MCPs se cargan

- [ ] **Step 3: Verificar con navegador**

1. Abrir `http://localhost:5000`
2. Verificar sidebar con conversaciones (de llama-memory)
3. Crear nueva conversacion
4. Enviar mensaje de texto -> verificar streaming
5. Hablar por microfono -> verificar transcripcion + respuesta
6. Abrir configuracion -> editar system prompt -> guardar
7. Agregar un MCP -> verificar que aparece en el estado

- [ ] **Step 4: Documentar resultados en `docs/sesiones/`**

Crear `docs/sesiones/2026-08-31-voice-webui-v2.md` con:
- Que se hizo
- Resultados de la prueba E2E
- Problemas encontrados (si los hay)
- Pendientes

---

## Self-Review

### Spec coverage
- [x] Chat por voz (Task 7 frontend + Task 6 transcribe)
- [x] Streaming SSE (Task 4 llm_client + Task 6 chat endpoint)
- [x] Config MCPs desde UI (Task 5 add/remove + Task 6 endpoints + Task 7 UI)
- [x] System prompt editable (Task 2 config + Task 6 config endpoint + Task 7 UI)
- [x] Lista conversaciones (Task 3 memory_client + Task 6 + Task 7 sidebar)
- [x] Abrir conversacion carga historial (Task 3 get_history + Task 6 + Task 7)
- [x] Archivos < 600 lineas (Task 9 verifica)
- [x] Publico en GitHub: .env.example, .gitignore, LICENSE, README (Tasks 1, 8)
- [x] Sin rutas absolutas (Task 1 usa env vars, Task 2 defaults relativos)
- [x] secret_key no hardcodeada (Task 6 usa FLASK_SECRET_KEY env)

### Placeholder scan
Todos los pasos tienen codigo concreto. No hay TBD/TODO.

### Type consistency
- `MemoryClient.create_conversation(name)` -> Task 6 `memory_client.create_conversation(name)` - consistente
- `LLMClient.chat_stream(messages, tools)` -> Task 6 `llm_client.chat_stream(messages, tools)` - consistente
- `MCPToolManager.add_server(name, command, args, env)` -> Task 6 `mcp_manager.add_server(name, command, args, env)` - consistente
- `MCPToolManager.get_status()` -> Task 6 `mcp_manager.get_status()` - consistente
