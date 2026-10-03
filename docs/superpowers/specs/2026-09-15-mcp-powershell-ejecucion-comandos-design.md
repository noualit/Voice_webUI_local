# Diseno: MCP local "powershell" para ejecucion de comandos

Fecha: 2026-09-15
Estado: aprobado por el usuario (pendiente de revision del spec)

## Contexto y problema

El usuario pidio al modelo listar los equipos online en el rango
192.0.2.130-253 "usando tools". Reproduccion contra el modelo real
(qwen3.6-35b, 66 tools disponibles) demostro que el modelo INTENTA usar
tools pero no puede:

1. No existe ninguna tool de escaneo de red -> el modelo improvisa con
   `write_file` + `run_command` (escribe un script de ping sweep en
   `/tmp/ping_sweep.py` y lo ejecuta con `python3`).
2. El entorno de ejecucion es Windows 11, pero el modelo asume Linux
   (`python3`, `/tmp/`) -> `run_command` devuelve `Exit code: 9009`
   (comando no encontrado en Windows).
3. El modelo no entiende el fallo (9009 no es autoexplicativo) y entra en
   un loop de reintentos con variaciones inutiles, sin generar nunca la
   respuesta final.

Causa raiz: el modelo no tiene una via FIABLE de ejecutar comandos en el
entorno real del usuario, y el prompt no le informa del entorno (Windows,
PowerShell, python del env llama).

Decision de arquitectura del usuario: NO crear una tool por tarea (p.ej.
`scan_network`). Dar al modelo una CAPACIDAD GENERICA de ejecucion
(PowerShell/Python) y que resuelva las tareas el mismo.

## Decisiones aprobadas

- Crear un server MCP local (FastMCP) "powershell" que ejecuta comandos
  en el equipo del usuario (donde corre la app Voice-WebUI).
- Tools del MCP:
  - `run_powershell(script: str, timeout: int = 60)`
  - `run_python(script: str, timeout: int = 60)` (python del env llama)
- Ambas devuelven `{"stdout", "stderr", "exit_code", "elapsed_ms"}`.
- Aprobacion de ejecucion UNA VEZ POR CONVERSACION (primera llamada ->
  modal; aprobada -> el resto de la conversacion sin preguntar). Aplica
  tambien a `run_command` de dia (misma politica).
- El prompt educa al modelo sobre el entorno: Windows 11, PowerShell 5.1,
  python del env llama, nunca `python3` ni rutas `/tmp/`.

## Arquitectura

### Componente 1: Server MCP "powershell"

- Ubicacion: `mcp_servers/powershell/server.py` (dentro del repo,
  autocontenido, versionable; patron FastMCP).
- `run_powershell(script, timeout=60)`:
  - Ejecuta `powershell -NoProfile -NonInteractive` con el script via
    `-EncodedCommand` (UTF-16LE) para soportar scripts largos y caracteres
    especiales sin problemas de escaping.
  - Captura stdout, stderr y exit code con timeout (mata el proceso si se
    excede).
- `run_python(script, timeout=60)`:
  - Ejecuta `C:\ProgramData\miniconda3\envs\llama\python.exe -c script`
    (o via stdin para scripts largos).
  - Misma estructura de salida.
- Descripcion de la tool (la ve el modelo en el prompt):
  "Ejecuta comandos en el equipo local del usuario — Windows 11,
  PowerShell 5.1. Para escanear equipos online usa Test-Connection en
  paralelo. NUNCA uses python3 ni rutas /tmp/ (entorno Windows). Si un
  comando falla, lee stderr y corrige."

### Componente 2: Integracion en la app

- Registro en `mcp-config.json`:
  `{"powershell": {"command": "C:\\ProgramData\\miniconda3\\envs\\llama\\python.exe", "args": ["mcp_servers/powershell/server.py"], "transport": "stdio"}}`.
  La app lo arranca al boot (start_all) como a filesystem/git; aparece
  como servidor "powershell" en la UI con sus tools.
- Aprobacion una vez por conversacion (cambio en `src/app.py`):
  - Estado nuevo: set de pares `(conversation_id, tool_name)` aprobados.
  - En el loop de tool_calls, cuando la tool esta en
    `APPROVAL_REQUIRED_TOOLS` (ahora: `run_command`, `run_powershell`,
    `run_python`):
    - Si `(conversation_id, tool_name)` ya esta aprobado -> ejecutar
      directamente.
    - Si no -> modal de aprobacion (mecanismo existente
      `_approval_events`/`_approval_results`); si se aprueba, registrar el
      par aprobado.
  - El set se limpia al cambiar de conversacion (o se guarda por
    conversation_id; la conversacion nueva empieza sin aprobaciones).
- Prompt de entorno: bloque de instrucciones para el servidor "powershell"
  en `mcp_prompts` de `config.json` (format_tools_prompt ya agrupa por
  servidor y usa el prompt especifico si existe).

### Componente 3: Manejo de errores

- El MCP devuelve `exit_code` + `stderr` claros; el modelo puede leer y
  corregir (ya no hay "Exit code: 9009" sin contexto).
- No hay cambios en el parser XML: los tool calls del MCP siguen el flujo
  estandar (prefijo de servidor `powershell.run_powershell` resuelto por
  `_resolve_tool_name`).

## Seguridad

- Ejecucion arbitraria de comandos: solo tras aprobacion explicita del
  usuario (una vez por conversacion).
- Timeout por comando (default 60s) evita comandos colgados.
- El MCP corre en el equipo del usuario (misma maquina que la app), con
  los permisos del usuario que ejecuta la app.

## Testing (TDD)

1. Unit del server MCP:
   - `run_powershell("Write-Output 'hola'")` -> stdout "hola", exit 0.
   - `run_powershell("exit 3")` -> exit_code 3.
   - Comando inexistente -> exit_code != 0 y stderr no vacio (Windows).
   - `run_python("print(1+1)")` -> stdout "2".
   - Timeout: comando `Start-Sleep -Seconds 10` con timeout 2 -> el
     proceso se mata y devuelve error de timeout.
2. Integracion:
   - `mcp-config.json` incluye el servidor "powershell" (test de config).
   - Aprobacion por conversacion: primera llamada -> approval_request;
     segunda llamada misma conversacion -> sin modal (mock de
     `_approval_events` y del ejecutor).
   - El despacho ejecuta `run_powershell` via `mcp_manager.execute_tool`.
3. E2E real (manual, no en pytest): el modelo escanea
   `192.0.2.130-253`, el usuario aprueba una vez, recibe la lista de
   equipos online con nombre de host.

## Criterios de exito

- El modelo, ante "equipos online en el rango X-Y", genera
  `powershell.run_powershell` con un comando Windows correcto
  (Test-Connection o ping) y responde la lista sin loop infinito.
- El modal de aprobacion aparece UNA vez por conversacion.
- Suite pytest completa en verde.
- Ningun archivo supera las 600 lineas (mcp_servers/powershell/server.py
  es nuevo y pequeno; app.py crece moderadamente con el cambio de
  aprobacion).

## Fuera de alcance (YAGNI)

- Deteccion de puertos TCP en el escaneo (se pidio solo ping + hostname;
  el modelo puede hacerlo con run_powershell si el usuario lo pide).
- Interfaz de gestion de aprobaciones en la UI (solo el modal existente).
- Sandboxing/aislamiento del MCP (los comandos corren con los permisos del
  usuario de la app).
