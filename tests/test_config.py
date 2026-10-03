# tests/test_config.py
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_config_defaults(monkeypatch):
    """Comprueba los DEFAULTS, no lo que haya en el .env de la maquina.

    Antes este test comparaba contra 'http://localhost:8080/...' leyendo el
    entorno de verdad, asi que solo pasaba si el .env local coincidia con el
    default. Al alinear .env con la IP real del equipo fallo, y con razon: un
    test que depende de un archivo gitignored no es portable."""
    from config import AppConfig
    for var in ("LLAMA_URL", "LLAMA_MODEL", "MEMORY_URL", "MEMORY_USER"):
        monkeypatch.delenv(var, raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        cfg = AppConfig(path=Path(cfg_path))
        assert cfg.llama_url == "http://localhost:8080/v1/chat/completions"
        assert cfg.model == "qwen3.6-35b"
        assert cfg.memory_url == "http://localhost:9001"
        assert cfg.memory_user == "default"
        # El system_prompt vive SOLO en config.json (decision 03/09: unico prompt,
        # sin fallback en codigo). Sin config.json el default es vacio.
        assert cfg.system_prompt == ""
        # Debe haberse creado el archivo
        assert os.path.exists(cfg_path)


def test_config_roundtrip():
    from config import AppConfig
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        cfg = AppConfig(path=Path(cfg_path))
        cfg.system_prompt = "Nuevo prompt"
        cfg.save()
        cfg2 = AppConfig(path=Path(cfg_path))
        assert cfg2.system_prompt == "Nuevo prompt"


def test_config_env_overrides():
    from config import AppConfig
    os.environ["LLAMA_URL"] = "http://other:9999/v1/chat/completions"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            cfg = AppConfig(path=Path(cfg_path))
            assert cfg.llama_url == "http://other:9999/v1/chat/completions"
    finally:
        os.environ.pop("LLAMA_URL", None)


def test_config_upload_dir_default_resuelto_absoluto():
    from config import AppConfig, PROJECT_ROOT
    try:
        os.environ.pop("UPLOAD_DIR", None)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(path=Path(tmp) / "config.json")
            # Sin UPLOAD_DIR, el default "uploads/" se resuelve contra
            # PROJECT_ROOT y queda absoluto.
            assert cfg.upload_dir == str(PROJECT_ROOT / "uploads")
    finally:
        os.environ.pop("UPLOAD_DIR", None)


def test_config_upload_dir_env_override():
    from config import AppConfig
    os.environ["UPLOAD_DIR"] = "C:/custom/uploads"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(path=Path(tmp) / "config.json")
            assert cfg.upload_dir == "C:/custom/uploads"
    finally:
        os.environ.pop("UPLOAD_DIR", None)


def test_config_upload_dir_roundtrip():
    from config import AppConfig
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.json"
        cfg = AppConfig(path=cfg_path)
        cfg.upload_dir = "C:/custom/uploads"
        cfg.save()
        cfg2 = AppConfig(path=cfg_path)
        assert cfg2.upload_dir == "C:/custom/uploads"


def test_config_json_gana_al_env_var_pero_lo_avisa(capsys):
    """config.json se escribe desde la UI y es la fuente autoritativa: si el env
    var ganara, cambiar la URL en la interfaz seria imposible. Pero el silencio
    costaba una sesion entera de diagnostico (2026-09-26): .env decia
    localhost:8080, config.json decia 192.0.2.10:8080 y no habia forma de
    saber cual mandaba. Ahora el env var que se queda fuera debe decirselo."""
    from config import AppConfig
    os.environ["LLAMA_URL"] = "http://localhost:8080/v1/chat/completions"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            json.dump({"llama_url": "http://192.0.2.10:8080/v1/chat/completions"},
                      open(cfg_path, "w", encoding="utf-8"))
            cfg = AppConfig(path=cfg_path)
            # El JSON manda (la UI lo escribe), pero el conflicto se avisa.
            assert cfg.llama_url == "http://192.0.2.10:8080/v1/chat/completions"
            salida = capsys.readouterr()
            assert "LLAMA_URL" in salida.out
            assert "192.0.2.10" in salida.out
    finally:
        os.environ.pop("LLAMA_URL", None)


def test_config_json_sin_conflicto_no_avisa(capsys):
    """Sin divergencia no hay ruido en el log."""
    from config import AppConfig
    os.environ["LLAMA_URL"] = "http://192.0.2.10:8080/v1/chat/completions"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            json.dump({"llama_url": "http://192.0.2.10:8080/v1/chat/completions"},
                      open(cfg_path, "w", encoding="utf-8"))
            AppConfig(path=cfg_path)
            assert "LLAMA_URL" not in capsys.readouterr().out
    finally:
        os.environ.pop("LLAMA_URL", None)


def test_env_example_avisa_de_que_config_json_manda():
    """.env.example no puede seguir sugiriendo que editar LLAMA_URL ahi cambia
    algo: si config.json existe (y lo crea la propia app al arrancar), manda el."""
    from config import PROJECT_ROOT
    ejemplo = PROJECT_ROOT / ".env.example"
    if not ejemplo.exists():
        pytest.skip(".env.example no presente")
    texto = ejemplo.read_text(encoding="utf-8")
    lineas = texto.splitlines()
    urls = [i for i, l in enumerate(lineas) if l.strip().startswith("LLAMA_URL=")]
    assert len(urls) == 1, "LLAMA_URL debe aparecer una sola vez en .env.example"
    # El comentario inmediatamente anterior tiene que advertir del conflicto:
    # buscar la palabra suelta 'config.json' en el fichero no sirve, porque
    # MCP_CONFIG_PATH=./mcp-config.json la contiene siempre.
    previos = "\n".join(lineas[max(0, urls[0] - 6):urls[0]])
    assert "config.json" in previos and "manda" in previos.lower(), previos


def test_system_prompt_config_sin_afirmaciones_obsoletas():
    """El system_prompt de produccion (config.json del proyecto, gitignored) no
    debe afirmar cosas falsas/obsoletas sobre tools: run_command NO es PowerShell
    (shell=True usa cmd.exe en Windows) y para escanear redes existe scan_hosts
    (su descripcion dice USO OBLIGATORIO), no python_eval. Tampoco debe citar
    tools inexistentes del catalogo. Se salta si config.json no existe (CI sin
    config local)."""
    from config import PROJECT_ROOT
    cfg_path = PROJECT_ROOT / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json no presente (gitignored)")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    prompt = data.get("system_prompt", "")
    assert prompt, "config.json debe definir system_prompt"
    assert "PowerShell" not in prompt
    assert "python_eval" not in prompt
    assert "run_command" not in prompt
