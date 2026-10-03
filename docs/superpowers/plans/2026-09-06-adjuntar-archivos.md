# Adjuntar Archivos (Documentos como Texto) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir subir archivos (PDF/TXT/DOCX) a la conversacion; el backend extrae el texto y lo inyecta en el mensaje del LLM, persistiendo la referencia (marcador) en llama-memory.

**Architecture:** Nuevo modulo `src/attachments.py` (logica pura: extraccion, saneado, upload, resolucion de marcadores) + endpoint `POST /api/upload` + resolucion de marcadores `[adjunto:<file_id>|<nombre>]` en el unico punto `_build_chat_messages`. Frontend: split de `app.js` (639 lineas, supera el limite de 600) en `chatState()` + `attachmentState()` registrados con `Alpine.data` (sin build step).

**Tech Stack:** Flask, pypdf, python-docx, Alpine.js (CDN), llama-memory (persistencia), pytest (TDD).

**Spec:** `docs/superpowers/specs/2026-09-06-voice-webui-adjuntar-archivos-design.md`

---

## Refinamiento del spec (documentado)

- `_build_chat_messages` recibe `user` como **parametro opcional** (`user: str | None = None`) con fallback a `config.memory_user`, en lugar de requerido. Razon: los tests existentes (`tests/test_app_conversations.py:296,302,316`) llaman `_build_chat_messages(mc, ...)` con 3 argumentos sin request context; hacer `user` obligatorio los romperia. `api_chat` pasa `_current_user() or config.memory_user` explicitamente.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/attachments.py` (nuevo) | Logica pura: constantes de limites, `sanitize_filename`, `extract_text`, `save_upload`, `resolve_attachments`. Sin imports de Flask. |
| `src/app.py` (modificar) | `api_upload` (ruta fina), resolucion en `_build_chat_messages`, validacion de adjuntos en `api_chat`. |
| `src/config.py` (modificar) | Campo `upload_dir` (env `UPLOAD_DIR`, default `uploads/`), persistido en config.json. |
| `tests/test_attachments.py` (nuevo) | Tests de attachments.py. |
| `tests/test_app.py` (modificar) | Tests de `/api/upload` y resolucion en `/api/chat`. |
| `tests/test_config.py` (modificar) | Tests de `upload_dir`. |
| `tests/conftest.py` (modificar) | Env `UPLOAD_DIR` a tempdir por test. |
| `requirements.txt` (modificar) | `+pypdf`, `+python-docx`. |
| `.gitignore` (modificar) | `+uploads/`. |
| `static/app.js` (modificar) | `voiceApp()` -> `chatState()` + registro `Alpine.data`. |
| `static/attachments.js` (nuevo) | `attachmentState()`: upload, chips, marcadores. |
| `static/index.html` (modificar) | `x-data="voiceApp"`, boton adjuntar, chips, orden de scripts. |
| `static/style.css` (modificar) | Estilos de chips y boton adjuntar. |

---

### Task 1: `src/attachments.py` — sanitize_filename + extract_text (TDD)

**Files:**
- Create: `src/attachments.py`
- Test: `tests/test_attachments.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_attachments.py`:

```python
# tests/test_attachments.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from attachments import extract_text, sanitize_filename


def _make_pdf(text: str) -> bytes:
    """Genera un PDF minimo de una pagina con el texto dado (para tests).
    Calcula offsets del xref en runtime para que pypdf lo parsee sin reparaciones."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


def test_extract_text_txt_utf8(tmp_path):
    p = tmp_path / "nota.txt"
    p.write_text("Hola mundo", encoding="utf-8")
    assert extract_text(p) == "Hola mundo"


def test_extract_text_txt_latin1(tmp_path):
    p = tmp_path / "nota.txt"
    p.write_bytes("Caf\xe9".encode("latin-1"))
    assert extract_text(p) == "Caf\xe9"


def test_extract_text_pdf(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(_make_pdf("Hola PDF"))
    assert extract_text(p) == "Hola PDF"


def test_extract_text_docx(tmp_path):
    import docx
    d = docx.Document()
    d.add_paragraph("Primer parrafo")
    d.add_paragraph("Segundo parrafo")
    p = tmp_path / "nota.docx"
    d.save(str(p))
    assert extract_text(p) == "Primer parrafo\nSegundo parrafo"


def test_extract_text_extension_no_soportada(tmp_path):
    p = tmp_path / "datos.bin"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_text(p)


def test_sanitize_filename_quita_rutas():
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_sanitize_filename_quita_caracteres_peligrosos():
    assert sanitize_filename("a[b]c|d.txt") == "a_b_c_d.txt"


def test_sanitize_filename_vacio_devuelve_archivo():
    assert sanitize_filename("") == "archivo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attachments.py -v -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'attachments'`

- [ ] **Step 3: Write minimal implementation**

Create `src/attachments.py`:

```python
# src/attachments.py
# Logica de adjuntos de archivos a la conversacion (documentos como texto).
# Modulo de logica pura: sin imports de Flask, para poder testearlo y
# reutilizarlo sin dependencias del framework.
import re
import uuid
from pathlib import Path

ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_CHARS = 20000
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_TOTAL_ATTACHMENT_CHARS = 40000
# Marcador: [adjunto:<file_id de 12 hex>|<nombre visible saneado>]
MARKER_RE = re.compile(r"\[adjunto:([0-9a-f]{12})\|([^\]]*)\]")
_TRUNCATE_SUFFIX = "\n...[truncado]"


def sanitize_filename(name: str) -> str:
    """Devuelve un nombre de archivo seguro: sin rutas ni caracteres peligrosos.

    Elimina el path (solo base name), reemplaza caracteres que romperian el
    marcador ([]|) o el sistema de archivos, y limita la longitud a 80 chars
    conservando la extension.
    """
    base = Path(name or "archivo").name
    base = re.sub(r'[\[\]|\\/:*?"<>]', "_", base)
    base = re.sub(r"[\x00-\x1f]", "", base)
    base = base.strip(" ._")
    if len(base) > 80:
        stem, ext = Path(base).stem, Path(base).suffix
        base = stem[: 80 - len(ext)] + ext
    return base or "archivo"


def _extract_txt(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(path: Path) -> str:
    import docx
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def extract_text(path: Path) -> str:
    """Extrae el texto plano de un archivo segun su extension."""
    ext = path.suffix.lower()
    if ext == ".txt":
        return _extract_txt(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    raise ValueError(f"Extension no soportada: {ext}")
```

- [ ] **Step 4: Install dependencies**

Run: `pip install pypdf python-docx`
Expected: packages installed successfully

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_attachments.py -v -p no:cacheprovider`
Expected: PASS (9 tests). Si `test_extract_text_pdf` falla por el PDF minimo, revisar el helper `_make_pdf` (offsets del xref) antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add src/attachments.py tests/test_attachments.py
git commit -m "feat(attachments): extraccion de texto txt/pdf/docx y saneado de nombres"
```

---

### Task 2: `src/attachments.py` — save_upload + resolve_attachments (TDD)

**Files:**
- Modify: `src/attachments.py` (append)
- Test: `tests/test_attachments.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_attachments.py`:

```python
from unittest.mock import MagicMock

from attachments import (
    MAX_ATTACHMENT_CHARS,
    MAX_TOTAL_ATTACHMENT_CHARS,
    MAX_UPLOAD_BYTES,
    resolve_attachments,
    save_upload,
)


def _fake_file(name: str, content: str):
    fake = MagicMock()
    fake.filename = name
    fake.save.side_effect = lambda path: Path(path).write_text(content, encoding="utf-8")
    return fake


def test_save_upload_ok(tmp_path):
    result = save_upload(_fake_file("informe.txt", "contenido x100"), "ana", tmp_path)
    assert len(result["file_id"]) == 12
    assert result["name"] == "informe.txt"
    assert result["chars"] == len("contenido x100")
    assert result["truncated"] is False
    assert "warning" not in result
    files = list((tmp_path / "ana").iterdir())
    assert len(files) == 1
    assert files[0].name.startswith(result["file_id"] + "_")


def test_save_upload_extension_no_permitida(tmp_path):
    with pytest.raises(ValueError, match="Extension no permitida"):
        save_upload(_fake_file("datos.bin", "x"), "ana", tmp_path)


def test_save_upload_demasiado_grande_borra_archivo(tmp_path):
    big = "x" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="demasiado grande"):
        save_upload(_fake_file("grande.txt", big), "ana", tmp_path)
    user_dir = tmp_path / "ana"
    assert not user_dir.exists() or not any(user_dir.iterdir())


def test_save_upload_trunca_caracteres(tmp_path):
    content = "a" * (MAX_ATTACHMENT_CHARS + 500)
    result = save_upload(_fake_file("largo.txt", content), "ana", tmp_path)
    assert result["truncated"] is True
    assert result["chars"] == MAX_ATTACHMENT_CHARS + len("\n...[truncado]")


def test_save_upload_pdf_escaneado_warning(tmp_path):
    fake = MagicMock()
    fake.filename = "escaneado.pdf"
    # PDF valido pero sin texto extraible
    fake.save.side_effect = lambda path: Path(path).write_bytes(_make_pdf(""))
    result = save_upload(fake, "ana", tmp_path)
    assert result["chars"] == 0
    assert result["warning"]


def test_resolve_attachments_reemplaza_marcador(tmp_path):
    meta = save_upload(_fake_file("informe.txt", "El contenido del informe."), "ana", tmp_path)
    marker = f"[adjunto:{meta['file_id']}|informe.txt]"
    resolved = resolve_attachments(f"Resume esto\n{marker}", "ana", tmp_path)
    assert "Resume esto" in resolved
    assert 'Contenido del archivo "informe.txt"' in resolved
    assert "El contenido del informe." in resolved
    assert marker not in resolved


def test_resolve_attachments_archivo_ausente(tmp_path):
    resolved = resolve_attachments("Hola\n[adjunto:abcdef123456|perdido.pdf]", "ana", tmp_path)
    assert "[archivo adjunto no disponible: perdido.pdf]" in resolved
    assert "Hola" in resolved


def test_resolve_attachments_pdf_escaneado_sin_texto(tmp_path):
    fake = MagicMock()
    fake.filename = "escaneado.pdf"
    fake.save.side_effect = lambda path: Path(path).write_bytes(_make_pdf(""))
    meta = save_upload(fake, "ana", tmp_path)
    marker = f"[adjunto:{meta['file_id']}|escaneado.pdf]"
    resolved = resolve_attachments(marker, "ana", tmp_path)
    assert "[sin texto extraible: escaneado.pdf]" in resolved


def test_resolve_attachments_limite_total(tmp_path):
    metas = []
    for i in range(2):
        metas.append(save_upload(
            _fake_file(f"grande{i}.txt", "x" * 30000), "ana", tmp_path))
    markers = "\n".join(
        f"[adjunto:{m['file_id']}|grande{i}.txt]" for i, m in enumerate(metas))
    resolved = resolve_attachments(f"Texto\n{markers}", "ana", tmp_path)
    # El texto limpio del usuario nunca se trunca
    assert resolved.startswith("Texto")
    assert "[aviso: adjuntos truncados" in resolved
    assert len(resolved) <= MAX_TOTAL_ATTACHMENT_CHARS + 100
```

Nota: el import de `MAX_*` al inicio del archivo de tests choca con el primer import (lineas 1-10 ya existentes). El plan asume que los imports se anaden al bloque superior del archivo o en el punto de uso; el ejecutor debe mantener los imports al inicio del archivo (estilo del proyecto).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attachments.py -v -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'save_upload'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/attachments.py`:

```python
def _truncate(text: str, limit: int = MAX_ATTACHMENT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + _TRUNCATE_SUFFIX, True


def save_upload(file_storage, user: str, upload_dir: Path) -> dict:
    """Guarda el archivo subido, extrae y trunca su texto.

    Devuelve {file_id, name, chars, truncated, warning?}. Lanza ValueError si
    la extension no es valida, el archivo supera MAX_UPLOAD_BYTES o la
    extraccion falla (en esos casos el archivo se borra del disco).
    """
    name = sanitize_filename(file_storage.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Extension no permitida: {ext or '(sin extension)'}")
    file_id = uuid.uuid4().hex[:12]
    user_dir = Path(upload_dir) / user
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{file_id}_{name}"
    file_storage.save(str(dest))
    try:
        if dest.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Archivo demasiado grande")
        text = extract_text(dest)
    except ValueError:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise ValueError("No se pudo extraer texto del archivo")
    text, truncated = _truncate(text)
    result = {"file_id": file_id, "name": name, "chars": len(text), "truncated": truncated}
    if not text.strip():
        result["warning"] = "No se pudo extraer texto (PDF escaneado?)"
    return result


def _load_attachment(user: str, upload_dir: Path, file_id: str) -> tuple[str, str] | None:
    """Devuelve (nombre_real, contenido_truncado) del adjunto o None si no existe."""
    user_dir = Path(upload_dir) / user
    matches = list(user_dir.glob(f"{file_id}_*"))
    if not matches:
        return None
    path = matches[0]
    name = path.name[len(file_id) + 1:]
    try:
        text = extract_text(path)
    except Exception:
        text = ""
    text, _ = _truncate(text)
    return name, text


def resolve_attachments(text: str, user: str, upload_dir: Path) -> str:
    """Reemplaza los marcadores [adjunto:file_id|nombre] por el contenido.

    El texto limpio del usuario nunca se trunca; solo los bloques de adjuntos
    se recortan para no superar MAX_TOTAL_ATTACHMENT_CHARS por mensaje.
    """
    parts: list[str] = []
    total_chars = 0
    last_end = 0
    truncated_total = False
    for match in MARKER_RE.finditer(text):
        parts.append(text[last_end:match.start()])
        last_end = match.end()
        file_id, display_name = match.group(1), match.group(2)
        loaded = _load_attachment(user, upload_dir, file_id)
        if loaded is None:
            parts.append(f"[archivo adjunto no disponible: {display_name}]")
            continue
        name, content = loaded
        if not content.strip():
            parts.append(f"[sin texto extraible: {name}]")
            continue
        block = f'[Contenido del archivo "{name}" ({len(content)} caracteres)]\n{content}'
        remaining = MAX_TOTAL_ATTACHMENT_CHARS - total_chars
        if remaining <= 0:
            truncated_total = True
            continue
        if len(block) > remaining:
            block = block[:remaining] + _TRUNCATE_SUFFIX
            truncated_total = True
        total_chars += len(block)
        parts.append(block)
    parts.append(text[last_end:])
    resolved = "".join(parts)
    if truncated_total:
        resolved += "\n[aviso: adjuntos truncados por limite de caracteres]"
    return resolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_attachments.py -v -p no:cacheprovider`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add src/attachments.py tests/test_attachments.py
git commit -m "feat(attachments): save_upload y resolve_attachments con limites y marcadores"
```

---

### Task 3: `AppConfig.upload_dir` + conftest + requirements + gitignore

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`
- Modify: `tests/conftest.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_config_upload_dir_default_resuelto_absoluto():
    from config import AppConfig
    with tempfile.TemporaryDirectory() as tmp:
        cfg = AppConfig(path=Path(tmp) / "config.json")
        # Si UPLOAD_DIR no esta definido, el default "uploads/" se resuelve
        # contra PROJECT_ROOT y queda absoluto.
        assert Path(cfg.upload_dir).is_absolute()
        assert Path(cfg.upload_dir).name == "uploads"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'upload_dir'`

- [ ] **Step 3: Write minimal implementation**

Modify `src/config.py`:

```python
        self.memory_user = os.environ.get("MEMORY_USER", "default")
        self.upload_dir = os.environ.get("UPLOAD_DIR", "uploads/")
        self.system_prompt = ""
        self._load()
```

En `_load`, anadir `"upload_dir"` a la tupla de claves:

```python
                for key in ("llama_url", "model", "temperature", "whisper_model",
                            "mcp_config_path", "memory_url", "memory_user",
                            "system_prompt", "upload_dir"):
                    if key in data:
                        setattr(self, key, data[key])
```

Despues de la resolucion de `mcp_config_path`, anadir la resolucion de `upload_dir`:

```python
        mcp = Path(self.mcp_config_path)
        if not mcp.is_absolute():
            self.mcp_config_path = str(PROJECT_ROOT / mcp)
        up = Path(self.upload_dir)
        if not up.is_absolute():
            self.upload_dir = str(PROJECT_ROOT / up)
```

En `save`, anadir al dict:

```python
            "memory_user": self.memory_user,
            "upload_dir": self.upload_dir,
            "system_prompt": self.system_prompt,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v -p no:cacheprovider`
Expected: PASS. Nota: `test_config_upload_dir_default_resuelto_absoluto` depende de que `UPLOAD_DIR` no este en el entorno; el fixture autouse de conftest lo pondra (ver Step 5). Ejecutar la suite completa de test_config despues de Step 5 para confirmar.

- [ ] **Step 5: Update conftest (UPLOAD_DIR a tempdir)**

Modify `tests/conftest.py`:

```python
    old_env = {}
    for var in ("CONFIG_PATH", "MCP_CONFIG_PATH", "UPLOAD_DIR"):
        if var in os.environ:
            old_env[var] = os.environ[var]
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
        os.environ["MCP_CONFIG_PATH"] = os.path.join(tmp, "mcp-config.json")
        os.environ["UPLOAD_DIR"] = os.path.join(tmp, "uploads")
        with open(os.path.join(tmp, "mcp-config.json"), "w") as f:
            json.dump({"mcpServers": {}}, f)
        yield tmp
    for var, val in old_env.items():
        os.environ[var] = val
    for var in ("CONFIG_PATH", "MCP_CONFIG_PATH", "UPLOAD_DIR"):
        if var not in old_env:
            os.environ.pop(var, None)
```

- [ ] **Step 6: Update requirements.txt y .gitignore**

`requirements.txt` — anadir:

```
pypdf>=4.0
python-docx>=1.1
```

`.gitignore` — anadir al final:

```
# Archivos subidos por el usuario (adjuntos de conversacion)
uploads/
```

- [ ] **Step 7: Run full config suite**

Run: `python -m pytest tests/test_config.py -v -p no:cacheprovider`
Expected: PASS (todos, incluyendo el default resuelto con UPLOAD_DIR del fixture)

- [ ] **Step 8: Commit**

```bash
git add src/config.py tests/test_config.py tests/conftest.py requirements.txt .gitignore
git commit -m "feat(config): upload_dir configurable (UPLOAD_DIR) + deps pypdf/python-docx + gitignore uploads"
```

---

### Task 4: `app.py` — api_upload + resolucion de marcadores en chat (TDD)

**Files:**
- Modify: `src/app.py`
- Test: `tests/test_app.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_upload_sin_archivo_400(app_module):
    resp = app_module.app.test_client().post(
        "/api/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_extension_no_permitida_400(app_module):
    test_client = app_module.app.test_client()
    data = {"file": (io.BytesIO(b"x"), "datos.bin")}
    resp = test_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_ok_devuelve_metadata(app_module):
    import app as app_module_ref
    test_client = app_module_ref.app.test_client()
    data = {"file": (io.BytesIO("Hola archivo".encode("utf-8")), "nota.txt")}
    resp = test_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == "nota.txt"
    assert body["chars"] == len("Hola archivo")
    assert body["file_id"] and len(body["file_id"]) == 12
    assert body["truncated"] is False


def test_chat_resuelve_marcadores_y_guarda_referencia(app_module):
    """Con adjuntos, el LLM recibe el contenido resuelto y la memoria guarda
    la referencia (marcador), no el contenido."""
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    def _fake_memory_for_user(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    captured = {}

    def _fake_chat_stream(messages, tools):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    # Subir un archivo real al upload_dir temporal del fixture
    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat",
            json={"text": f"Resume\n{marker}", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

    msgs = captured["messages"]
    user_msg = msgs[-1]
    assert user_msg["role"] == "user"
    assert "contenido del adjunto" in user_msg["content"]
    assert marker not in user_msg["content"]
    # La memoria guarda el texto crudo con el marcador (referencia), no el contenido
    saved = created["mc"].save_memory.call_args_list[0][0][0]
    assert marker in saved
    assert "contenido del adjunto" not in saved


def test_chat_resuelve_marcadores_en_historial(app_module):
    """Los marcadores en mensajes user del historial tambien se resuelven."""
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"

    def _fake_memory_for_user_hist(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {
            "memories": [
                {"text": f"Pregunta previa\n{marker}", "tags": ["role:user"]},
            ]
        }
        created["mc"] = mc
        return mc

    captured = {}

    def _fake_chat_stream(messages, tools):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "ok"}

    with patch.object(app_module_ref, "_memory_for_user",
                      side_effect=_fake_memory_for_user_hist), \
         patch.object(app_module_ref.llm_client, "chat_stream",
                      side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat", json={"text": "Nueva", "conversation_id": "conv-1"})
        assert resp.status_code == 200
        list(resp.response)

    hist_user = captured["messages"][1]
    assert hist_user["role"] == "user"
    assert "contenido del adjunto" in hist_user["content"]


def test_chat_mas_de_5_adjuntos_400(app_module):
    import app as app_module_ref
    markers = "\n".join(f"[adjunto:{i:012x}|f{i}.txt]" for i in range(6))
    resp = app_module_ref.app.test_client().post(
        "/api/chat", json={"text": f"hola\n{markers}", "conversation_id": "conv-1"})
    assert resp.status_code == 400


def test_chat_resend_con_adjuntos_resuelve_y_no_guarda_pregunta(app_module):
    import app as app_module_ref
    from pathlib import Path
    from unittest.mock import MagicMock as _MM
    from attachments import save_upload
    created = {}

    def _fake_memory_for_user(user):
        mc = _MM()
        mc.user = user
        mc.get_history.return_value = {"memories": []}
        created["mc"] = mc
        return mc

    upload_dir = Path(app_module_ref.config.upload_dir)
    fake = _MM()
    fake.filename = "nota.txt"
    fake.save.side_effect = lambda path: Path(path).write_text(
        "contenido del adjunto", encoding="utf-8")
    meta = save_upload(fake, "ana", upload_dir)
    marker = f"[adjunto:{meta['file_id']}|nota.txt]"
    captured = {}

    def _fake_chat_stream(messages, tools):
        captured["messages"] = list(messages)
        yield {"type": "delta", "content": "Nueva respuesta"}

    with patch.object(app_module_ref, "_memory_for_user", side_effect=_fake_memory_for_user), \
         patch.object(app_module_ref.llm_client, "chat_stream", side_effect=_fake_chat_stream):
        test_client = app_module_ref.app.test_client()
        test_client.post("/api/login", json={"name": "ana"})
        resp = test_client.post(
            "/api/chat",
            json={"text": f"Resume\n{marker}", "conversation_id": "conv-1", "resend": True})
        assert resp.status_code == 200
        list(resp.response)

    # El LLM recibe el contenido resuelto
    assert "contenido del adjunto" in captured["messages"][-1]["content"]
    # Solo 1 save_memory (la respuesta), no la pregunta (resend)
    assert len(created["mc"].save_memory.call_args_list) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -k "upload or adjuntos or marcadores" -v -p no:cacheprovider`
Expected: FAIL with `404 NOT FOUND` en `/api/upload` y sin resolucion de marcadores

- [ ] **Step 3: Write minimal implementation**

Modify `src/app.py`:

1. Import de attachments (junto a los imports existentes del modulo):

```python
from attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MARKER_RE,
    resolve_attachments,
    save_upload,
)
```

2. `_build_chat_messages`: anadir parametro `user` opcional y resolver marcadores:

```python
def _build_chat_messages(mem_client: MemoryClient, conversation_id: str | None,
                         text: str, user: str | None = None) -> list:
    """Construye la lista de mensajes para el LLM.

    Incluye el system prompt, el historial de la conversacion (limitado a
    MAX_HISTORY_MESSAGES, manteniendo los mas recientes) y el mensaje actual
    del usuario. Los marcadores de adjuntos [adjunto:...] se resuelven a su
    contenido (unico punto de resolucion: mensajes nuevos, resend e historial).
    Si falla la carga del historial, continua solo con system+user.
    """
    if user is None:
        user = config.memory_user
    messages = [{"role": "system", "content": config.system_prompt}]
    if conversation_id:
        try:
            mem = mem_client.get_history(conversation_id)
            history = mem.get("memories", [])[-MAX_HISTORY_MESSAGES:]
            for m in history:
                role = _llm_role(_memory_role(m))
                content = m.get("text", "")
                if role == "user":
                    content = resolve_attachments(content, user, Path(config.upload_dir))
                messages.append({"role": role, "content": content})
        except Exception as e:
            app.logger.warning("No se pudo cargar historial de %s: %s", conversation_id, e)
    messages.append({"role": "user", "content": resolve_attachments(text, user, Path(config.upload_dir))})
    return messages
```

3. `api_chat`: validar maximo de adjuntos y pasar el usuario:

```python
    resend = bool(data.get("resend", False))
    if not text:
        return jsonify({"error": "Falta el texto"}), 400
    if len(MARKER_RE.findall(text)) > MAX_ATTACHMENTS_PER_MESSAGE:
        return jsonify({"error": f"Maximo {MAX_ATTACHMENTS_PER_MESSAGE} adjuntos por mensaje"}), 400
```

Y dentro de `generate()`:

```python
    def generate():
        mem_client = _get_memory_client()
        user = _current_user() or config.memory_user
        messages = _build_chat_messages(mem_client, conversation_id, text, user)
```

4. Nuevo endpoint `api_upload` (anadir tras `api_transcribe`):

```python
@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Sube un archivo adjunto a la conversacion (documento como texto).

    Guarda el archivo en uploads/<usuario>/, extrae el texto y devuelve la
    metadata {file_id, name, chars, truncated}. El contenido no viaja al
    frontend; el backend lo re-lee al resolver los marcadores en /api/chat.
    """
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "No se recibio archivo"}), 400
    user = _current_user() or config.memory_user
    try:
        result = save_upload(file, user, Path(config.upload_dir))
    except ValueError as e:
        msg = str(e)
        status = 413 if "demasiado grande" in msg else 400
        return jsonify({"error": msg}), status
    return jsonify(result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -k "upload or adjuntos or marcadores" -v -p no:cacheprovider`
Expected: PASS (7 tests nuevos)

- [ ] **Step 5: Run full suite (regresion)**

Run: `python -m pytest tests -q -p no:cacheprovider`
Expected: PASS (todos, incluidos los 3 tests existentes de `_build_chat_messages` con 3 argumentos en `test_app_conversations.py`)

- [ ] **Step 6: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "feat(app): endpoint /api/upload y resolucion de marcadores de adjuntos en chat"
```

---

### Task 5: Frontend split — `app.js` -> `chatState()` + registro `Alpine.data`

**Files:**
- Modify: `static/app.js`
- Modify: `static/index.html` (solo el body x-data y el orden de scripts; el resto en Task 7)

Requiere que `static/attachments.js` exista con `attachmentState()` (Task 6) para que el registro no falle en runtime. El orden de ejecucion real es: Task 6 crea attachments.js; Task 5 refactoriza app.js; Task 7 ajusta index.html. Ejecutar Task 6 antes de verificar Task 5, o verificar ambas juntas.

- [ ] **Step 1: Refactor `voiceApp()` -> `chatState()`**

En `static/app.js`:
- Renombrar `function voiceApp() {` a `function chatState() {`
- Al final del archivo (tras el cierre de `chatState`), anadir el registro:

```js
// Registro del componente Alpine con el patron Alpine.data (sin build step).
// attachmentState() vive en static/attachments.js y debe cargarse antes.
document.addEventListener("alpine:init", () => {
  Alpine.data("voiceApp", () => ({ ...chatState(), ...attachmentState() }));
});
```

- [ ] **Step 2: Integrar adjuntos en sendText/openConversation/resendMessage**

En `static/app.js`, dentro de `chatState()`:

**`sendText()`** — usar adjuntos pendientes y guardar el mensaje limpio con `attachments`:

```js
async sendText() {
  const cleanDraft = this.draft.trim();
  const attachments = [...(this.pendingAttachments || [])];
  const markers = attachments.map((a) => this.buildMarker(a)).join("\n");
  const text = cleanDraft + (markers ? "\n" + markers : "");
  if (!text.trim()) return;
  this.draft = "";
  this.pendingAttachments = [];
  this.messages.push({ role: "user", content: cleanDraft, attachments: attachments });
  this.loading = true;
  this.streamCancelled = false;
  this.metrics = { elapsed_ms: 0, chars: 0, tokens: 0, prompt_tokens: 0, tokens_est: 0, tokens_per_sec: 0 };
  this.scrollToBottom();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        conversation_id: this.currentConvId,
      }),
    });
    // ...resto del stream identico al actual (reader/decoder/botIdx)...
```

**`openConversation()`** — parsear marcadores en mensajes de usuario:

```js
async openConversation(id) {
  this.currentConvId = id;
  try {
    const res = await fetch(`/api/conversations/${id}`);
    const data = await res.json();
    const mems = data.memories || [];
    this.messages = mems.map((m) => {
      const role = m.role === "user" ? "user" : m.role === "note" ? "note" : "assistant";
      if (role === "user") {
        const parsed = this.parseMarkers(m.text || "");
        return { role, content: parsed.clean_text, attachments: parsed.attachments };
      }
      return { role, content: m.text || "" };
    });
    this.scrollToBottom();
  } catch (e) {
    console.error("Error abriendo conversacion", e);
  }
},
```

**`resendMessage()`** — reconstruir texto + marcadores desde `msg.attachments`:

```js
async resendMessage(botIdx) {
  let userMsg = null;
  for (let i = botIdx - 1; i >= 0; i--) {
    if (this.messages[i].role === "user") {
      userMsg = this.messages[i];
      break;
    }
  }
  if (!userMsg) {
    this.statusText = "No hay mensaje anterior para reenviar";
    setTimeout(() => (this.statusText = "Listo"), 2000);
    return;
  }

  const atts = userMsg.attachments || [];
  const markers = atts.map((a) => this.buildMarker(a)).join("\n");
  const text = (userMsg.content || "").trim() + (markers ? "\n" + markers : "");

  this.loading = true;
  this.streamCancelled = false;
  this.metrics = { elapsed_ms: 0, chars: 0, tokens: 0, prompt_tokens: 0, tokens_est: 0, tokens_per_sec: 0 };
  this.messages[botIdx] = { ...this.messages[botIdx], content: "" };
  this.scrollToBottom();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        conversation_id: this.currentConvId,
        resend: true,
      }),
    });
    // ...resto del stream identico al actual...
```

Nota: el resto de `sendText` y `resendMessage` (reader/decoder/bucle de eventos) NO cambia; solo cambian la construccion del `text` y el push del mensaje.

- [ ] **Step 3: Cambiar `x-data` en index.html**

En `static/index.html`, cambiar `<body x-data="voiceApp()" x-init="init()">` a:

```html
<body x-data="voiceApp" x-init="init()">
```

- [ ] **Step 4: Verificar sin regresion (Playwright smoke)**

Ejecutar la app (Task 7 completa el HTML; si Task 6 y 7 no estan hechas, este smoke fallara por `attachmentState is not defined` — saltar al Task 7 y volver aqui).

Run: `python src/app.py` (si llama.cpp no corre, la app levanta igual; el chat dara error de conexion, eso es aceptable para el smoke de UI)
Expected: la pagina carga sin errores de consola JS, el login funciona, la sidebar carga.

Verificacion manual alternativa (sin Playwright): abrir `http://localhost:5000` y comprobar en DevTools que no hay `ReferenceError: attachmentState is not defined`.

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/index.html
git commit -m "refactor(frontend): app.js a chatState() + Alpine.data e integracion de adjuntos en chat"
```

---

### Task 6: `static/attachments.js` — `attachmentState()`

**Files:**
- Create: `static/attachments.js`

- [ ] **Step 1: Write the implementation**

Create `static/attachments.js`:

```js
// static/attachments.js
// Estado y logica de adjuntos de archivos (documentos como texto).
// Se mezcla con chatState() en el registro de Alpine.data (ver app.js).
function attachmentState() {
  return {
    pendingAttachments: [],

    async attachFiles(event) {
      const files = Array.from(event.target.files || []);
      event.target.value = "";
      if (files.length === 0) return;
      if (this.pendingAttachments.length + files.length > 5) {
        this.statusText = "Maximo 5 adjuntos por mensaje";
        setTimeout(() => (this.statusText = "Listo"), 3000);
        return;
      }
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file);
        this.statusText = "Subiendo " + file.name + "...";
        try {
          const res = await fetch("/api/upload", { method: "POST", body: formData });
          const data = await res.json();
          if (!res.ok || data.error) {
            this.statusText = "Error al subir: " + (data.error || res.status);
          } else {
            this.pendingAttachments.push({
              file_id: data.file_id,
              name: data.name,
              chars: data.chars,
            });
            this.statusText = data.warning || "Listo";
          }
        } catch (e) {
          this.statusText = "Error de red al subir archivo";
        }
        setTimeout(() => (this.statusText = "Listo"), 3000);
      }
    },

    removePendingAttachment(index) {
      this.pendingAttachments.splice(index, 1);
    },

    buildMarker(att) {
      const name = String(att.name || "").replace(/[\[\]|]/g, "_").slice(0, 80);
      return `[adjunto:${att.file_id}|${name}]`;
    },

    parseMarkers(text) {
      // Extrae [adjunto:<file_id>|<nombre>] de un mensaje de usuario y devuelve
      // { clean_text, attachments: [{file_id, name}] }.
      const attachments = [];
      const clean = String(text || "").replace(
        /\[adjunto:([0-9a-f]{12})\|([^\]]*)\]/g,
        (m, id, name) => {
          attachments.push({ file_id: id, name: name });
          return "";
        }
      );
      return { clean_text: clean.replace(/\n{3,}/g, "\n\n").trim(), attachments };
    },
  };
}
```

- [ ] **Step 2: Verificar sintaxis**

Run: `node --check static/attachments.js`
Expected: sin salida (sintaxis valida). Si node no esta disponible, omitir este paso y verificar via navegador en Task 7.

- [ ] **Step 3: Commit**

```bash
git add static/attachments.js
git commit -m "feat(frontend): attachmentState con upload, chips y marcadores"
```

---

### Task 7: `static/index.html` — boton adjuntar, chips y orden de scripts

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Orden de scripts**

En `static/index.html`, cambiar el bloque de scripts a:

```html
<script defer src="/attachments.js"></script>
<script defer src="/app.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
```

- [ ] **Step 2: Boton adjuntar + input + chips pendientes**

En el `.input-area` (antes del boton de microfono), anadir:

```html
<button class="btn btn-attach" @click="$refs.fileInput.click()"
        :disabled="loading" title="Adjuntar archivo">+</button>
<input type="file" x-ref="fileInput" class="hidden-file"
       accept=".txt,.pdf,.docx" multiple @change="attachFiles($event)">
```

Despues del boton `Enviar` y ANTES de `metrics-bar`, anadir la zona de chips pendientes:

```html
<div class="attachments-pending" x-show="(pendingAttachments || []).length > 0">
  <template x-for="(att, i) in pendingAttachments" :key="att.file_id">
    <span class="attachment-chip">
      <span class="attachment-name" x-text="att.name"></span>
      <span class="attachment-meta" x-text="att.chars + ' chars'"></span>
      <button class="btn btn-sm attachment-remove" @click="removePendingAttachment(i)">x</button>
    </span>
  </template>
</div>
```

- [ ] **Step 3: Chips en mensajes de usuario**

Reemplazar el template `msg.role === 'user'`:

```html
<template x-if="msg.role === 'user'">
  <div class="msg-user-wrap">
    <template x-if="msg.attachments && msg.attachments.length > 0">
      <div class="msg-attachments">
        <template x-for="att in msg.attachments" :key="att.file_id">
          <span class="attachment-chip" x-text="att.name"></span>
        </template>
      </div>
    </template>
    <div class="msg-text" x-text="msg.content"></div>
  </div>
</template>
```

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): boton adjuntar, chips pendientes y chips en mensajes de usuario"
```

---

### Task 8: `static/style.css` — estilos de chips y boton adjuntar

**Files:**
- Modify: `static/style.css` (append al final)

- [ ] **Step 1: Write the styles**

Append a `static/style.css` (coherentes con las variables del tema existente: `--bg-input`, `--border`, `--accent`, `--text`):

```css
/* Adjuntos de archivos (documentos como texto) */
.btn-attach { min-width: 36px; font-size: 18px; line-height: 1; }
.hidden-file { display: none; }
.attachments-pending {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
  padding-top: 6px;
}
.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-input);
  font-size: 12px;
  max-width: 240px;
}
.attachment-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.attachment-meta { color: var(--text-dim, #888); }
.attachment-remove { padding: 0 4px; font-size: 12px; }
.msg-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 4px;
}
.msg-user-wrap { display: flex; flex-direction: column; }
```

Nota: `--text-dim` puede no existir; si no existe, usar un gris fijo `#888` (el fallback ya esta en el CSS).

- [ ] **Step 2: Verificar visual (Playwright)**

Ejecutar la app, login, adjuntar un `.txt` y comprobar que el chip se renderiza. Ver Task 9 para el E2E completo.

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "style(frontend): estilos de chips de adjuntos y boton adjuntar"
```

---

### Task 9: Verificacion completa + E2E + documento de sesion

**Files:**
- Modify: `docs/sesiones/2026-09-06-voice-webui-adjuntar-archivos.md` (nuevo)

- [ ] **Step 1: Suite completa**

Run: `python -m pytest tests -q -p no:cacheprovider`
Expected: PASS (todos). Sin tests flaky preexistentes rotos (`tests/test_llama_tool_calling.py` puede fallar si llama.cpp esta ocupado; no relacionado).

- [ ] **Step 2: Smoke de rutas**

Run: `python -c "import sys; sys.path.insert(0, 'src'); import app; print([str(r) for r in app.app.url_map.iter_rules() if 'upload' in str(r)])"`
Expected: `['/api/upload']` presente

- [ ] **Step 3: E2E Playwright (flujo real)**

1. Levantar la app: `python src/app.py`
2. Navegar a `http://localhost:5000`, login con nombre de prueba
3. Crear un archivo `prueba.txt` local con contenido conocido
4. Click en el boton adjuntar, seleccionar `prueba.txt`
5. Verificar chip pendiente con nombre y chars
6. Escribir "Resume el contenido del archivo" y Enviar
7. Verificar: si llama.cpp responde, la respuesta referencia el contenido (flujo completo); si no, verificar que el mensaje de error es de conexion (backend sano)
8. Recargar la pagina, reabrir la conversacion → el chip del adjunto sigue en el mensaje del usuario (persistencia)
9. Click en reload (resend) → el adjunto se reenvia (sin error de marcador)

- [ ] **Step 4: Documento de sesion**

Crear `docs/sesiones/2026-09-06-voice-webui-adjuntar-archivos.md` con la estructura del proyecto (Objetivo / Diseno / Tests / Results / Pending), incluyendo:
- Decisiones tomadas (documentos como texto, inyeccion, persistencia archivo+referencia, enfoque A marcadores)
- El refinamiento del spec (`_build_chat_messages` con user opcional)
- Pendientes: GC de archivos huerfanos, cache de extraccion

- [ ] **Step 5: Commit final**

```bash
git add docs/sesiones/2026-09-06-voice-webui-adjuntar-archivos.md
git commit -m "docs: sesion 2026-09-06 adjuntar-archivos (documentos como texto)"
```