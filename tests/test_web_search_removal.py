# tests/test_web_search_removal.py
"""Tests para verificar que el servidor web-search fue eliminado correctamente."""
import json  
import sys 
from pathlib import Path  

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_mcp_config_no_tiene_web_search():
    """Verificar que mcp-config.json NO tiene servidor 'web-search'."""
    config_path = Path(r"D:\python\Voice-WebUI\mcp-config.json")  
    if not config_path.exists():  
        pytest.skip("mcp-config.json no encontrado")  

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    assert "web-search" not in config.get("mcpServers", {}), (
        "El servidor 'web-search' debe ser eliminado de mcp-config.json"  
    )  


def test_config_json_no_tiene_mcp_prompt_web_search():
    """Verificar que config.json NO tiene mcp_prompt para 'web-search'."""
    config_path = Path(r"D:\python\Voice-WebUI\config.json")  
    if not config_path.exists():  
        pytest.skip("config.json no encontrado")  

    data = json.loads(config_path.read_text(encoding="utf-8"))
    mcp_prompts = data.get("mcp_prompts", {})
    
    assert "web-search" not in mcp_prompts, (
        "El mcp_prompt 'web-search' debe ser eliminado de config.json"  
    )  


def test_system_prompt_no_menciona_web_search_directamente():
    """Verificar que el system_prompt no requiere web-search como servidor separado."""
    config_path = Path(r"D:\python\Voice-WebUI\config.json")  
    if not config_path.exists():  
        pytest.skip("config.json no encontrado")  

    data = json.loads(config_path.read_text(encoding="utf-8"))
    system_prompt = data.get("system_prompt", "")
    
    # El system prompt deberia mencionar web_search como herramienta, no como servidor
    # (de diaMCP, no de un servidor separado)
    assert "web-search" not in system_prompt.lower() or "herramienta" in system_prompt.lower(), (
        "El system prompt no debe referirse a 'web-search' como servidor independiente"  
    )


def test_dia_mcp_tiende_web_search_tool():
    """Verificar que el servidor 'dia' tiene la herramienta web_search."""
    script_path = Path(r"D:\github\diaMCP\dia-mcp-stdio.py")  
    if not script_path.exists():  
        pytest.skip("dia-mcp-stdio.py no encontrado")  

    content = script_path.read_text(encoding="utf-8")
    
    assert "@mcp.tool()" in content and "def web_search" in content, (
        "El servidor 'dia' debe tener la herramienta web_search"  
    )
