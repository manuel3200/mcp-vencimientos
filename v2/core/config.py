import os
import secrets
from typing import Optional, Set
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv()

FORBIDDEN_DEFAULT_SECRETS: Set[str] = {
    "mcp-super-secret-key-change-in-prod-2026",
    "mcp-evolution-key-2026",
    "admin123",
    "password",
    "123456",
    "12345678",
    "changeme",
    "secret",
    "default",
}


def required_random_secret(name: str, raw_value: Optional[str] = None, min_length: int = 32) -> str:
    """Valida que un secreto crítico no sea vacío, corto ni un valor por defecto conocido (V01).
    Nunca incluye el valor rechazado en el mensaje de excepción.
    """
    value = (raw_value if raw_value is not None else os.environ.get(name, "")).strip()
    if not value or len(value) < min_length or value.lower() in FORBIDDEN_DEFAULT_SECRETS or value in FORBIDDEN_DEFAULT_SECRETS:
        raise RuntimeError(f"Configuración de seguridad inválida o insegura para la variable obligatoria: {name}")
    return value


class Settings:
    # Rutas base
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Directorios y almacenamiento
    DATA_DIR: str = os.getenv("DATA_DIR", "/app/data")
    DB_PATH: str = os.path.join(DATA_DIR, "services.db")
    LOG_PATH: str = os.path.join(DATA_DIR, "system.log")

    # Directorio de plantillas
    TEMPLATES_DIR: str = os.path.join(BASE_DIR, "presentation", "templates")

    # Servidor Web
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")

    # Seguridad y Sesiones (V01: sin secretos predecibles por defecto; efímero aleatorio solo si no se definió en entorno de test)
    _RAW_SESSION_SECRET: str = os.getenv("SESSION_SECRET_KEY", "").strip()
    SESSION_SECRET_KEY: str = (
        _RAW_SESSION_SECRET
        if (_RAW_SESSION_SECRET and _RAW_SESSION_SECRET not in FORBIDDEN_DEFAULT_SECRETS)
        else secrets.token_urlsafe(48)
    )
    DB_SECRET_KEY: str = os.getenv("DB_SECRET_KEY", "").strip()
    BACKUP_ENCRYPTION_KEY: str = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
    AUDIT_HMAC_KEY: str = os.getenv("AUDIT_HMAC_KEY", "").strip()
    FINANCE_API_TOKEN: str = (
        os.getenv("FINANCE_API_TOKEN") or os.getenv("STREAMVAULT_FINANCE_API_TOKEN") or ""
    ).strip()
    N8N_WEBHOOK_SECRET: str = os.getenv("N8N_WEBHOOK_SECRET", "").strip()

    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()
    EVOLUTION_WEBHOOK_SECRET: str = os.getenv("EVOLUTION_WEBHOOK_SECRET", "").strip()

    # O01: Soportar tanto ADMIN_USERNAME como ADMIN_USER
    ADMIN_USERNAME: str = (
        os.getenv("ADMIN_USERNAME") or os.getenv("ADMIN_USER") or "admin"
    ).strip().lower()
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "").strip()
    ADMIN_WHATSAPP: str = os.getenv("ADMIN_WHATSAPP", "").strip()

    # Dominio Público y Enlaces Efímeros
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000").strip().rstrip("/")

    # Bot de Telegram (O01: Soportar tanto TELEGRAM_CHAT_ID como TELEGRAM_ADMIN_CHAT_ID)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = (
        os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_CHAT_ID") or ""
    ).strip()

    # Tareas Programadas y Alertas
    DAYS_BEFORE_ALERT: int = int(os.getenv("DAYS_BEFORE_ALERT", "7"))
    ALERT_HOUR: int = int(os.getenv("ALERT_HOUR", "9"))
    ALERT_MINUTE: int = int(os.getenv("ALERT_MINUTE", "0"))
    TIMEZONE: str = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")

    # Integración WhatsApp (Evolution API) (V01: sin clave por defecto predecible)
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080").strip().rstrip("/")
    EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "").strip()
    EVOLUTION_INSTANCE_NAME: str = os.getenv("EVOLUTION_INSTANCE_NAME", "streaming-bot").strip()

    # Chatwoot
    CHATWOOT_URL: str = os.getenv("CHATWOOT_URL", "http://chatwoot-rails:3000").strip().rstrip("/")
    CHATWOOT_TOKEN: str = os.getenv("CHATWOOT_TOKEN", "").strip()
    CHATWOOT_ACCOUNT_ID: str = os.getenv("CHATWOOT_ACCOUNT_ID", "1").strip()
    CHATWOOT_WEBHOOK_SECRET: str = os.getenv("CHATWOOT_WEBHOOK_SECRET", "").strip()

    def validate_startup_secrets(self, strict: bool = True) -> None:
        """Valida los secretos configurados antes de iniciar la aplicación (V01)."""
        raw_session = os.environ.get("SESSION_SECRET_KEY", "").strip()
        if strict or raw_session:
            required_random_secret("SESSION_SECRET_KEY", raw_session, min_length=32)

        raw_admin_pass = os.environ.get("ADMIN_PASSWORD", "").strip()
        if raw_admin_pass:
            if raw_admin_pass.lower() in FORBIDDEN_DEFAULT_SECRETS or len(raw_admin_pass) < 8:
                raise RuntimeError("Configuración inválida: ADMIN_PASSWORD insegura o por defecto prohibida")

        raw_evo_key = os.environ.get("EVOLUTION_API_KEY", "").strip()
        if raw_evo_key and raw_evo_key in FORBIDDEN_DEFAULT_SECRETS:
            raise RuntimeError("Configuración inválida: EVOLUTION_API_KEY utiliza un valor por defecto prohibido")


settings = Settings()
