# tests/test_login_identity.py
# Tests para la identidad de usuario que gobierna el namespace de memoria.
#
# El problema: api_login aceptaba texto libre y ese texto era la clave del
# namespace de memoria (cabecera X-User). Medido el 2026-10-01: 9 memorias bajo
# el nombre corto tecleado (`ana`) y 1 bajo la misma cuenta con dominio
# (`ana@EJEMPLO`, que es `config.memory_user`), o sea dos historiales para la
# misma persona, y ademas cualquiera que teclease el nombre de otro leia sus
# memorias.
#
# Aqui la identidad la fija la configuracion (que es la cuenta real de
# Windows/AD, p. ej. `ana@EJEMPLO`), no lo que escriba el usuario. Se aceptan
# las formas de escribir esa misma identidad (corto, con dominio, en
# mayusculas) pero se guarda siempre la forma canonica, para que el namespace
# no dependa de como se teclea.
#
# La cuenta usada aqui es INVENTADA. La real vivia en este fichero, y un
# nombre de persona junto a un dominio de Active Directory, en un test
# publicado, identifica a la organizacion y a la persona duena de la cuenta.
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def client(app_module):
    """Cliente de Flask con config.memory_user = ana@EJEMPLO."""
    app_module.config.memory_user = "ana@EJEMPLO"
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Forma canonica
# ---------------------------------------------------------------------------
def test_canonical_user_normaliza_mayusculas_y_espacios():
    from user_identity import canonical_user

    assert canonical_user("  ANA@EJEMPLO  ") == "ana@ejemplo"
    assert canonical_user("Ana") == "ana"
    assert canonical_user("") == ""


def test_canonical_user_conserva_el_dominio():
    """Quitar el @DOMAIN fundiria cuentas homonimas de dominios distintos.

    El caso real es al reves: `ana` y `ana@EJEMPLO` tienen que acabar en el
    mismo namespace, y eso se resuelve canonizando la forma *configurada*
    (que si lleva dominio), no borrando el dominio de lo tecleado.
    """
    from user_identity import canonical_user

    assert canonical_user("ana@ejemplo") == "ana@ejemplo"


def test_misma_identidad_acepta_formas_de_la_misma_cuenta():
    from user_identity import same_identity

    assert same_identity("ana", "ana@EJEMPLO") is True
    assert same_identity("ANA@EJEMPLO", "ana@ejemplo") is True
    assert same_identity(" Ana ", "ana@ejemplo") is True
    assert same_identity("ana@ejemplo", "ana@ejemplo") is True


def test_misma_identidad_rechaza_otra_cuenta():
    from user_identity import same_identity

    assert same_identity("jonathan", "ana@EJEMPLO") is False
    assert same_identity("otro@OTRODOM", "ana@EJEMPLO") is False
    assert same_identity("ana2", "ana@EJEMPLO") is False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_login_acepta_el_nombre_corto_de_la_cuenta_configurada(client):
    """Escribir `ana` es escribir la misma cuenta que `ana@EJEMPLO`."""
    r = client.post("/api/login", json={"name": "ana"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["user"] == "ana@ejemplo"


def test_login_acepta_la_cuenta_con_dominio(client):
    r = client.post("/api/login", json={"name": "ANA@EJEMPLO"})
    assert r.status_code == 200
    assert r.get_json()["user"] == "ana@ejemplo"


def test_login_rechaza_otra_cuenta(client):
    """Antes esto entraba y leia las memorias de la otra persona."""
    r = client.post("/api/login", json={"name": "jonathan"})
    assert r.status_code == 401
    assert "error" in r.get_json()


def test_login_guarda_la_forma_canonica_y_no_lo_tecleado(client, app_module):
    """La sesion guarda la identidad canonica: el namespace no puede variar."""
    client.post("/api/login", json={"name": "ana"})
    with client.session_transaction() as sess:
        assert sess["user"] == "ana@ejemplo"
    # El cliente de memoria de la sesion usa la identidad canonica
    assert app_module._memory_for_user("ana@ejemplo").user == "ana@ejemplo"


def test_el_cliente_global_se_construye_con_la_identidad_configurada(app_module):
    """Sin sesion, el cliente global tambien va a la identidad canonica.

    Antes se construia con `config.memory_user` tal cual y la sesion usaba el
    texto tecleado: dos namespaces para la misma persona.

    Se sustituye AppConfig por un stub en vez de tocar el entorno: la precedencia
    real (config.json sobre .env) es otro asunto y probarla aqui solo haria el
    test fragil. Lo que se comprueba es el cableado: que `_init_globals` pase la
    identidad canonica, no el valor crudo de la configuracion.
    """
    from unittest.mock import patch, MagicMock

    class _CfgStub:
        memory_url = "http://localhost:9001"
        memory_user = "ana@EJEMPLO"
        llama_url = "http://localhost:8080/v1/chat/completions"
        model = "qwen3.6-35b"
        llm_timeout = 600
        whisper_model = "small"
        mcp_config_path = "mcp-config.json"
        upload_dir = "uploads/"
        system_prompt = ""

    captured = {}

    def _capture(url, user):
        captured["url"] = url
        captured["user"] = user
        return MagicMock()

    with patch.object(app_module, "AppConfig", return_value=_CfgStub()), \
         patch.object(app_module, "MemoryClient", side_effect=_capture), \
         patch.object(app_module, "LLMClient"), \
         patch.object(app_module, "MCPToolManager"):
        app_module._init_globals()

    assert captured["url"] == "http://localhost:9001"
    assert captured["user"] == "ana@ejemplo", (
        f"el cliente global se construyo con {captured['user']!r}: "
        f"sin canonizar, cada forma de escribir el nombre crea un namespace"
    )
