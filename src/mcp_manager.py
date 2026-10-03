# mcp_manager.py
# MCPToolManager: gestiona servidores MCP (add/remove/restart/start_all/stop_all).
# Los clientes (MCPClient stdio, MCPHTTPClient http) viven en mcp_clients.py.

import json
import os
import threading
import time

from mcp_clients import MCPClient, MCPHTTPClient


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

    def _make_client(self, name: str, cfg: dict):
        """Crea el cliente MCP segun el transport configurado.

        - transport "http" (o "sse"): usa MCPHTTPClient contra `url`.
        - cualquier otro valor: usa MCPClient (stdio/subprocess).
        """
        transport = (cfg.get("transport") or "").lower()
        if transport in ("http", "sse"):
            url = cfg.get("url", "")
            if not url:
                return None
            return MCPHTTPClient(
                name=name,
                url=url,
                headers=cfg.get("headers") or {},
                timeout=int(cfg.get("timeout", 30)),
            )

        # Todo lo que no es http/sse se considera stdio (subprocess),
        # siempre y cuando tenga 'command'.
        command = cfg.get("command", "")
        if not command:
            return None
        return MCPClient(
            name=name,
            command=command,
            args=cfg.get("args", []),
            env=cfg.get("env") or {},
        )

    def start_all(self):
        self._reconnect_stop = False
        servers = self.load_config()
        for name, cfg in servers.items():
            if cfg.get("disabled"):
                continue
            client = self._make_client(name, cfg)
            if client is None:
                continue
            result = client.start()
            if result:
                self.clients[name] = client
                for t in client.tools:
                    tool_copy = dict(t)
                    tool_copy["_mcp_server"] = name
                    self.all_tools.append(tool_copy)
            else:
                import logging
                logging.getLogger(__name__).error("Fallo al iniciar servidor MCP '%s'", name)
        # Iniciar hilo de fondo que reintenta arrancar servidores caudos.
        # Esto permite que un servidor que no estaba listo al boot (p.ej.
        # el servidor MCP AD que arranca 30s despues) se conecte automaticamente.
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def stop_all(self):
        self._reconnect_stop = True
        for name, client in self.clients.items():
            client.stop()
        self.clients.clear()
        self.all_tools.clear()

    def _reconnect_once(self):
        """Intenta reconectar servidores configurados que no estan en clients.

        Se llama desde el hilo de reconexion cada 30 segundos.
        """
        config = self.load_config()
        for name, cfg in config.items():
            if cfg.get("disabled"):
                continue
            if name in self.clients:
                continue
            client = self._make_client(name, cfg)
            if client is None:
                continue
            if client.start():
                self.clients[name] = client
                for t in client.tools:
                    tool_copy = dict(t)
                    tool_copy["_mcp_server"] = name
                    self.all_tools.append(tool_copy)

    def _reconnect_loop(self):
        """Bucle de fondo que reintenta arrancar servidores caudos cada 30s."""
        while not self._reconnect_stop:
            self._reconnect_once()
            time.sleep(30)

    def restart_server(self, name: str):
        """Detiene y reinicia un servidor MCP por su nombre."""
        if name in self.clients:
            self.clients[name].stop()
            del self.clients[name]
            self._rebuild_tools()
        servers = self.load_config()
        if name in servers:
            cfg = servers[name]
            if cfg.get("disabled"):
                return False
            client = self._make_client(name, cfg)
            if client and client.start():
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

    def add_http_server(self, name: str, url: str, headers: dict | None = None, timeout: int = 30):
        servers = self.load_config()
        servers[name] = {
            "transport": "http",
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
        }
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
                # Servidor MCP de origen: permite agrupar el prompt por servidor
                # (format_tools_prompt genera un bloque por servidor).
                "_mcp_server": t.get("_mcp_server", ""),
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
        config = self.load_config()
        for name in config:
            client = self.clients.get(name)
            if client is None:
                # Configurado pero no arrancado (fallo al iniciar o disabled):
                # se muestra offline para que la UI permita eliminarlo.
                servers.append({"name": name, "online": False, "tools": 0})
                continue
            online = client.running
            if hasattr(client, "process") and client.process:
                online = online and client.process.poll() is None
            # Para HTTP clients, hacer health check en vivo para detectar
            # servidores remotos que cayeron despues de iniciarse.
            elif isinstance(client, MCPHTTPClient):
                online = online and client._live_health_check()
            servers.append({
                "name": name,
                "online": online,
                "tools": len(client.tools),
            })
        return {"servers": servers, "total_tools": len(self.all_tools)}
