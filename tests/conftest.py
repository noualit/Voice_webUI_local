# tests/conftest.py
# Fixtures compartidos para todos los tests de la app.
import sys
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def setup_app():
    """Configura env vars y crea config minimal para cada test."""
    old_env = {}
    for var in ("CONFIG_PATH", "MCP_CONFIG_PATH", "UPLOAD_DIR"):
        if var in os.environ:
            old_env[var] = os.environ[var]
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        os.environ["UPLOAD_DIR"] = os.path.join(tmp, "uploads")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        yield tmp
    for var, val in old_env.items():
        os.environ[var] = val
    for var in ("CONFIG_PATH", "MCP_CONFIG_PATH", "UPLOAD_DIR"):
        if var not in old_env:
            os.environ.pop(var, None)


@pytest.fixture
def app_module(setup_app):
    """Importa y prepara la app con mocks para los globals pesados."""
    import app as app_module
    # Reinicializar con config real (sin whisper)
    app_module.config = app_module.AppConfig()
    # Identidad de la maquina para los tests. El login solo admite la cuenta
    # configurada (ver src/user_identity.py), asi que la fixture declara "ana"
    # como tal: es el nombre con el que hacen login casi todos los tests, y asi
    # el namespace que se exercisea es siempre el mismo.
    app_module.config.memory_user = "ana"
    app_module.set_config_provider(lambda: app_module.config)  # Inyectar config en conversations.py
    app_module.app.secret_key = "test-secret"
    # Mocks para componentes externos
    app_module.memory_client = MagicMock()
    app_module.memory_client.list_conversations.return_value = {"conversations": []}
    app_module.memory_client.create_conversation.return_value = {"conversation_id": "123", "name": "x"}
    app_module.memory_client.get_history.return_value = {"memories": []}
    app_module.memory_client.health.return_value = {"status": "ok"}
    app_module.mcp_manager = MagicMock()
    app_module.mcp_manager.get_status.return_value = {"servers": [], "total_tools": 0}
    app_module.mcp_manager.get_openai_tools.return_value = []
    app_module.mcp_manager.execute_tool.return_value = {"result": "ok"}
    # Tools MCP simuladas: _tool_exists (defensa de ejecucion, bug 2026-09-14)
    # consulta mcp_manager.all_tools para decidir si una tool es real. Sin esto,
    # ningun test podria ejecutar tools MCP.
    app_module.mcp_manager.all_tools = [
        {"name": "web_search"},
        {"name": "run_command"},
        {"name": "get_timezone"},
        {"name": "list_users"},
        {"name": "get_users"},
        {"name": "list_organizational_units"},
        {"name": "run_powershell"},
    ]
    # LLM mock
    app_module.llm_client = MagicMock()
    yield app_module
    # Limpiar cache de metas entre tests para que sea determinista
    import conversations as conversations_ref
    with conversations_ref._meta_cache_lock:
        conversations_ref._meta_cache.clear()
    # Limpiar aprobaciones por conversacion entre tests (estado global nuevo)
    if hasattr(app_module, "_approved_tools_per_conv"):
        app_module._approved_tools_per_conv.clear()