# tests/test_reasoning_marker_filter.py
"""Test que '>' (marcador de blockquote del reasoning de Qwen 3.x) no se emite
como delta al usuario cuando el tool call esta en reasoning_content.

Bug 2026-09-13: con --reasoning on, Qwen 3.x a veces ensaya el tool call SOLO
en reasoning_content y emite '>' en content. El '>' llega al frontend antes de
que se detecte el tool call, confundiendo al usuario.

El fix: filtrar '>' del contenido en chat_stream ANTES de emitir deltas.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_reasoning_marker_gt_not_emitted_as_delta():
    """Cuando reasoning_content contiene un tool call y content contiene solo
    '>' (marcador de blockquote del razonamiento), '>' NO debe emitirse como
    delta al usuario. El tool call debe detectarse desde reasoning_content."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

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
        assert calls[0]["function"]["name"] == "dia.get_time"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {}
        # El marcador '>' NO debe emitirse como delta (fix bug 2026-09-13)
        delta_events = [e for e in events if e["type"] == "delta"]
        all_content = "".join(e["content"] for e in delta_events)
        assert ">" not in all_content, (
            "El marcador '>' del reasoning no debe emitirse como delta"
        )


def test_reasoning_marker_gt_filtered_from_normal_content():
    """Cuando el contenido contiene '>' como marcador de razonamiento mezclado
    con texto normal, el '>' se filtra pero el texto se conserva."""
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-27b", 600)

    fake_lines = [
        b'data: {"choices": [{"delta": {"content": "Hoy es 13 de septiembre"}}]}',
        b'data: {"choices": [{"delta": {"content": ">"}}]}',
        b'data: {"choices": [{"delta": {"content": ", 2026"}}]}',
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
        delta_events = [e for e in events if e["type"] == "delta"]
        all_content = "".join(e["content"] for e in delta_events)
        # El '>' se filtra pero el texto se conserva
        assert ">" not in all_content
        assert "Hoy es 13 de septiembre" in all_content
        assert ", 2026" in all_content


def test_reasoning_marker_gt_still_allows_tool_call_detection():
    """Filtrar '>' no debe interferir con la deteccion de tool calls en
    reasoning_content. El tool call debe seguir detectandose."""
    import json
    from llm_client import LLMClient
    client = LLMClient("http://localhost:8080/v1/chat/completions", "qwen3.6-35b", 600)

    # reasoning_content trae el tool call; content trae '>'
    fake_lines = [
        b'data: {"choices": [{"delta": {"reasoning_content": "Let me check the date.\\n<tool_call>{\\"name\\": \\"dia.get_time\\", \\"arguments\\": {}}</tool_call>"}}]}',
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
        assert calls[0]["function"]["name"] == "dia.get_time"
