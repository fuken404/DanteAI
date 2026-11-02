"""Configuración por variables de entorno. Se importa primero para que .env ya esté cargado."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

CHAT_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MEMORY_MODEL = os.getenv("MEMORY_MODEL", "claude-haiku-5-5")
PUBLIC_MODEL = os.getenv("PUBLIC_MODEL", "claude-haiku-5-5")
SECRET_VARIABLES = ("TELEGRAM_TOKEN", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "DATABASE_URL", "WEBHOOK_SECRET")


def allowed_user_ids(raw: str | None = None) -> set[int]:
    """Lista de acceso. Si está vacía o mal escrita no entra nadie: nunca queda abierta por error."""
    raw = os.getenv("ALLOWED_USER_IDS", "") if raw is None else raw
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdecimal():
            ids.add(int(part))
        elif part:
            logging.getLogger(__name__).warning("ALLOWED_USER_IDS tiene un valor inválido; se ignora")
    return ids


def public_demo_enabled() -> bool:
    """Apagada salvo que se pida: abrir el bot gasta crédito."""
    return os.getenv("PUBLIC_DEMO", "").strip().lower() in ("1", "true", "si", "sí", "yes")


def daily_limit_per_user() -> int:
    return int(os.getenv("PUBLIC_DAILY_LIMIT_PER_USER", "10"))


def daily_limit_total() -> int:
    return int(os.getenv("PUBLIC_DAILY_LIMIT_TOTAL", "100"))


def webhook_base_url() -> str | None:
    url = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
    if url:
        return url
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    return f"https://{hostname}" if hostname else None


class RedactingFormatter(logging.Formatter):
    """Quita los secretos de todo lo registrado: la URL de Telegram lleva el token dentro."""

    def __init__(self, *args, secrets: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._secrets = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        secrets=[os.getenv(name, "") for name in SECRET_VARIABLES],
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Estas librerías registran cada URL en INFO. No aportan y son ruido con datos sensibles.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
