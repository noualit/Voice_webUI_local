# Voice-WebUI

Aplicacion Flask de chat por voz con herramientas MCP. Web UI moderna (Alpine.js) con chat por texto y voz, streaming de respuestas en tiempo real (SSE), gestion de conversaciones persistentes via `llama-memory`, y configuracion de servidores MCP desde la interfaz.

## Caracteristicas

- **Chat por voz**: grabacion con el microfono del navegador y transcripcion con Whisper (STT).
- **Chat por texto**: conversacion con LLM local (Qwen via llama.cpp).
- **Streaming en tiempo real**: respuestas por SSE, token a token.
- **Herramientas MCP**: ejecuta herramientas de servidores MCP (stdio o HTTP/SSE) desde la conversacion.
- **Conversaciones persistentes**: historial guardado en `llama-memory` (PostgreSQL + PGVector).
- **Configuracion desde la UI**: system prompt global, URL del LLM, modelo, temperatura y servidores MCP (agregar/eliminar).
- **Tema oscuro**: interfaz limpia y responsive.

## Arquitectura

```
Voice-WebUI (Flask + Alpine.js)
    |
    |---> llama.cpp (LLM local, OpenAI-compatible API)
    |        http://<host>:8080/v1/chat/completions
    |
    |---> llama-memory (servicio de memoria/conversaciones, FastAPI :9001)
    |        PostgreSQL + PGVector
    |
    |---> MCP servers (stdio y HTTP/SSE)
             herramientas ejecutables desde el chat
```

El codigo de la aplicacion vive en `src/`:

```
src/
├── app.py           # Backend Flask (API REST + streaming SSE)
├── config.py        # Configuracion + persistencia JSON
├── conversations.py # Blueprint de conversaciones (listado, metas, rename)
├── llm_client.py    # Cliente LLM (llama.cpp, OpenAI-compatible)
├── memory_client.py # Cliente llama-memory (conversaciones)
├── memory_tools.py  # Tools de memoria expuestas al LLM (create/save/search)
└── mcp_manager.py   # Gestion de servidores MCP (stdio y HTTP/SSE)
```

## Requisitos

- Python 3.11
- Flask, whisper, requests, python-dotenv (ver `requirements.txt`)
- Servicio `llama-memory` corriendo (para conversaciones persistentes)
- LLM local via llama.cpp (endpoint OpenAI-compatible)

## Instalacion

```powershell
pip install -r requirements.txt
```

## Configuracion

La configuracion se hace mediante variables de entorno (archivo `.env`) y/o el archivo `config.json` que se genera al primer arranque.

Copia el ejemplo:

```powershell
Copy-Item .env.example .env
```

Variables principales:

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `LLAMA_URL` | `http://localhost:8080/v1/chat/completions` | Endpoint OpenAI-compatible del LLM |
| `LLAMA_MODEL` | `qwen3.6-35b` | Nombre del modelo |
| `LLAMA_TEMPERATURE` | `0.6` | Temperatura de muestreo |
| `WHISPER_MODEL` | `small` | Modelo Whisper para STT |
| `MEMORY_URL` | `http://localhost:9001` | URL del servicio llama-memory |
| `MEMORY_USER` | `default` | Usuario de memoria |
| `MCP_CONFIG_PATH` | `./mcp-config.json` | Ruta del archivo de configuracion MCP |
| `CONFIG_PATH` | `config.json` | Ruta del archivo de configuracion de la app |
| `FLASK_SECRET_KEY` | `dev-local-secret` | Clave secreta de Flask (cambiar en produccion) |
| `PORT` | `5000` | Puerto del servidor Flask |

## Ejecucion

```powershell
python src/app.py
```

Luego abre `http://localhost:5000`.

## Uso

1. **Nueva conversacion**: boton "+ Nueva" en la barra lateral.
2. **Enviar texto**: escribe y presiona Enter o el boton "Enviar".
3. **Enviar voz**: mantiene presionado el boton "Hablar" y suelta al terminar. El audio se transcribe y envia.
4. **Configuracion**: boton "Configuracion" en la barra lateral inferior para ajustar system prompt, LLM y servidores MCP.

## Servidores MCP

Los servidores MCP se definen en `mcp-config.json` (formato standard). Puedes agregarlos desde la UI con nombre, comando y argumentos, o editando el archivo directamente.

Soporta dos transportes:
- **stdio**: el servidor se lanza como subproceso local (`command` + `args`).
- **http/sse**: servidor remoto, se configura con `"transport": "http"` y `"url"` (tambien desde la UI con el boton de servidor HTTP).

```json
{
  "mcpServers": {
    "mi-servidor": {
      "command": "python",
      "args": ["-m", "mi_servidor"],
      "env": {}
    }
  }
}
```

## Tests

```powershell
python -m pytest tests/ -v
```

## Licencia

MIT - ver [LICENSE](LICENSE).
