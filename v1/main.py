import os
import re
import json
import secrets
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
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
    return serializer.dumps({"user": username.lower(), "auth": True}, salt="session-auth")

def verify_session_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="session-auth", max_age=86400 * 7)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

def create_preauth_cookie(username: str) -> str:
    return serializer.dumps({"user": username.lower(), "step": "2fa"}, salt="preauth")

def verify_preauth_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="preauth", max_age=300)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

# ==========================================
# Modelos Pydantic para Carga en Lote
# ==========================================
class ItemCuentaLote(BaseModel):
    plataforma: str = Field(description="Plataforma (Netflix, Disney+, Max, etc.)")
    correo: str = Field(description="Correo o usuario de la cuenta")
    contrasena: str = Field(description="Contraseña de la cuenta")
    fecha_vencimiento: str = Field(description="Fecha de vencimiento formato YYYY-MM-DD")
    precio: str = Field(default="", description="Precio cobrado al cliente")
    recurrencia: str = Field(default="mensual", description="mensual, trimestral, anual, unico")
    perfil: str = Field(default="", description="Nombre de perfil si aplica")
    pin: str = Field(default="", description="PIN del perfil si aplica")

# ==========================================
# 1. Herramientas FastMCP para Gemini Spark
# ==========================================
mcp = FastMCP("Streaming CRM & Expiry Bot")

@mcp.tool()
def registrar_ventas_en_lote(
    cliente: str,
    cuentas: Union[List[ItemCuentaLote], List[Dict[str, Any]], str],
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final"
) -> str:
    """Registra múltiples ventas de cuentas/perfiles a un cliente en UNA SOLA OPERACIÓN masiva.
    Permite cargar 10, 20 o 30 cuentas de una sola vez para que el usuario solo tenga que dar permiso ('Allow') 1 sola vez.
    - cliente: Nombre o alias del cliente (ej. 'Matías').
    - cuentas: Lista de cuentas con plataforma, correo, contrasena, fecha_vencimiento, precio, recurrencia, perfil, pin.
    - whatsapp: Teléfono del cliente.
    - telegram: Usuario de Telegram (@usuario).
    - tipo_cliente: 'revendedor' o 'consumidor_final'.
    """
    # Manejar si Gemini lo envía como string JSON
    lista_items = cuentas
    if isinstance(cuentas, str):
        try:
            lista_items = json.loads(cuentas)
        except Exception:
            lista_items = []

    cargadas = 0
    errores = 0

    for item in lista_items:
        try:
            if isinstance(item, ItemCuentaLote):
                c_dict = item.model_dump()
            elif isinstance(item, dict):
                c_dict = item
            else:
                continue

            database.assign_or_sell_account(
                client_name=cliente,
                platform=c_dict.get("plataforma", "Streaming"),
                email=c_dict.get("correo", ""),
                password=c_dict.get("contrasena", ""),
                expiry_date=c_dict.get("fecha_vencimiento", ""),
                whatsapp=whatsapp,
                telegram=telegram,
                client_type=tipo_cliente,
                profile_name=c_dict.get("perfil", ""),
                profile_pin=c_dict.get("pin", ""),
                recurrence=c_dict.get("recurrencia", "mensual"),
                price=c_dict.get("precio", "")
            )
            cargadas += 1
        except Exception as e:
            logger.error(f"Error cargando cuenta individual: {e}")
            errores += 1

    tipo_badge = "👔 Revendedor" if "revend" in tipo_cliente.lower() else "👤 Consumidor Final"
    return (
        f"🎉 CARGA MASIVA COMPLETADA:\n"
        f"• Cliente: {cliente} ({tipo_badge})\n"
        f"• WhatsApp: {whatsapp or '-'} | Telegram: {telegram or '-'}\n"
        f"• Total de cuentas registradas con éxito: {cargadas}\n"
        + (f"• Errores: {errores}\n" if errores > 0 else "")
        + "Todas las cuentas ya están disponibles en el panel y programadas para alerta 2 días antes."
    )

@mcp.tool()
def vender_o_asignar_servicio(
    cliente: str,
    plataforma: str,
    correo: str,
    contrasena: str,
    fecha_vencimiento: str,
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final",
    perfil: str = "",
    pin: str = "",
    precio: str = "",
    recurrencia: str = "mensual",
    notas: str = ""
) -> str:
    """Registra una venta o asignación individual de cuenta o perfil de streaming a un cliente."""
    try:
        acc = database.assign_or_sell_account(
            client_name=cliente,
            platform=plataforma,
            email=correo,
            password=contrasena,
            expiry_date=fecha_vencimiento,
            whatsapp=whatsapp,
            telegram=telegram,
            client_type=tipo_cliente,
            profile_name=perfil,
            profile_pin=pin,
            recurrence=recurrencia,
            price=precio,
            notes=notas
        )
        return (
            f"✅ Venta registrada exitosamente para {acc['client_name']} ({acc['client_code']}):\n"
            f"• Plataforma: {acc['platform']}" + (f" (Perfil: {acc['profile_name']})" if acc.get('profile_name') else "") + "\n"
            f"• Correo: {acc['email']}\n"
            f"• Clave: {acc['password']}" + (f" | PIN: {acc['profile_pin']}" if acc.get('profile_pin') else "") + "\n"
            f"• Vence: {acc['expiry_date']} | Recurrencia: {acc['recurrence']}\n"
            f"• Tipo: {'👔 Revendedor' if acc.get('client_type') == 'revendedor' else '👤 Consumidor Final'}\n"
            f"• Contacto: WhatsApp: {acc.get('whatsapp') or '-'} | Telegram: {acc.get('telegram') or '-'}"
        )
    except Exception as e:
        return f"❌ Error al registrar venta: {str(e)}"

@mcp.tool()
def buscar_cliente(query: str) -> str:
    """Busca un cliente por nombre/alias ('Maik'), código (CLI-001), WhatsApp o Telegram.
    Devuelve su información de contacto y todas sus cuentas activas o caídas.
    """
    client = database.search_client(query)
    if not client:
        return f"❌ No se encontró ningún cliente que coincida con '{query}'."

    tipo = "👔 Revendedor" if client.get("client_type") == "revendedor" else "👤 Consumidor Final"
    lines = [
        f"👤 <b>Cliente:</b> {client['name']} ({client['client_code']})",
        f"• Tipo: {tipo}",
        f"• WhatsApp: {client.get('whatsapp') or 'No registrado'}",
        f"• Telegram: {client.get('telegram') or 'No registrado'}",
        f"• Notas: {client.get('notes') or '-'}",
        "\n📺 <b>Servicios contratados:</b>"
    ]

    accounts = client.get("accounts", [])
    if not accounts:
        lines.append("  (No tiene cuentas asociadas actualmente)")
    else:
        for a in accounts:
            estado_icon = "✅ Activa" if a["status"] == "ocupada" else ("🚨 CAÍDA" if a["status"] == "caida" else a["status"])
            perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
            pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
            lines.append(
                f"  • {a['platform']}{perf}: {a['email']} | Clave: {a['password']}{pin}\n"
                f"    Vence: {a['expiry_date']} | Estado: {estado_icon} | Precio: {a.get('price') or '-'}"
            )

    return "\n".join(lines)

@mcp.tool()
def marcar_cuenta_caida(correo_o_id: str, motivo: str = "Suscripción caída") -> str:
    """Marca una cuenta o perfil como 'caida' para colocarla en la lista de reclamos."""
    acc = database.mark_account_fallen(correo_o_id, reason=motivo)
    if not acc:
        return f"❌ No se encontró ninguna cuenta activa con el identificador '{correo_o_id}'."

    client_name = acc.get("client_name") or "Sin cliente"
    return (
        f"🚨 Cuenta marcada como CAÍDA:\n"
        f"• Plataforma: {acc['platform']}\n"
        f"• Correo: {acc['email']}\n"
        f"• Cliente afectado: {client_name}\n"
        f"• Motivo: {motivo}\n\n"
        f"💡 Puedes pedirme: 'Cámbiame este correo {acc['email']} por una libre' para asignarle reemplazo automático."
    )

@mcp.tool()
def agregar_stock_libre(
    plataforma: str,
    correo: str,
    contrasena: str,
    perfil: str = "",
    pin: str = "",
    costo: str = "",
    notas: str = ""
) -> str:
    """Agrega una cuenta o perfil libre al inventario disponible para la venta o reemplazo."""
    try:
        acc = database.add_free_account(
            platform=plataforma,
            email=correo,
            password=contrasena,
            profile_name=perfil,
            profile_pin=pin,
            cost=costo,
            notes=notas
        )
        return (
            f"✅ Cuenta libre agregada al stock disponible:\n"
            f"• ID: {acc['id']}\n"
            f"• Plataforma: {acc['platform']}\n"
            f"• Correo: {acc['email']}\n"
            f"• Clave: {acc['password']}" + (f" | Perfil: {acc['profile_name']}" if acc.get("profile_name") else "")
        )
    except Exception as e:
        return f"❌ Error al agregar stock: {str(e)}"

@mcp.tool()
def reemplazar_cuenta_caida(correo_o_id: str) -> str:
    """Reemplazo inteligente de cuenta por una libre de la misma plataforma."""
    res = database.replace_fallen_account(correo_o_id)
    if not res:
        return f"❌ No se encontró ninguna cuenta caída o activa con '{correo_o_id}'."

    if not res.get("success"):
        old = res.get("old_account", {})
        return (
            f"⚠️ NO HAY STOCK DISPONIBLE:\n"
            f"No se encontraron cuentas libres de la plataforma '{old.get('platform')}' en el inventario.\n"
            f"La cuenta {old.get('email')} permanece marcada como caída."
        )

    new_acc = res["new_account"]
    old_acc = res["old_account"]
    client_name = new_acc.get("client_name") or "Cliente"

    return (
        f"🎉 REEMPLAZO EXITOSO REALIZADO:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Cliente:</b> {client_name}\n"
        f"📺 <b>Plataforma:</b> {new_acc['platform']}\n"
        f"❌ <b>Cuenta anterior (Caída):</b> {old_acc['email']}\n"
        f"✨ <b>NUEVA CUENTA ASIGNADA:</b>\n"
        f"• Correo: <code>{new_acc['email']}</code>\n"
        f"• Contraseña: <code>{new_acc['password']}</code>" + (f"\n• Perfil: {new_acc['profile_name']}" if new_acc.get("profile_name") else "") + (f" [PIN: {new_acc['profile_pin']}]" if new_acc.get("profile_pin") else "") + "\n"
        f"📅 <b>Mantiene vencimiento:</b> <code>{new_acc['expiry_date']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 Copia estos datos y envíaselos a {client_name}."
    )

@mcp.tool()
def consultar_stock_libre(plataforma: str = "") -> str:
    """Consulta el inventario de cuentas y perfiles libres listos para entregar."""
    stock = database.get_free_stock(plataforma if plataforma else None)
    if not stock:
        msg = f"de la plataforma '{plataforma}'" if plataforma else "en el inventario"
        return f"📦 No hay cuentas libres disponibles {msg}."

    lines = [f"📦 <b>Stock Disponible ({len(stock)} cuentas libres):</b>\n"]
    for s in stock:
        perf = f" (Perfil: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        lines.append(f"• [{s['id']}] {s['platform']}{perf}: <code>{s['email']}</code> | Clave: <code>{s['password']}</code>{pin}")

    return "\n".join(lines)

@mcp.tool()
def consultar_cuentas_caidas() -> str:
    """Lista todas las cuentas marcadas como caídas pendientes de reclamo."""
    fallen = database.get_fallen_accounts()
    if not fallen:
        return "🎉 ¡Excelente! No hay ninguna cuenta caída actualmente."

    lines = [f"🚨 <b>Cuentas Caídas Pendientes ({len(fallen)}):</b>\n"]
    for f in fallen:
        client_name = f.get("client_name") or "Sin cliente asignado"
        lines.append(
            f"• [{f['id']}] {f['platform']}: {f['email']}\n"
            f"  Cliente: {client_name} | Tel: {f.get('whatsapp') or '-'} | Tg: {f.get('telegram') or '-'}\n"
            f"  Detalle: {f.get('notes') or '-'}\n"
        )
    return "\n".join(lines)

@mcp.tool()
def renovar_suscripcion(correo_o_id: str, nueva_fecha_vencimiento: str) -> str:
    """Extiende o renueva la fecha de vencimiento de una cuenta tras recibir el pago."""
    ok = database.renew_account(correo_o_id, nueva_fecha_vencimiento)
    if ok:
        return f"✅ Cuenta '{correo_o_id}' renovada exitosamente hasta el {nueva_fecha_vencimiento}."
    return f"❌ No se encontró ninguna cuenta con '{correo_o_id}'."

@mcp.tool()
def listar_clientes_activos() -> str:
    """Muestra el listado completo de clientes registrados con su número de cuentas activas."""
    clients = database.list_all_clients()
    if not clients:
        return "No hay clientes registrados en la base de datos."

    lines = [f"👥 <b>Clientes Registrados ({len(clients)}):</b>\n"]
    for c in clients:
        tipo = "👔 Revendedor" if c.get("client_type") == "revendedor" else "👤 Final"
        lines.append(
            f"• [{c['client_code']}] {c['name']} ({tipo}) - Cuentas activas: {c.get('active_accounts_count', 0)}\n"
            f"  WhatsApp: {c.get('whatsapp') or '-'} | Telegram: {c.get('telegram') or '-'}"
        )
    return "\n".join(lines)

@mcp.tool()
def cambiar_clave_admin(nueva_contrasena: str, usuario: str = "admin") -> str:
    """Cambia o restablece la contraseña de acceso al panel web del administrador."""
    try:
        clean_user = usuario.strip().lower()
        clean_pass = nueva_contrasena.strip()
        database.create_or_update_admin(clean_user, clean_pass)
        return f"✅ Contraseña del usuario '{clean_user}' actualizada correctamente."
    except Exception as e:
        return f"❌ Error al actualizar contraseña: {str(e)}"

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
    """Ejecuta una comprobación inmediata de vencimientos de streaming y envía alertas con datos de contacto por Telegram."""
    enviadas = await check_and_send_alerts(days_window=dias_anticipacion)
    return f"Comprobación manual completada. Se enviaron {enviadas} alerta(s) de vencimiento por Telegram."


# ==========================================
# 2. Servidor Web FastAPI & Lifespan
# ==========================================
mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    
    raw_user = os.getenv("ADMIN_USERNAME")
    raw_pass = os.getenv("ADMIN_PASSWORD")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    admin_pass = raw_pass.strip() if raw_pass and raw_pass.strip() else "admin123"

    existing = database.get_admin_user(admin_user)
    totp_secret = existing.get("totp_secret") if existing else pyotp.random_base32()
    database.create_or_update_admin(admin_user, admin_pass, totp_secret)
    logger.info(f"Usuario administrador '{admin_user}' sincronizado con éxito.")

    start_scheduler()
    logger.info("Aplicación y tareas programadas iniciadas.")
    async with mcp_app.lifespan(app):
        yield
    stop_scheduler()
    logger.info("Aplicación detenida.")

app = FastAPI(
    title="Gemini Streaming CRM & MCP Bot",
    description="Servidor MCP para Gemini Spark y CRM de Streaming con 2FA",
    version="2.1.0",
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
    <title>Iniciar Sesión - Streaming CRM & MCP</title>
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
        .btn-recovery { width: 100%; background: transparent; border: 1px solid #334155; color: #38bdf8; border-radius: 8px; padding: 10px; font-size: 0.85rem; font-weight: 500; cursor: pointer; transition: all 0.2s; margin-top: 14px; }
        .btn-recovery:hover { background: #1e293b; border-color: #38bdf8; }
        .error-msg { background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; text-align: center; }
        .success-msg { background: #064e3b; border: 1px solid #047857; color: #6ee7b7; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; text-align: center; }
        .footer { text-align: center; margin-top: 24px; font-size: 0.8rem; color: #64748b; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-box">🔐</div>
        <h2>Acceso Administrativo</h2>
        <p class="subtitle">CRM de Streaming & Gemini MCP</p>
        {{MESSAGE_HTML}}
        <form action="/login" method="POST">
            <div class="form-group">
                <label>Usuario</label>
                <input type="text" name="username" required autofocus placeholder="admin" value="admin">
            </div>
            <div class="form-group">
                <label>Contraseña</label>
                <input type="password" name="password" required placeholder="••••••••">
            </div>
            <button type="submit" class="btn">Continuar</button>
        </form>

        <form action="/recuperar" method="POST">
            <button type="submit" class="btn-recovery">📲 Enviar clave temporal a mi Telegram</button>
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
async def login_page(request: Request, error: Optional[str] = None, msg: Optional[str] = None):
    session_user = verify_session_cookie(request.cookies.get("session_token"))
    if session_user:
        return RedirectResponse(url="/", status_code=302)
        
    message_html = ""
    if error:
        message_html = f'<div class="error-msg">{error}</div>'
    elif msg:
        message_html = f'<div class="success-msg">{msg}</div>'
        
    return LOGIN_HTML_TEMPLATE.replace("{{MESSAGE_HTML}}", message_html)

@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    user = username.strip().lower()
    passw = password.strip()
    
    if not database.verify_admin_credentials(user, passw):
        return RedirectResponse(url="/login?error=Usuario+o+contrase%C3%B1a+incorrectos", status_code=302)

    otp = f"{secrets.randbelow(900000) + 100000}"
    database.set_telegram_otp(user, otp, duration_seconds=300)

    msg = (
        "🔐 <b>Código de Verificación 2FA</b>\n\n"
        "Alguien está iniciando sesión en el Panel de Streaming.\n\n"
        f"Tu código de acceso es: <code>{otp}</code>\n\n"
        "⏱️ <i>Válido durante 5 minutos.</i>"
    )
    await send_telegram_message(msg)

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

@app.post("/recuperar")
async def recover_password():
    raw_user = os.getenv("ADMIN_USERNAME")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    
    temp_pass = f"{secrets.randbelow(900000) + 100000}"
    database.create_or_update_admin(admin_user, temp_pass)
    
    msg = (
        "🔑 <b>Recuperación de Contraseña</b>\n\n"
        "Has solicitado una clave temporal de acceso al Panel Web:\n\n"
        f"• Usuario: <code>{admin_user}</code>\n"
        f"• Contraseña temporal: <code>{temp_pass}</code>\n\n"
        "Ingresa con estos datos en https://mcp.juanconnect.online"
    )
    ok = await send_telegram_message(msg)
    if ok:
        return RedirectResponse(url="/login?msg=Se+envi%C3%B3+tu+clave+temporal+a+Telegram", status_code=302)
    else:
        return RedirectResponse(url="/login?error=Fallo+al+enviar+mensaje+a+Telegram", status_code=302)

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

    if database.verify_telegram_otp(preauth_user, code):
        is_valid = True
    else:
        admin_data = database.get_admin_user(preauth_user)
        if admin_data and admin_data.get("totp_secret"):
            totp = pyotp.TOTP(admin_data["totp_secret"])
            if totp.verify(code):
                is_valid = True

    if not is_valid:
        return RedirectResponse(url="/2fa?error=C%C3%B3digo+inv%C3%A1lido+o+expirado", status_code=302)

    session = create_session_cookie(preauth_user)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_token",
        value=session,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7
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
# 4. Panel de Control Web Completo (CRM)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active_accounts = database.get_active_accounts()
    free_stock = database.get_free_stock()
    fallen_accounts = database.get_fallen_accounts()
    expiring_soon = database.get_expiring_streaming_accounts(days_window=3)

    active_rows = ""
    for a in active_accounts:
        days = a.get("days_remaining")
        badge = "badge-ok"
        badge_txt = f"En {days}d"
        if days is None:
            badge = "badge-warn"; badge_txt = "Fecha inválida"
        elif days < 0:
            badge = "badge-danger"; badge_txt = f"Vencida (-{abs(days)}d)"
        elif days <= 2:
            badge = "badge-warn"; badge_txt = f"¡Vence en {days}d!"

        wa_clean = re.sub(r'[^0-9]', '', a.get("whatsapp", ""))
        wa_link = f'<a href="https://wa.me/{wa_clean}" target="_blank" style="color: #22c55e;">{a.get("whatsapp")}</a>' if wa_clean else '-'
        tg_clean = a.get("telegram", "").lstrip("@")
        tg_link = f'<a href="https://t.me/{tg_clean}" target="_blank" style="color: #38bdf8;">@{tg_clean}</a>' if tg_clean else '-'
        client_tag = f"👔 {a.get('client_name')}" if "revend" in (a.get("client_type") or "").lower() else f"👤 {a.get('client_name')}"

        perf = f"<br><small style='color:#94a3b8;'>Perf: {a['profile_name']}</small>" if a.get("profile_name") else ""
        pin = f"<small style='color:#94a3b8;'>PIN: {a['profile_pin']}</small>" if a.get("profile_pin") else ""

        active_rows += f"""
        <tr>
            <td><strong>{client_tag}</strong><br><small style="color:#64748b;">{a.get('client_code') or ''}</small></td>
            <td>{wa_link}<br>{tg_link}</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{a['platform']}</span>{perf}</td>
            <td><code>{a['email']}</code><br><code>{a['password']}</code> {pin}</td>
            <td><code>{a['expiry_date']}</code></td>
            <td><span class="badge {badge}">{badge_txt}</span></td>
            <td>{a.get('price') or '-'}</td>
            <td>
                <form action="/api/mark-fallen/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar {a['email']} como caída?');">
                    <button type="submit" class="btn-action btn-warn" title="Reportar Caída">🚨 Caída</button>
                </form>
                <form action="/api/delete-account/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar cuenta?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not active_rows:
        active_rows = "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas activas asignadas actualmente.</td></tr>"

    stock_rows = ""
    for s in free_stock:
        perf = f" (Perf: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        stock_rows += f"""
        <tr>
            <td><strong>{s['platform']}</strong>{perf}</td>
            <td><code>{s['email']}</code></td>
            <td><code>{s['password']}</code>{pin}</td>
            <td>{s.get('cost') or '-'}</td>
            <td><span class="badge badge-ok">Disponible</span></td>
            <td>
                <form action="/api/delete-account/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar del stock?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not stock_rows:
        stock_rows = "<tr><td colspan='6' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas libres en stock. Agrega cuentas o pídeselo a Gemini.</td></tr>"

    fallen_rows = ""
    for f in fallen_accounts:
        client_name = f.get("client_name") or "Sin cliente asignado"
        fallen_rows += f"""
        <tr>
            <td><strong>{f['platform']}</strong></td>
            <td><code>{f['email']}</code></td>
            <td><strong>{client_name}</strong></td>
            <td><small style="color:#fca5a5;">{f.get('notes') or 'Reportada'}</small></td>
            <td>
                <form action="/api/auto-replace/{f['id']}" method="POST" style="display:inline;">
                    <button type="submit" class="btn-action btn-replace" title="Buscar reemplazo en stock de la misma plataforma">🔄 Reemplazar Automáticamente</button>
                </form>
            </td>
        </tr>
        """
    if not fallen_rows:
        fallen_rows = "<tr><td colspan='5' style='text-align:center;color:#10b981;padding:20px;'>🎉 ¡No hay cuentas caídas! Todo el sistema está funcionando.</td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Streaming CRM & MCP Bot</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; margin: 0; padding: 24px; }}
            .container {{ max-width: 1100px; margin: 0 auto; }}
            .header-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
            h1 {{ margin: 0; color: #38bdf8; font-size: 1.6rem; display: flex; align-items: center; gap: 10px; }}
            .card {{ background: #161e2e; border: 1px solid #1e293b; border-radius: 14px; padding: 20px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .stat-box {{ background: #0b0f19; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; }}
            .stat-box h4 {{ margin: 0 0 6px 0; font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }}
            .stat-box p {{ margin: 0; font-size: 1.4rem; font-weight: 700; color: #fff; }}
            .stat-fallen {{ border-left: 4px solid #ef4444; }}
            .stat-stock {{ border-left: 4px solid #10b981; }}
            .stat-active {{ border-left: 4px solid #38bdf8; }}
            .stat-soon {{ border-left: 4px solid #f59e0b; }}
            .tabs {{ display: flex; gap: 10px; border-bottom: 1px solid #334155; margin-bottom: 20px; }}
            .tab-btn {{ background: none; border: none; color: #94a3b8; padding: 12px 18px; font-size: 0.95rem; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent; }}
            .tab-btn.active {{ color: #38bdf8; border-bottom-color: #38bdf8; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid #1e293b; font-size: 0.9rem; }}
            th {{ color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }}
            .badge {{ padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 700; display: inline-block; }}
            .badge-ok {{ background: #064e3b; color: #6ee7b7; }}
            .badge-warn {{ background: #78350f; color: #fde047; }}
            .badge-danger {{ background: #7f1d1d; color: #fca5a5; }}
            code {{ font-family: monospace; background: #0b0f19; padding: 3px 6px; border-radius: 4px; font-size: 0.85rem; }}
            .btn {{ background: #0284c7; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.85rem; font-weight: 600; }}
            .btn:hover {{ background: #0369a1; }}
            .btn-action {{ background: transparent; border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; cursor: pointer; font-size: 0.8rem; font-weight: 600; }}
            .btn-warn {{ color: #f59e0b; border-color: #78350f; }}
            .btn-warn:hover {{ background: #451a03; }}
            .btn-replace {{ background: #059669; color: white; border: none; }}
            .btn-replace:hover {{ background: #047857; }}
            .btn-logout {{ background: #1e293b; color: #cbd5e1; text-decoration: none; padding: 8px 14px; border-radius: 8px; font-size: 0.85rem; }}
            .btn-logout:hover {{ background: #334155; color: #fff; }}
            .endpoint-banner {{ background: #0369a1; color: white; padding: 12px 18px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; margin-top: 15px; font-size: 0.9rem; }}
        </style>
        <script>
            function showTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                document.getElementById(tabId).style.display = 'block';
                document.getElementById('btn-' + tabId).classList.add('active');
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header-bar">
                <h1>⚡ Streaming CRM & Gemini MCP</h1>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size:0.85rem; color:#94a3b8;">Admin: <strong>{user}</strong></span>
                    <a href="/logout" class="btn-logout">Cerrar Sesión</a>
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-box stat-active">
                    <h4>Cuentas Activas</h4>
                    <p>{len(active_accounts)}</p>
                </div>
                <div class="stat-box stat-stock">
                    <h4>Stock Libre</h4>
                    <p>{len(free_stock)}</p>
                </div>
                <div class="stat-box stat-fallen">
                    <h4>Cuentas Caídas</h4>
                    <p>{len(fallen_accounts)}</p>
                </div>
                <div class="stat-box stat-soon">
                    <h4>Vencen Pronto (3d)</h4>
                    <p>{len(expiring_soon)}</p>
                </div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 10px;">
                        <form action="/api/test-telegram" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#475569;">📲 Test Telegram</button>
                        </form>
                        <form action="/api/check-now" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#059669;">🔍 Escanear Vencimientos Ahora</button>
                        </form>
                    </div>
                </div>
                <div class="endpoint-banner">
                    <span><strong>Conexión Gemini Spark:</strong> <code>https://mcp.juanconnect.online/mcp</code></span>
                    <span>Modo CRM Activo</span>
                </div>
            </div>

            <div class="card">
                <div class="tabs">
                    <button id="btn-tab-active" class="tab-btn active" onclick="showTab('tab-active')">👥 Clientes & Activas ({len(active_accounts)})</button>
                    <button id="btn-tab-stock" class="tab-btn" onclick="showTab('tab-stock')">📦 Stock Libre ({len(free_stock)})</button>
                    <button id="btn-tab-fallen" class="tab-btn" onclick="showTab('tab-fallen')">🚨 Cuentas Caídas ({len(fallen_accounts)})</button>
                </div>

                <div id="tab-active" class="tab-content" style="display:block;">
                    <table>
                        <thead>
                            <tr>
                                <th>Cliente</th>
                                <th>Contacto</th>
                                <th>Servicio</th>
                                <th>Credenciales</th>
                                <th>Vence</th>
                                <th>Estado</th>
                                <th>Precio</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {active_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-stock" class="tab-content" style="display:none;">
                    <table>
                        <thead>
                            <tr>
                                <th>Plataforma</th>
                                <th>Correo</th>
                                <th>Contraseña</th>
                                <th>Costo</th>
                                <th>Estado</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stock_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-fallen" class="tab-content" style="display:none;">
                    <table>
                        <thead>
                            <tr>
                                <th>Plataforma</th>
                                <th>Correo Caído</th>
                                <th>Cliente</th>
                                <th>Detalle</th>
                                <th>Acción Inmediata</th>
                            </tr>
                        </thead>
                        <tbody>
                            {fallen_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.post("/api/mark-fallen/{account_id}")
async def mark_fallen_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.mark_account_fallen(str(account_id), reason="Marcada desde el Panel")
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/auto-replace/{account_id}")
async def auto_replace_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.replace_fallen_account(str(account_id))
    if res and res.get("success"):
        new_a = res["new_account"]
        msg = f"✅ Reemplazo exitoso para {new_a.get('client_name')}: {new_a['platform']} -> {new_a['email']}"
        await send_telegram_message(f"🔄 <b>Reemplazo de Cuenta</b>\n\n{msg}")
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/delete-account/{account_id}")
async def delete_account_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_account(account_id)
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/test-telegram")
async def test_telegram_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    ok = await send_telegram_message("🔔 <b>Prueba de Telegram exitosa desde el Panel Web de Streaming CRM</b>")
    return JSONResponse({"ok": ok})

@app.post("/api/check-now")
async def check_now_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    sent = await check_and_send_alerts()
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@app.get("/health")
async def health():
    return {"status": "ok", "service": "streaming-crm-mcp", "version": "2.1.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
