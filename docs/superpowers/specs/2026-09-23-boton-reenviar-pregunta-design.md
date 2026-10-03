# 2026-09-23 - Boton "Regenerar respuesta" junto a la pregunta del usuario

## Contexto

El boton `↻` (`.btn-reload`, tooltip "Regenerar respuesta") aparece actualmente
en el mensaje del asistente (`static/index.html:160`), a la izquierda de cada
respuesta del LLM. Su comportamiento (`resendMessage(botIdx)` en
`static/chatState.js:540`) es: buscar el ultimo mensaje `user` anterior a esa
respuesta, reenviar esa pregunta al backend con `resend: true`, y regenerar el
contenido de esa respuesta (la reemplaza, no duplica mensajes).

El usuario propone que el boton aparezca junto a la pregunta del usuario: es la
convencion de Claude.ai y Gemini, donde las acciones de reintentar/regenerar van
asociadas al mensaje del usuario porque lo que se re-ejecuta es la pregunta.

## Decision

Mover el boton al mensaje del usuario manteniendo la misma accion
("Regenerar la respuesta existente", opcion A aprobada). No se cambia la
semantica: el boton de la pregunta N regenera la respuesta N (la siguiente).

## Diseno

### 1. `static/index.html`

- Mover el boton del template `assistant` (linea 160) al template `user`
  (lineas 144-157), dentro de `msg-user-wrap`, junto a la pregunta.
- Mostrarlo solo si la pregunta tiene una respuesta regenerable:
  `x-show="hasAssistantAfter(i)"`.

### 2. `static/chatState.js`

- `resendMessage(userIdx)`: recibe el indice del mensaje `user` y resuelve el
  indice del siguiente mensaje `assistant` (el que se regenera). El resto del
  flujo (fetch con `resend: true`, streamProcessor, reutilizacion del mensaje
  assistant en `botIdx`) no cambia.
- Nueva funcion `hasAssistantAfter(i)`: true si existe un mensaje `assistant`
  en indices posteriores a `i`.
- Mensaje de error si no hay respuesta que regenerar: "No hay respuesta que
  regenerar" (sustituye a "No hay mensaje anterior para reenviar").

### 3. `static/style.css`

- Ajuste menor de posicion de `.btn-reload` dentro de `msg-user-wrap` (hoy
  esta alineado a la izquierda de la respuesta; en la pregunta se alinea igual
  dentro del wrap del usuario).

### 4. Backend

- Sin cambios. `resend: true` ya esta soportado (`/api/chat`) y reutiliza el
  mensaje assistant existente.

## Casos limite

| Caso | Comportamiento |
|---|---|
| Pregunta con respuesta siguiente | El boton regenera esa respuesta (comportamiento actual, nueva ubicacion) |
| Respuesta vacia/cancelada (assistant existe) | El boton permite reintentar (mejora sobre el estado actual) |
| Ultima pregunta sin respuesta (no hay assistant) | El boton no se muestra (nada que regenerar) |

## Verificacion

El proyecto no tiene tests de frontend (Alpine.js puro, sin build step); la
suite pytest (backend) no cambia porque el backend no se toca. Verificacion
manual con navegador (Playwright):

1. El boton aparece junto a la pregunta, no junto a la respuesta.
2. Hacer clic regenera la respuesta siguiente (se reemplaza el contenido).
3. En una conversacion de varios turnos, el boton de la pregunta N regenera la
   respuesta N (no la ultima).
4. Una pregunta sin respuesta siguiente no muestra el boton.

## Archivos afectados

- `static/index.html`
- `static/chatState.js`
- `static/style.css`