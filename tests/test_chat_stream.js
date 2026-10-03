// Tests de static/chatStream.js.
//
// Importa el modulo REAL con vm.runInThisContext. Antes este archivo llevaba
// una COPIA de createStreamProcessor: cualquier cambio en static/chatStream.js
// no lo tocaba y la suite pasaba en verde sin validar nada (bug 2026-10-02).
//
// Ejecutar con: node tests/test_chat_stream.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// requestAnimationFrame no existe en Node: el flush de deltas se agrupa con
// rAF en el navegador.
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);

const SOURCE = path.join(__dirname, "..", "static", "chatStream.js");
vm.runInThisContext(fs.readFileSync(SOURCE, "utf8"), { filename: SOURCE });
const createStreamProcessor = globalThis.createStreamProcessor;


// ============================================================
// Mini runner
// ============================================================

let testsRun = 0;
let testsPassed = 0;
let testsFailed = 0;
const failures = [];

function assert(condition, message) {
  testsRun++;
  if (condition) {
    testsPassed++;
  } else {
    testsFailed++;
    failures.push(message);
    console.log(`  FAIL: ${message}`);
  }
}

function assertEquals(actual, expected, message) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  assert(a === e, `${message} (esperado ${e}, obtenido ${a})`);
}

/** Deja correr los rAF pendientes (flush de deltas). */
const tick = () => new Promise((r) => setTimeout(r, 1));

/** Stream falso que emite los eventos como chunks SSE reales. */
function fakeStream(events) {
  const encoder = new TextEncoder();
  const chunks = events.map((e) => encoder.encode(`data: ${JSON.stringify(e)}\n\n`));
  let i = 0;
  return {
    getReader: () => ({
      read: async () =>
        i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined },
    }),
  };
}

/** Conduce el processor por readStream(), igual que sendText()/resendMessage(). */
async function runStream(messages, events, initialBotIdx = null) {
  const processor = createStreamProcessor({
    messages,
    updateDelta: () => {},
    applyMetricsEvent: () => {},
    scrollToBottom: () => {},
    isCancelled: () => false,
    onDone: () => {},
  });
  const res = await processor.readStream(fakeStream(events), initialBotIdx, true);
  await tick();
  return res;
}

/** Forma compacta de un array de mensajes para comparar de un vistazo. */
const shape = (messages) =>
  messages.map((m) => `${m.role}${m.name ? ":" + m.name : ""}=${JSON.stringify(m.content ?? m.result ?? "")}`);


// ============================================================
// Tests
// ============================================================

// Bug 2026-10-02: la respuesta del modelo se renderizaba ANTES de las tools.
// processEvent pedia ensureAssistantMessage() en la rama "tool", lo que fijaba
// currentBotIdx en un slot situado ANTES del push de la tool; todos los delta
// siguientes iban a ese slot.
async function test_tools_antes_de_la_respuesta() {
  console.log("\nTest 1: las tools van antes de la respuesta final (sin preambulo)");

  const messages = [{ role: "user", content: "Analiza la alerta" }];

  await runStream(messages, [
    { type: "tool", name: "web_search", arguments: "{}", result: "r1" },
    { type: "tool", name: "web_fetch", arguments: "{}", result: "r2" },
    { type: "delta", content: "Conclusion" },
    { type: "done", response: "Conclusion" },
  ]);
  await tick();

  assertEquals(
    shape(messages),
    [
      'user="Analiza la alerta"',
      'tool:web_search="r1"',
      'tool:web_fetch="r2"',
      'assistant="Conclusion"',
    ],
    "Orden: user, tool, tool, assistant"
  );

  // Sin burbuja de assistant vacia: es lo que dejaba la implementacion previa.
  assert(
    !messages.some((m) => m.role === "assistant" && !m.content),
    "No debe quedar ningun mensaje assistant vacio"
  );
}

async function test_preambulo_conservado_sobre_las_tools() {
  console.log("\nTest 2: el preambulo del modelo se conserva encima de las tools");

  // Caso real (alerta de trafico): Qwen escribe el analisis, luego llama a
  // web_search/web_fetch, y despues emite la respuesta final.
  const messages = [{ role: "user", content: "Analiza la alerta" }];

  await runStream(messages, [
    { type: "delta", content: "Resumen de la alerta" },
    { type: "tool", name: "web_search", arguments: "{}", result: "r1" },
    { type: "tool", name: "web_search", arguments: "{}", result: "r2" },
    { type: "tool", name: "web_fetch", arguments: "{}", result: "r3" },
    { type: "delta", content: "Conclusion" },
    { type: "done", response: "Conclusion" },
  ]);
  await tick();

  assertEquals(
    shape(messages),
    [
      'user="Analiza la alerta"',
      'assistant="Resumen de la alerta"',
      'tool:web_search="r1"',
      'tool:web_search="r2"',
      'tool:web_fetch="r3"',
      'assistant="Conclusion"',
    ],
    "Orden: user, preambulo, tools, respuesta final"
  );

  // El preambulo no debe pisarse con la respuesta final.
  assert(
    messages[1].content === "Resumen de la alerta",
    "El preambulo debe conservar su texto"
  );
}

async function test_done_sin_deltas_tras_las_tools() {
  console.log("\nTest 3: done sin deltas despues de las tools crea la respuesta al final");

  const messages = [{ role: "user", content: "Analiza la alerta" }];

  await runStream(messages, [
    { type: "tool", name: "web_search", arguments: "{}", result: "r1" },
    { type: "done", response: "Conclusion" },
  ]);
  await tick();

  assertEquals(
    shape(messages),
    ['user="Analiza la alerta"', 'tool:web_search="r1"', 'assistant="Conclusion"'],
    "Orden: user, tool, assistant"
  );
}

async function test_no_reutiliza_assistant_del_turno_anterior() {
  console.log("\nTest 4: no reutiliza el assistant del turno anterior");

  const messages = [
    { role: "user", content: "Turno 1" },
    { role: "assistant", content: "Respuesta turno 1" },
    { role: "user", content: "Turno 2" },
  ];

  await runStream(messages, [
    { type: "delta", content: "Hola" },
    { type: "done", response: "Hola" },
  ]);
  await tick();

  assertEquals(
    shape(messages),
    [
      'user="Turno 1"',
      'assistant="Respuesta turno 1"',
      'user="Turno 2"',
      'assistant="Hola"',
    ],
    "El turno 1 queda intacto y el turno 2 crea su assistant"
  );
  assert(
    messages[1].content === "Respuesta turno 1",
    "La respuesta del turno 1 no debe modificarse"
  );
}

async function test_resend_reutiliza_el_assistant_existente() {
  console.log("\nTest 5: resend reutiliza el assistant existente si no hay tools");

  // resendMessage() limpia el contenido del assistant y vuelve a generar.
  const messages = [
    { role: "user", content: "Pregunta" },
    { role: "assistant", content: "" },
  ];

  await runStream(messages, [
    { type: "delta", content: "Hola" },
    { type: "done", response: "Hola" },
  ], 1);
  await tick();

  assertEquals(
    shape(messages),
    ['user="Pregunta"', 'assistant="Hola"'],
    "Se reutiliza el slot en vez de crear uno nuevo"
  );
}

async function test_resend_con_tools_no_deja_burbuja_vacia() {
  console.log("\nTest 6: resend con tools no deja una burbuja assistant vacia");

  const messages = [
    { role: "user", content: "Pregunta" },
    { role: "assistant", content: "" },
  ];

  await runStream(messages, [
    { type: "tool", name: "web_search", arguments: "{}", result: "r1" },
    { type: "delta", content: "Hola" },
    { type: "done", response: "Hola" },
  ], 1);
  await tick();

  assertEquals(
    shape(messages),
    ['user="Pregunta"', 'tool:web_search="r1"', 'assistant="Hola"'],
    "El assistant vacio se descarta y la respuesta va tras la tool"
  );
}

async function test_texto_parcial_entre_tools() {
  console.log("\nTest 7: texto parcial entre dos tandas de tools");

  const messages = [{ role: "user", content: "Pregunta" }];

  await runStream(messages, [
    { type: "tool", name: "web_search", arguments: "{}", result: "r1" },
    { type: "delta", content: "A" },
    { type: "tool", name: "web_search", arguments: "{}", result: "r2" },
    { type: "done", response: "Final" },
  ]);
  await tick();

  assertEquals(
    shape(messages),
    [
      'user="Pregunta"',
      'tool:web_search="r1"',
      'assistant="A"',
      'tool:web_search="r2"',
      'assistant="Final"',
    ],
    "Orden: user, tool, texto, tool, respuesta final"
  );
}


// ============================================================
// Ejecutar
// ============================================================

(async () => {
  console.log("=== Running static/chatStream.js tests ===");

  await test_tools_antes_de_la_respuesta();
  await test_preambulo_conservado_sobre_las_tools();
  await test_done_sin_deltas_tras_las_tools();
  await test_no_reutiliza_assistant_del_turno_anterior();
  await test_resend_reutiliza_el_assistant_existente();
  await test_resend_con_tools_no_deja_burbuja_vacia();
  await test_texto_parcial_entre_tools();

  console.log(`\n=== Total: ${testsPassed}/${testsRun} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) process.exit(1);
})();