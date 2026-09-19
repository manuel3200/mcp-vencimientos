"""
StreamVault v2 - High Performance Modular CRM & Financial Platform
Clean Architecture / BFF Entrypoint
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import pyotp

import database
import system_logger
from core.security import (
    create_preauth_cookie,
    create_session_cookie,
    verify_preauth_cookie,
    verify_session_cookie,
)
from mcp_server.instance import mcp
import mcp_server.tools  # Registers all FastMCP tools
from mcp_server.models import ItemCuentaLote
from routers import register_routers
from scheduler import start_scheduler, stop_scheduler
from telegram_bot import start_telegram_polling, stop_telegram_polling

# 1. Inicializar logger unificado
system_logger.setup_system_logging()
logger = logging.getLogger("main")

# 2. Servidor FastMCP HTTP
mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida de la aplicación v2: base de datos, admin, servicios y MCP."""
    # 1. Inicializar esquema de base de datos de forma idempotente
    database.init_db()

    # 2. Sincronizar credenciales del administrador desde variables de entorno
    raw_user = os.getenv("ADMIN_USERNAME")
    raw_pass = os.getenv("ADMIN_PASSWORD")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    admin_pass = raw_pass.strip() if raw_pass and raw_pass.strip() else "admin123"

    existing = database.get_admin_user(admin_user)
    totp_secret = existing.get("totp_secret") if existing else pyotp.random_base32()
    database.create_or_update_admin(admin_user, admin_pass, totp_secret)
    logger.info(f"Usuario administrador '{admin_user}' sincronizado con éxito (v2).")

    # 3. Iniciar servicios en segundo plano
    start_scheduler()
    start_telegram_polling()
    logger.info("StreamVault v2 iniciado con Scheduler y Telegram Polling.")

    # 4. Iniciar contexto de FastMCP
    async with mcp_app.lifespan(app):
        yield

    # 5. Apagado ordenado
    stop_telegram_polling()
    stop_scheduler()
    logger.info("StreamVault v2 detenido correctamente.")

# 3. Creación de la instancia FastAPI
app = FastAPI(
    title="StreamVault v2 CRM & Interactive Financial Platform",
    description="Servidor Modular v2 para Gemini Spark, CRM de Streaming y Finanzas",
    version="2.0.0",
    lifespan=lifespan,
)

# 4. Montar aplicación FastMCP en /mcp
app.mount("/mcp", mcp_app)

# 5. Middleware de Protección OAuth 2.0 para /mcp
@app.middleware("http")
async def mcp_oauth_guard(request: Request, call_next):
    """Protege los endpoints /mcp con OAuth 2.0 Bearer Token (RFC 6749 / RFC 6750)."""
    path = request.url.path
    if path.startswith("/mcp"):
        # Permitir discovery público (.well-known)
        if "/.well-known/" in path:
            return await call_next(request)

        # Extraer token Bearer
        auth_header = request.headers.get("Authorization", "").strip()
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.query_params.get("access_token", "").strip()

        oauth_cfg = database.get_oauth_settings()
        if oauth_cfg.get("enabled", 1):
            token_data = database.verify_oauth_access_token(token) if token else None
            if not token_data:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "unauthorized",
                        "message": "Acceso protegido por OAuth 2.0. Se requiere token Bearer válido.",
                    },
                    headers={
                        "WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token is missing or invalid"'
                    },
                )
    return await call_next(request)

# 6. Health check endpoint
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "streamvault-v2-crm",
        "version": "2.0.0",
    }

# 7. Registrar todos los routers modulares
register_routers(app)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
