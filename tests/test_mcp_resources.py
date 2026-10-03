# tests/test_mcp_resources.py  
"""Tests para verificar soporte de recursos MCP en el servidor HTTP."""  
import json  
import sys 
from pathlib import Path  

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_mcp_config_usa_http_para_dia():
    """Verificar que mcp-config.json usa transport HTTP para el servidor 'dia'."""
    config_path = Path(r"D:\python\Voice-WebUI\mcp-config.json")  
    if not config_path.exists():  
        pytest.skip("mcp-config.json no encontrado")  

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    dia_config = config.get("mcpServers", {}).get("dia", {})
    
    assert "transport" in dia_config or "url" in dia_config, (
        "El servidor 'dia' debe usar transport HTTP con url definida"  
    )  

def test_mcp_http_client_soporta_resources():
    """Verificar que MCPHTTPClient tiene metodos para recursos MCP."""
    from mcp_clients import MCPHTTPClient
    
    client = MCPHTTPClient(name="test", url="http://localhost:8086")
    
    # Debe tener metodo list_resources o similar  
    assert hasattr(client, "list_resources"), (
        "MCPHTTPClient debe tener metodo list_resources"  
    )  

def test_format_tools_prompt_incluye_recursos():  
    """Verificar que format_tools_prompt incluye recursos MCP en el prompt."""  
    from llm_client import format_tools_prompt
    
    # Crear un recurso de ejemplo
    resources = [
        {
            "uri": "diamcp://context/time", 
            "name": "Current time", 
            "description": "Current date and time in ISO format"  
        },  
        {  
            "uri": "diamcp://context/system", 
            "name": "System info",
             "description": "System information for context"
         }
    ]

    tools = []  # Sin herramientas, solo recursos para este test
    
    prompt = format_tools_prompt(tools, {}, resources=resources)  
    
    assert "diamcp://context/time" in prompt, (  
        "El prompt debe incluir los URIs de recursos MCP"  
    )  
