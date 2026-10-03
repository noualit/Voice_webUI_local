# AGENTS.md - Voice-WebUI

## Proyecto

App Flask de chat por voz con herramientas MCP. Whisper (STT) + Qwen 3.6-35B-A3B (llama.cpp) + MCP tools (stdio). Frontend Alpine.js sin build step, conversaciones persistentes via llama-memory.

## Arquitectura

Todo el codigo de la app vive en `src/` (layout estandar de proyecto Python):

- `src/app.py` - Backend Flask (API REST + streaming SSE)
- `src/config.py` - Configuracion con defaults + persistencia JSON
- `src/llm_client.py` - Cliente LLM con streaming (llama.cpp OpenAI-compatible)
- `src/llm_prompt.py` - Construccion de prompts, estimacion de tokens, formateo
- `src/llm_tool_parsing.py` - Parsing de tool calls (XML, peg-native, BPE)
- `src/memory_client.py` - Cliente HTTP de llama-memory (conversaciones)
- `src/mcp_clients.py` - Clientes MCP (MCPClient stdio + MCPHTTPClient HTTP)
- `src/mcp_manager.py` - Manejo de servidores MCP (add/remove/restart/execute)
- `static/` - Frontend Alpine.js (index.html, style.css, app.js)
- `tests/` - Suite de tests pytest (TDD)
- `LOCAL_RULES.md` - Reglas del proyecto (leer antes de cualquier cambio)

## Configuracion

- MCP config: `mcp-config.json` (relativo al proyecto, configurable via `MCP_CONFIG_PATH`)
- Config app: `config.json` (relativo al proyecto, configurable via `CONFIG_PATH`)
- Todo configurable via variables de entorno (ver `.env.example`)
- Puerto Flask: `5000` (configurable via `PORT`)

## Ejecucion

```powershell
pip install -r requirements.txt
python src/app.py
```

Los tests insertan `src/` en `sys.path` para importar los modulos.

## Dependencias criticas

- `flask` - Web framework
- `whisper` - Speech-to-text (OpenAI)
- `requests` - HTTP client para llama.cpp y llama-memory
- `subprocess` - MCP clients (stdio)
- `python-dotenv` - Carga de variables de entorno

## Convenciones

- Windows 11 + PowerShell 5.1 siempre
- Python 3.11 (Miniconda)
- Sin emojis en prints/logs
- TDD obligatorio (ver LOCAL_RULES.md)
- Archivos < 600 lineas (codigo, no docs)

## Gotchas

- Los globals de `app.py` (config, llm_client, memory_client, mcp_manager, whisper_model) se inicializan en `_init_globals()`; los tests los reasignan con mocks directamente
- `AppConfig` lee `CONFIG_PATH` del env var en cada instancia (no como constante de modulo) para que los tests con tempdirs funcionen
- Las rutas de `static/`, `config.json` y `mcp-config.json` se resuelven contra `PROJECT_ROOT` (= raiz del repo, padre de `src/`), no contra el cwd, para que la app funcione se ejecute desde donde se ejecute
- El frontend usa Alpine.js desde CDN (sin build step)
- `atexit` handler cierra MCP servers limpiamente
- Para publicar en GitHub: no hay rutas absolutas ni IPs locales en codigo; todo via env vars con defaults
- **`.env`: `ADMIN_USER` va con comillas SIMPLES.** python-dotenv interpreta escapes dentro de comillas dobles: `"DOMINIO\administrador"` convierte `\a` en BEL (0x07) y el usuario real queda corrupto, con lo que WinRM responde "Acesso negado" y no se ve la causa. Cubierto por `test_env_de_usuario_debe_usar_comillas_simples`
- `run_powershell_admin` solo acepta hosts de `ADMIN_ALLOWED_HOSTS` (default: `localhost`). El LLM maneja esas credenciales, y sin allowlist podria usarlas contra cualquier host de la red
- `admin_runner` construye la credencial con `[System.Security.SecureString]` y no con `ConvertTo-SecureString`: ese cmdlet vive en `Microsoft.PowerShell.Security`, que no carga cuando el proceso se lanza con un `PSModulePath` heredado raro (servicio NSSM, env conda)
- El env de Python con las deps del proyecto es `conda env llama` (`C:\ProgramData\miniconda3\envs\llama\python.exe`). El `base` no tiene `flask` ni `whisper`
- El script del usuario de `run_powershell_admin` se escribe en un archivo aparte y se ejecuta con `Invoke-Command -FilePath`. No se interpola dentro de `ScriptBlock { }` porque una llave del script lo parte
- **Nunca invocar `powershell` con `-EncodedCommand` para ejecutar scripts del LLM.** En ese modo PowerShell trata la entrada como protocolo Automation CLIXML, no como texto del usuario, y serializa a stderr el stream de error y el de progreso como XML con escapes `_x000D__x000A_`. El LLM recibe un bloque ilegible y, en el caso de los errores, no puede saber qué fallo. Hay que escribir el script en un `.ps1` temporal (UTF-8 **con BOM**, si no PS 5.1 lo lee como ANSI) y lanzarlo con `-File`. Con `-File` ambos streams llegan en texto plano y el progreso no genera ruido
- `.ps1` temporal siempre con `-ExecutionPolicy Bypass`; sin él la política de ejecución local bloquea el script
- El `.ps1` temporal se borra en `finally` y su ruta se sustituye por `<script>` en stdout/stderr: PowerShell la incluye en cada mensaje de error y filtraría el nombre de usuario del equipo
- Los helpers de temporales viven en `mcp_servers/powershell/ps_files.py`, no duplicados en cada modulo: `server.py` no puede depender de `admin_runner` para eso (importa `dotenv`, que no está garantizado en el entorno del servicio). `make_result()` es el unico sitio donde se construye el contrato de retorno de las tools de ejecucion
- **`has_error` no es `exit_code != 0`.** Un cmdlet con error no terminante (`Get-Item` de una ruta inexistente) deja `exit_code` en 0 con stderr lleno, y el modelo leía ese 0 como éxito y afirmaba al usuario que la orden se había hecho sin mirar stderr (2026-09-26). `has_error = bool(stderr.strip())`. Cubierto por `test_run_powershell_has_error_true_aunque_el_exit_code_sea_cero`; el aviso al modelo vive en `format_tools_prompt` y en `mcp_prompts.powershell` de `config.json`
- **Ni CIM ni `Get-CimInstance` dan el tamaño de una carpeta.** No prometerlo en el docstring de una tool: el docstring se inyecta como descripción en el prompt, y ahí una afirmación falsa hace que el modelo espere métricas que la tool no da. Lo medido el 2026-09-26: `Get-ChildItem -Recurse` tarda 43 s en `C:\Users` (122 GB) y el recorrido con stack de `System.IO.DirectoryInfo` 28 s, con el **mismo total al byte**. Cero benefit en inventarse CIM
- `run_disk_analysis` salta los reparse points (junction/symlink) porque el directorio `Temp\pytest-of-<usuario>\pytest-current` de pytest apunta a su propio padre. Un recorrido que lo siga repite el subárbol: medido, 343 s y 221 GB (casi el doble) en vez de 28 s
- **No meter un array en un método .NET que toma `object` dentro de un script PowerShell 5.1**: `$q.Enqueue(@($path, $level))` lo desenvuelve y guarda solo el primer elemento, así que el nivel se pierde siempre. Por eso `Get-LevelDirs` usa **dos colas en paralelo** (ruta y nivel); ni `[object]@()` ni una tupla lo arreglan
- `run_disk_analysis` acepta letra (`"D"`) o ruta absoluta (`"D:\models"`), no solo letra: antes hacía `$driveRoot = "{entrada}:\"` con cualquier input, así que una ruta absoluta no encontraba nada. `max_depth` **acota las carpetas listadas** y se valida (`>= 1`) en Python; el tamaño de cada carpeta listada cubre su subárbol entero
- **`config.json` gana sobre `.env` en `AppConfig`**, porque lo escribe la UI: si el env var ganara, cambiar la URL desde la interfaz sería imposible. Pero el silencio costaba una sesión entera de diagnóstico (2026-09-26: `.env` decía `localhost:8080` y `config.json` la IP del servidor de inferencia, sin forma de saber cuál mandaba). Ahora `_avisar_conflicto_env()` lo dice por consola. Editar `LLAMA_URL` en `.env` no hace nada mientras exista `config.json`
- Los scripts PowerShell largos viven como constantes de módulo con marcadores `__PLACEHOLDER__`, no en f-strings: un f-string obliga a doblar cada llave (`{{`) y hace el código inrevisable. Los valores se escapan duplicando `'`
- `run_python_impl` no fija `encoding`: hoy el locale del servicio es cp65001 (UTF-8) y funciona, pero es una dependencia implícita del entorno
- **Una app corre como servicio NSSM en la sesion 0, y ahi no hay escritorio.** Medido desde SYSTEM: `Start-Process calc` devuelve exit_code 0, sin stderr y **no abre ningun proceso**; `C:\WINDOWS\system32\calc.exe` tambien, y `shell:AppsFolder\...Calculator...` falla porque la calculadora es una app Store por usuario y el perfil de SYSTEM no la tiene. Por eso existe `run_desktop_app` y por eso el prompt manda no usar `Start-Process` para nada visible. Ningun `exit_code 0` demuestra que algo **visible** ocurrio
- `run_desktop_app` va por el Task Scheduler con `New-ScheduledTaskPrincipal -LogonType Interactive`: la tarea hereda el token del usuario y corre en su sesion (1), mientras que el servicio sigue en la 0. Descubre el usuario del `explorer.exe` de una sesion `!= 0` con `GetOwner`, no hardcodeado, y devuelve error si no hay ninguna sesion activa
- **El exito de `run_desktop_app` es un proceso NUEVO en la sesion del usuario, no el codigo de salida.** Compara PIDs antes/despues, no nombres, porque el shim `calc.exe` no es el proceso final (ese es `CalculatorApp`). La lista puede traer procesos ajenos que arrancó de paso (el navegador), asi que va un `resumen` con el recuento: el exito es que haya **al menos** uno
- No incluir `MainWindowHandle` en esa salida: desde SYSTEM devuelve 0 para procesos de otro usuario, asi que el campo seria siempre `False` y el modelo lo leeria como "no se abrio ventana". Es informacion falsa
- `New-ScheduledTaskAction -Argument ''` falla con `ParameterArgumentValidationError`: el parametro no acepta cadena vacia, hay que pasarlo solo si hay argumentos
- Las tools de MCP traen `inputSchema`, no `parameters`. Para llamar a llama.cpp hay que pasar por `MCPToolManager.get_openai_tools()`, que es quien traduce; mandar el `inputSchema` crudo hace que el chat template renderice un `<tool_call>` vacio y el servidor devuelva `500 Failed to parse input`

## Honestidad

- NUNCA simular ejecucion de comandos en equipos remotos.
- Si no hay infraestructura real (SSH, WinRM, agente), decirlo explicitamente.
- Ver LOCAL_RULES.md para reglas completas de anti-patterns.
