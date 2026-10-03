// Procesador de eventos SSE del stream del chat.
// Maneja deltas, tool calls, metrics, cancelled, error y done events.
// Compartido entre sendText() y resendMessage().

function createStreamProcessor(config) {
  const { messages, updateDelta, applyMetricsEvent, scrollToBottom } = config;

  /** Buffer para batchear deltas y evitar re-renders excesivos en Alpine.js */
  let deltaBuffer = "";

  /** Indice del mensaje asistente actual de este stream.
   * Se setea la primera vez que se crea un assistant en este stream y se
   * reutiliza para TODOS los eventos posteriores (deltas, tools, done).
   * Esto evita que ensureAssistantMessage() busque hacia atras y encuentre
   * respuestas de turnos anteriores. */
  let currentBotIdx = null;

  /** Planificar la aplicacion batcheada de deltas acumulados */
  function flushDeltaBuffer() {
    if (!deltaBuffer) return;
    const content = deltaBuffer;
    deltaBuffer = "";

    // Bug 2026-09-24 (H2): en flujo sin tools, sendText() no crea el assistant
    // al inicio, asi que los deltas se descartaban y la resposta saltaba
    // entera al final con el done. ensureAssistantMessage() lo crea bajo
    // demanda: en la cola del array si no hay, o ahi mismo si ya hay texto.
    const botIdx = ensureAssistantMessage();

    // Mutar el objeto existente en vez de crear uno nuevo: Alpine detecta el
    // cambio porque se modifica la propiedad directamente. Se fuerza la
    // reactividad cambiando la referencia del array.
    messages[botIdx].content += content;
    messages.length = messages.length;

    updateDelta(content.length);
    scrollToBottom();
  }

  /** Planificador de flush: agrupa multiples deltas en un solo frame */
  let flushScheduled = false;

  function scheduleFlush() {
    if (flushScheduled) return;
    flushScheduled = true;
    requestAnimationFrame(() => {
      flushScheduled = false;
      flushDeltaBuffer();
    });
  }

  /** Crea un nuevo mensaje asistente y retorna su indice */
  function createAssistantMessage() {
    messages.push({ role: "assistant", content: "" });
    return messages.length - 1;
  }

  /** Actualiza un mensaje asistente existente o lo crea si no existe.
   *
   * Solo se reutiliza un assistant que sea el ULTIMO elemento del array. La
   * regla es "un assistant esta abierto para escritura si y solo si esta al
   * final": si hay una tool detras, ese assistant ya esta cerrado y la
   * respuesta nueva va DESPUES de la tool. Antes se buscaba hacia atras por
   * cualquier assistant posterior al ultimo user, lo que hacia que, con
   * currentBotIdx fijado por el evento tool, la respuesta final acabara
   * siempre por encima de las tools (bug 2026-10-02). */
  function ensureAssistantMessage() {
    if (currentBotIdx !== null) {
      return currentBotIdx;
    }

    const last = messages[messages.length - 1];
    if (last && last.role === "assistant") {
      currentBotIdx = messages.length - 1;
      return currentBotIdx;
    }

    currentBotIdx = createAssistantMessage();
    return currentBotIdx;
  }

  /** Cierra el slot de assistant abierto y lo suelta.
   *
   * Se llama al llegar una tool: el texto que hubiera era PREAMBULO del modelo
   * (Qwen responde y LUEGO llama a web_search). El backend lo descarta y lo
   * reenvia solo via el evento done, asi que se deja el mensaje cerrado donde
   * esta, encima de la tool, en vez de fijarlo y arrastrarlo. Un assistant sin
   * texto (el slot que resendMessage deja limpio) se elimina: como burbuja
   * vacia solo ocupa sitio y rompe el orden. */
  function closeAssistantSlot() {
    if (currentBotIdx === null) return;
    const idx = currentBotIdx;
    const msg = messages[idx];
    currentBotIdx = null;

    if (msg && msg.role === "assistant" && !(msg.content || "").trim()) {
      messages.splice(idx, 1);
      messages.length = messages.length;
    }
  }

  /** Procesa un evento SSE individual. */
  function processEvent(evt, botIdx, isNewMsg) {

    if (evt.type === "delta") {
      // Acumular deltas en buffer; se aplican batcheados via requestAnimationFrame.
      deltaBuffer += evt.content;
      scheduleFlush();

    } else if (evt.type === "tool") {
      // Flush pending deltas para no perder texto.
      flushDeltaBuffer();

      // El texto pendiente era preambulo del modelo: se cierra el assistant
      // (queda encima de la tool) y se suelta el slot, para que la respuesta
      // final se cree DESPUES de esta tool. Antes se llamaba aqui a
      // ensureAssistantMessage(), que fijaba el slot ANTES del push y dejaba
      // la respuesta final siempre por encima de las tools (bug 2026-10-02).
      closeAssistantSlot();

      messages.push({
        role: "tool",
        name: evt.name,
        arguments: evt.arguments || null,
        result: evt.result,
      });

      scrollToBottom();

    } else if (evt.type === "metrics") {
      applyMetricsEvent(evt);

    } else if (evt.type === "approval_request") {
      // El modelo quiere ejecutar una tool peligrosa (p.ej. run_command).
      // El backend espera la decision del usuario antes de ejecutarla; el
      // frontend muestra un modal y responde via POST /api/chat/approve.
      config.onApprovalRequest?.(evt);

    } else if (evt.type === "approval_expired") {
      // Bug 2026-09-18 (reportado por el usuario: "apruebo y no ejecuta"):
      // cuando APPROVAL_TIMEOUT expiraba, el backend hacia pop(_approval_*)
      // SILENCIOSO y el modal quedaba colgado pidiendo permiso; si el usuario
      // aprobaba tarde, POST /api/chat/approve daba 404 y la tool nunca
      // ejecutaba. El backend ahora emite approval_expired al caducar; aqui
      // cerramos el modal para que no quede colgado.
      config.onApprovalExpired?.(evt);

    } else if (evt.type === "cancelled") {
      config.onCancelled?.(botIdx);

    } else if (evt.type === "error") {
      messages.push({ role: "error", content: evt.message });

    } else if (evt.type === "done") {
      // Flush pending deltas antes de aplicar la respuesta final.
      flushDeltaBuffer();

      // Si no hubo deltas, crear el mensaje asistente con la respuesta final.
      botIdx = ensureAssistantMessage();
      // evt.response es la fuente de verdad: contiene el texto final limpio del backend.
      messages[botIdx].content = evt.response;

      config.onDone?.(evt);

    } else if (evt.type === "thinking") {
      // Rama sin uso: app.py NO emite eventos "thinking" (los unicos tipos que
      // emite son delta, metrics, tool, approval_request, approval_expired,
      // cancelled, error y done). Se deja el mismo criterio de las demas ramas.
      botIdx = ensureAssistantMessage();
      messages[botIdx].content = evt.content || "";

    } else if (evt.type === "tool_calls") {
      // Rama sin uso: app.py NO emite eventos "tool_calls" al frontend (los
      // tool calls nativos solo se anaden al historial que ve el LLM, en
      // app.py, y al usuario le llega el evento "tool" con el resultado).
      // Se cierra el slot igual que en la rama "tool" para no reintroducir el
      // orden invertido si algun dia se emite.
      flushDeltaBuffer();
      closeAssistantSlot();

      if (evt.tool_calls) {
        const idx = ensureAssistantMessage();
        messages[idx]._tool_calls = evt.tool_calls;
      }

    }

    return botIdx;
  }

  /** Lee y procesa el stream SSE completo hasta cancelacion o final. */
  async function readStream(stream, initialBotIdx, isNewMsg) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let botIdx = initialBotIdx;
    let cancelled = false;

    // resendMessage() pasa el indice del assistant que se esta regenerando.
    // Se adopta como slot abierto: si no, closeAssistantSlot() no lo ve y la
    // regeneracion que vuelve a llamar a tools deja una burbuja vacia.
    if (typeof initialBotIdx === "number" && messages[initialBotIdx]?.role === "assistant") {
      currentBotIdx = initialBotIdx;
    }

    while (true) {
      if (config.isCancelled()) break;
      const { done, value } = await reader.read();
      if (done) break;
      if (config.isCancelled()) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split("\n");
      for (const line of lines) {
        if (config.isCancelled()) break;
        if (line.startsWith("data: ")) {
          let evt;
          try {
            evt = JSON.parse(line.slice(6));
          } catch (e) {
            // Linea SSE parcial o invalida: normal en streaming (un evento
            // puede dividirse entre chunks). No es un error real.
            continue;
          }
          try {
            botIdx = processEvent(evt, botIdx, isNewMsg);
          } catch (e) {
            // Bug 2026-09-24 (H2): un fallo en processEvent pierde el evento
            // Y el botIdx (los siguientes eventos escriben en el indice
            // equivocado). Dejarlo visible en consola para no tragar bugs
            // silenciosamente.
            console.warn("[chatStream] error procesando evento:", evt && evt.type, e);
          }
        }
      }

      if (config.isCancelled()) break;

    }

    return { botIdx, cancelled };

  }

  return { readStream, processEvent };

}
