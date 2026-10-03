# Voice-WebUI v2: Chat por Voz + MCP + Memoria

Fecha: 2026-08-31

## Objetivo

Reemplazar la Web-UI actual (single-file Flask con HTML inline) por una arquitectura
backend/frontend separada que ofrezca chat por voz con streaming, configuracion de
MCPs desde la UI, system prompt global configurable, y lista de conversaciones
persistente via `llama-memory`.

## Contexto

- El proyecto actual `voice2llama_mcp.py` es un archivo unico de 591 lineas con
  Flask + Whisper + MCP clients + HTML inline. Funciona pero no escala.
- `llama-memory` (`D:\python\llama-memory`) ya resuelve almacenamiento persistente
  de conversaciones en PostgreSQL + PGVector, con memoria semantica y MCP tools.
- El LLM local es Qwen 3.6-27B via llama.cpp en `http://192.0.2.92:8080`.
- El modelo no tiene reconocimiento de voz; Whisper (modelo `small`) lo provee.

## Decisiones de arquitectura

| Decision | Opcion elegida | Razon |
|----------|---------------|-------|
| Frontend | Alpine.js + HTML/CSS estatico | Reactividad sin build step, suficiente para esta UI |
| Backend | Flask (API + servir static) | Ya en el proyecto, simple |
| Streaming | SSE | Estandar para chat, simple con Flask |
| Almacenamiento | llama-memory vía HTTP (puerto 9001) | Servicios separados, reutiliza lo existente |
| MCP config | Editor basico (agregar/eliminar) | Suficiente para uso personal |
| System prompt | Global unico | Simple, configurable desde UI |
| Conversaciones | Via llama-memory API | Sin duplicar almacenamiento |

## Arquitectura

```
+----------------+     HTTP     +------------------+     HTTP     +------------------+
|   Browser      | <---------> |  Voice-WebUI     | <--------->  |  llama.cpp        |
|   (Alpine.js)  |   JSON/SSE  |  (Flask + Whisper)|             |  (Qwen 3.6-27B)   |
+----------------+             +------------------+             +------------------+
                                    |        ^
                                    |        | HTTP (MCP tools)
                                    v        |
                              +------------------+     HTTP     +------------------+
                              |  llama-memory    | <--------->  |  PostgreSQL       |
                              |  (FastAPI:9001)  |              |  (PGVector)       |
                              +------------------+              +------------------+
```

## Componentes

### Backend (Voice-WebUI)

| Archivo | Responsabilidad |
|---------|----------------|
| `app.py` | Flask app: rutas API, servir static, Whisper |
| `mcp_manager.py` | MCP clients (reusado del actual), config JSON |
| `llm_client.py` | Cliente HTTP para llama.cpp con streaming |
| `memory_client.py` | Cliente HTTP para llama-memory |
| `config.json` | System prompt global, parametros, MCP path |

### Frontend (Alpine.js)

| Archivo | Responsabilidad |
|---------|----------------|
| `static/index.html` | Pagina principal: sidebar + chat + config |
| `static/app.js` | Logica frontend: estados, SSE, microfono |
| `static/style.css` | Estilos (tema oscuro tipo llama-server) |

### Endpoints API

| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/` | Servir `index.html` |
| GET | `/api/config` | Obtener configuracion (system prompt, params, MCPs) |
| POST | `/api/config` | Guardar configuracion |
| POST | `/api/transcribe` | Audio -> texto (Whisper) |
| POST | `/api/chat` | Enviar mensaje, responde via SSE |
| GET | `/api/conversations` | Lista conversaciones (de llama-memory) |
| GET | `/api/conversations/<id>` | Historial de una conversacion |
| POST | `/api/conversations` | Crear nueva conversacion |
| GET | `/api/mcp-status` | Estado de MCP servers |
| POST | `/api/mcp/add` | Agregar servidor MCP |
| POST | `/api/mcp/remove` | Eliminar servidor MCP |

### Flujo de chat con streaming

1. Usuario habla -> `/api/transcribe` -> texto
2. Frontend envia `POST /api/chat` con `{text, conversation_id}`
3. Flask arma payload: system prompt + historial + tools
4. Flask envia a llama.cpp con `stream=True`
5. Flask reenvia tokens via SSE: `data: {"delta": "..."}`
6. Si hay tool_calls, Flask los ejecuta via MCP manager
7. Resultados se agregan al contexto y se continua el loop (max 5 iteraciones)
8. Al finalizar, se envia evento `done` con el historial actualizado

### Integracion con llama-memory

Voice-WebUI usa llama-memory via HTTP en `http://localhost:9001`:

| Uso | Endpoint llama-memory |
|-----|----------------------|
| Crear conversacion | `POST /mcp` tools/call `create_conversation` |
| Listar conversaciones | `POST /mcp` tools/call `list_conversations` |
| Historial | `POST /mcp` tools/call `get_conversation_history` |
| System prompt base | `GET /system-prompt` |

El system prompt es editable en la UI y se persiste en `config.json`.

### MCP config

- Archivo: `D:\llama\mcp\mcp-config.json` (reusar el actual)
- Desde UI: agregar servidor (nombre, command, args) o eliminar
- Al guardar: se reescribe el JSON y se reinicia el MCP client afectado

### Configuracion global (`config.json`)

```json
{
  "llama_url": "http://192.0.2.92:8080/v1/chat/completions",
  "model": "qwen3.6-27b",
  "temperature": 0.6,
  "system_prompt": "...",
  "mcp_config_path": "D:\\llama\\mcp\\mcp-config.json",
  "memory_url": "http://localhost:9001",
  "whisper_model": "small"
}
```

## Testing

- **Unit tests** para `mcp_manager`, `llm_client`, `memory_client`
- **Integration tests** con Flask test client (sin llama.cpp real, con mocks)
- **TDD obligatorio**: test primero, codigo despues
- Suite: `pytest tests/ -q -p no:cacheprovider`

## Publicacion publica (GitHub)

El proyecto se publicara en GitHub como publico al finalizar. Implicaciones:

### Configuracion
- `config.json` NO debe contener rutas absolutas locales (como `D:\llama\mcp\...`) ni IPs locales.
- Las rutas y endpoints se configuran via variables de entorno con defaults razonables.
- Ejemplo: `MCP_CONFIG_PATH` (default `./mcp-config.json`), `LLAMA_URL` (default `http://localhost:8080`), `MEMORY_URL` (default `http://localhost:9001`).
- `config.json` se genera al primer arranque con los defaults, y el usuario lo edita.

### Documentacion
- `README.md` con: descripcion, arquitectura, instalacion, configuracion, ejecucion, requerimientos (Python 3.11, llama.cpp, llama-memory, PostgreSQL).
- `LICENSE` (MIT, coincidiendo con llama-memory).
- `.gitignore` para `__pycache__/`, `.env`, `config.json` (si aplica), archivos temporales.
- `requirements.txt` versionado (no usar `pip freeze` crudo; listar directas).

### Seguridad
- `app.secret_key` NO hardcodeada; se lee de variable de entorno con default para desarrollo local.
- No exponer credenciales de PostgreSQL ni API keys en el repo.
- Documentar en README que es para uso local/demo, no para exposicion publica sin hardening.

### Estructura
- `example.env` (o `.env.example`) con todas las variables documentadas.
- La estructura de archivos del spec es la base; se agregan `README.md`, `LICENSE`, `.gitignore`, `requirements.txt`.

## Limites y exclusiones

No se incluyen en esta version:
- Permisos de herramientas (aprobar/rechazar tool calls)
- Edicion de mensajes / branching
- Tema claro/oscuro (solo oscuro)
- Streaming de transcripcion (Whisper completo primero)
- Multi-usuario / autenticacion

## Criterios de exito

1. Chat por voz funciona de punta a punta (grabar -> transcribir -> LLM -> respuesta hablable)
2. Streaming muestra la respuesta token a token
3. MCPs se pueden agregar/eliminar desde la UI
4. System prompt se edita desde la UI y se aplica
5. La sidebar lista conversaciones de llama-memory
6. Abrir una conversacion carga su historial
7. Todos los archivos < 600 lineas
8. El repo esta listo para publicarse: sin rutas absolutas, sin secretos, con README, LICENSE y .gitignore
