# Especificación de Diseño: Reenviar mensaje (reload/regenerate)

## 2026-09-05 - Voice-WebUI

## Contexto

La WebUI de llama-server tiene un botón de "reload" (recarga) en cada respuesta del asistente que permite regenerar la respuesta a la última pregunta del usuario, sin que el usuario tenga que escribirla otra vez. Voice-WebUI no tiene esta funcionalidad.

El usuario pidió añadirla "al estilo de la WebUI de llama-server".

## Alcance

**Incluido:**
- Botón de reload en cada mensaje del asistente (frontend)
- Reenvío del último mensaje del usuario al backend con un flag `resend`
- El backend ignora el guardado del mensaje del usuario (ya está en memoria) pero sí guarda la nueva respuesta del asistente
- Reemplazo del contenido del mensaje del asistente original por el nuevo stream (no se crea un nuevo mensaje)
- Tool calls durante el reload funcionan igual que en un chat normal
- Métricas de tokens/velocidad se muestran igual que en un chat normal

**No incluido (YAGNI):**
- Reenviar un mensaje histórico arbitrario (solo el último del usuario)
- Modificar el mensaje del usuario antes de reenviar (edición inline)
- Historial de respuestas alternativas (mostrar la respuesta anterior al regenerar)
- Rate limiting de reloads

## Arquitectura

### Backend (`src/app.py`)

`/api/chat` (POST) acepta un nuevo campo opcional `resend: boolean` (default `false`).

Cuando `resend=true`:
- **No** se guarda el mensaje del usuario (ya está en memoria como `role:user`)
- **Sí** se guarda la respuesta del asistente con `role:assistant` (igual que un chat normal)
- El resto del flujo (streaming SSE, tool calls, anti-loop, métricas) es **idéntico**

Se reutiliza la misma lógica de `_persist_chat` con un parámetro `save_user: bool`:

```python
def _persist_chat(mem_client, conversation_id, user_text, assistant_response, save_user=True):
    if save_user and conversation_id:
        mem_client.save_memory(user_text, conversation_id, tags=["role:user"])
    if conversation_id and assistant_response:
        mem_client.save_memory(assistant_response, conversation_id, tags=["role:assistant"])
```

### Frontend (`static/app.js` + `index.html`)

**`app.js`:**
- Nueva función `resendMessage(botIdx)`:
  1. Buscar el último mensaje del usuario anterior al `botIdx` en `this.messages`
  2. Si no hay, mostrar error y salir
  3. Poner `loading = true`, limpiar métricas
  4. Enviar `POST /api/chat` con `{text: userMsg.content, conversation_id, resend: true}`
  5. Consumir el stream, **reemplazando** el contenido del mensaje en `botIdx` (no se crea uno nuevo)
  6. Al terminar, `loadConversations()` (igual que `sendText`)

**`index.html`:**
- Template del mensaje del asistente incluye un botón de reload (recarga) con icono circular arrow
- El botón solo se muestra cuando `msg.role === 'assistant'`

**`style.css`:**
- Estética del botón reload (pequeño, al lado del contenido del mensaje)

## Flujo de datos

```
1. usuario: "Hola" → asistente: "Hola, ¿qué tal?" [botón reload ↻]
   ↓ click reload en el msg asistente
2. frontend busca el último msg user anterior → "Hola"
3. frontend: POST /api/chat {text:"Hola", conversation_id, resend:true}
4. backend: no guarda "Hola" (save_user=false), genera stream
5. backend: guarda nueva respuesta como role:assistant
6. frontend: reemplaza el contenido del msg asistente (botIdx) con el nuevo stream
```

## Edge cases

- **Sin mensaje de usuario previo** → error 400 (no hay nada que reenviar)
- **Conversación vacía** → error 400
- **Tool calls durante el reload** → funcionan igual (el modelo puede usar herramientas de nuevo); los tool events se añaden como msgs `role:tool` antes del msg asistente (igual que un chat normal)
- **Cancelación** → el botón Stop funciona igual (usa `/api/chat/cancel`)
- **Métricas** → se muestran igual que en un chat normal (estimadas durante, reales al terminar)
- **Streaming del primer delta** → el msg asistente ya existe (botIdx), así que solo se actualiza el contenido, no se crea uno nuevo

## Tests (TDD)

Ver `tests/test_app.py`:
- `test_chat_resend_no_guarda_pregunta_pero_si_respuesta` — con `resend:true`, el msg user NO se guarda (1 save_memory) pero la respuesta SÍ (2 save_memory total)
- `test_chat_resend_sin_mensagem_principal_devuelve_400` — `resend:true` sin `text` → 400
- `test_chat_resend_flujo_completo` — flujo completo con stream, tool calls, done

## Verificación

- Tests unitarios (backend + frontend)
- Smoke test de rutas registradas
- E2E con Playwright: login → enviar msg → click reload → nueva respuesta visible