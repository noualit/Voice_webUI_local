# tests/test_dia_tools.py
"""Tests para verificar que las herramientas del servidor diaMCP stdio se descubren correctamente."""
import json  
import os 
import subprocess 
import sys  
from pathlib import Path  

import pytest 

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_dia_mcp_stdio_has_get_time_tool():  
    """Verificar que dia-mcp-stdio.py contiene la funcion get_time."""  
    script_path = Path(r"D:\github\diaMCP\dia-mcp-stdio.py")  
    if not script_path.exists():
        pytest.skip("dia-mcp-stdio.py no encontrado")  

    content = script_path.read_text(encoding="utf-8")
    assert "@mcp.tool()" in content, "El archivo debe tener al menos un @mcp.tool()"
    assert "def get_time" in content, "Debe definir la funcion get_time"


def test_dia_mcp_stdio_has_resource_wrapper_tools():  
    """Verificar que dia-mcp-stdio.py tiene las herramientas wrapper de recursos MCP."""  
    script_path = Path(r"D:\github\diaMCP\dia-mcp-stdio.py")  
    if not script_path.exists():
        pytest.skip("dia-mcp-stdio.py no encontrado")  

    content = script_path.read_text(encoding="utf-8")

    # Debe tener las herramientas wrapper de recursos MCP
    assert "def get_workspace_summary" in content, (
        "Debe definir la funcion get_workspace_summary (equivalente a recurso diamcp://workspace/summary)"  
    )  
    assert "def get_tools_list" in content, (
        "Debe definir la funcion get_tools_list (equivalente a recurso diamcp://tools/list)"  
    )  


def test_dia_mcp_stdio_compiles():
    """Verificar que dia-mcp-stdio.py compila sin errores sintacticos."""
    script_path = Path(r"D:\github\diaMCP\dia-mcp-stdio.py")  
    if not script_path.exists():  
        pytest.skip("dia-mcp-stdio.py no encontrado")  

    import py_compile
    try:
        py_compile.compile(str(script_path), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"dia-mcp-stdio.py tiene errores de sintaxis: {e}")


def test_dia_mcp_stdio_imports_path():  
    """Verificar que dia-mcp-stdio.py importa pathlib.Path."""  
    script_path = Path(r"D:\github\diaMCP\dia-mcp-stdio.py")
    if not script_path.exists():
        pytest.skip("dia-mcp-stdio.py no encontrado")

    content = script_path.read_text(encoding="utf-8")
    assert "from pathlib import Path" in content or "import pathlib; pathlib.Path" in content, (
        "El archivo debe importar pathlib.Path para definir WORKSPACE_DIR"  
    )

