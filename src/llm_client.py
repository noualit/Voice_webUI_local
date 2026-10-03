# llm_client.py
import json
import re
import requests

from llm_prompt import (
    MAX_PROMPT_TOKENS,
    PromptTooLargeError,
    estimate_prompt_tokens,
    _filter_reasoning_markers,
    format_tools_prompt,
)
from llm_tool_parsing import (
    _TOOL_TAG_OPENERS,
    _KNOWN_TOOL_NAMES,
    is_known_tool_name,
    parse_xml_tool_calls,
    parse_peg_native_call,
    find_peg_native_call,
    flush_peg_call,
)

# Re-export para compatibilidad con importaciones existentes
__all__ = [
    'LLMClient',
    'format_tools_prompt',
    'estimate_prompt_tokens',
    'PromptTooLargeError',
    'MAX_PROMPT_TOKENS',
    'is_known_tool_name',
    'parse_xml_tool_calls',
    'parse_peg_native_call',
    'find_peg_native_call',
    'flush_peg_call',
]

class LLMClient:
    """Cliente HTTP para llama.cpp con streaming SSE y tool calling."""

    def __init__(self, base_url: str, model: str, timeout: int = 600,
                 max_tokens: int = 16384, max_prompt_tokens: int = MAX_PROMPT_TOKENS):
        # NOTA (2026-09-12): repeat_penalty, max_tokens, min_p y
        # reasoning_budget_message son LEGACY. El payload ya NO los envia: la
        # config de generacion la controla el servidor llama.cpp via CLI. Se
        # mantienen en la firma para no romper llamadas existentes/tests.
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        # Limite de tokens estimados del prompt (guard de defensa en profundidad,
        # bug 2026-09-24/25). Si el prompt lo supera, chat_stream lanza
        # PromptTooLargeError antes de llamar a llama.cpp.
        self.max_prompt_tokens = max_prompt_tokens

    # Delegados thin para compatibilidad con tests
    @staticmethod
    def _is_known_tool_name(name: str, whitelist) -> bool:
        return is_known_tool_name(name, whitelist)

    @staticmethod
    def _parse_xml_tool_calls(text: str, known_names: set | None = None) -> list | None:
        return parse_xml_tool_calls(text, known_names)

    @staticmethod
    def _parse_peg_native_call(text: str, known_names: set | None = None) -> list | None:
        return parse_peg_native_call(text, known_names)

    @classmethod
    def _find_peg_native_call(cls, text: str, known_names: set | None = None) -> tuple | None:
        return find_peg_native_call(text, known_names)

    @classmethod
    def _flush_peg_call(cls, peg_buffer: str, known_names: set | None = None) -> tuple[list, str]:
        return flush_peg_call(peg_buffer, known_names)

    def _payload(self, messages: list, tools: list | None, stream: bool) -> dict:
        # La app NO envia parametros de generacion (min_p,
        # penalties, reasoning...): los controla el servidor llama.cpp via CLI
        # (decision 2026-09-12). Unica excepcion: max_tokens (limite duro finito
        # para evitar generaciones infinitas / cuelgues del slot).
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": self.max_tokens,
            # Pide a llama.cpp que incluya el bloque "usage" (prompt_tokens,
            # completion_tokens, total_tokens) en el ultimo chunk del stream.
            # Permite mostrar tokens reales consumidos y tokens/segundo reales
            # en el frontend, en lugar de estimar con chars/4.
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat_stream(self, messages: list, tools: list | None,
                    known_tool_names: set | None = None):
        """
        Envia chat completion con stream=True y produce un generador de dicts.
        Cada yield es un dict: {"type": "delta", "content": "..."} o
        {"type": "tool_calls", "tool_calls": [...]}.

        known_tool_names: whitelist dinamica con las tools reales disponibles
        (la construye app.py desde get_openai_tools + get_memory_tools). Si es
        None, se usa la whitelist estatica _KNOWN_TOOL_NAMES. Se valida contra
        ella TODO nombre de tool, venga del formato que venga (XML, peg-native,
        delta.tool_calls JSON nativo): los nombres que no correspondan a tools
        reales se descartan (bug 2026-09-14: el modelo alucinaba
        mcp_memory_search_memories / query / limit / mcp_ y la app respondia
        'Herramienta X no encontrada').
        """
        whitelist = known_tool_names if known_tool_names is not None else _KNOWN_TOOL_NAMES
        # Guard de tamano de prompt (defensa en profundidad, bug 2026-09-24/25):
        # llama.cpp devuelve 400 "exceeds the available context size" si
        # prompt + generacion supera n_ctx_slot (65536). La truncacion de
        # tool results (LLM_RESULT_LIMIT) previene el caso comun; este guard
        # cubre cualquier prompt que se escape (historial enorme, tool result
        # gigante sin truncar, etc.) con un error claro ANTES del POST.
        estimated = estimate_prompt_tokens(messages, tools)
        if estimated > self.max_prompt_tokens:
            raise PromptTooLargeError(
                f"El prompt es demasiado grande: ~{estimated} tokens estimados "
                f"(limite {self.max_prompt_tokens}). El contexto de llama.cpp "
                "no puede procesarlo. Inicia una conversacion nueva o reduce "
                "el historial."
            )
        payload = self._payload(messages, tools, stream=True)
        r = requests.post(self.base_url, json=payload, stream=True, timeout=self.timeout)
        r.raise_for_status()

        tool_calls_accum = {}
        # Buffer para tool calls en formato XML de Qwen (texto plano) que
        # llama.cpp no haya convertido a delta.tool_calls.
        xml_buffer = ""
        # Buffer para tool calls en formato peg-native de llama.cpp ([call:...]).
        # Qwen 3.x con chat_format peg-native genera las calls como texto plano:
        #   [call:diamcp://context/dia.get_timezone|{"params":{...}}]
        # Se acumula hasta completar el JSON y se emite como tool_calls con raw
        # (mismo flujo texto plano que el XML, bug 2026-09-12).
        peg_buffer = ""
        # Buffer para fragmentos pendientes de apertura de tag XML de tool call
        # (p.ej. "<tool_code" sin ">") que llegan DIVIDIDOS entre deltas por la
        # tokenizacion BPE de Qwen (bug 2026-09-13: el modelo emitio
        # "<tool_code>memoria.create_conversation(...)</tool_code>" y la app lo
        # mostro como texto crudo porque ningun delta contenia el tag completo).
        # Se concatena con el siguiente delta para detectar el tag al completarse.
        xml_pending = ""
        # Acumulador del razonamiento del modelo (reasoning_content). Con
        # --reasoning on, Qwen 3.x razona antes de responder y a veces ENSAYA
        # el tool call SOLO en el razonamiento, emitiendo basura (p. ej. '>')
        # en content (bug 2026-09-13). Si el content no trae tool calls pero
        # el reasoning contiene un tool call completo, se emite al final.
        reasoning_accum = ""
        # Bandera: se emitieron tool calls desde content/peg. Si es False al
        # cerrar el stream, se intenta extraer del reasoning_accum.
        emitted_tool_calls = False
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                # Capturar el bloque "usage" del chunk final (enviado por llama.cpp
                # cuando stream_options.include_usage=true). El chunk final tiene
                # choices vacio pero incluye usage; NO debe descartarse.
                if data.get("usage"):
                    yield {"type": "usage", "usage": data["usage"]}
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                # Acumular el razonamiento del modelo (clave reasoning_content
                # en llama.cpp con --reasoning on; thinking/reasoning como
                # fallback segun build). El razonamiento se IGNORA para el
                # texto visible pero se guarda por si contiene tool calls.
                reasoning_piece = (
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("thinking")
                )
                if reasoning_piece:
                    reasoning_accum += reasoning_piece
                if "content" in delta and delta["content"]:
                    content = delta["content"]
                    # El contenido se usa RAW para detectar tool calls (los
                    # tags XML como </tool_call> contienen '>'). Solo se filtran los
                    # marcadores '>' al emitir deltas al frontend.
                    if xml_pending:
                        # Concatenar el fragmento pendiente de apertura de tag
                        # (dividido entre deltas, bug 2026-09-13) con el contenido
                        # actual para detectar el tag cuando se complete.
                        content = xml_pending + content
                        xml_pending = ""
                    if peg_buffer:
                        # Ya estamos acumulando un [call:...] de tool call
                        peg_buffer += content
                        events, peg_buffer = flush_peg_call(peg_buffer, known_names=whitelist)
                        for evt in events:
                            yield evt
                    elif xml_buffer:
                        # Ya estamos acumulando un XML de tool call
                        xml_buffer += content
                        # Cierre con o sin 's' (</tool_calls> vs </tool_call>),
                        # formato <tool name="...">...</tool> (</tool>) o
                        # formato <tool_code>...</tool_code> o minimalista
                        # </tools>
                        if (
                            "</tool_call" in xml_buffer
                            or "</tool>" in xml_buffer
                            or "</tool_code>" in xml_buffer
                            or "</tools>" in xml_buffer
                        ):
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                # Incluir "raw" con el XML original para que app.py
                                # use el flujo XML (assistant content = XML crudo,
                                # resultado como user) en lugar del flujo JSON
                                # (role:tool), que crashea llama.cpp sin tools en
                                # payload (bug 2026-09-08).
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    elif "[call:" in content:
                        # Comienza el [call:...] en este delta; el texto previo
                        # (p. ej. razonamiento del modelo) se emite como delta.
                        idx = content.index("[call:")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        peg_buffer = content[idx:]
                        events, peg_buffer = flush_peg_call(peg_buffer, known_names=whitelist)
                        for evt in events:
                            if evt["type"] == "tool_calls":
                                emitted_tool_calls = True
                            yield evt
                    elif "<tool_call>" in content or "<tool_calls>" in content:
                        # Comienza el XML en este delta; el texto previo (p. ej.
                        # razonamiento del modelo) se emite como delta normal.
                        # La apertura puede ser <tool_call> o <tool_calls> (con
                        # 's', bug 2026-09-13: el modelo alterna ambas).
                        idx = content.find("<tool_call>")
                        if idx == -1:
                            idx = content.find("<tool_calls>")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        xml_buffer = content[idx:]
                        # Cierre con o sin 's' (</tool_calls> vs </tool_call>)
                        if "</tool_call" in xml_buffer:
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    elif "<tool name=" in content:
                        # Formato 6 (bug 2026-09-12): <tool name="...">JSON</tool>
                        # (MCP canonico con atributo), con o sin wrapper <tools>.
                        # El texto previo (p. ej. razonamiento) se emite como delta.
                        idx = content.index("<tool name=")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        xml_buffer = content[idx:]
                        if "</tool>" in xml_buffer:
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    elif "<tool_code>" in content:
                        # Formato 7 (bug 2026-09-12): el modelo escribe codigo
                        # print(dia.tool(...)) dentro de <tool_code>. Solo se
                        # ejecuta el patron seguro (ver _parse_xml_tool_calls).
                        idx = content.index("<tool_code>")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        xml_buffer = content[idx:]
                        if "</tool_code>" in xml_buffer:
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    elif "<tools>" in content:
                        # Formato minimalista (bug 2026-09-12): <tools><tool_name>
                        # </tool_name></tools>. Se acumula en xml_buffer y se parsea
                        # al cerrar </tools>.
                        idx = content.index("<tools>")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        xml_buffer = content[idx:]
                        if "</tools>" in xml_buffer:
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    elif "<name>" in content:
                        # Formato standalone (bug 2026-09-13): <name>tool_name</name>
                        # <arguments>JSON</arguments>. Se acumula en xml_buffer y se
                        # parsea al cerrar </arguments> o </name> (sin wrapper).
                        idx = content.index("<name>")
                        pre = content[:idx]
                        if pre.strip():
                            yield {"type": "delta", "content": _filter_reasoning_markers(pre)}
                        xml_buffer = content[idx:]
                        if "</arguments>" in xml_buffer or "</name>" in xml_buffer:
                            calls = parse_xml_tool_calls(xml_buffer, known_names=whitelist)
                            if calls:
                                emitted_tool_calls = True
                                yield {"type": "tool_calls", "raw": xml_buffer, "tool_calls": calls}
                            xml_buffer = ""
                    else:
                        # Sin tag completo en este delta: comprobar si el contenido
                        # TERMINA con un fragmento que es prefijo de una apertura de
                        # tag de tool (p.ej. "<tool_code" sin ">"). Si es asi, guardar
                        # el fragmento en xml_pending para concatenarlo con el
                        # siguiente delta (bug 2026-09-13: tags divididos por BPE) y
                        # emitir solo el texto anterior. Si no, delta normal.
                        pending_idx = -1
                        for i in range(len(content) - 1, max(len(content) - 16, -1), -1):
                            suffix = content[i:]
                            if any(op.startswith(suffix) for op in _TOOL_TAG_OPENERS):
                                pending_idx = i
                                break
                        if pending_idx > 0:
                            yield {"type": "delta", "content": _filter_reasoning_markers(content[:pending_idx])}
                            xml_pending = content[pending_idx:]
                        elif pending_idx == 0:
                            xml_pending = content
                        else:
                            yield {"type": "delta", "content": _filter_reasoning_markers(content)}
                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if "id" in tc and tc["id"]:
                            tool_calls_accum[idx]["id"] = tc["id"]
                        if tc.get("function"):
                            if tc["function"].get("name"):
                                tool_calls_accum[idx]["function"]["name"] += tc["function"]["name"]
                            if tc["function"].get("arguments"):
                                tool_calls_accum[idx]["function"]["arguments"] += tc["function"]["arguments"]
            # Al final, si hay tool calls acumuladas (JSON de llama.cpp), emitirlas
            if tool_calls_accum:
                calls = [tool_calls_accum[i] for i in sorted(tool_calls_accum)]
                # Filtrar contra la whitelist (bug 2026-09-14): llama.cpp puede
                # materializar en JSON nativo tool calls que el modelo alucino
                # (nombres que no son tools reales). Ejecutarlas solo produce
                # 'Herramienta X no encontrada'.
                calls = [
                    c for c in calls
                    if LLMClient._is_known_tool_name(
                        c.get("function", {}).get("name", ""), whitelist
                    )
                ]
                emitted_tool_calls = True
                if calls:
                    yield {"type": "tool_calls", "tool_calls": calls}
                else:
                    emitted_tool_calls = False
            # Si quedo XML o [call:...] sin cerrar (incompleto), emitirlo como
            # texto para no perder contenido.
            if xml_buffer:
                yield {"type": "delta", "content": xml_buffer}
            if peg_buffer:
                yield {"type": "delta", "content": peg_buffer}
            if xml_pending:
                yield {"type": "delta", "content": xml_pending}
            # Si el content NO trajo tool calls pero el razonamiento ensayo un
            # tool call completo, emitirlo (bug 2026-09-13: con --reasoning on,
            # Qwen 3.x a veces emite el tool call SOLO en reasoning_content y
            # basura en content). Solo se emite el ULTIMO ensayo: el modelo
            # descarta los anteriores y el ultimo es el que decidio; emitir
            # todos ejecutaria la tool varias veces.
            if not emitted_tool_calls and reasoning_accum:
                reasoning_calls = parse_xml_tool_calls(reasoning_accum, known_names=whitelist)
                if reasoning_calls:
                    # raw = solo el bloque XML del ultimo ensayo (no todo el
                    # razonamiento, que app.py usaria como assistant content).
                    import re as _re
                    matches = list(
                        _re.finditer(
                            r"<tool_calls?>(.*?)</tool_calls?>",
                            reasoning_accum,
                            _re.DOTALL,
                        )
                    )
                    raw = matches[-1].group(0) if matches else reasoning_accum
                    yield {
                        "type": "tool_calls",
                        "raw": raw,
                        "tool_calls": reasoning_calls[-1:],
                    }
        finally:
            r.close()
