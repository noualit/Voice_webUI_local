# tests/test_llm_client.py
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_chat_stream_yields_deltas():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Hola"}}]}',
        b'data: {"choices": [{"delta": {"content": " mundo"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()) as mock_post:
        chunks = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        deltas = [c["content"] for c in chunks if c["type"] == "delta"]
        assert deltas == ["Hola", " mundo"]
        # Verificar stream=True en payload
        payload = mock_post.call_args[1]["json"]
        assert payload["stream"] is True
        assert payload["model"] == "qwen3.6-27b"


def test_chat_stream_detects_tool_calls():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    # Respuesta con tool call
    fake_lines = [
        b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "search_memories", "arguments": "{\\"query\\": \\"test\\"}"}}]}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "search_memories"
        assert calls[0]["function"]["arguments"] == '{"query": "test"}'


def test_chat_stream_raises_on_error():
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    class FakeResp:
        def raise_for_status(self):
            raise Exception("HTTP 500")
        def iter_lines(self):
            return iter([])
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        try:
            list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
            assert False, "Debio lanzar excepcion"
        except Exception as e:
            assert "HTTP 500" in str(e)


def test_payload_no_incluye_parametros_de_generacion():
    """El payload NO debe incluir parametros de generacion (min_p,
    penalties, reasoning...): los controla el servidor llama.cpp via CLI
    (decision 2026-09-12). Unica excepcion: max_tokens (limite duro finito)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    for param in ("min_p", "presence_penalty", "frequency_penalty",
                  "repeat_penalty", "reasoning_format",
                  "reasoning_control", "reasoning_budget_message"):
        assert param not in payload, \
            f"El payload no debe incluir {param} (lo controla el servidor CLI); got {payload.get(param)}"
    assert payload["model"] == "qwen3.6-27b"
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


def test_payload_incluye_max_tokens_finito():
    """max_tokens SI se envia: limite duro FINITO (16384) para evitar que el
    servidor genere sin techo (n_predict=-1) y el slot se cuelgue si el modelo
    no encuentra stop token. La app no impone sampling, solo un tope duro."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    assert payload["max_tokens"] == 16384, \
        f"max_tokens debe ser 16384 (finito, limite duro); got {payload.get('max_tokens')}"


def test_payload_no_incluye_reasoning_budget_message():
    """reasoning_budget_message no se envia por request: el servidor usa su
    --reasoning-budget-message del CLI (decision 2026-09-12)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    assert "reasoning_budget_message" not in payload


def test_payload_incluye_include_usage():
    """El payload debe incluir stream_options.include_usage=true para que llama.cpp
    envie el bloque 'usage' (completion_tokens, prompt_tokens) en el chunk final del
    stream, lo que permite mostrar tokens reales consumidos en el frontend."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    assert payload["stream_options"] == {"include_usage": True}, \
        f"stream_options.include_usage debe ser True; got {payload.get('stream_options')}"


def test_chat_stream_expone_usage_del_chunk_final():
    """chat_stream debe capturar el bloque 'usage' del ultimo chunk (enviado por
    llama.cpp cuando include_usage=true) y exponerlo en un evento 'usage'."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Hola"}}]}',
        # Chunk final con usage (choices vacio, como especifica OpenAI)
        b'data: {"choices": [], "usage": {"prompt_tokens": 44, "completion_tokens": 48, "total_tokens": 92}}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        usage_events = [e for e in events if e["type"] == "usage"]
        assert len(usage_events) == 1, f"Debe haber 1 evento usage, hay {len(usage_events)}"
        assert usage_events[0]["usage"]["completion_tokens"] == 48
        assert usage_events[0]["usage"]["prompt_tokens"] == 44
        assert usage_events[0]["usage"]["total_tokens"] == 92


def test_chat_stream_parsea_tool_call_xml_completo():
    """Cuando llama.cpp NO convierte el XML de tool call a JSON (Qwen genera
    <tool_call><function=...> en texto plano), chat_stream debe parsearlo y
    emitirlo como evento tool_calls en lugar de deltas de texto."""
    import json
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Voy a escanear:\\n"}}]}',
        b'data: {"choices": [{"delta": {"content": "<tool_call>\\n<function=run_command>\\n<parameter=command>\\nping 192.0.2.1\\n</parameter>\\n</function>\\n</tool_call>"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
            events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
            tool_events = [e for e in events if e["type"] == "tool_calls"]
            assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
            calls = tool_events[0]["tool_calls"]
            assert calls[0]["function"]["name"] == "run_command"
            args = json.loads(calls[0]["function"]["arguments"])
            assert args["command"] == "ping 192.0.2.1"
            # El texto de razonamiento previo se emite como delta, pero el XML
            # NO debe aparecer como texto suelto.
            delta_events = [e for e in events if e["type"] == "delta"]
            assert delta_events == [{"type": "delta", "content": "Voy a escanear:\n"}]
            all_content = "".join(e["content"] for e in delta_events)
            assert "<tool_call>" not in all_content, "El XML no debe emitirse como texto"


def test_payload_no_incluye_presence_penalty():
    """El payload NO debe incluir presence_penalty: los penalties los controla el
    servidor llama.cpp via CLI (decision 2026-09-12). La app no impone config de
    generacion."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    assert "presence_penalty" not in payload


def test_payload_no_incluye_frequency_penalty():
    """El payload NO debe incluir frequency_penalty: los penalties los controla el
    servidor llama.cpp via CLI (decision 2026-09-12). La app no impone config de
    generacion."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)
    payload = client._payload([{"role": "user", "content": "hi"}], None, stream=True)
    assert "frequency_penalty" not in payload


def test_chat_stream_parsea_tool_call_xml_dividido_en_deltas():
    """El XML puede llegar dividido en multiples deltas de contenido; el parser
    debe acumularlos hasta cerrar </tool_call> y luego emitir el tool call."""
    import json
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "<tool_call>"}}]}',
        b'data: {"choices": [{"delta": {"content": "\\n<function=web_search>"}}]}',
        b'data: {"choices": [{"delta": {"content": "\\n<parameter=query>\\nultimas noticias\\n</parameter>"}}]}',
        b'data: {"choices": [{"delta": {"content": "\\n</function>\\n</tool_call>"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "web_search"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["query"] == "ultimas noticias"


def test_chat_stream_emite_raw_xml_en_tool_calls():
    """El evento tool_calls del XML debe incluir el XML crudo ('raw').

    La app necesita el XML original para construir la continuacion del chat en
    texto plano (assistant content = XML), porque llama.cpp NO puede procesar
    role:tool sin payload["tools"] (la grammar PEG rompe con Qwen 3.x). Sin raw,
    la app no puede reconstruir la continuacion y el servidor crashea
    (ChunkedEncodingError, bug 2026-09-08)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    xml = "<tool_call>\n<function=create_conversation>\n<parameter=name>\nTema\n</parameter>\n</function>\n</tool_call>"
    chunk = {"choices": [{"delta": {"content": xml}}]}
    fake_lines = [
        b"data: " + json.dumps(chunk).encode("utf-8"),
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1
        assert tool_events[0]["raw"] == xml


def test_chat_stream_emite_raw_xml_cuando_xml_cerrado_en_mismo_delta():
    """raw tambien se emite cuando el XML llega completo en un solo delta (no
    acumulado en buffer)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    xml = "<tool_call>\n<function=web_search>\n<parameter=query>\nnoticias\n</parameter>\n</function>\n</tool_call>"
    chunk = {"choices": [{"delta": {"content": xml}}]}
    fake_lines = [
        b"data: " + json.dumps(chunk).encode("utf-8"),
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1
        assert tool_events[0]["raw"] == xml


def test_parse_peg_native_call_extrae_tool_y_args():
    """El parser debe entender el formato peg-native de llama.cpp:
    [call:URI|JSON]. El nombre de la tool sale del ultimo segmento del URI y los
    argumentos de la clave 'params' del JSON (bug 2026-09-12: el modelo genera
    las tool calls en este formato y la app no las reconocia)."""
    from llm_client import LLMClient
    calls = LLMClient._parse_peg_native_call(
        '[call:diamcp://context/dia.get_timezone|{"params":{"timezone":"Europa/Madrid"}}]'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "dia.get_timezone"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europa/Madrid"}


def test_parse_peg_native_call_sin_params_usa_json_completo():
    """Si el JSON del [call:...] no tiene clave 'params', los argumentos son el
    JSON completo."""
    from llm_client import LLMClient
    calls = LLMClient._parse_peg_native_call('[call:web_search|{"query":"llama"}]')
    assert calls is not None
    assert calls[0]["function"]["name"] == "web_search"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"query": "llama"}


def test_parse_peg_native_call_con_array_en_args():
    """El JSON de argumentos puede contener corchetes internos (arrays); el
    parser debe encontrar el cierre correcto del [call:...]."""
    from llm_client import LLMClient
    calls = LLMClient._parse_peg_native_call(
        '[call:list_users|{"params":{"ous":["OU1","OU2"]}}]'
    )
    assert calls is not None
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"ous": ["OU1", "OU2"]}


def test_parse_peg_native_call_devuelve_none_sin_match():
    """Sin [call:...] completo no devuelve nada (texto normal o call incompleto)."""
    from llm_client import LLMClient
    assert LLMClient._parse_peg_native_call("texto normal sin calls") is None
    assert LLMClient._parse_peg_native_call("[call:incompleto") is None


def test_chat_stream_detecta_tool_call_peg_native():
    """Cuando llama.cpp emite la tool call en formato peg-native ([call:...]),
    chat_stream debe parsearla y emitirla como evento tool_calls con raw, igual
    que con el XML (bug 2026-09-12: el modelo Qwen 3.6 genera las calls en este
    formato y la app las mostraba como texto)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '[call:diamcp://context/dia.get_timezone|{"params":{"timezone":"Europa/Madrid"}}]'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Voy a consultar:\\n"}}]}',
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "dia.get_timezone"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europa/Madrid"}
        # El texto previo se emite como delta y el [call:...] NO como texto suelto
        delta_events = [e for e in events if e["type"] == "delta"]
        assert delta_events == [{"type": "delta", "content": "Voy a consultar:\n"}]
        all_content = "".join(e["content"] for e in delta_events)
        assert "[call:" not in all_content, "El [call:...] no debe emitirse como texto"


def test_chat_stream_detecta_tool_call_peg_dividido_en_deltas():
    """El [call:...] puede llegar dividido en multiples deltas; chat_stream debe
    acumularlos hasta completar el JSON y emitir el tool call."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "[call:diamcp://context/dia."}}]}',
        b'data: {"choices": [{"delta": {"content": "get_timezone|{\\"params\\":{\\"timezone\\":\\"Europa/Madrid\\"}}]"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "dia.get_timezone"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europa/Madrid"}


def test_parse_xml_tool_calls_formato_json_in_xml():
    """Qwen 3.6 tambien genera el XML con JSON dentro en lugar de
    <function=...> (bug 2026-09-12): <tool_call>{"name": "get_time",
    arguments: {"timezone":"Europe/Madrid"}}</tool_calls>. El parser debe
    extraer name y arguments de ese formato."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_call>{"name": "get_time", arguments: {"timezone":"Europe/Madrid"}}</tool_calls>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_json_in_xml_con_apertura_tool_calls():
    """La apertura puede ser <tool_calls> (con s) igual que el cierre
    </tool_calls> (bug 2026-09-13: el modelo genero <tool_calls> con JSON y la
    app lo mostro como texto porque el regex de apertura solo aceptaba
    <tool_call>)."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_calls>{"name": "get_time", arguments: {"timezone":"Europe/Madrid"}}</tool_calls>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_json_in_xml_con_saltos_de_linea():
    """El JSON dentro de <tool_call> puede venir formateado con saltos de
    linea (bug 2026-09-13: el modelo genero <tool_call>\n{\n  "name":
    "dia.get_time",\n  "arguments": {}\n}\n</tool_call>)."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_call>\n{\n  "name": "dia.get_time",\n  "arguments": {}\n}\n</tool_call>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "dia.get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {}


def test_chat_stream_detecta_tool_call_xml_json_in_xml():
    """chat_stream debe emitir como tool_calls el XML con JSON dentro
    (<tool_call>{"name": ...}...</tool_calls>), no mostrarlo como texto."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool_call>{"name": "get_time", arguments: {"timezone":"Europe/Madrid"}}</tool_calls>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europe/Madrid"}


def test_chat_stream_detecta_tool_call_xml_tool_calls_con_s_en_apertura():
    """chat_stream debe detectar la apertura <tool_calls> (con s) igual que
    <tool_call> (bug 2026-09-13: el modelo genero <tool_calls> con JSON dentro
    y la app lo mostro como texto porque el check solo buscaba <tool_call>)."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool_calls>{"name": "dia.get_time", "arguments": {}}</tool_calls>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "dia.get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {}


def test_parse_xml_tool_calls_formato_tool_name_atributo():
    """Formato MCP canonico con <tool name="..."> y JSON como contenido
    (bug 2026-09-12, modelo ahora genera esto al pedir fecha/hora):
    <tool name="dia.get_time">{ "timezone": "Europe/Madrid" }</tool>
    Sin </tool_calls>, sin <parameters>, sin wrapper <tools>."""
    from llm_client import LLMClient
    text = '<tool name="dia.get_time">{ "timezone": "Europe/Madrid" }</tool>'
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "dia.get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_tools_contenedor():
    """El modelo envuelve las tools en <tools><tool name=...>...</tool></tools>.
    El parser debe capturar la tool incluso sin </tool_calls> wrapper
    (bug 2026-09-12). Ejemplo real del usuario:."""
    from llm_client import LLMClient
    text = """<tool_call>
{
  "name": "create_conversation",
  "arguments": {
    "name": "Consulta de fecha actual"
  }
}
</tool_call>

<tools>
  <tool name="dia.get_time">
    { "timezone": "Europe/Madrid" }
  </tool>
</tools>"""
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    # Debe capturar AMBOS: el JSON-in-XML Y el <tool name=...>
    names = [c["function"]["name"] for c in calls]
    assert "create_conversation" in names
    assert "dia.get_time" in names
    # Verificar args del tool name
    tc = next(c for c in calls if c["function"]["name"] == "dia.get_time")
    args = json.loads(tc["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_chat_stream_detecta_tool_call_xml_tool_name():
    """chat_stream debe emitir tool_calls cuando el modelo genera formato
    <tool name="...">JSON</tool> sin </tool_calls> wrapper."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool name="get_time">{ "timezone": "Europe/Madrid" }</tool>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]
    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_tool_code():
    """El modelo a veces escribe codigo para llamar tools (bug 2026-09-12):
    <tool_code>print(dia.get_time())</tool_code>. Parsear SOLO el patron seguro
    (llamada directa sin args o con kwargs literales)."""
    from llm_client import LLMClient
    text = '<tools>\n<tool_code>\nprint(dia.get_time())\n</tool_code>\n</tools>'
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "dia.get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {}


def test_parse_xml_tool_calls_formato_tool_code_con_kwargs():
    """Variante con kwargs literales: print(dia.get_time(timezone="Europe/Madrid"))
    -> args {"timezone": "Europe/Madrid"}."""
    from llm_client import LLMClient
    text = '<tool_code>\nprint(dia.get_time(timezone="Europe/Madrid"))\n</tool_code>'
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert calls[0]["function"]["name"] == "dia.get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_tool_code_complejo_no_ejecuta():
    """FAIL-SAFE: codigo no trivial (multi-statement, posicionales, variables)
    NO debe generar tool_call: no se puede mapear a args MCP de forma fiable y
    evaluar codigo del modelo es un riesgo."""
    from llm_client import LLMClient
    text = '<tool_code>\nresult = dia.web_search("noticias")\nprint(result)\n</tool_code>'
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is None
    # Posicionales tampoco: no sabemos que parametro es cada posicion
    text2 = '<tool_code>\nprint(dia.web_search("noticias"))\n</tool_code>'
    calls2 = LLMClient._parse_xml_tool_calls(text2)
    assert calls2 is None


def test_chat_stream_detecta_tool_call_xml_tool_code():
    """chat_stream debe emitir tool_calls cuando el modelo genera
    <tool_code>print(dia.get_time())</tool_code>."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool_code>\nprint(dia.get_time())\n</tool_code>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]
    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "dia.get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {}


def test_chat_stream_detecta_tool_call_xml_tool_code_dividido_en_deltas():
    """BUG (2026-09-13): Qwen 3.6 genero '<tool_code>memoria.create_conversation(
    name="Consulta fecha actual")</tool_code>' pero la app lo mostro como texto
    crudo. Causa: con tokenizacion BPE el tag de apertura llega DIVIDIDO entre
    deltas (p.ej. '<tool_code' en uno y '>memoria...' en el siguiente), y
    ninguno contiene '<tool_code>' completo, asi que el check de deteccion no
    dispara y el contenido cae en la rama delta normal. chat_stream debe
    acumular fragmentos pendientes de apertura de tag y detectar el tag cuando
    se completa."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "<tool_code"}}]}',
        b'data: {"choices": [{"delta": {"content": ">memoria.create_conversation(name=\\"Consulta fecha actual\\")</tool_code>"}}]}',
        b'data: [DONE]',
    ]
    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, (
            f"Debe haber 1 tool_calls (tag dividido entre deltas), hay {len(tool_events)}; "
            f"eventos: {events}"
        )
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "memoria.create_conversation"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"name": "Consulta fecha actual"}


def test_parse_xml_tool_calls_formato_nombre_contenido_y_parameters():
    """Qwen 3.6 tambien genera el nombre como contenido de la etiqueta con JSON
    en <parameters> (bug 2026-09-12):
    <tool_call>dia.get_time</tool_call><parameters>{"timezone":"Europe/Madrid"}
    </parameters></function_calls>."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_call>dia.get_time</tool_call><parameters>{"timezone": "Europe/Madrid"}</parameters></function_calls>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "dia.get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_mcp_canonico():
    """Formato MCP canonico de Qwen con <name> y <parameters>:
    <tool_call><name>get_time</name><parameters>{...}</parameters></tool_call>."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_calls><tool_call><name>get_time</name><parameters>{"timezone":"Europe/Madrid"}</parameters></tool_call></tool_calls>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_chat_stream_detecta_tool_call_xml_nombre_contenido():
    """chat_stream debe emitir como tool_calls el formato nombre-como-contenido
    + <parameters>, no mostrarlo como texto."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool_call>dia.get_time</tool_call><parameters>{"timezone": "Europe/Madrid"}</parameters></function_calls>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "dia.get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_formato_call_tool_nativo():
    """Formato nativo de Qwen 3.6 (bug 2026-09-12):
    <tool_call>call_tool("get_system_info", {})</tool_call><tool_calls></tool_calls>.
    El nombre y los args van dentro de call_tool(...), no en XML."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls(
        '<tool_call>call_tool("get_system_info", {"timezone": "Europe/Madrid"})</tool_call><tool_calls></tool_calls>'
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_system_info"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_chat_stream_detecta_tool_call_xml_call_tool_nativo():
    """chat_stream debe emitir como tool_calls el formato call_tool(...) nativo,
    no mostrarlo como texto."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = '<tool_call>call_tool("get_system_info", {"timezone": "Europe/Madrid"})</tool_call><tool_calls></tool_calls>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 evento tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "get_system_info"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_json_in_xml_con_comillas_en_arguments():
    """Qwen 3.6 genera el JSON-in-XML con "arguments": CON comillas (JSON valido
    completo), p. ej. al pedir la fecha: {"name": "get_time", "arguments":
    {"timezone": "Europe/Madrid"}}. El parser debe extraer los args (bug
    2026-09-12: el regex solo buscaba 'arguments:' sin comillas y devolvia {})."""
    from llm_client import LLMClient
    text = """<tool_call>
{
  "name": "get_time",
  "arguments": {
    "timezone": "Europe/Madrid"
  }
}
</tool_call>"""
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_multiple_json_in_xml_con_comillas():
    """Varios tool_calls JSON-in-XML seguidos (create_conversation + get_time),
    cada uno con "arguments": con comillas. Ambos deben extraer sus args."""
    from llm_client import LLMClient
    text = """<tool_call>
{
  "name": "create_conversation",
  "arguments": {
    "name": "Consulta de fecha actual"
  }
}
</tool_call>

<tool_call>
{
  "name": "get_time",
  "arguments": {
    "timezone": "Europe/Madrid"
  }
}
</tool_call>"""
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "create_conversation"
    args0 = json.loads(calls[0]["function"]["arguments"])
    assert args0 == {"name": "Consulta de fecha actual"}
    assert calls[1]["function"]["name"] == "get_time"
    args1 = json.loads(calls[1]["function"]["arguments"])
    assert args1 == {"timezone": "Europe/Madrid"}


def test_format_tools_prompt_incluye_tools_y_instrucciones_xml():
    """format_tools_prompt genera el bloque de tools para inyectar en el system prompt.

    El modelo debe conocer las tools disponibles (name, description, parameters)
    y el formato <tool_call> XML que la app parsea, ya que las tools NO se envian
    en el payload (evita la grammar PEG de llama.cpp que rompe con el thinking
    de Qwen 3.x).
    """
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {
            "name": "list_users",
            "description": "List users in Active Directory",
            "parameters": {"type": "object", "properties": {"ou": {"type": "string"}}},
        },
        "_mcp_server": "AD",
    }]
    prompt = format_tools_prompt(tools)
    # Contiene el bloque del servidor AD
    assert "## AD" in prompt
    # Contiene la descripcion de la tool en formato markdown
    assert "**list_users**" in prompt
    assert "List users in Active Directory" in prompt
    # Contiene los parametros formateados
    assert "ou" in prompt
    # Sin tools no genera bloque
    assert format_tools_prompt([]) == ""


def test_format_tools_prompt_agrupa_por_servidor():
    """Tools de servidores distintos generan bloques separados."""
    from llm_client import format_tools_prompt
    tools = [
        {
            "type": "function",
            "function": {"name": "list_users", "description": "AD users",
                         "parameters": {"type": "object", "properties": {}}},
            "_mcp_server": "AD",
        },
        {
            "type": "function",
            "function": {"name": "search_memories", "description": "Busca memorias",
                         "parameters": {"type": "object", "properties": {}}},
            "_mcp_server": "memoria",
        },
    ]
    prompt = format_tools_prompt(tools)
    assert "## AD" in prompt
    assert "## memoria" in prompt
    assert "**list_users**" in prompt
    assert "**search_memories**" in prompt
    assert "AD users" in prompt
    assert "Busca memorias" in prompt


def test_format_tools_prompt_usa_prompt_especifico_por_servidor():
    """mcp_prompts sobreescribe el prompt por defecto de cada servidor."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {"name": "list_users", "description": "AD users",
                     "parameters": {"type": "object", "properties": {}}},
        "_mcp_server": "AD",
    }]
    prompt = format_tools_prompt(tools, mcp_prompts={"AD": "PARA AD: consulta el directorio."})
    assert "PARA AD: consulta el directorio." in prompt
    # El prompt por defecto de AD no debe aparecer
    assert "Active Directory" not in prompt


def test_format_tools_prompt_fallback_sin_prompt_definido():
    """Servidor sin prompt en mcp_prompts usa el prompt por defecto."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {"name": "list_users", "description": "AD users",
                     "parameters": {"type": "object", "properties": {}}},
        "_mcp_server": "AD",
    }]
    prompt = format_tools_prompt(tools, mcp_prompts={})
    # Sin mcp_prompts definido, usa el default del servidor AD
    assert "Instrucciones para las herramientas de AD:" in prompt


def test_format_tools_prompt_servidor_sin_prompt_default():
    """Servidor desconocido sin prompt definido usa un fallback generico."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {"name": "foo", "description": "Foo",
                     "parameters": {"type": "object", "properties": {}}},
        "_mcp_server": "desconocido",
    }]
    prompt = format_tools_prompt(tools, mcp_prompts={})
    assert "## desconocido" in prompt
    assert "**foo**" in prompt
    assert "Foo" in prompt


def test_format_tools_prompt_ejemplo_es_parseable_por_parse_xml():
    """El formato XML ensenado en el prompt debe ser parseable por _parse_xml_tool_calls
    (consistencia entre lo que se le pide al modelo y lo que la app interpreta)."""
    from llm_client import LLMClient, format_tools_prompt
    tools = [{
        "type": "function",
        "function": {
            "name": "search_memories",
            "description": "Busca memorias",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    prompt = format_tools_prompt(tools)
    # Simula que el modelo repite el ejemplo del prompt
    calls = LLMClient._parse_xml_tool_calls(
        "<tool_call>\n<function=search_memories>\n<parameter=query>\ntemas previos\n</parameter>\n</function>\n</tool_call>"
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "search_memories"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["query"] == "temas previos"


def test_parse_xml_tool_calls_formato_minimalista():
    """Formato minimalista sin parametros: <tools><get_time></tools>.
    El modelo Qwen 3.6 lo genera cuando la tool no requiere argumentos
    (bug 2026-09-12: antes salia como texto plano en la respuesta)."""
    from llm_client import LLMClient
    text = "<tools><get_time></tools>"
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {}


def test_parse_xml_tool_calls_formato_minimalista_multiples_tools():
    """Varias tools en formato minimalista: <tools><tool1></tool1><tool2>
    </tool2></tools>. Ambas deben extraerse."""
    from llm_client import LLMClient
    text = "<tools><create_conversation></create_conversation><get_time></get_time></tools>"
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert len(calls) == 2
    names = [c["function"]["name"] for c in calls]
    assert "create_conversation" in names
    assert "get_time" in names


def test_chat_stream_detecta_formato_minimalista():
    """chat_stream debe emitir tool_calls para el formato minimalista
    <tools><get_time></tools>, no mostrarlo como texto."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    raw = "<tools><get_time></tools>"
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]
    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls, hay {len(tool_events)}"
        assert tool_events[0]["raw"] == raw
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {}
        # El formato minimalista NO debe aparecer como texto en los deltas
        delta_events = [e for e in events if e["type"] == "delta"]
        all_content = "".join(e["content"] for e in delta_events)
        assert "<tools>" not in all_content, "El XML minimalista no debe emitirse como texto"


def test_chat_stream_detecta_formato_minimalista_dividido_en_deltas():
    """El formato minimalista puede llegar dividido en multiples deltas;
    chat_stream debe acumular hasta </tools> y luego emitir tool_calls."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "<tools>"}}]}',
        b'data: {"choices": [{"delta": {"content": "<get_time>"}}]}',
        b'data: {"choices": [{"delta": {"content": "</get_time></tools>"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls, hay {len(tool_events)}"
        calls = tool_events[0]["tool_calls"]
        assert calls[0]["function"]["name"] == "get_time"


def test_chat_stream_detecta_tool_call_en_reasoning_content():
    """Bug 2026-09-13: con --reasoning on, Qwen 3.x a veces ensaya el tool call
    SOLO en reasoning_content y emite '>' en content (marcador de blockquote del
    pensamiento). El fix: filtrar '>' del content en chat_stream y, si el content
    no trajo tool calls pero el reasoning contiene un tool call completo, emitirlo
    igualmente. El '>' NO debe llegar al usuario."""
    import json
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    # reasoning_content trae el ensayo del tool call; content trae solo '>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"role": "assistant", "content": null}}]}',
        b'data: {"choices": [{"delta": {"reasoning_content": "The user asks the date.\\n<tool_call>{\\"name\\": \\"dia.get_time\\", \\"arguments\\": {}}</tool_call>\\nWait, let me use MCP format.\\n<tool_call><name>dia.get_time</name><parameters>{}</parameters></tool_call>"}}]}',
        b'data: {"choices": [{"delta": {"content": ">"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls, hay {len(tool_events)}"
        calls = tool_events[0]["tool_calls"]
        # Solo el ULTIMO ensayo del reasoning (el que el modelo decidio)
        assert len(calls) == 1, f"Debe haber 1 call (solo el ultimo ensayo), hay {len(calls)}"
        assert calls[0]["function"]["name"] == "dia.get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {}
        # El marcador '>' se filtra: NO se emite como delta (fix bug 2026-09-13)
        delta_events = [e for e in events if e["type"] == "delta"]
        all_content = "".join(e["content"] for e in delta_events)
        assert ">" not in all_content, "El marcador '>' del reasoning no debe emitirse"
        # El XML del reasoning NO debe emitirse como texto
        assert "<tool_call>" not in all_content, "El XML del reasoning no debe emitirse como texto"


def test_chat_stream_no_duplica_tool_call_cuando_content_trae_xml():
    """Si el content SI trae el tool call (caso normal), el ensayo del reasoning
    NO debe generar un segundo tool_calls: se ejecutaria la tool dos veces."""
    import json
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"role": "assistant", "content": null}}]}',
        b'data: {"choices": [{"delta": {"reasoning_content": "I will call get_time.\\n<tool_call><name>dia.get_time</name><parameters>{}</parameters></tool_call>"}}]}',
        b'data: {"choices": [{"delta": {"content": "<tool_call><name>dia.get_time</name><parameters>{}</parameters></tool_call>"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 1, f"Debe haber 1 tool_calls (no duplicado), hay {len(tool_events)}"
        calls = tool_events[0]["tool_calls"]
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "dia.get_time"


def test_chat_stream_reasoning_sin_tool_call_fluye_normal():
    """Si el reasoning no contiene tool calls y el content es texto normal, todo
    fluye como hasta ahora: solo deltas, sin tool_calls."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"role": "assistant", "content": null}}]}',
        b'data: {"choices": [{"delta": {"reasoning_content": "The user said >. I should ask for clarification."}}]}',
        b'data: {"choices": [{"delta": {"content": "Hola, en que puedo ayudarte?"}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 0, f"No debe haber tool_calls, hay {len(tool_events)}"
        delta_events = [e for e in events if e["type"] == "delta"]
        all_content = "".join(e["content"] for e in delta_events)
        assert "Hola, en que puedo ayudarte?" in all_content


def test_parse_xml_tool_calls_no_inventa_tool_call_fantasma():
    """Bug 2026-09-14: XML malformado con <tool_call> anidado en <tools> y cierre
    </tool> (en vez de </tool_call>) hacia que el parser minimalista convirtiera
    los tags estructurales <tool_call>/<name>/<arguments> en tool calls fantasma
    ('Herramienta tool_call no encontrada' en la app). Los tags estructurales NO
    son tools: el parser solo debe emitir la tool legitima (dia.get_time), nunca
    tool_call/name/arguments como herramientas."""
    from llm_client import LLMClient
    text = """<tools>
<tool_call>
<name>dia.get_time</name>
<arguments>{}</arguments>
</tool>
</tools>"""
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None, "Debe recuperar la tool legitima del XML malformado"
    names = [c["function"]["name"] for c in calls]
    assert "dia.get_time" in names, f"La tool legitima debe estar; got {names}"
    for fantasma in ("tool_call", "name", "arguments"):
        assert fantasma not in names, f"Tag estructural {fantasma!r} no debe ser una tool; got {names}"


def test_parse_xml_tool_calls_minimalista_solo_nombres_conocidos():
    """El formato minimalista <tools><tag></tools> SOLO debe generar tool calls
    para nombres de tools conocidas, no para cualquier tag XML (bug 2026-09-14:
    <tool_call>, <arguments> se convertian en tools fantasma)."""
    from llm_client import LLMClient
    # Tags estructurales no pueden ser tools
    assert LLMClient._parse_xml_tool_calls("<tools><tool_call></tools>") is None
    assert LLMClient._parse_xml_tool_calls("<tools><arguments></tools>") is None
    assert LLMClient._parse_xml_tool_calls("<tools><name></tools>") is None
    # Tool real sin args sigue funcionando
    calls = LLMClient._parse_xml_tool_calls("<tools><get_time></tools>")
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {}


def test_parse_xml_tool_calls_minimalista_con_prefijo_servidor():
    """El formato minimalista acepta el prefijo de servidor (dia.get_time)
    resolviendo el nombre base (get_time) contra la whitelist de tools."""
    from llm_client import LLMClient
    calls = LLMClient._parse_xml_tool_calls("<tools><dia.get_time></tools>")
    assert calls is not None
    assert calls[0]["function"]["name"] == "dia.get_time"


def test_parse_xml_tool_calls_nombre_contenido_invalido_no_genera_call():
    """Bug 2026-09-14: <tool_call><arguments></arguments></tool_call> (contenido
    que NO es un nombre de tool) hacia que el parser usara TODO el bloque como
    nombre de la herramienta. El bloque debe ser un nombre de tool conocido;
    si no, no se genera ninguna call."""
    from llm_client import LLMClient
    text = "<tool_call><arguments></arguments></tool_call>"
    assert LLMClient._parse_xml_tool_calls(text) is None, (
        "El bloque XML no es un nombre de tool valido; no debe generar call"
    )


def test_parse_xml_tool_calls_valida_contra_whitelist_dinamica():
    """Bug 2026-09-14: el modelo alucina nombres con prefijos inventados
    (mcp_memory_search_memories, mcp_, query, limit) que NO estan en la whitelist
    estatica. El parser debe aceptar una whitelist DINAMICA (las tools reales
    disponibles) y descartar cualquier nombre que no este en ella, en TODOS los
    formatos."""
    from llm_client import LLMClient
    whitelist = {"search_memories", "get_time"}
    # Formato principal <function=...> con nombre fantasma -> descartado
    text = ("<tool_call>\n<function=mcp_memory_search_memories>\n"
            "<parameter=query>\ntest\n</parameter>\n</function>\n</tool_call>")
    assert LLMClient._parse_xml_tool_calls(text, known_names=whitelist) is None, (
        "mcp_memory_search_memories no esta en la whitelist; debe descartarse"
    )
    # El mismo formato con tool real -> si genera call
    text_ok = ("<tool_call>\n<function=search_memories>\n"
               "<parameter=query>\ntest\n</parameter>\n</function>\n</tool_call>")
    calls = LLMClient._parse_xml_tool_calls(text_ok, known_names=whitelist)
    assert calls is not None
    assert calls[0]["function"]["name"] == "search_memories"


def test_parse_xml_tool_calls_todos_los_formatos_validan_whitelist():
    """Bug 2026-09-14: los formatos JSON-in-XML, <tool name=...> y tool_code
    dejaban pasar nombres fantasma (mcp_, query). Con whitelist dinamica deben
    descartarse; con whitelist que los incluye deben funcionar."""
    from llm_client import LLMClient
    whitelist = {"search_memories", "get_time"}
    # JSON-in-XML con nombre fantasma
    assert LLMClient._parse_xml_tool_calls(
        '<tool_call>{"name": "mcp_", "arguments": {}}</tool_call>',
        known_names=whitelist,
    ) is None
    # <tool name=...> con nombre fantasma
    assert LLMClient._parse_xml_tool_calls(
        '<tool name="query">{}</tool>', known_names=whitelist
    ) is None
    # tool_code con nombre fantasma
    assert LLMClient._parse_xml_tool_calls(
        "<tool_code>print(mcp_memory_search_memories())</tool_code>",
        known_names=whitelist,
    ) is None
    # Los mismos formatos con tool real -> si generan call
    assert LLMClient._parse_xml_tool_calls(
        '<tool name="get_time">{}</tool>', known_names=whitelist
    ) is not None
    assert LLMClient._parse_xml_tool_calls(
        "<tool_code>print(get_time())</tool_code>", known_names=whitelist
    ) is not None


def test_parse_peg_native_call_valida_whitelist():
    """Bug 2026-09-14: el formato peg-native [call:URI|JSON] dejaba pasar
    nombres fantasma (mcp_memory_search_memories). Con whitelist dinamica debe
    descartarse; con tool real debe funcionar."""
    from llm_client import LLMClient
    whitelist = {"search_memories", "get_time"}
    assert LLMClient._parse_peg_native_call(
        "[call:mcp_memory_search_memories|{}]", known_names=whitelist
    ) is None
    calls = LLMClient._parse_peg_native_call(
        '[call:search_memories|{"params":{"query":"x"}}]', known_names=whitelist
    )
    assert calls is not None
    assert calls[0]["function"]["name"] == "search_memories"


def test_chat_stream_filtra_tool_calls_nativos_fantasma():
    """Bug 2026-09-14: los tool_calls JSON nativos de llama.cpp (delta.tool_calls)
    NO pasan por el parser XML; con known_tool_names deben filtrarse los nombres
    que no corresponden a tools reales."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)
    whitelist = {"search_memories"}

    fake_lines = [
        b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "mcp_memory_search_memories", "arguments": "{}"}}]}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream(
            [{"role": "user", "content": "hi"}], {}, known_tool_names=whitelist
        ))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 0, (
            f"La tool fantasma debe filtrarse; got {tool_events}"
        )


def test_chat_stream_filtra_xml_fantasma_con_whitelist():
    """Bug 2026-09-14: chat_stream debe pasar known_tool_names al parser XML
    para descartar tool calls fantasma generadas como texto plano."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)
    whitelist = {"search_memories"}

    raw = "<tool_call>\n<function=mcp_memory_search_memories>\n<parameter=query>\ntest\n</parameter>\n</function>\n</tool_call>"
    fake_lines = [
        b'data: {"choices": [{"delta": {"content": ' + json.dumps(raw).encode("utf-8") + b'}}]}',
        b'data: [DONE]',
    ]

    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()):
        events = list(client.chat_stream(
            [{"role": "user", "content": "hi"}], {}, known_tool_names=whitelist
        ))
        tool_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(tool_events) == 0, (
            f"El XML fantasma debe descartarse; got {tool_events}"
        )


def test_parse_xml_tool_calls_formato_function_tag_estandar():
    """Bug 2026-09-15: el modelo genera <function>get_time</function> (SIN '=')
    con <arguments><timezone>valor</timezone></arguments> (XML anidado, no JSON).
    El parser solo reconocia <function=nombre> y <arguments>JSON</arguments>:
    las tool calls eran invisibles -> respuestas vacias y loops."""
    from llm_client import LLMClient
    text = ("<tool_call>\n<function>get_time</function>\n<arguments>\n"
            "<timezone>Europe/Madrid</timezone>\n</arguments>\n</tool_call>")
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None, "El formato <function>nombre</function> debe parsearse"
    assert calls[0]["function"]["name"] == "get_time"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": "Europe/Madrid"}


def test_parse_xml_tool_calls_function_tag_arguments_vacios():
    """Bug 2026-09-15: <timezone></timezone> (valor vacio) debe convertirse en
    {"timezone": ""}, no romper el parseo."""
    from llm_client import LLMClient
    text = ("<tool_call>\n<function>get_time</function>\n<arguments>\n"
            "<timezone></timezone>\n</arguments>\n</tool_call>")
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"timezone": ""}


def test_parse_xml_tool_calls_function_tag_sin_arguments():
    """Bug 2026-09-15: <function>get_time</function> sin bloque <arguments>
    debe generar la call con args vacios."""
    from llm_client import LLMClient
    text = "<tool_call>\n<function>get_time</function>\n</tool_call>"
    calls = LLMClient._parse_xml_tool_calls(text)
    assert calls is not None
    assert calls[0]["function"]["name"] == "get_time"
    assert json.loads(calls[0]["function"]["arguments"]) == {}


def test_format_tools_prompt_incluye_formato_xml_exacto():
    """Bug 2026-09-15: el system prompt de tools NO mostraba el formato XML de
    llamada; Qwen 3.6 adivinaba <function>nombre</function> (sin '=') y las
    tool calls eran invisibles. format_tools_prompt debe incluir el ejemplo
    exacto con <function=...> y <parameter=...>."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Obtiene la hora",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string", "description": "Zona horaria"}},
            },
        },
        "_mcp_server": "dia",
    }]
    out = format_tools_prompt(tools, {})
    assert "<function=" in out, "El ejemplo debe usar <function=nombre>"
    assert "<parameter=" in out, "El ejemplo debe usar <parameter=arg>"
    assert "</tool_call>" in out, "El ejemplo debe mostrar el cierre del bloque"


def test_format_tools_prompt_ejemplo_usa_tool_real_no_placeholders():
    """Bug 2026-09-24: el ejemplo de formato con placeholders
    (nombre_de_la_herramienta, arg1) hacia que Qwen 3.6 confundiera el nombre
    del servidor MCP ('dia') con el nombre de la tool y emitiera
    <function=dia> en ~50% de los intentos (verificado con el prompt exacto
    de la app, 6979 tokens). El parser descarta 'dia' (whitelist) -> tool
    call perdida -> respuesta vacia. El ejemplo debe usar una tool REAL del
    prompt (get_time) para que el modelo imite el formato correcto
    (verificado 3/3 con get_time + timezone Europe/Madrid)."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Obtiene la hora",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string", "description": "Zona horaria"}},
            },
        },
        "_mcp_server": "dia",
    }]
    out = format_tools_prompt(tools, {})
    assert "nombre_de_la_herramienta" not in out, "El ejemplo NO debe usar placeholders"
    assert "arg1" not in out, "El ejemplo NO debe usar placeholders de parametro"
    assert "<function=get_time>" in out, "El ejemplo debe usar una tool real del prompt"
    assert "<parameter=timezone>" in out, "El ejemplo debe usar un parametro real de la tool"
    assert "NUNCA uses el nombre del servidor" in out, "Debe advertir contra el prefijo del servidor"


def test_format_tools_prompt_ejemplo_fallback_sin_get_time():
    """Si get_time no esta entre las tools, el ejemplo usa la primera tool
    real disponible (nombre real, no placeholders) para no inducir llamadas
    a get_time inexistente."""
    from llm_client import format_tools_prompt
    tools = [{
        "type": "function",
        "function": {
            "name": "list_users",
            "description": "Lista usuarios",
            "parameters": {
                "type": "object",
                "properties": {"ou": {"type": "string", "description": "Unidad organizativa"}},
            },
        },
        "_mcp_server": "AD",
    }]
    out = format_tools_prompt(tools, {})
    assert "nombre_de_la_herramienta" not in out, "El ejemplo NO debe usar placeholders"
    assert "<function=list_users>" in out, "El ejemplo debe usar la primera tool real"
    assert "<parameter=ou>" in out, "El ejemplo debe usar un parametro real de la tool"


def test_estimate_prompt_tokens_vacio():
    """estimate_prompt_tokens debe devolver 0 con una lista de mensajes vacia."""
    from llm_client import estimate_prompt_tokens
    assert estimate_prompt_tokens([]) == 0


def test_estimate_prompt_tokens_cuenta_caracteres():
    """estimate_prompt_tokens debe estimar tokens proporcionales al tamano del
    JSON serializado (chars/3, CONSERVADOR a proposito: el JSON real tokeniza
    ~3.88 chars/token medido en el bug 2026-09-24, asi que chars/3 sobre-estima
    un ~25% y el guard corta antes del 400 'exceeds the available context size')."""
    from llm_client import estimate_prompt_tokens
    msgs = [{"role": "user", "content": "abcd"}]
    est = estimate_prompt_tokens(msgs)
    assert est > 0, "Un mensaje no vacio debe estimar > 0 tokens"
    # Proporcionalidad lineal: doble contenido -> estimado mayor
    msgs2 = [{"role": "user", "content": "abcd" * 2}]
    assert estimate_prompt_tokens(msgs2) > est
    # Conservadora: nunca estima por debajo de chars/4 (el real es ~3.88)
    total_chars = len('{"role": "user", "content": "abcd"}')
    assert est >= total_chars // 4, "La estimacion debe sobre-estimar (defensa)"


def test_estimate_prompt_tokens_incluye_tools_del_payload():
    """El guard estima messages + tools: las tools viajan en el payload JSON y
    tambien consumen contexto en llama.cpp (el system prompt de texto de la app
    con 90 tools son ~7000 tokens; hay que contar ambas fuentes)."""
    from llm_client import estimate_prompt_tokens
    msgs = [{"role": "user", "content": "hi"}]
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Obtiene la hora",
            "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}},
        },
    }]
    solo_msgs = estimate_prompt_tokens(msgs)
    con_tools = estimate_prompt_tokens(msgs, tools)
    assert con_tools > solo_msgs, "Las tools deben sumar tokens al estimado"
    # Proporcionalidad: doble tools -> estimado mayor (no solo un flag)
    con_tools_doble = estimate_prompt_tokens(msgs, tools + tools)
    assert con_tools_doble > con_tools


def test_chat_stream_rechaza_prompt_gigante_sin_llamar_a_llama():
    """Defensa en profundidad (bug 2026-09-24/25): si el prompt estimado supera
    el limite de contexto, chat_stream debe lanzar PromptTooLargeError con un
    mensaje claro ANTES de hacer el POST, en lugar de recibir el 400 opaco de
    llama.cpp 'exceeds the available context size'."""
    import llm_client as llm_client_module
    from llm_client import LLMClient, PromptTooLargeError
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600,
                       max_prompt_tokens=100)
    msgs = [{"role": "user", "content": "x" * 1000}]  # ~250+ tokens estimados > 100
    # Las tools tambien viajan en el payload JSON y consumen contexto: el guard
    # debe contar messages + tools (caso real: 90 tools ~= 7000 tokens).
    tools = [{"type": "function", "function": {"name": "get_time", "description": "x" * 500,
              "parameters": {"type": "object", "properties": {}}}}]

    with patch("requests.post") as mock_post:
        try:
            list(client.chat_stream(msgs, tools))
            assert False, "Debio lanzar PromptTooLargeError"
        except PromptTooLargeError as e:
            assert "prompt" in str(e).lower() or "demasiado grande" in str(e).lower()
            assert "100" in str(e), "El mensaje debe incluir el limite"
        mock_post.assert_not_called(), "NO debe llamarse a llama.cpp con un prompt gigante"


def test_chat_stream_acepta_prompt_dentro_del_limite():
    """El guard no debe romper los prompts normales: con el limite por defecto
    (60000 tokens) un chat normal sigue llegando a llama.cpp."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Hola"}}]}',
        b'data: [DONE]',
    ]
    mock_iter = iter(fake_lines)

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def iter_lines(self):
            return mock_iter
        def raise_for_status(self):
            pass
        def close(self):
            pass

    with patch("requests.post", return_value=FakeResp()) as mock_post:
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], {}))
        assert any(e["type"] == "delta" for e in events)
        mock_post.assert_called_once()
