# tests/test_mcp_manager.py
import sys
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


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
        # Mock restart_server para no arrancar procesos reales
        with patch.object(mgr, "restart_server", return_value=True):
            mgr.add_server("newserver", "python", ["-m", "server"])
        servers = mgr.load_config()
        assert "newserver" in servers
        assert servers["newserver"]["command"] == "python"
        assert servers["newserver"]["args"] == ["-m", "server"]


def test_add_http_server_saves_to_config():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {})
        mgr = MCPToolManager(path)
        with patch.object(mgr, "restart_server", return_value=True):
            mgr.add_http_server("httpsrv", "http://localhost:8111/mcp",
                                {"Authorization": "Bearer x"}, 15)
        servers = mgr.load_config()
        assert servers["httpsrv"]["transport"] == "http"
        assert servers["httpsrv"]["url"] == "http://localhost:8111/mcp"
        assert servers["httpsrv"]["headers"] == {"Authorization": "Bearer x"}
        assert servers["httpsrv"]["timeout"] == 15


def test_make_client_http_crea_http_client():
    from mcp_manager import MCPToolManager
    from mcp_clients import MCPHTTPClient
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {})
        mgr = MCPToolManager(path)
        client = mgr._make_client("httpsrv", {
            "transport": "http",
            "url": "http://localhost:8111/mcp",
            "headers": {"a": "b"},
            "timeout": 10,
        })
        assert isinstance(client, MCPHTTPClient)
        assert client.url == "http://localhost:8111/mcp"
        assert client.timeout == 10


def test_make_client_http_sin_url_devuelve_none():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {})
        mgr = MCPToolManager(path)
        assert mgr._make_client("x", {"transport": "http"}) is None


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
        # El servidor MCP se propaga para poder agrupar el prompt por servidor
        assert tools[0]["_mcp_server"] == "srv"


def test_start_all_skips_disabled_servers():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "enabled": {"command": "python", "args": ["-m", "enabled"]},
            "disabled": {"command": "python", "args": ["-m", "disabled"], "disabled": True},
        })
        mgr = MCPToolManager(path)
        with patch.object(MCPToolManager, "_make_client") as mock_make:
            mgr.start_all()
            # Solo debe crearse cliente para el servidor habilitado
            created = [call.args[0] for call in mock_make.call_args_list]
            assert created == ["enabled"]


def test_restart_server_skips_disabled():
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "disabled": {"command": "python", "args": ["-m", "disabled"], "disabled": True},
        })
        mgr = MCPToolManager(path)
        with patch.object(MCPToolManager, "_make_client") as mock_make:
            ok = mgr.restart_server("disabled")
            assert ok is False
            mock_make.assert_not_called()


def test_get_status_incluye_total_tools():
    """get_status debe devolver servers y total_tools (antes el return de
    total_tools era inalcanzable por un return previo)."""
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "srv1": {"command": "python", "args": []},
            "srv2": {"command": "python", "args": []},
        })
        mgr = MCPToolManager(path)
        mgr.clients = {
            "srv1": MagicMock(running=True, tools=[{"name": "a"}, {"name": "b"}], process=None),
            "srv2": MagicMock(running=False, tools=[{"name": "c"}], process=None),
        }
        mgr.all_tools = [
            {"name": "a", "_mcp_server": "srv1"},
            {"name": "b", "_mcp_server": "srv1"},
            {"name": "c", "_mcp_server": "srv2"},
        ]
        status = mgr.get_status()
        assert status["total_tools"] == 3
        assert len(status["servers"]) == 2
        assert status["servers"][0]["tools"] == 2


def test_get_status_incluye_servers_configurados_aunque_no_arrancaran():
    """Un server guardado en config que fallo al arrancar (no esta en clients)
    debe aparecer en get_status con online=False; si no, la UI no permite
    eliminarlo."""
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "activo": {"command": "python", "args": []},
            "fallido": {"transport": "http", "url": "http://localhost:9/mcp"},
        })
        mgr = MCPToolManager(path)
        mgr.clients = {
            "activo": MagicMock(running=True, tools=[{"name": "a"}], process=None),
        }
        mgr.all_tools = [{"name": "a", "_mcp_server": "activo"}]
        status = mgr.get_status()
        names = {s["name"]: s for s in status["servers"]}
        assert "activo" in names
        assert names["activo"]["online"] is True
        assert "fallido" in names
        assert names["fallido"]["online"] is False
        assert names["fallido"]["tools"] == 0


def test_get_status_verifica_conectividad_viva_http_client():
    """get_status debe hacer un health check en vivo a los HTTP clients, no
    solo confiar en el flag cached 'running'. Si el servidor remoto esta
    caido, el status debe reflejarlo aunque running=True."""
    from mcp_manager import MCPToolManager
    from mcp_clients import MCPHTTPClient
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "online_srv": {"transport": "http", "url": "http://localhost:9999/mcp"},
            "offline_srv": {"transport": "http", "url": "http://localhost:9/mcp"},
        })
        mgr = MCPToolManager(path)
        # Simular cliente HTTP con running=True pero que el health check falla
        mock_http = MagicMock(spec=MCPHTTPClient)
        mock_http.running = True
        mock_http.tools = [{"name": "a"}]
        mock_http.process = None
        # Health check en vivo devuelve False (servidor caido)
        mock_http._live_health_check.return_value = False

        mgr.clients = {
            "online_srv": mock_http,
            "offline_srv": mock_http,
        }
        mgr.all_tools = [{"name": "a", "_mcp_server": "online_srv"}]

        status = mgr.get_status()
        names = {s["name"]: s for s in status["servers"]}
        # El health check en vivo debe haberse llamado
        mock_http._live_health_check.assert_called()
        # Ambos deben aparecer offline porque el mock devuelve False
        assert names["online_srv"]["online"] is False
        assert names["offline_srv"]["online"] is False


def test_get_status_mantiene_online_si_health_check_pasa():
    """Si el health check en vivo pasa, el cliente HTTP se marca online."""
    from mcp_manager import MCPToolManager
    from mcp_clients import MCPHTTPClient
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "srv": {"transport": "http", "url": "http://localhost:8111/mcp"},
        })
        mgr = MCPToolManager(path)
        mock_http = MagicMock(spec=MCPHTTPClient)
        mock_http.running = True
        mock_http.tools = [{"name": "a"}]
        mock_http.process = None
        mock_http._live_health_check.return_value = True

        mgr.clients = {"srv": mock_http}
        mgr.all_tools = [{"name": "a", "_mcp_server": "srv"}]

        status = mgr.get_status()
        names = {s["name"]: s for s in status["servers"]}
        assert names["srv"]["online"] is True


def test_reconnect_thread_reconnecta_servidores_fallidos():
    """El hilo de reconexion debe intentar arrancar servidores configurados
    que no estan en clients. Cuando el servidor se vuelve accesible, debe
    reconectarse automaticamente."""
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "fallido": {"transport": "http", "url": "http://localhost:9/mcp"},
        })
        mgr = MCPToolManager(path)
        # Al inicio no hay clientes (simula fallo en boot)
        assert "fallido" not in mgr.clients

        # Simular que start() ahora funciona
        with patch.object(mgr, "_make_client") as mock_make:
            mock_client = MagicMock()
            mock_client.start.return_value = True
            mock_client.tools = [{"name": "x"}]
            mock_make.return_value = mock_client

            # Ejecutar el bucle de reconexion una vez (no esperar el hilo)
            mgr._reconnect_once()

            assert "fallido" in mgr.clients
            mock_client.start.assert_called_once()


def test_reconnect_no_interfiere_con_servidores_corriendo():
    """El hilo de reconexion NO debe intentar reconectar servidores que ya
    estan en clients y corriendo."""
    from mcp_manager import MCPToolManager
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_config(tmp, {
            "activo": {"command": "python", "args": ["-m", "srv"]},
            "fallido": {"transport": "http", "url": "http://localhost:9/mcp"},
        })
        mgr = MCPToolManager(path)
        mock_activo = MagicMock()
        mock_activo.running = True
        mock_activo.tools = [{"name": "a"}]
        mock_activo.process = None
        mgr.clients = {"activo": mock_activo}

        with patch.object(mgr, "_make_client") as mock_make:
            mgr._reconnect_once()

            # Solo debe crear cliente para el servidor fallido, no para activo
            created_names = [call.args[0] for call in mock_make.call_args_list]
            assert created_names == ["fallido"]


# --- Tests para _normalize_arguments (bug 2026-09-17) ---

def test_normalize_arguments_list_computers_string_a_lista():
    """list_computers: attributes como string 'a,b,c' se convierte a lista."""
    from mcp_clients import MCPHTTPClient
    result = MCPHTTPClient._normalize_arguments("list_computers", {
        "attributes": "name,operatingSystem,lastLogonTimestamp"
    })
    assert result["attributes"] == ["name", "operatingSystem", "lastLogonTimestamp"]


def test_normalize_arguments_list_computers_ya_lista_no_muta():
    """list_computers: si attributes ya es lista, se devuelve igual (sin copia)."""
    from mcp_clients import MCPHTTPClient
    original = {"attributes": ["name", "description"]}
    result = MCPHTTPClient._normalize_arguments("list_computers", original)
    assert result["attributes"] == ["name", "description"]
    # Cuando ya es lista, no se crea copia innecesaria
    assert result is original


def test_normalize_arguments_otra_tool_no_muta():
    """Otras tools no son afectadas por la normalizacion."""
    from mcp_clients import MCPHTTPClient
    result = MCPHTTPClient._normalize_arguments("list_users", {
        "filter_criteria": "(&(objectClass=user))"
    })
    assert result == {"filter_criteria": "(&(objectClass=user))"}


def test_normalize_arguments_attributes_vacio_no_muta():
    """list_computers con attributes vacio no muta."""
    from mcp_clients import MCPHTTPClient
    result = MCPHTTPClient._normalize_arguments("list_computers", {
        "attributes": ""
    })
    assert result["attributes"] == ""


def test_normalize_arguments_attributes_none_no_muta():
    """list_computers con attributes None no muta."""
    from mcp_clients import MCPHTTPClient
    result = MCPHTTPClient._normalize_arguments("list_computers", {})
    assert "attributes" not in result


def test_normalize_arguments_no_dict_no_muta():
    """Si arguments no es dict, se devuelve igual."""
    from mcp_clients import MCPHTTPClient
    assert MCPHTTPClient._normalize_arguments("list_computers", "no es dict") == "no es dict"
    assert MCPHTTPClient._normalize_arguments("list_computers", None) is None


# --- Tests para error handling en call_tool ---

def test_http_call_tool_devuelve_error_del_servidor():
    """Cuando el servidor MCP HTTP responde con error JSON-RPC, la app
    debe devolver el error al modelo (no None) para que reciba feedback util."""
    from mcp_clients import MCPHTTPClient

    client = MCPHTTPClient("test", "http://localhost:9999/mcp")
    client._post = MagicMock(return_value={
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid arguments for tool 'list_computers'"}
    })

    result = client.call_tool("list_computers", {"attributes": "bad"})
    assert result is not None
    assert "error" in result
    assert "Invalid arguments" in result["error"]


def test_http_call_tool_devuelve_resultado_normal():
    """call_tool HTTP devuelve el resultado cuando el servidor responde con 'result'."""
    from mcp_clients import MCPHTTPClient

    client = MCPHTTPClient("test", "http://localhost:9999/mcp")
    client._post = MagicMock(return_value={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"computers": ["PC1", "PC2"]}
    })

    result = client.call_tool("list_computers", {})
    assert result == {"computers": ["PC1", "PC2"]}


def test_http_call_tool_normaliza_attributes_antes_de_llamar():
    """call_tool HTTP normaliza attributes de string a lista antes de enviar."""
    from mcp_clients import MCPHTTPClient

    client = MCPHTTPClient("test", "http://localhost:9999/mcp")
    captured_payload = {}

    def capture_post(payload, *args, **kwargs):
        captured_payload["params"] = payload.get("params", {})
        return {"jsonrpc": "2.0", "id": 1, "result": {"computers": []}}

    client._post = capture_post
    client.call_tool("list_computers", {"attributes": "name,os,logon"})

    # Los argumentos normalizados deben llegar al servidor como lista
    assert captured_payload["params"]["arguments"]["attributes"] == ["name", "os", "logon"]


def test_stdio_call_tool_devuelve_error_del_servidor():
    """Cuando el servidor MCP stdio responde con error JSON-RPC, call_tool
    debe devolver el error (usando _send_request_full)."""
    from mcp_clients import MCPClient

    client = MCPClient("test", "python", ["-m", "test"])
    # Simular respuesta de error del servidor
    client._send_request_full = MagicMock(return_value={
        "error": {"code": -32602, "message": "Invalid arguments"}
    })

    result = client.call_tool("bad_tool", {})
    assert result is not None
    assert "error" in result
    assert result["error"]["message"] == "Invalid arguments"


# --- Bug 2026-09-23: timeout del cliente vs timeout de la tool ---

def test_stdio_call_tool_respeta_timeout_del_argumento_de_la_tool():
    """El cliente debe esperar el 'timeout' que el LLM pasa como argumento de
    la tool (p.ej. run_powershell(timeout=60)) con margen, no el default de 30s.

    Bug 2026-09-23: con un ping sweep de 254 hosts, el cliente cortaba a 30s
    y la app devolvia "no devolvio resultado (posible error de conexion con
    el servidor MCP)" aunque el server seguia trabajando.
    """
    from mcp_clients import MCPClient

    client = MCPClient("test", "python", ["-m", "test"])
    captured = {}

    def fake_send(method, params, timeout):
        captured["timeout"] = timeout
        return {"result": {"stdout": "ok", "stderr": "", "exit_code": 0}}

    client._send_request_full = fake_send
    result = client.call_tool("run_powershell", {"script": "ping", "timeout": 60})
    assert result is not None
    # Margen por encima del timeout de la tool para que el server devuelva
    assert captured["timeout"] > 60


def test_stdio_call_tool_sin_timeout_argumento_usa_default_30s():
    """Sin argumento 'timeout' en la tool, el cliente mantiene el default de 30s."""
    from mcp_clients import MCPClient

    client = MCPClient("test", "python", ["-m", "test"])
    captured = {}

    def fake_send(method, params, timeout):
        captured["timeout"] = timeout
        return {"result": {"ok": True}}

    client._send_request_full = fake_send
    client.call_tool("get_system_info", {})
    assert captured["timeout"] == 30


def test_stdio_call_tool_con_timeout_no_reintenta():
    """Con timeout en los argumentos, call_tool NO reintenta: un reintento
    re-ejecuta la tool en el servidor (subprocess duplicado en tools largas,
    p.ej. 3 sweeps de ping en paralelo en el bug 2026-09-23)."""
    from mcp_clients import MCPClient

    client = MCPClient("test", "python", ["-m", "test"])
    calls = []
    client._send_request_full = lambda method, params, timeout: (calls.append(timeout) or None)
    result = client.call_tool("run_powershell", {"script": "x", "timeout": 60})
    assert result is None
    assert len(calls) == 1  # un solo intento, sin reintento duplicado
