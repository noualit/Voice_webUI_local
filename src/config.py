# config.py
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Directorio raiz del proyecto (padre de src/) para resolver los archivos de
# configuracion de forma independiente del directorio de trabajo actual (cwd).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config.json")))

# Variables cuyo valor de .env puede quedar obsoleto: config.json se escribe
# desde la UI y se carga DESPUES, asi que gana sobre el entorno (si no, cambiar
# la URL en la interfaz seria imposible). El conflicto se avisa porque el
# silencio costo una sesion entera de diagnostico el 2026-09-26: .env apuntaba
# a localhost:8080 y config.json a la IP del servidor de inferencia, sin forma
# de saber cual mandaba.
_ENV_VS_JSON = (("llama_url", "LLAMA_URL"),)


class AppConfig:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config.json")))
        self.llama_url = os.environ.get("LLAMA_URL", "http://localhost:8080/v1/chat/completions")
        self.model = os.environ.get("LLAMA_MODEL", "qwen3.6-35b")
        self.llm_timeout = int(os.environ.get("LLM_TIMEOUT", "600"))
        self.whisper_model = os.environ.get("WHISPER_MODEL", "small")
        self.mcp_config_path = os.environ.get("MCP_CONFIG_PATH", str(PROJECT_ROOT / "mcp-config.json"))
        self.memory_url = os.environ.get("MEMORY_URL", "http://localhost:9001")
        self.memory_user = os.environ.get("MEMORY_USER", "default")
        self.upload_dir = os.environ.get("UPLOAD_DIR", "uploads/")
        self.system_prompt = ""
        self.mcp_prompts: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key in ("llama_url", "model", "llm_timeout",
                            "whisper_model", "mcp_config_path", "memory_url",
                            "memory_user", "system_prompt", "upload_dir",
                            "mcp_prompts"):
                    if key in data:
                        setattr(self, key, data[key])
            except (json.JSONDecodeError, OSError):
                pass
        self._avisar_conflicto_env()
        # Resolver mcp_config_path relativo contra la raiz del proyecto, para que
        # no dependa del directorio de trabajo actual al ejecutar desde src/.
        mcp = Path(self.mcp_config_path)
        if not mcp.is_absolute():
            self.mcp_config_path = str(PROJECT_ROOT / mcp)
        # Resolver upload_dir relativo contra la raiz del proyecto.
        up = Path(self.upload_dir)
        if not up.is_absolute():
            self.upload_dir = str(PROJECT_ROOT / up)
        if not self.path.exists():
            self.save()

    def _avisar_conflicto_env(self) -> None:
        """Deja por consola que valor manda y cual se queda fuera.

        Sin esto, editar LLAMA_URL en .env no produce ningun efecto visible
        (gana config.json) y parece que el cambio se ignora a proposito.
        """
        for attr, env_var in _ENV_VS_JSON:
            desde_env = os.environ.get(env_var)
            if desde_env and desde_env != getattr(self, attr):
                print(f"[config] AVISO: {self.path} define {attr}='{getattr(self, attr)}' "
                      f"y manda sobre {env_var}='{desde_env}' (editado en .env, "
                      f"sin efecto). Para cambiarlo, edita {self.path} o la UI.")

    def save(self):
        data = {
            "llama_url": self.llama_url,
            "model": self.model,
            "llm_timeout": self.llm_timeout,
            "whisper_model": self.whisper_model,
            "mcp_config_path": self.mcp_config_path,
            "memory_url": self.memory_url,
            "memory_user": self.memory_user,
            "upload_dir": self.upload_dir,
            "system_prompt": self.system_prompt,
            "mcp_prompts": self.mcp_prompts,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
