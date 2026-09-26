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
from core.config import settings, FORBIDDEN_DEFAULT_SECRETS
from core.principal import (
    authenticate_service_token,
    set_current_mcp_principal,
    reset_current_mcp_principal,
    Principal,
)
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
    # 0. Validar secretos críticos en arranque (Fail-Closed en producción - V01)
    settings.validate_startup_secrets()

    # 1. Inicializar esquema de base de datos de forma idempotente
    database.init_db()

    # 2. Bootstrap seguro del administrador (V01 / O02: no sobrescribe contraseña existente en cada reinicio)
    raw_user = os.getenv("ADMIN_USERNAME") or os.getenv("ADMIN_USER")
    raw_pass = os.getenv("ADMIN_PASSWORD")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    admin_pass = raw_pass.strip() if raw_pass and raw_pass.strip() else ""

    existing = database.get_admin_user(admin_user)
    force_reset = os.getenv("FORCE_ADMIN_PASSWORD_RESET", "0").strip() in ("1", "true", "yes")

    if existing is None:
        if not admin_pass or admin_pass in FORBIDDEN_DEFAULT_SECRETS:
            if settings.APP_ENV == "production":
                raise RuntimeError(
                    "CRITICAL [V01]: ADMIN_PASSWORD no está configurada o usa un valor por defecto inseguro "
                    "para el bootstrap inicial en producción."
                )
            import secrets as _sec
            admin_pass = _sec.token_urlsafe(24)
            logger.warning(f"Bootstrap dev/test: se generó contraseña efímera aleatoria para '{admin_user}'.")
        database.bootstrap_admin_once(admin_user, admin_pass, pyotp.random_base32())
        logger.info(f"Usuario administrador '{admin_user}' creado en bootstrap inicial (v2).")
    elif force_reset and admin_pass and admin_pass not in FORBIDDEN_DEFAULT_SECRETS:
        totp_secret = existing.get("totp_secret") or pyotp.random_base32()
        database.create_or_update_admin(admin_user, admin_pass, totp_secret)
        logger.warning(f"Contraseña del administrador '{admin_user}' restablecida por FORCE_ADMIN_PASSWORD_RESET=1.")
    else:
        logger.info(f"Usuario administrador '{admin_user}' ya existe; se preserva su credencial en BD (O02).")

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

# 5. Middleware de Protección OAuth 2.0 y Contexto de Identidad para /mcp (V05, V08)
@app.middleware("http")
async def mcp_oauth_guard(request: Request, call_next):
    """Protege los endpoints /mcp con OAuth 2.0 / Service Bearer Token (RFC 6750) e inyecta el Principal."""
    path = request.url.path
    if path.startswith("/mcp"):
        # Permitir discovery público (.well-known)
        if "/.well-known/" in path:
            return await call_next(request)

        # Rechazar explícitamente access_token en query string para evitar fuga en logs/proxies (RFC 6750 §2.3)
        if request.query_params.get("access_token"):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "No se permite enviar access_token en la URL. Utilice la cabecera Authorization: Bearer.",
                },
            )

        # Extraer token exclusivamente de cabecera Authorization: Bearer
        auth_header = request.headers.get("Authorization", "").strip()
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        oauth_cfg = database.get_oauth_settings()
        principal = authenticate_service_token(token) if token else None

        if oauth_cfg.get("enabled", 1):
            if not principal:
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
        elif principal is None:
            # Solo cuando OAuth está explícitamente deshabilitado en entorno local de pruebas
            principal = Principal(subject="local-dev", kind="admin", scopes=frozenset({"*", "mcp:admin"}))

        ctx_token = set_current_mcp_principal(principal)
        try:
            return await call_next(request)
        finally:
            reset_current_mcp_principal(ctx_token)

    return await call_next(request)

# 5. Middleware de Cabeceras de Seguridad HTTP (OWASP A05:2021)
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Inyecta cabeceras HTTP de endurecimiento y seguridad defensiva en todas las respuestas."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: 'unsafe-inline' 'unsafe-eval' data: blob:; "
            "img-src 'self' data: https: blob:; "
            "font-src 'self' https: data:; "
            "frame-ancestors 'none';"
        )
    return response

# 6. Manejador Global de Excepciones Sanitizado (OWASP CWE-209 Anti-Information Disclosure)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global de excepciones no controladas.
    Registra el stack trace completo en el log seguro pero devuelve una respuesta sanitizada al cliente.
    """
    logger.exception(f"Excepción no controlada en {request.method} {request.url.path}: {exc}")
    accept_header = request.headers.get("accept", "").lower()
    if "text/html" in accept_header:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            status_code=500,
            content="""<!DOCTYPE html><html><head><title>Error 500 - StreamVault</title></head>
            <body style='font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:2rem;text-align:center;'>
            <h2>Error Interno del Servidor</h2><p style='color:#94a3b8;'>Ocurrió un error procesando su solicitud. El evento ha sido registrado en auditoría.</p>
            </body></html>"""
        )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": "Internal Server Error",
            "message": "Ocurrió un error interno procesando su solicitud."
        }
    )

# 7. Health check endpoint
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
