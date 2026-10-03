# Especificacion de Diseno: Adjuntar archivos a la conversacion (documentos como texto)

## 2026-09-06 - Voice-WebUI

## Contexto

La WebUI de llama-server permite subir archivos a la conversacion. Voice-WebUI no tiene esta funcionalidad. El usuario pidio anadirla "como en la webUI de llama-server".

El modelo actual es text-only (`Qwen3.6-35B-A3B-APEX-I-Quality.gguf`, alias `qwen3.6-27b`) sin mmproj, por lo que la subida de media (image_url) de llama-server no aplica. La decision de diseno (confirmada con el usuario) es:

- **Documentos como texto**: PDF, TXT, DOCX.
- **Inyeccion en el mensaje**: el backend extrae el texto y lo antepone al mensaje del usuario para el LLM (marcador + contenido).
- **Persistencia**: archivos en disco (`uploads/`) + referencia (marcador) en la memoria de la conversacion.

## Alcance

**Incluido:**
- Endpoint `POST /api/upload` (multipart) que guarda el archivo en disco, extrae el texto y devuelve `{file_id, name, chars, truncated}`
- Marcadores `[adjunto:<file_id>|<nombre_visible>]` en el texto del mensaje del usuario
- Resolucion de marcadores en un unico punto (`_build_chat_messages`): historial + mensaje actual
- Persistencia: el mensaje guarda el texto crudo con marcadores; el contenido extraido vive solo en disco
- Frontend: boton adjuntar, chips de adjuntos pendientes, chips en mensajes de usuario, resend con adjuntos
- Split de `static/app.js` (639 lineas, supera el limite de 600) con el patron `Alpine.data` sin build step
- Nuevas dependencias: `pypdf`, `python-docx`
- Nuevo modulo `src/attachments.py` (logica pura sin Flask)
- `UPLOAD_DIR` configurable (env, default `PROJECT_ROOT/uploads`), anadido a `AppConfig`

**No incluido (YAGNI):**
- Descarga/visualizacion de archivos subidos (sin endpoint de servir archivos)
- GC de archivos huerfanos (subidos pero nunca enviados) — pendiente documentado
- OCR para PDFs escaneados (se detecta texto vacio y se avisa)
- Imagenes vision (requiere mmproj; el modelo actual es text-only)
- Audio/video como adjuntos (whisper ya cubre transcripcion por voz)

## Arquitectura

### Backend

#### `src/attachments.py` (nuevo, logica pura sin Flask)

Constantes:
- `ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}`
- `MAX_UPLOAD_BYTES = 10 * 1024 * 1024`
- `MAX_ATTACHMENT_CHARS = 20000` (por archivo)
- `MAX_ATTACHMENTS_PER_MESSAGE = 5`
- `MAX_TOTAL_ATTACHMENT_CHARS = 40000` (por mensaje)
- `MARKER_RE = re.compile(r"\[adjunto:([0-9a-f]{12})\|([^\]]*)\]")`

Funciones:
- `sanitize_filename(name: str) -> str`: `Path(name).name` (elimina rutas), reemplaza caracteres peligrosos (`[`, `]`, `|`, caracteres de control, espacios raros) por `_`, limita a 80 chars, conserva la extension.
- `extract_text(path: Path) -> str`: segun extension. `.txt` utf-8 con fallback latin-1; `.pdf` con `pypdf.PdfReader` (extrae por pagina, une con `\n`); `.docx` con `python-docx` (parrafos).
- `save_upload(file_storage, user: str, upload_dir: Path) -> dict`: valida extension y tamano, genera `file_id = uuid4().hex[:12]`, guarda en `upload_dir/user/file_id_sanitized_name`, extrae texto, trunca a `MAX_ATTACHMENT_CHARS` (con `truncated: True`). Devuelve `{file_id, name, chars, truncated, warning?}`. Si la extraccion falla (PDF corrupto) borra el archivo y lanza `ValueError`. Si el texto es vacio (PDF escaneado) conserva el archivo y devuelve `warning`.
- `resolve_attachments(text: str, user: str, upload_dir: Path) -> str`: reemplaza cada marcador por un bloque:
  ```
  [Contenido del archivo "nombre.pdf" (1234 caracteres)]
  <texto extraido>
  ```
  El nombre del bloque usa el nombre real en disco (autoritativo, saneado en el upload). Si el archivo no existe: `[archivo adjunto no disponible: <nombre>]`. Si el total supera `MAX_TOTAL_ATTACHMENT_CHARS`, trunca el bloque combinado con aviso final.

#### `src/app.py` (cambios)

- `AppConfig`: nuevo campo `upload_dir` (env `UPLOAD_DIR`, default `uploads/`), resuelto relativo a `PROJECT_ROOT` como `mcp_config_path`. Persistido en `config.json` (no se expone en el API; el frontend no lo necesita).
- `_build_chat_messages(mem_client, conversation_id, text, user)`: nuevo parametro `user`. Resuelve marcadores en los mensajes `role:user` del historial y en el mensaje actual. Es el UNICO punto de resolucion.
- `api_chat`: pasa `_current_user() or config.memory_user` a `_build_chat_messages`. `_persist_chat` guarda el texto crudo (con marcadores), sin resolver.
- `api_upload` (nuevo, POST `/api/upload`): ruta fina que delega en `attachments.save_upload(request.files.get("file"), user, Path(config.upload_dir))`. Errores: 400 sin archivo/extension no soportada/extraccion fallida, 413 demasiado grande.
- `_persist_chat`: sin cambios de firma; el `user_text` que llega ya contiene los marcadores.

#### `src/config.py` (cambios)

- Campo `upload_dir` con default `os.environ.get("UPLOAD_DIR", "uploads/")`, resuelto relativo a `PROJECT_ROOT`, incluye `"upload_dir"` en las claves de `_load`/`save`.

### Frontend

#### Split de `static/app.js` (639 lineas > 600)

Patron `Alpine.data` sin build step: se cargan varios scripts y se registra un unico componente:

- `static/app.js` → define `chatState()` (estado y metodos actuales del chat, config, MCP, voz, metricas)
- `static/attachments.js` (nuevo) → define `attachmentState()` (adjuntos pendientes, upload, chips, parseo de marcadores)
- `static/index.html` → el body pasa de `x-data="voiceApp()"` a `x-data="voiceApp"`, y un tercer script (o el propio `app.js` al final) registra `Alpine.data("voiceApp", () => ({ ...chatState(), ...attachmentState() }))`

Nota: `Alpine.data` debe registrarse antes de la inicializacion de Alpine (el CDN de alpinejs se carga con `defer` despues de `app.js`), por lo que el registro puede ir en `app.js` tras definir `chatState`.

#### `static/attachments.js` (nuevo)

- Estado: `pendingAttachments: []`
- `attachFiles(event)`: para cada archivo seleccionado (max 5, aviso si excede) → `POST /api/upload` → push `{file_id, name, chars}` a `pendingAttachments`. Errores visibles en `statusText`.
- `removePendingAttachment(index)`
- `buildMarker(att)`: `[adjunto:<file_id>|<name_saneado>]`
- `parseMarkers(text)`: extrae `{clean_text, attachments:[{file_id, name}]}` de un mensaje de usuario
- `renderMessage(...)`: el template renderiza chips (nombre) + texto limpio

#### `static/app.js` (cambios dentro del split)

- `sendText()`: si hay `pendingAttachments`, el texto enviado es `draft + "\n" + markers`; limpia `pendingAttachments` al enviar
- `openConversation()`: para mensajes `role:user`, parsea marcadores → `msg.content` limpio + `msg.attachments`
- `resendMessage()`: reconstruye `msg.content + markers` desde `msg.attachments` (funciona sin cambios de backend)
- El template de mensaje usuario (`index.html`) muestra chips de `msg.attachments` + texto

#### `static/index.html` (cambios)

- Boton adjuntar (clip) en input-area + input file oculto (`accept=".txt,.pdf,.docx"`, multiple)
- Zona de chips para `pendingAttachments` (nombre + x)
- Chips en los mensajes de usuario (de `msg.attachments`)
- Carga de scripts: `attachments.js` antes de `app.js`, y registro de `Alpine.data`

#### `static/style.css` (cambios)

- Estilos de chips (pendientes y en mensajes) y del boton adjuntar, coherentes con el tema actual

## Flujo de datos

```
1. usuario: click clip -> selecciona informe.pdf
2. frontend: POST /api/upload (multipart)
3. backend: guarda uploads/<user>/<file_id>_informe.pdf, extrae texto, trunca a 20000 chars
   -> {file_id, name: "informe.pdf", chars: 18432, truncated: false}
4. frontend: chip "informe.pdf (18432 chars)" en pendingAttachments
5. usuario escribe "resume esto" + Enviar
6. frontend: POST /api/chat {"text": "resume esto\n[adjunto:<file_id>|informe.pdf]", conversation_id}
7. backend: _build_chat_messages resuelve el marcador ->
   user: "[Contenido del archivo \"informe.pdf\" (18432 caracteres)]\n<texto>..."  (+ historial resuelto)
8. backend: stream al LLM, guarda en memoria el texto crudo con marcadores (role:user) y la respuesta (role:assistant)
9. al reabrir la conversacion: el frontend parsea el marcador -> chip "informe.pdf" + texto "resume esto"
10. resend: frontend reenvia "resume esto\n[adjunto:<file_id>|informe.pdf]" -> backend resuelve igual
```

## Edge cases

- **Archivo borrado del disco pero marcador en memoria** → `[archivo adjunto no disponible: <nombre>]` (no rompe el chat)
- **PDF escaneado (0 chars)** → archivo guardado, bloque `[sin texto extraible: <nombre>]`, `warning` en el upload
- **PDF corrupto** → 400, archivo borrado
- **Extension no permitida** → 400
- **Archivo > 10 MB** → 413
- **Mas de 5 adjuntos en un mensaje** → el frontend avisa y limita; el backend tambien valida (400 si excede)
- **Texto combinado > 40000 chars** → truncado con aviso en el bloque
- **Resend** → reenvia texto + marcadores, resolucion identica
- **Sin sesion (dev mode)** → user = `config.memory_user` (mismo criterio que `_get_memory_client`)
- **Nombres con caracteres raros** → saneados en el upload y en el marcador
- **Historial con muchos adjuntos** → relectura de disco por request (aceptable; sin cache por YAGNI)

## Tests (TDD)

### `tests/test_attachments.py` (nuevo)

- `test_extract_text_txt_utf8` / `test_extract_text_txt_latin1`
- `test_extract_text_pdf` (PDF minimo generado a mano en el test)
- `test_extract_text_docx` (generado con python-docx en el test)
- `test_sanitize_filename_quita_rutas_y_peligrosos`
- `test_save_upload_valida_extension` / `test_save_upload_limite_bytes` (413)
- `test_save_upload_trunca_caracteres`
- `test_resolve_attachments_reemplaza_marcador`
- `test_resolve_attachments_archivo_ausente`
- `test_resolve_attachments_limite_total`

### `tests/test_app.py` (adiciones)

- `test_upload_sin_archivo_400`
- `test_upload_extension_no_permitida_400`
- `test_upload_ok_devuelve_metadata` (upload_dir en tempdir via env `UPLOAD_DIR`)
- `test_chat_resuelve_marcadores_en_mensaje_y_historial` (mock LLM: verifica que los mensajes enviados al LLM contienen el contenido resuelto y que `save_memory` guarda el texto crudo con marcadores)
- `test_chat_resend_con_adjuntos`

### `tests/conftest.py`

- Anadir `UPLOAD_DIR` a las env vars del fixture (tempdir) y a la limpieza

### `requirements.txt`

- `+pypdf`, `+python-docx`

### `.gitignore`

- `+uploads/`

## Verificacion

- Suite completa: `python -m pytest tests -q -p no:cacheprovider`
- Smoke test de rutas registradas (`/api/upload`)
- E2E Playwright: login → adjuntar `.txt` → enviar → chip visible en el mensaje y en el historial recargado → resend regenera con adjunto

## Pendiente documentado

- GC de archivos huerfanos en `uploads/` (subidos pero nunca enviados)
- Cache de contenido extraido por `file_id` si la relectura de disco se vuelve costosa