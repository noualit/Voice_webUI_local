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
        keep = max(0, 80 - len(ext))
        base = stem[:keep] + ext[:max(0, 80 - keep)]
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
    user = sanitize_filename(user)
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
            # Avisos acotados en la practica: no cuentan para la cuota
            parts.append(f"[archivo adjunto no disponible: {display_name}]")
            continue
        name, content = loaded
        if not content.strip():
            # Avisos acotados en la practica: no cuentan para la cuota
            parts.append(f"[sin texto extraible: {name}]")
            continue
        block = f'[Contenido del archivo "{name}" ({len(content)} caracteres)]\n{content}'
        remaining = MAX_TOTAL_ATTACHMENT_CHARS - total_chars
        if remaining <= 0:
            truncated_total = True
            continue
        if len(block) > remaining:
            block = block[:max(0, remaining - len(_TRUNCATE_SUFFIX))] + _TRUNCATE_SUFFIX
            truncated_total = True
        total_chars += len(block)
        parts.append(block)
    parts.append(text[last_end:])
    resolved = "".join(parts)
    if truncated_total:
        resolved += "\n[aviso: adjuntos truncados por limite de caracteres]"
    return resolved
