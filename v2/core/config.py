import os
from typing import Optional
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv()

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

    # Seguridad y Sesiones
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "mcp-super-secret-key-change-in-prod-2026")
    BACKUP_ENCRYPTION_KEY: str = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()
    EVOLUTION_WEBHOOK_SECRET: str = os.getenv("EVOLUTION_WEBHOOK_SECRET", "").strip()
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin").strip().lower()
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123").strip()
    ADMIN_WHATSAPP: str = os.getenv("ADMIN_WHATSAPP", "").strip()

    # Dominio Público y Enlaces Efímeros
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000").strip().rstrip("/")

    # Bot de Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # Tareas Programadas y Alertas
    DAYS_BEFORE_ALERT: int = int(os.getenv("DAYS_BEFORE_ALERT", "7"))
    ALERT_HOUR: int = int(os.getenv("ALERT_HOUR", "9"))
    ALERT_MINUTE: int = int(os.getenv("ALERT_MINUTE", "0"))
    TIMEZONE: str = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")

    # Integración WhatsApp (Evolution API)
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080").strip().rstrip("/")
    EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "mcp-evolution-key-2026").strip()
    EVOLUTION_INSTANCE_NAME: str = os.getenv("EVOLUTION_INSTANCE_NAME", "streaming-bot").strip()

    # Chatwoot
    CHATWOOT_URL: str = os.getenv("CHATWOOT_URL", "http://chatwoot-rails:3000").strip().rstrip("/")
    CHATWOOT_TOKEN: str = os.getenv("CHATWOOT_TOKEN", "").strip()
    CHATWOOT_ACCOUNT_ID: str = os.getenv("CHATWOOT_ACCOUNT_ID", "1").strip()
    CHATWOOT_WEBHOOK_SECRET: str = os.getenv("CHATWOOT_WEBHOOK_SECRET", "").strip()


settings = Settings()
