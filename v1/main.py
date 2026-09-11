import os
import secrets
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import pyotp

import database
from telegram_bot import send_telegram_message, format_and_send_alert
from scheduler import start_scheduler, stop_scheduler, check_and_send_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

# ==========================================
# Configuración de Seguridad y Sesiones
# ==========================================
SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "mcp-super-secret-key-change-in-prod-2026")
serializer = URLSafeTimedSerializer(SECRET_KEY)

def create_session_cookie(username: str) -> str:
    return serializer.dumps({"user": username, "auth": True}, salt="session-auth")

def verify_session_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="session-auth", max_age=86400 * 7) # 7 días
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

def create_preauth_cookie(username: str) -> str:
    return serializer.dumps({"user": username, "step": "2fa"}, salt="preauth")

def verify_preauth_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="preauth", max_age=300) # 5 minutos
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

# ==========================================
# 1. Definición del Servidor FastMCP
# ==========================================
mcp = FastMCP("Vencimientos & Telegram Bot")

@mcp.tool()
def agregar_servicio(
    nombre: str,
    fecha_vencimiento: str,
    costo: str = "",
    categoria: str = "Servicio",
    recurrencia: str = "mensual",
    notas: str = ""
) -> str:
    """Registra un nuevo servicio o suscripción para monitoreo de vencimiento.
    - nombre: Nombre del servicio (ej. 'Netflix', 'Hosting Oracle', 'Dominio juanconnect.online').
    - fecha_vencimiento: Formato 'YYYY-MM-DD' (ej. '2026-10-15').
    - costo: Precio o tarifa (ej. '$12 USD', '1500 ARS').
    - categoria: Tipo de servicio (ej. 'Hosting', 'Suscripción', 'Dominio', 'Seguro').
    - recurrencia: Periodicidad ('mensual', 'anual', 'unico', 'trimestral').
    - notas: Detalles adicionales o enlaces.
    """
    try:
        svc = database.add_service(
            name=nombre,
            expiry_date=fecha_vencimiento,
            category=categoria,
            recurrence=recurrencia,
            cost=costo,
            notes=notas
        )
        return (
            f"✅ Servicio '{svc['name']}' agregado con éxito.\n"
            f"- ID: {svc['id']}\n"
            f"- Vence: {svc['expiry_date']}\n"
            f"- Costo: {svc['cost'] or 'No especificado'}\n"
            f"- Recurrencia: {svc['recurrence']}"
        )
    except Exception as e:
        return f"❌ Error al agregar servicio: {str(e)}"

@mcp.tool()
def listar_servicios() -> str:
    """Obtiene la lista completa de todos los servicios registrados y su estado actual."""
    svcs = database.list_services()
    if not svcs:
        return "No hay servicios registrados actualmente."
    
    output = [f"📋 Total de servicios registrados: {len(svcs)}\n"]
    for s in svcs:
        days = s.get("days_remaining")
        if days is None:
            estado = "⚠️ Fecha inválida"
        elif days < 0:
            estado = f"🚨 VENCIDO hace {abs(days)} días"
        elif days == 0:
            estado = "⚠️ Vence HOY"
        elif days <= 2:
            estado = f"🔔 Vence en {days} días (¡Próximo!)"
        else:
            estado = f"✅ Vence en {days} días"
            
        output.append(
            f"• [ID: {s['id']}] {s['name']} ({s['category']})\n"
            f"  Vence: {s['expiry_date']} | Estado: {estado}\n"
            f"  Costo: {s['cost'] or '-'} | Recurrencia: {s['recurrence']}\n"
        )
    return "\n".join(output)

@mcp.tool()
def proximos_vencimientos(dias_anticipacion: int = 7) -> str:
    """Consulta los servicios que están por vencer en los próximos días (por defecto 7 días)."""
    svcs = database.get_expiring_services(days_window=dias_anticipacion)
    if not svcs:
        return f"No hay servicios que venzan en los próximos {dias_anticipacion} días."
    
    output = [f"🔔 Servicios por vencer en los próximos {dias_anticipacion} días ({len(svcs)}):\n"]
    for s in svcs:
        days = s.get("days_remaining")
        desc_dias = "HOY" if days == 0 else (f"en {days} días" if days > 0 else f"vencido hace {abs(days)} días")
        output.append(
            f"• {s['name']} (ID: {s['id']}) - Vence {desc_dias} ({s['expiry_date']})\n"
            f"  Costo: {s['cost'] or '-'} | Recurrencia: {s['recurrence']}"
        )
    return "\n".join(output)

@mcp.tool()
def eliminar_servicio(id_servicio: int) -> str:
    """Elimina un servicio registrado a partir de su ID numérico."""
    ok = database.delete_service(service_id=id_servicio)
    if ok:
        return f"✅ Servicio con ID {id_servicio} eliminado correctamente."
    return f"❌ No se encontró ningún servicio con ID {id_servicio}."

@mcp.tool()
def renovar_servicio(id_servicio: int, nueva_fecha_vencimiento: str) -> str:
    """Actualiza la fecha de vencimiento de un servicio tras haberlo pagado o renovado."""
    ok = database.update_service_date(service_id=id_servicio, new_expiry_date=nueva_fecha_vencimiento)
    if ok:
        return f"✅ Fecha de vencimiento actualizada a {nueva_fecha_vencimiento} para el servicio ID {id_servicio}."
    return f"❌ No se pudo actualizar el servicio ID {id_servicio}."

@mcp.tool()
async def enviar_alerta_prueba_telegram(mensaje: str = "Prueba de conexión con Gemini MCP Bot") -> str:
    """Envía un mensaje de prueba al chat de Telegram configurado."""
    text = (
        "🤖 <b>Test de Conexión Gemini MCP</b>\n\n"
        f"{mensaje}\n\n"
        "✅ ¡Si recibes este mensaje, la integración de Telegram está funcionando al 100%!"
    )
    ok = await send_telegram_message(text)
    if ok:
        return "✅ Mensaje de prueba enviado exitosamente a Telegram."
    return "❌ Error: Verifica que TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID estén configurados correctamente."

@mcp.tool()
async def verificar_vencimientos_ahora(dias_anticipacion: int = 2) -> str:
    """Ejecuta una comprobación inmediata de vencimientos y envía alertas por Telegram si aplica."""
    enviadas = await check_and_send_alerts(days_window=dias_anticipacion)
    return f"Comprobación manual completada. Se enviaron {enviadas} alerta(s) por Telegram."


# ==========================================
# 2. Servidor Web FastAPI & Lifespan
# ==========================================
mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicialización de DB
    database.init_db()
    
    # Crear usuario administrador inicial si no existe
    admin_user = os.getenv("ADMIN_USERNAME", "admin").strip()
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123").strip()
    if not database.get_admin_user(admin_user):
        totp_secret = pyotp.random_base32()
        database.create_or_update_admin(admin_user, admin_pass, totp_secret)
        logger.info(f"Usuario administrador '{admin_user}' creado exitosamente.")
    else:
        # Asegurar contraseña de las variables de entorno si se especificó
        if os.getenv("ADMIN_PASSWORD"):
            database.create_or_update_admin(admin_user, admin_pass)

    start_scheduler()
    logger.info("Aplicación y tareas programadas iniciadas.")
    async with mcp_app.lifespan(app):
        yield
    stop_scheduler()
    logger.info("Aplicación detenida.")

app = FastAPI(
    title="Gemini Expiry Alert MCP & Web Panel",
    description="Servidor MCP para Gemini Spark y Panel con Autenticación 2FA",
    version="1.1.0",
    lifespan=lifespan
)

# Montar MCP en /mcp (Accesible para Gemini Spark)
app.mount("/mcp", mcp_app)


# ==========================================
# 3. Vistas de Autenticación y 2FA
# ==========================================
LOGIN_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iniciar Sesión - Control de Vencimientos</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .login-card { background: #161e2e; border: 1px solid #1e293b; border-radius: 16px; padding: 36px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .icon-box { background: #1e293b; width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; font-size: 28px; }
        h2 { text-align: center; margin: 0 0 8px 0; font-size: 1.4rem; color: #38bdf8; }
        p.subtitle { text-align: center; color: #94a3b8; font-size: 0.9rem; margin: 0 0 24px 0; }
        .form-group { margin-bottom: 18px; }
        label { display: block; margin-bottom: 6px; font-size: 0.85rem; color: #cbd5e1; font-weight: 500; }
        input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; background: #0b0f19; border: 1px solid #334155; border-radius: 8px; padding: 12px 14px; color: #fff; font-size: 0.95rem; outline: none; transition: border-color 0.2s; }
        input:focus { border-color: #38bdf8; }
        .btn { width: 100%; background: #0284c7; color: white; border: none; border-radius: 8px; padding: 12px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; margin-top: 8px; }
        .btn:hover { background: #0369a1; }
        .error-msg { background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; text-align: center; }
        .footer { text-align: center; margin-top: 24px; font-size: 0.8rem; color: #64748b; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-box">🔐</div>
        <h2>Acceso Administrativo</h2>
        <p class="subtitle">Panel de Vencimientos & Gemini MCP</p>
        {{ERROR_HTML}}
        <form action="/login" method="POST">
            <div class="form-group">
                <label>Usuario</label>
                <input type="text" name="username" required autofocus placeholder="admin">
            </div>
            <div class="form-group">
                <label>Contraseña</label>
                <input type="password" name="password" required placeholder="••••••••">
            </div>
            <button type="submit" class="btn">Continuar</button>
        </form>
        <div class="footer">Autenticación de Dos Factores (2FA) Requerida</div>
    </div>
</body>
</html>
"""

TWOFA_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Doble Factor 2FA - Verificación</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .login-card { background: #161e2e; border: 1px solid #1e293b; border-radius: 16px; padding: 36px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); text-align: center; }
        .icon-box { background: #1e293b; width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; font-size: 28px; }
        h2 { margin: 0 0 8px 0; font-size: 1.4rem; color: #38bdf8; }
        p.subtitle { color: #94a3b8; font-size: 0.9rem; margin: 0 0 20px 0; line-height: 1.4; }
        .form-group { margin-bottom: 20px; }
        input[type="text"] { width: 100%; box-sizing: border-box; background: #0b0f19; border: 1px solid #334155; border-radius: 8px; padding: 14px; color: #38bdf8; font-size: 1.8rem; font-family: monospace; letter-spacing: 6px; text-align: center; outline: none; transition: border-color 0.2s; }
        input:focus { border-color: #38bdf8; }
        .btn { width: 100%; background: #059669; color: white; border: none; border-radius: 8px; padding: 12px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #047857; }
        .error-msg { background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; }
        .hint { margin-top: 15px; font-size: 0.85rem; color: #64748b; }
        .back-link { display: inline-block; margin-top: 15px; font-size: 0.85rem; color: #38bdf8; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-box">📲</div>
        <h2>Verificación 2FA</h2>
        <p class="subtitle">Ingresa el código de 6 dígitos que enviamos a tu <b>Telegram</b> (o tu código de Google Authenticator).</p>
        {{ERROR_HTML}}
        <form action="/2fa" method="POST">
            <div class="form-group">
                <input type="text" name="otp_code" maxlength="6" pattern="[0-9]{6}" required autofocus placeholder="000000" autocomplete="off">
            </div>
            <button type="submit" class="btn">Verificar e Ingresar</button>
        </form>
        <div class="hint">El código expira en 5 minutos</div>
        <a href="/login" class="back-link">← Volver al login</a>
    </div>
</body>
</html>
"""

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    # Si ya está autenticado, redirigir directo al dashboard
    session_user = verify_session_cookie(request.cookies.get("session_token"))
    if session_user:
        return RedirectResponse(url="/", status_code=302)
        
    error_html = f'<div class="error-msg">{error}</div>' if error else ""
    return LOGIN_HTML_TEMPLATE.replace("{{ERROR_HTML}}", error_html)

@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    user = username.strip()
    if not database.verify_admin_credentials(user, password):
        return RedirectResponse(url="/login?error=Usuario+o+contrase%C3%B1a+incorrectos", status_code=302)

    # Generar código OTP de 6 dígitos para Telegram
    otp = f"{secrets.randbelow(900000) + 100000}"
    database.set_telegram_otp(user, otp, duration_seconds=300)

    # Enviar el código por Telegram
    msg = (
        "🔐 <b>Código de Verificación 2FA</b>\n\n"
        "Alguien está iniciando sesión en el Panel Web de Vencimientos.\n\n"
        f"Tu código de acceso es: <code>{otp}</code>\n\n"
        "⏱️ <i>Válido durante 5 minutos. Si no fuiste tú, revisa tu contraseña.</i>"
    )
    await send_telegram_message(msg)

    # Generar cookie de pre-autenticación
    preauth = create_preauth_cookie(user)
    response = RedirectResponse(url="/2fa", status_code=302)
    response.set_cookie(
        key="preauth_token",
        value=preauth,
        httponly=True,
        samesite="lax",
        max_age=300
    )
    return response

@app.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request, error: Optional[str] = None):
    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login", status_code=302)
        
    error_html = f'<div class="error-msg">{error}</div>' if error else ""
    return TWOFA_HTML_TEMPLATE.replace("{{ERROR_HTML}}", error_html)

@app.post("/2fa")
async def twofa_submit(request: Request, otp_code: str = Form(...)):
    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login?error=Sesi%C3%B3n+expirada.+Intenta+nuevamente.", status_code=302)

    code = otp_code.strip()
    is_valid = False

    # 1. Validar si coincide con el OTP de Telegram
    if database.verify_telegram_otp(preauth_user, code):
        is_valid = True
    else:
        # 2. Validar si coincide con TOTP (Google Authenticator)
        admin_data = database.get_admin_user(preauth_user)
        if admin_data and admin_data.get("totp_secret"):
            totp = pyotp.TOTP(admin_data["totp_secret"])
            if totp.verify(code):
                is_valid = True

    if not is_valid:
        return RedirectResponse(url="/2fa?error=C%C3%B3digo+inv%C3%A1lido+o+expirado", status_code=302)

    # Código válido: Establecer sesión completa
    session = create_session_cookie(preauth_user)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_token",
        value=session,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7 # 7 días de sesión
    )
    response.delete_cookie("preauth_token")
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_token")
    response.delete_cookie("preauth_token")
    return response


# ==========================================
# 4. Panel de Control Web (Protegido por 2FA)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    svcs = database.list_services()
    has_token = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    has_chat_id = bool(os.getenv("TELEGRAM_CHAT_ID"))
    tz = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")
    alert_hour = os.getenv("ALERT_HOUR", "9")
    days_window = os.getenv("DAYS_BEFORE_ALERT", "2")
    
    rows_html = ""
    for s in svcs:
        days = s.get("days_remaining")
        badge_class = "badge-ok"
        badge_text = f"En {days} días"
        if days is None:
            badge_class = "badge-warn"
            badge_text = "Fecha inválida"
        elif days < 0:
            badge_class = "badge-danger"
            badge_text = f"Vencido (-{abs(days)}d)"
        elif days <= 2:
            badge_class = "badge-warn"
            badge_text = f"¡Vence en {days}d!"
            
        rows_html += f"""
        <tr>
            <td>{s['id']}</td>
            <td><strong>{s['name']}</strong></td>
            <td>{s['category']}</td>
            <td><code>{s['expiry_date']}</code></td>
            <td><span class="badge {badge_class}">{badge_text}</span></td>
            <td>{s['cost'] or '-'}</td>
            <td>{s['recurrence']}</td>
            <td>
                <form action="/api/delete-service/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar {s['name']}?');">
                    <button type="submit" class="btn-del" title="Eliminar servicio">🗑️</button>
                </form>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = "<tr><td colspan='8' style='text-align: center; color: #888;'>No hay servicios registrados aún. Pídeselo a Gemini o usa la API.</td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MCP Expiry Alert Bot</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
            .container {{ max-width: 950px; margin: 0 auto; }}
            .header-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
            h1 {{ margin: 0; color: #38bdf8; display: flex; align-items: center; gap: 10px; font-size: 1.5rem; }}
            .user-tag {{ font-size: 0.85rem; background: #334155; padding: 6px 12px; border-radius: 20px; display: flex; align-items: center; gap: 8px; }}
            .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .status-box {{ background: #0f172a; padding: 15px; border-radius: 8px; border: 1px solid #334155; }}
            .status-box h4 {{ margin: 0 0 8px 0; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; }}
            .status-box p {{ margin: 0; font-size: 1.1rem; font-weight: bold; }}
            .badge {{ padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }}
            .badge-ok {{ background: #065f46; color: #6ee7b7; }}
            .badge-warn {{ background: #854d0e; color: #fde047; }}
            .badge-danger {{ background: #991b1b; color: #fca5a5; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ padding: 12px 10px; text-align: left; border-bottom: 1px solid #334155; font-size: 0.95rem; }}
            th {{ color: #94a3b8; font-weight: 600; }}
            .endpoint-box {{ background: #0284c7; color: white; padding: 12px 16px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; margin-top: 15px; }}
            code {{ font-family: monospace; background: #0f172a; padding: 3px 6px; border-radius: 4px; }}
            .btn {{ background: #2563eb; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.9rem; font-weight: 500; }}
            .btn:hover {{ background: #1d4ed8; }}
            .btn-logout {{ background: #475569; color: #cbd5e1; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; }}
            .btn-logout:hover {{ background: #64748b; color: white; }}
            .btn-del {{ background: transparent; border: none; cursor: pointer; font-size: 1.1rem; padding: 4px; border-radius: 4px; }}
            .btn-del:hover {{ background: #334155; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header-bar">
                <h1>⚡ Gemini Spark MCP - Panel</h1>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span class="user-tag">👤 <strong>{user}</strong> (2FA Activo)</span>
                    <a href="/logout" class="btn-logout">Cerrar Sesión</a>
                </div>
            </div>

            <div class="card">
                <div class="status-grid">
                    <div class="status-box">
                        <h4>Bot de Telegram</h4>
                        <p>{'✅ Conectado' if (has_token and has_chat_id) else '❌ Sin Configurar'}</p>
                    </div>
                    <div class="status-box">
                        <h4>Verificación Diaria</h4>
                        <p>{alert_hour}:00 hs ({tz})</p>
                    </div>
                    <div class="status-box">
                        <h4>Anticipación de Alerta</h4>
                        <p>{days_window} días antes</p>
                    </div>
                    <div class="status-box">
                        <h4>Servicios Registrados</h4>
                        <p>{len(svcs)}</p>
                    </div>
                </div>
                <div style="display: flex; gap: 10px;">
                    <form action="/api/test-telegram" method="POST" style="display: inline;">
                        <button type="submit" class="btn">📲 Probar Alerta Telegram</button>
                    </form>
                    <form action="/api/check-now" method="POST" style="display: inline;">
                        <button type="submit" class="btn" style="background: #059669;">🔍 Escanear Vencimientos</button>
                    </form>
                </div>
                <div class="endpoint-box">
                    <span><strong>URL para Gemini Spark:</strong> <code>https://mcp.juanconnect.online/mcp</code></span>
                </div>
            </div>

            <div class="card">
                <h3>📅 Servicios Registrados</h3>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Nombre</th>
                            <th>Categoría</th>
                            <th>Vence</th>
                            <th>Estado</th>
                            <th>Costo</th>
                            <th>Recurrencia</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.post("/api/test-telegram")
async def test_telegram(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    ok = await send_telegram_message("🔔 <b>Prueba manual exitosa:</b> El bot de alertas está operativo.")
    return JSONResponse({"ok": ok, "message": "Mensaje enviado" if ok else "Fallo al enviar mensaje"})

@app.post("/api/check-now")
async def manual_check(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    sent = await check_and_send_alerts()
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@app.post("/api/delete-service/{service_id}")
async def delete_service_api(service_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    database.delete_service(service_id)
    return RedirectResponse(url="/", status_code=302)

@app.get("/health")
async def health():
    return {"status": "ok", "arm_server": True, "service": "gemini-expiry-mcp"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
