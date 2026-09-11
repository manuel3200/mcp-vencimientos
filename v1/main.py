import os
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastmcp import FastMCP

import database
from telegram_bot import send_telegram_message, format_and_send_alert
from scheduler import start_scheduler, stop_scheduler, check_and_send_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

# ==========================================
# 1. Definición del Servidor MCP
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
    """Actualiza la fecha de vencimiento de un servicio tras haberlo pagado o renovado.
    - id_servicio: ID numérico del servicio.
    - nueva_fecha_vencimiento: Nueva fecha en formato 'YYYY-MM-DD'.
    """
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
    # Inicialización de DB y Scheduler
    database.init_db()
    start_scheduler()
    logger.info("Aplicación iniciada exitosamente.")
    async with mcp_app.lifespan(app):
        yield
    # Apagado ordenado
    stop_scheduler()
    logger.info("Aplicación detenida.")

app = FastAPI(
    title="Gemini Expiry Alert MCP",
    description="Servidor MCP para Gemini Spark con alertas automáticas vía Telegram",
    version="1.0.0",
    lifespan=lifespan
)

# Montar MCP en /mcp para que coincida con https://tu-dominio/mcp
app.mount("/mcp", mcp_app)

# Dashboard web amigable en la raíz
@app.get("/", response_class=HTMLResponse)
async def dashboard():
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
        </tr>
        """

    if not rows_html:
        rows_html = "<tr><td colspan='7' style='text-align: center; color: #888;'>No hay servicios registrados aún. Pídeselo a Gemini o usa la API.</td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MCP Expiry Alert Bot</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
            h1 {{ margin-top: 0; color: #38bdf8; display: flex; align-items: center; gap: 10px; font-size: 1.6rem; }}
            .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .status-box {{ background: #0f172a; padding: 15px; border-radius: 8px; border: 1px solid #334155; }}
            .status-box h4 {{ margin: 0 0 8px 0; color: #94a3b8; font-size: 0.85rem; text-transform: uppercase; }}
            .status-box p {{ margin: 0; font-size: 1.1rem; font-weight: bold; }}
            .badge {{ padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }}
            .badge-ok {{ background: #065f46; color: #6ee7b7; }}
            .badge-warn {{ background: #854d0e; color: #fde047; }}
            .badge-danger {{ background: #991b1b; color: #fca5a5; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ padding: 12px 10px; text-align: left; border-bottom: 1px solid #334155; font-size: 0.95rem; }}
            th {{ color: #94a3b8; font-weight: 600; }}
            .endpoint-box {{ background: #0284c7; color: white; padding: 12px 16px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; }}
            code {{ font-family: monospace; background: #0f172a; padding: 3px 6px; border-radius: 4px; }}
            .btn {{ background: #2563eb; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.9rem; }}
            .btn:hover {{ background: #1d4ed8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>⚡ Gemini Spark MCP - Expiry & Telegram Bot</h1>
                <p>Servidor activo en Docker ARM (Oracle Cloud). Listo para integrarse con Gemini Spark.</p>
                <div class="endpoint-box">
                    <span><strong>URL para Gemini Spark:</strong> <code>https://tu-dominio/mcp</code></span>
                </div>
            </div>

            <div class="card">
                <h3>⚙️ Estado del Sistema</h3>
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
                        <button type="submit" class="btn" style="background: #059669;">🔍 Escanear Ahora</button>
                    </form>
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
async def test_telegram():
    ok = await send_telegram_message("🔔 <b>Prueba manual exitosa:</b> El bot de alertas está operativo.")
    return JSONResponse({"ok": ok, "message": "Mensaje enviado" if ok else "Fallo al enviar mensaje"})

@app.post("/api/check-now")
async def manual_check():
    sent = await check_and_send_alerts()
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@app.get("/health")
async def health():
    return {"status": "ok", "arm_server": True, "service": "gemini-expiry-mcp"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
