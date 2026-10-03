# mcp_clients.py
# Clientes MCP: MCPClient (stdio/subprocess) y MCPHTTPClient (HTTP transport).
# Separado de mcp_manager.py para mantener archivos bajo 600 lineas.

import json
import os
import shutil
import subprocess
import threading
import time

import requests


class MCPClient:
    """Cliente MCP sobre stdio (subprocess).

    Inicia un proceso subprocess, envia JSON-RPC por stdin/stdout,
    y maneja la comunicacion con servidores MCP locales.
    """

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
        """Resuelve el comando y sus argumentos para subprocess.Popen.

        Devuelve (cmd, args, use_shell, cwd) donde:
        - cmd: el ejecutable a invocar (str)
        - args: lista de argumentos (list[str])
        - use_shell: True solo si es estrictamente necesario
        - cwd: directorio de trabajo (str o None)

        En Windows, los archivos .cmd/.bat NO se ejecutan directamente con
        shell=False. La solucion es invocarlos via cmd.exe /c con la ruta
        completa al .cmd/.bat como primer argumento.
        """
        cmd = self.command
        cwd = None
        # npx: usar la ruta completa al ejecutable (npx.cmd en Windows)
        if cmd.lower() == "npx":
            npx_path = shutil.which("npx")
            if npx_path:
                return npx_path, self.args, False, cwd
            # Fallback: buscar .cmd explicitamente
            npx_cmd = shutil.which("npx.cmd")
            if npx_cmd:
                return npx_cmd, self.args, False, cwd
            # Si no se encontro npx, intentar con cmd /c
            return "cmd", ["/c", "npx"] + self.args, True, cwd
        # cmd.exe explicito
        if cmd.lower() in ("cmd", "cmd.exe"):
            cmd_path = shutil.which("cmd.exe") or "cmd.exe"
            return cmd_path, self.args, True, cwd
        # Si es una ruta absoluta a .cmd/.bat, invocar via cmd.exe /c
        ext = os.path.splitext(cmd)[1].lower()
        if ext in (".cmd", ".bat"):
            return "cmd.exe", ["/c", cmd] + self.args, True, cwd
        # Comando normal: resolver via PATH
        resolved = shutil.which(cmd)
        if resolved:
            # .cmd/.bat -> invocar via cmd.exe /c <ruta> [args...]
            ext = os.path.splitext(resolved)[1].lower()
            if ext in (".cmd", ".bat"):
                return "cmd.exe", ["/c", resolved] + self.args, True, cwd
            return resolved, self.args, False, cwd
        # No se encontro en PATH: usar tal cual (puede fallar)
        return cmd, self.args, False, cwd

    def start(self):
        env = os.environ.copy()
        env.update(self.env)
        cmd, args, use_shell, cwd = self._resolve_command()
        try:
            popen_kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "env": env,
                "bufsize": 1,
                "shell": use_shell,
                "cwd": cwd or os.getcwd(),
            }
            # En Windows, usar CREATE_NEW_PROCESS_GROUP para evitar problemas
            # con las seales de Ctrl+C que matan el proceso hijo
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(
                [cmd] + args,
                **popen_kwargs,
            )
            # Esperar un poco para que el proceso se inicialice
            time.sleep(1)
            # Verificar si el proceso sigue vivo
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                return False
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
                return False
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
            except Exception:
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

    def _send_request_full(self, method, params, timeout=10):
        """Igual que _send_request pero devuelve el error completo del servidor
        (dict con 'error' key) en vez de None cuando el servidor responde con error.

        Util para call_tool donde el error del servidor es feedback util para el modelo.
        """
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
                    return {"error": resp["error"]}
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
        """Ejecuta una tool via MCP stdio con retry transitorio.

        Reintenta 2 veces si la llamada falla por error de red o timeout,
        ya que el proceso subprocess puede bloquearse momentaneamente.

        Devuelve el resultado de la tool, o un dict con 'error' si el servidor
        responde con error JSON-RPC (para feedback al modelo).

        Bug 2026-09-23: si el LLM pasa 'timeout' como argumento de la tool
        (p.ej. run_powershell(timeout=60)), el cliente debe esperar ese
        tiempo con margen en vez del default de 30s; si no, corta la llamada
        y la app reporta "no devolvio resultado (posible error de conexion
        con el servidor MCP)" aunque el server siga trabajando. Con timeout
        en los argumentos tampoco se reintenta: un reintento re-ejecuta la
        tool en el servidor (subprocess duplicado en tools largas).
        """
        tool_timeout = arguments.get("timeout") if isinstance(arguments, dict) else None
        try:
            # Margen fijo por encima del timeout de la tool: el server primero
            # ejecuta subprocess.run(timeout=N) y luego devuelve el dict.
            effective_timeout = int(tool_timeout) + 30 if tool_timeout else timeout
        except (TypeError, ValueError):
            effective_timeout = timeout
        max_retries = 0 if tool_timeout else 2
        for attempt in range(max_retries + 1):
            result = self._send_request_full("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            }, timeout=effective_timeout)
            if result is not None:
                return result
            if attempt < max_retries:
                time.sleep(0.5)
        return None

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


class MCPHTTPClient:
    """Cliente MCP sobre HTTP (streamable-HTTP o SSE).

    Permite conectar a servidores MCP expuestos via HTTP, como el que
    escucha un servidor remoto en /mcp (configurable en mcp-config.json).
    """

    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: int = 30):
        self.name = name
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.tools = []
        self.session_id: str | None = None
        self.running = False
        self._lock = threading.Lock()
        self._next_id = 0
        # Cache de recursos: se obtiene solo al iniciar (start) y se reutiliza.
        # list_resources() NO debe llamarse en cada request de chat (agrega
        # latencia HTTP innecesaria). Se invalida al reiniciar el servidor.
        self._resources_cache: list[dict] | None = None

    def _next_request_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _post(self, payload: dict, extra_headers: dict | None = None) -> dict | None:
        """Envia un JSON-RPC MCP por POST y devuelve el body JSON o None.

        Cumple con el spec streamable-HTTP transport:
        - MCP-Protocol-Version: obligatorio en cada POST
        - Mcp-Method: obligatorio (nombre del metodo JSON-RPC)
        - Mcp-Name: obligatorio para tools/call y resources/read
        """
        method = payload.get("method", "")
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # Headers obligatorios segun spec streamable-HTTP transport
            "MCP-Protocol-Version": "2024-11-05",
            "Mcp-Method": method,
        }
        # Mcp-Name requerido para metodos que operan sobre un recurso/nombre
        if method in ("tools/call", "resources/read"):
            name = payload.get("params", {}).get("name", "")
            if name:
                h["Mcp-Name"] = name
        h.update(self.headers)
        if extra_headers:
            h.update(extra_headers)
        # Reenviar session id si el servidor lo requiere
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        try:
            r = requests.post(
                self.url,
                json=payload,
                headers=h,
                timeout=self.timeout,
            )
            # Guardar session id devuelto por el servidor (streamable HTTP)
            sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if r.status_code == 202:
                return None
            if r.status_code >= 400:
                return None
            content_type = r.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return self._parse_sse(r.text)
            return r.json() if r.text else None
        except Exception:
            return None

    @staticmethod
    def _parse_sse(text: str) -> dict | None:
        for line in text.splitlines():
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    return None
        return None

    def start(self) -> bool:
        """ inicializa la sesion MCP y lista las tools."""
        try:
            init = self._post({
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "voice-webui", "version": "2.0.0"},
                },
            })
            if not init or "result" not in init:
                return False
            tools = self._post({
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/list",
                "params": {},
            })
            if tools and "result" in tools and "tools" in tools["result"]:
                self.tools = tools["result"]["tools"]
            # Cachear recursos al iniciar (no se consultan en cada request)
            self._resources_cache = self._fetch_resources()
            self.running = True
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_arguments(tool_name: str, arguments: dict) -> dict:
        """Normaliza argumentos de herramientas para compatibilidad con servidores MCP.

        Algunos servidores MCP esperan tipos que el modelo LLM a veces envia
        incorrectamente. Esta funcion corrige discrepancias comunes:

        - list_computers: 'attributes' como string 'a,b,c' -> lista ['a','b','c']
          (bug 2026-09-17: el modelo pasa attributes como string y el servidor
          rechaza con 'Input should be a valid list', devolviendo null que
          corta el loop sin respuesta).
        """
        if not isinstance(arguments, dict):
            return arguments
        # list_computers: attributes como string "a,b,c" -> ["a", "b", "c"]
        if tool_name == "list_computers":
            attrs = arguments.get("attributes")
            if isinstance(attrs, str) and attrs.strip():
                arguments = dict(arguments)  # Copia para no mutar original
                arguments["attributes"] = [
                    a.strip() for a in attrs.split(",") if a.strip()
                ]
        return arguments

    def call_tool(self, tool_name: str, arguments: dict, timeout: int = 30) -> dict | None:
        """Ejecuta una tool via MCP HTTP.

        Reintenta hasta 2 veces en caso de errores de red/transitorio,
        ya que el servidor MCP puede perder la conexion con servicios
        externos (LDAP, APIs remotas) sin previo aviso.

        Devuelve el resultado de la tool, o un dict con 'error' si el servidor
        responde con error JSON-RPC (para que el modelo reciba feedback util).
        """
        max_retries = 2
        # Normalizar argumentos antes de enviar
        arguments = self._normalize_arguments(tool_name, arguments)
        for attempt in range(max_retries + 1):
            try:
                old_timeout = self.timeout
                self.timeout = timeout
                result = self._post({
                    "jsonrpc": "2.0",
                    "id": self._next_request_id(),
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                })
                self.timeout = old_timeout
                if result and "result" in result:
                    return result["result"]
                # Servidor devolvio error JSON-RPC (p.ej. invalid arguments):
                # devolver el error para que el modelo reciba feedback util en
                # vez de null (bug 2026-09-17: null cortaba el loop sin respuesta).
                if result and "error" in result:
                    return {"error": result["error"].get("message", str(result["error"]))}
            except Exception:
                if attempt < max_retries:
                    time.sleep(0.5)  # Breve pausa antes de reintentar
                    continue
            return None

    def stop(self):
        self.running = False
        self.tools = []
        self.session_id = None
        self._resources_cache = None

    def _fetch_resources(self) -> list[dict] | None:
        """Consulta resources/list al servidor MCP y devuelve la lista."""
        try:
            result = self._post({
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "resources/list",
                "params": {},
            })
            if result and "result" in result and "resources" in result["result"]:
                return result["result"]["resources"]
            return []
        except Exception:
            return None

    def list_resources(self) -> list[dict] | None:
        """Devuelve los recursos cacheados al iniciar.

        NO hace peticion HTTP a menos que el cache este vacio (ej. reinicio).
        Los recursos no cambian entre requests de chat.
        """
        if self._resources_cache is not None:
            return self._resources_cache
        # Fallback: si el cache no se pobló (inicio antiguo), consultar ahora
        self._resources_cache = self._fetch_resources()
        return self._resources_cache

    def read_resource(self, uri: str) -> dict | None:
        """Leer un recurso MCP especifico (equivalente a resources/read)."""
        try:
            result = self._post({
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "resources/read",
                "params": {"uri": uri},
            })
            if result and "result" in result and "contents" in result["result"]:
                return result["result"]["contents"]
            return None
        except Exception:
            return None

    def _live_health_check(self) -> bool:
        """Verifica en vivo si el servidor MCP remoto responde.

        Envia un tools/list ligero y comprueba que devuelve resultado.
        No cachea: siempre hace peticion HTTP real.
        """
        try:
            tools = self._post({
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/list",
                "params": {},
            })
            if tools and "result" in tools and "tools" in tools["result"]:
                return True
            return False
        except Exception:
            return False
