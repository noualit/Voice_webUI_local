# MCP Powershell Ejecucion de Comandos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar al modelo una capacidad generica de ejecucion de comandos (PowerShell/Python) en el equipo local del usuario via un server MCP "powershell", con aprobacion humana una vez por conversacion.

**Architecture:** Server MCP FastMCP local (`mcp_servers/powershell/server.py`) con tools `run_powershell`/`run_python`, registrado en `mcp-config.json` (stdio, python del env llama). En `src/app.py`, la aprobacion de tools peligrosas pasa de "cada llamada" a "una vez por conversacion" (dict `conversation_id -> set[tools]`). El prompt de entorno se anade en `format_tools_prompt` para el servidor "powershell" (versionable, sin depender de config.json gitignored).

**Tech Stack:** Python 3.11 (env conda llama), FastMCP (SDK `mcp`), PowerShell 5.1, subprocess, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-mcp-powershell-ejecucion-comandos-design.md`

---

## File Structure

- **Create** `mcp_servers/powershell/server.py` — server MCP FastMCP con `run_powershell`/`run_python` (impl puras testables + tools MCP).
- **Create** `tests/test_powershell_mcp.py` — unit tests del server (echo, exit code, timeout, python).
- **Modify** `src/app.py` — `APPROVAL_REQUIRED_TOOLS` + estado `_approved_tools_per_conv` + logica en el loop de tool_calls.
- **Modify** `tests/conftest.py` — limpieza del nuevo estado en el fixture; `all_tools` incluye `run_powershell`.
- **Modify** `tests/test_app.py` — test de aprobacion una vez por conversacion.
- **Modify** `src/llm_client.py` — bloque de instrucciones de entorno para server "powershell" en `format_tools_prompt`.
- **Modify** `tests/test_mcp_prompts.py` — test del prompt de entorno powershell.
- **Modify** `mcp-config.json` — registro del server "powershell".

---

### Task 1: Server MCP powershell (TDD)

**Files:**
- Create: `mcp_servers/powershell/server.py`
- Create: `tests/test_powershell_mcp.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_powershell_mcp.py`:

```python
# tests/test_powershell_mcp.py
# Unit tests del server MCP local "powershell" (mcp_servers/powershell/server.py).
# Se testean las impl puras (run_powershell_impl / run_python_impl) sin levantar
# el transporte MCP. Comandos reales triviales: echo, exit codes, timeout.
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "mcp_servers" / "powershell")
)

import server


def test_run_powershell_echo():
    result = server.run_powershell_impl("Write-Output 'hola'", timeout=30)
    assert result["exit_code"] == 0
    assert "hola" in result["stdout"]


def test_run_powershell_exit_code():
    result = server.run_powershell_impl("exit 3", timeout=30)
    assert result["exit_code"] == 3


def test_run_python_echo():
    result = server.run_python_impl("print(1+1)", timeout=30)
    assert result["exit_code"] == 0
    assert "2" in result["stdout"]


def test_run_powershell_timeout_mata_proceso():
    result = server.run_powershell_impl("Start-Sleep -Seconds 10", timeout=2)
    assert result["exit_code"] == -1
    assert "Timeout" in result["stderr"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_powershell_mcp.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Write the minimal implementation**

Create `mcp_servers/powershell/server.py`:

```python
# mcp_servers/powershell/server.py
"""Server MCP local "powershell": ejecuta scripts PowerShell/Python en el
equipo del usuario (Windows 11, PowerShell 5.1).

El modelo Qwen 3.6 asumia Linux (python3, /tmp/) y fallaba en Windows con
'Exit code: 9009' (bug 2026-09-15). Este MCP le da una via FIABLE de ejecutar
comandos: el prompt de format_tools_prompt le indica el entorno y las tools
devuelven stdout/stderr/exit_code claros para que corrija sin loops.

run_python usa sys.executable: el server se lanza con el python del env llama
(ver mcp-config.json), asi que 'python' es el entorno correcto sin rutas
hardcodeadas.
"""
import base64
import subprocess
import sys
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("powershell")


def run_powershell_impl(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script PowerShell y devuelve {stdout, stderr, exit_code,
    elapsed_ms}. -EncodedCommand (UTF-16LE) evita problemas de escaping con
    scripts largos o caracteres especiales."""
    start = time.time()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "elapsed_ms": int((time.time() - start) * 1000),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": e.stdout or "",
            "stderr": f"Timeout: el comando excedio {timeout}s y se cancelo.",
            "exit_code": -1,
            "elapsed_ms": int((time.time() - start) * 1000),
        }


def run_python_impl(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script Python (env llama, via stdin) y devuelve la misma
    estructura que run_powershell_impl."""
    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "elapsed_ms": int((time.time() - start) * 1000),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": e.stdout or "",
            "stderr": f"Timeout: el comando excedio {timeout}s y se cancelo.",
            "exit_code": -1,
            "elapsed_ms": int((time.time() - start) * 1000),
        }


@mcp.tool()
def run_powershell(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script PowerShell en el equipo local del usuario (Windows,
    PowerShell 5.1). Devuelve stdout, stderr, exit_code y elapsed_ms. Si el
    comando falla, lee stderr y corrige el script."""
    return run_powershell_impl(script, timeout)


@mcp.tool()
def run_python(script: str, timeout: int = 60) -> dict[str, Any]:
    """Ejecuta un script Python (entorno llama) en el equipo local del
    usuario. Devuelve stdout, stderr, exit_code y elapsed_ms."""
    return run_python_impl(script, timeout)


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_powershell_mcp.py -q -p no:cacheprovider`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add mcp_servers/powershell/server.py tests/test_powershell_mcp.py
git commit -m "feat: server MCP powershell para ejecucion de comandos local"
```

---

### Task 2: Aprobacion una vez por conversacion (TDD)

**Files:**
- Modify: `src/app.py:105` (APPROVAL_REQUIRED_TOOLS), `src/app.py:116` (estado nuevo), `src/app.py:555-578` (logica del modal)
- Modify: `tests/conftest.py:56-63` (all_tools), `tests/conftest.py:66-70` (cleanup)
- Modify: `tests/test_app.py` (test nuevo al final)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_chat_aprobacion_una_vez_por_conversacion(app_module):
    """La aprobacion de tools peligrosas (run_powershell) se pide UNA vez por
    conversacion: primera llamada -> modal; aprobada -> el resto de la misma
    conversacion ejecuta sin modal. Otra conversacion vuelve a pedir permiso
    (2026-09-15)."""
    import app as app_module_ref
    import re

    calls = {"n": 0}

    def _fake_chat_stream(messages, tools, known_tool_names=None):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            yield {"type": "tool_calls", "tool_calls": [
                {"id": f"call_{calls['n']}", "type": "function",
                 "function": {"name": "run_powershell",
                              "arguments": json.dumps({"script": "Get-Date"})}}
            ]}
        else:
            yield {"type": "delta", "content": f"respuesta {calls['n']}"}

    executed = []

    def _execute_tool(name, args):
        executed.append((name, args))
        return {"result": "ok"}

    old_timeout = app_module_ref.APPROVAL_TIMEOUT
    app_module_ref.APPROVAL_TIMEOUT = 2  # fail-fast

    try:
        with patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
            app_module_ref.mcp_manager.execute_tool.side_effect = _execute_tool
            tc = app_module_ref.app.test_client()

            # 1a. Primera llamada (conv-apr-1): modal de aprobacion
            resp = tc.post("/api/chat", json={"text": "hora", "conversation_id": "conv-apr-1"})
            body = b"".join(resp.response).decode("utf-8")
            assert "approval_request" in body
            m = re.search(r'"approval_id": "([^"]+)"', body)
            assert m, "debe emitir approval_id"
            tc.post("/api/chat/approve", json={"approval_id": m.group(1), "approved": True})
            assert ("run_powershell", {"script": "Get-Date"}) in executed

            # 1b. Misma conversacion (conv-apr-1): sin modal, ejecuta directo
            executed.clear()
            resp2 = tc.post("/api/chat", json={"text": "hora otra vez", "conversation_id": "conv-apr-1"})
            body2 = b"".join(resp2.response).decode("utf-8")
            assert "approval_request" not in body2
            assert ("run_powershell", {"script": "Get-Date"}) in executed

            # 1c. Conversacion distinta (conv-apr-2): modal otra vez
            executed.clear()
            resp3 = tc.post("/api/chat", json={"text": "hora", "conversation_id": "conv-apr-2"})
            body3 = b"".join(resp3.response).decode("utf-8")
            assert "approval_request" in body3
            # No se aprueba -> la tool NO se ejecuta (bloqueada por timeout)
            assert executed == []
    finally:
        app_module_ref.APPROVAL_TIMEOUT = old_timeout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py::test_chat_aprobacion_una_vez_por_conversacion -q -p no:cacheprovider`
Expected: FAIL — `run_powershell` no esta en `APPROVAL_REQUIRED_TOOLS` (no pasa por el modal, se ejecuta directo) o `_tool_exists` False (falla la defensa).

- [ ] **Step 3: Write the minimal implementation**

3a. In `src/app.py`, line 105:

```python
# Tools que requieren aprobacion explicita del usuario antes de ejecutarse.
# run_command ejecuta comandos arbitrarios en el servidor (riesgo de seguridad:
# el modelo puede alucinar comandos, bug 2026-09-07). Ampliable por el usuario.
# run_powershell/run_python (MCP local "powershell", 2026-09-15) ejecutan
# comandos en el equipo del usuario: misma politica de aprobacion.
APPROVAL_REQUIRED_TOOLS = {"run_command", "run_powershell", "run_python"}
```

3b. In `src/app.py`, after line 116 (`_approval_lock = threading.Lock()`):

```python
# Tools peligrosas aprobadas por el usuario, agrupadas por conversation_id
# (aprobacion UNA vez por conversacion, 2026-09-15): la primera llamada a una
# tool de APPROVAL_REQUIRED_TOOLS pide permiso; si se aprueba, el resto de la
# conversacion ejecuta sin volver a preguntar. Una conversacion nueva empieza
# sin aprobaciones (su id no esta en el dict).
_approved_tools_per_conv: dict[str, set[str]] = {}
_approval_conv_lock = threading.Lock()
```

3c. In `src/app.py`, replace the approval block (lines 555-578). Old:

```python
                                elif tool_name in APPROVAL_REQUIRED_TOOLS:
                                    # Tool peligrosa: pedir aprobacion explicita al
                                    # usuario ANTES de ejecutar. El evento SSE
                                    # approval_request llega al frontend, que muestra
                                    # un modal y responde via POST /api/chat/approve.
                                    # Si nadie responde en APPROVAL_TIMEOUT segundos, la
                                    # tool se bloquea (fail-safe): nunca se ejecuta sin
                                    # consentimiento (bug 2026-09-07: run_command
                                    # alucinado sin control del usuario).
                                    approval_id = f"ap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{tool_name}"
                                    with _approval_lock:
                                        _approval_events[approval_id] = threading.Event()
                                        _approval_results[approval_id] = False
                                    yield f"data: {json.dumps({'type': 'approval_request', 'approval_id': approval_id, 'tool': tool_name, 'arguments': args})}\n\n"
                                    _approval_events[approval_id].wait(timeout=APPROVAL_TIMEOUT)
                                    with _approval_lock:
                                        approved = _approval_results.pop(approval_id, False)
                                        _approval_events.pop(approval_id, None)
                                    if approved:
                                        result = mcp_manager.execute_tool(tool_name, args)
                                    else:
                                        result = {"error": f"Tool '{tool_name}' bloqueada por el usuario (no aprobada)"}
```

New:

```python
                                elif tool_name in APPROVAL_REQUIRED_TOOLS:
                                    # Tool peligrosa: pedir aprobacion explicita al
                                    # usuario ANTES de ejecutar. El evento SSE
                                    # approval_request llega al frontend, que muestra
                                    # un modal y responde via POST /api/chat/approve.
                                    # Si nadie responde en APPROVAL_TIMEOUT segundos, la
                                    # tool se bloquea (fail-safe): nunca se ejecuta sin
                                    # consentimiento (bug 2026-09-07: run_command
                                    # alucinado sin control del usuario).
                                    # Aprobacion UNA vez por conversacion (2026-09-15):
                                    # si el usuario ya aprobo esta tool en esta
                                    # conversacion, se ejecuta directamente.
                                    with _approval_conv_lock:
                                        already_approved = tool_name in _approved_tools_per_conv.get(conversation_id, set())
                                    if already_approved:
                                        result = mcp_manager.execute_tool(tool_name, args)
                                    else:
                                        approval_id = f"ap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{tool_name}"
                                        with _approval_lock:
                                            _approval_events[approval_id] = threading.Event()
                                            _approval_results[approval_id] = False
                                        yield f"data: {json.dumps({'type': 'approval_request', 'approval_id': approval_id, 'tool': tool_name, 'arguments': args})}\n\n"
                                        _approval_events[approval_id].wait(timeout=APPROVAL_TIMEOUT)
                                        with _approval_lock:
                                            approved = _approval_results.pop(approval_id, False)
                                            _approval_events.pop(approval_id, None)
                                        if approved:
                                            with _approval_conv_lock:
                                                _approved_tools_per_conv.setdefault(conversation_id, set()).add(tool_name)
                                            result = mcp_manager.execute_tool(tool_name, args)
                                        else:
                                            result = {"error": f"Tool '{tool_name}' bloqueada por el usuario (no aprobada)"}
```

3d. In `tests/conftest.py`, `all_tools` list (lines 56-63): add `{"name": "run_powershell"}`.

3e. In `tests/conftest.py`, after the `yield app_module` cleanup (line 70), add:

```python
    # Limpiar aprobaciones por conversacion entre tests (estado global nuevo)
    if hasattr(app_module, "_approved_tools_per_conv"):
        app_module._approved_tools_per_conv.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py::test_chat_aprobacion_una_vez_por_conversacion tests/test_app.py::test_chat_run_command_pide_aprobacion_y_ejecuta_al_aprobar tests/test_app.py::test_chat_run_command_bloquea_si_no_se_aprueba -q -p no:cacheprovider`
Expected: 3 passed (los tests existentes de run_command siguen pasando)

- [ ] **Step 5: Commit**

```bash
git add src/app.py tests/conftest.py tests/test_app.py
git commit -m "feat: aprobacion de tools peligrosas una vez por conversacion"
```

---

### Task 3: Registro MCP + prompt de entorno (TDD)

**Files:**
- Modify: `mcp-config.json`
- Modify: `src/llm_client.py:130-134` (bloque powershell en format_tools_prompt)
- Modify: `tests/test_mcp_prompts.py` (test nuevo)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_prompts.py`:

```python
def test_format_tools_prompt_powershell_incluye_instrucciones_entorno():
    """El bloque del servidor "powershell" debe educar al modelo sobre el
    entorno (Windows/PowerShell) para que no genere comandos Linux (bug
    2026-09-15: python3 + /tmp -> exit 9009 -> loop sin respuesta)."""
    from llm_client import format_tools_prompt

    tools = [{
        "type": "function",
        "function": {"name": "run_powershell", "description": "Ejecuta un script PowerShell", "parameters": {}},
        "_mcp_server": "powershell",
    }]
    prompt = format_tools_prompt(tools, {})
    assert "## powershell" in prompt
    assert "Windows 11" in prompt
    assert "PowerShell 5.1" in prompt
    assert "Test-Connection" in prompt
    assert "python3" in prompt
    assert "/tmp/" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_prompts.py::test_format_tools_prompt_powershell_incluye_instrucciones_entorno -q -p no:cacheprovider`
Expected: FAIL — el bloque powershell no contiene las instrucciones.

- [ ] **Step 3: Write the minimal implementation**

In `src/llm_client.py`, inside the `for server, server_tools in sorted(grouped.items()):` loop, after the AD special block (line 132):

```python
            # Instrucciones de entorno para el MCP local "powershell"
            # (2026-09-15): el modelo asumia Linux (python3, /tmp/) y fallaba
            # en Windows con 'Exit code: 9009', entrando en loops sin respuesta.
            if server == "powershell":
                block += (
                    "\n  IMPORTANT: Ejecutas en el equipo local del usuario — "
                    "Windows 11, PowerShell 5.1. Para escanear equipos online "
                    "usa Test-Connection o 'ping -n 1 -w 400' en paralelo. "
                    "NUNCA uses 'python3' ni rutas '/tmp/' (entorno Windows). "
                    "Si un comando falla, lee stderr y corrige."
                )
```

Also modify `mcp-config.json` (add the server entry):

```json
    "powershell": {
      "command": "C:\\ProgramData\\miniconda3\\envs\\llama\\python.exe",
      "args": [
        "D:/python/Voice-WebUI/mcp_servers/powershell/server.py"
      ]
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_prompts.py tests/test_config.py -q -p no:cacheprovider`
Expected: all pass (incl. `test_system_prompt_config_sin_afirmaciones_obsoletas`: el system_prompt no cambia, solo el bloque del server powershell)

- [ ] **Step 5: Commit**

```bash
git add src/llm_client.py tests/test_mcp_prompts.py mcp-config.json
git commit -m "feat: registra MCP powershell y educa al modelo sobre el entorno Windows"
```

---

### Task 4: Verificacion E2E real (manual)

**Files:** ninguno (verificacion)

- [ ] **Step 1: Reiniciar la app con el codigo nuevo**

```bash
# Matar la app actual (PID que escuche en 5000) y relanzar
$listen = Get-NetTCPConnection -LocalPort 5000 -State Listen
Stop-Process -Id $listen.OwningProcess -Force
Start-Sleep -Seconds 2
Start-Process -FilePath "C:\ProgramData\miniconda3\envs\llama\python.exe" -ArgumentList "D:\python\Voice-WebUI\src\app.py" -WorkingDirectory "D:\python\Voice-WebUI" -RedirectStandardOutput "D:\python\Voice-WebUI\app.out.log" -RedirectStandardError "D:\python\Voice-WebUI\app.err.log" -WindowStyle Hidden
Start-Sleep -Seconds 8
Invoke-WebRequest -Uri "http://127.0.0.1:5000/" -UseBasicParsing | Select-Object StatusCode
```

Expected: HTTP 200; `/api/mcp-status` incluye `powershell` con 2 tools.

- [ ] **Step 2: Probar el MCP directo (sin modelo)**

Run: `python -c "import sys; sys.path.insert(0, 'mcp_servers/powershell'); import server; print(server.run_powershell_impl('Write-Output \"hola\"'))"`
Expected: stdout contiene "hola", exit_code 0.

- [ ] **Step 3: Flujo real con el modelo (script de reproduccion)**

Reutilizar el patron del script `repro_scan.py` (temp), peticion: "Lista los equipos online en el rango 192.0.2.130 a 192.0.2.253 usando herramientas." Verificar que el modelo genera `powershell.run_powershell` (o `run_python`) con un comando Windows correcto (Test-Connection/ping) y responde con la lista, SIN loop infinito ni comandos Linux.

- [ ] **Step 4: Flujo E2E con Playwright**

Login -> enviar la peticion de escaneo -> el modal "Permiso requerido" aparece UNA vez -> aprobar -> respuesta con la lista de equipos online. Segunda pregunta que requiera run_powershell en la misma conversacion -> SIN modal.

- [ ] **Step 5: Suite completa**

Run: `python -m pytest tests -q -p no:cacheprovider`
Expected: todos en verde (235 previos + 4 del server + 1 aprobacion + 1 prompt = 241).

---

## Self-Review

**Spec coverage:**
- MCP local FastMCP con run_powershell/run_python -> Task 1.
- Aprobacion una vez por conversacion (aplica a run_command) -> Task 2.
- Prompt de entorno Windows -> Task 3 (format_tools_prompt, versionable).
- Registro en mcp-config.json -> Task 3.
- Testing (unit, integracion, E2E real) -> Tasks 1, 2, 3, 4.
- Fuera de alcance (puertos TCP, UI aprobaciones, sandbox) -> respetado (no se tocan).

**Placeholder scan:** sin TBD/TODO; todos los pasos tienen codigo completo.

**Type consistency:** `run_powershell_impl`/`run_python_impl` consistentes en server.py y tests; `_approved_tools_per_conv` dict[str, set[str]] consistente en app.py, conftest y tests; `APPROVAL_REQUIRED_TOOLS` con los 3 nombres.