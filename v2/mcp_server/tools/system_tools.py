import os
import re
import json
import logging
from typing import Optional, List, Dict, Any, Union

from mcp_server.instance import mcp
from mcp_server.models import ItemCuentaLote
import database
import system_logger
import whatsapp_client
from telegram_bot import send_telegram_message, format_and_send_alert, send_full_backup_to_telegram
from scheduler import check_and_send_alerts, check_and_send_stock_alerts

logger = logging.getLogger("mcp")


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
def exportar_resumen_csv(tipo: str = "activas") -> str:
    """Genera y retorna una planilla en formato CSV para Excel. tipo puede ser: 'activas', 'stock', o 'transacciones'."""
    clean_type = tipo.strip().lower()
    if "stock" in clean_type:
        return database.export_free_stock_csv()
    elif "trans" in clean_type or "pago" in clean_type or "balance" in clean_type:
        return database.export_transactions_csv()
    else:
        return database.export_active_accounts_csv()


@mcp.tool()
def importar_stock_desde_csv(contenido_csv: str) -> str:
    """Carga masivamente cuentas y perfiles libres a partir de un texto o contenido CSV/Excel (separado por comas, puntos y comas o tabulaciones)."""
    res = database.import_free_stock_csv(contenido_csv)
    if not res.get("success"):
        return f"❌ Error en la importación: {res.get('error')}"
    err_msg = f" (Errores en {len(res['errors'])} líneas: {', '.join(res['errors'][:3])})" if res.get("errors") else ""
    return f"✅ Importación completada: {res['imported']} cuentas agregadas al stock libre, {res['skipped']} omitidas{err_msg}."


@mcp.tool()
def importar_ventas_desde_csv(contenido_csv: str) -> str:
    """Importa masivamente clientes y ventas activas con sus vencimientos desde un archivo o texto CSV/Excel."""
    res = database.import_sales_csv(contenido_csv)
    if not res.get("success"):
        return f"❌ Error en la importación: {res.get('error')}"
    err_msg = f" (Errores en {len(res['errors'])} líneas: {', '.join(res['errors'][:3])})" if res.get("errors") else ""
    return f"✅ Importación de ventas completada: {res['imported']} suscripciones y clientes asignados con éxito, {res['skipped']} omitidas{err_msg}."


@mcp.tool()
async def enviar_backup_telegram() -> str:
    """Genera y envía automáticamente copias de seguridad en formato Excel/CSV (cuentas activas, stock y balance) como archivos descargables al bot de Telegram."""
    from telegram_bot import send_full_backup_to_telegram
    ok = await send_full_backup_to_telegram()
    if ok:
        return "✅ Copia de seguridad en Excel/CSV generada y enviada a tu chat de Telegram."
    return "❌ Error al generar o enviar la copia de seguridad por Telegram."

# ==========================================
# Herramientas de Catálogo de Precios y Combos (Paso 7 - v2.8.0)
# ==========================================

@mcp.tool()
def consultar_plantillas_whatsapp() -> str:
    """Lista todas las plantillas de mensajes de WhatsApp configuradas en el sistema (cobro individual, cobro consolidado, entrega de accesos y reemplazo por caída)."""
    templates = database.get_whatsapp_templates()
    lines = ["📝 <b>PLANTILLAS DE WHATSAPP CONFIGURADAS:</b>\n"]
    for k, t in templates.items():
        lines.append(
            f"🔹 <b>{t['title']}</b> (Clave: <code>{k}</code>)\n"
            f"  Descripción: {t.get('description') or 'Sin descripción'}\n"
            f"  Última modificación: {t.get('updated_at') or 'Predeterminada'}\n"
            f"  Contenido:\n<pre>{t['content']}</pre>\n"
        )
    return "\n".join(lines)


@mcp.tool()
def guardar_plantilla_whatsapp(
    clave: str,
    contenido: str,
    titulo: str = "",
    descripcion: str = ""
) -> str:
    """Modifica y guarda una plantilla de WhatsApp del sistema.
    - clave: 'cobro', 'cobro_consolidado', 'entrega' o 'reemplazo'.
    - contenido: Texto del mensaje con etiquetas dinámicas {cliente}, {plataforma}, {email}, {password}, {perfil}, {pin}, {vencimiento}, {monto}, {metodos_pago}, etc.
    - titulo: Título descriptivo opcional de la plantilla.
    - descripcion: Breve detalle opcional sobre el uso de la plantilla.
    """
    ok = database.save_whatsapp_template(clave, contenido, title=titulo, description=descripcion)
    if not ok:
        return f"❌ No se pudo guardar la plantilla con clave '{clave}'."
    return f"✅ Plantilla '{clave}' actualizada correctamente con éxito. Se aplicará a todos los mensajes generados a partir de ahora."


@mcp.tool()
def restaurar_plantilla_whatsapp(clave: str) -> str:
    """Restaura una plantilla de WhatsApp a su texto predeterminado original de fábrica.
    - clave: 'cobro', 'cobro_consolidado', 'entrega' o 'reemplazo'.
    """
    ok = database.reset_whatsapp_template(clave)
    if not ok:
        return f"❌ Clave '{clave}' no reconocida o error al restaurar."
    return f"🔄 Plantilla '{clave}' restaurada exitosamente a sus valores originales de fábrica."


@mcp.tool()
def consultar_logs_sistema(
    nivel: str = "ALL",
    modulo: str = "",
    query: str = "",
    limite: int = 50
) -> str:
    """Consulta los logs y eventos recientes del sistema para diagnóstico y depuración de errores.
    - nivel: 'ALL', 'ERROR', 'WARNING', 'INFO'.
    - modulo: Filtrar por módulo (ej: 'main', 'telegram_bot', 'scheduler', 'database').
    - query: Término de búsqueda de texto o excepción en los logs.
    - limite: Cantidad máxima de registros a devolver (por defecto 50).
    """
    logs = system_logger.get_recent_logs(level=nivel, module=modulo, query=query, limit=limite)
    if not logs:
        return "📋 No se encontraron logs que coincidan con los filtros especificados."

    lines = [f"📋 <b>LOGS RECIENTES DEL SISTEMA ({len(logs)} eventos):</b>\n"]
    for l in logs:
        ico = "🚨" if l["level"] in ("ERROR", "CRITICAL") else ("⚠️" if l["level"] == "WARNING" else "ℹ️")
        lines.append(f"{ico} <code>{l['timestamp']}</code> [<b>{l['level']}</b>] [<b>{l['module']}</b>]: {l['message']}")
        if l.get("traceback"):
            lines.append(f"<pre>{l['traceback'][:500]}</pre>")
    return "\n".join(lines)


@mcp.tool()
def diagnostico_salud_sistema() -> str:
    """Devuelve un informe integral del estado de salud del sistema (base de datos, bot de Telegram, scheduler y resumen de errores recientes)."""
    rep = system_logger.get_system_health_report()
    db = rep["database"]
    ls = rep["logs_summary"]
    status_ico = "🟢" if rep["status"] == "OK" else ("🟡" if rep["status"] == "WARNING" else "🔴")

    out = [
        f"{status_ico} <b>REPORTE DE SALUD DEL SISTEMA ({rep['status']}):</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⏱️ <b>Tiempo Activo (Uptime):</b> {rep['uptime']}",
        f"🗄️ <b>Base de Datos:</b> {db['status']} ({db['size']} | {db['total_accounts']} cuentas)",
        f"🤖 <b>Bot Telegram:</b> {rep['telegram']['status']}",
        f"⏰ <b>Scheduler Alertas:</b> {rep['scheduler']['status']}",
        f"📊 <b>Buffer de Eventos:</b> {ls['total_buffered']}",
        f"⚠️ <b>Advertencias:</b> {ls['warnings_count']} | 🚨 <b>Errores:</b> {ls['errors_count']}",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    if ls.get("last_error"):
        le = ls["last_error"]
        out.append(f"🚨 <b>Último Error Registrado ({le['timestamp']}) en [{le['module']}]:</b>\n<code>{le['message']}</code>")
    else:
        out.append("✨ <i>Todos los servicios operan con normalidad sin errores recientes.</i>")
    return "\n".join(out)


@mcp.tool()
def listar_proveedores() -> str:
    """Lista todos los proveedores mayoristas registrados, cuentas contratadas y total pagado en ARS."""
    sups = database.get_suppliers()
    if not sups:
        return "🏢 No hay proveedores mayoristas registrados actualmente. Usa 'crear_o_actualizar_proveedor' para dar de alta uno."

    lines = ["🏢 <b>PROVEEDORES MAYORISTAS REGISTRADOS:</b>\n"]
    for s in sups:
        lines.append(
            f"🔹 <b>{s['name']}</b> (ID: <code>{s['id']}</code>)\n"
            f"  📱 Contacto: {s.get('contact') or 'No registrado'}\n"
            f"  💳 Datos de Pago: <code>{s.get('payment_info') or 'Sin datos'}</code>\n"
            f"  📺 Cuentas Madre: <b>{s['master_accounts_count']}</b> ({s['profiles_count']} perfiles)\n"
            f"  💰 Total Pagado: <b>{s['total_spent_formatted']}</b>\n"
            f"  📝 Notas: {s.get('notes') or '-'}\n"
        )
    return "\n".join(lines)


@mcp.tool()
def crear_o_actualizar_proveedor(
    nombre: str,
    contacto: str = "",
    info_pago: str = "",
    notas: str = "",
    id_proveedor: Optional[int] = None
) -> str:
    """Registra o modifica un proveedor mayorista:
    - nombre: Nombre del proveedor o empresa mayorista (ej: 'Streaming Mayorista ARG').
    - contacto: Teléfono WhatsApp o usuario de Telegram.
    - info_pago: CBU, CVU, Alias o Binance USDT para abonarle las cuentas.
    - notas: Condiciones de garantía, horarios o acuerdos.
    - id_proveedor: ID si se desea editar uno existente.
    """
    res = database.save_supplier(
        supplier_id=id_proveedor,
        name=nombre,
        contact=contacto,
        payment_info=info_pago,
        notes=notas
    )
    act = "actualizado" if id_proveedor else "creado"
    return (
        f"✅ PROVEEDOR {act.upper()} CON ÉXITO:\n"
        f"• ID: <code>{res['id']}</code>\n"
        f"• Nombre: <b>{res['name']}</b>\n"
        f"• Contacto: {res.get('contact') or '-'}\n"
        f"• Info de Pago: <code>{res.get('payment_info') or '-'}</code>"
    )


@mcp.tool()
async def consultar_estado_whatsapp() -> str:
    """Verifica el estado de conexión de la instancia de WhatsApp en Evolution API (Conectado / Desconectado / QR pendiente)."""
    st = await whatsapp_client.check_connection_status()
    cfg = whatsapp_client.get_evolution_config()
    
    if st.get("connected"):
        return (
            f"🟢 <b>WHATSAPP CONECTADO Y OPERATIVO:</b>\n"
            f"• Instancia: <code>{st.get('instance')}</code>\n"
            f"• Estado: <b>En línea (Open)</b>\n"
            f"• Servidor API: <code>{cfg['api_url']}</code>\n"
            f"• Auto-Cobro diario (09:00 AM): {'✅ Activado' if cfg['auto_send_expiry'] else '⏸️ Desactivado'}\n"
            f"• Auto-Envío en ventas: {'✅ Activado' if cfg['auto_send_sales'] else '⏸️ Desactivado'}\n"
            f"• Auto-Respuesta / Webhook: {'✅ Activado' if cfg['auto_reply_enabled'] else '⏸️ Desactivado'}"
        )
    else:
        err = st.get("error") or "No conectado"
        return (
            f"🔴 <b>WHATSAPP DESCONECTADO:</b>\n"
            f"• Instancia: <code>{st.get('instance')}</code>\n"
            f"• Estado actual: <code>{st.get('state')}</code>\n"
            f"• Detalle: {err}\n\n"
            f"👉 Abre la pestaña '💬 WhatsApp & Mensajería' en el panel web para escanear el código QR."
        )


@mcp.tool()
async def enviar_whatsapp_cliente(
    destinatario: str = "",
    mensaje: str = "",
    telefono: str = "",
    delay_segundos: float = 2.0
) -> str:
    """Envía un mensaje de texto por WhatsApp directamente a un cliente o contacto usando Evolution API:
    - destinatario: Puede ser el NOMBRE o alias del cliente en el CRM, contacto en Chatwoot, o directamente su número de teléfono (+549...).
    - mensaje: Texto del mensaje a enviar.
    - telefono: (Opcional) Número del cliente si no se especificó en destinatario.
    - delay_segundos: Simulación de escritura anti-baneo en segundos (por defecto 2.0).
    """
    target = (destinatario or telefono or "").strip()
    if not target:
        return "❌ Error: Debes indicar el nombre del cliente o su número de teléfono."
    if not mensaje.strip():
        return "❌ Error: El mensaje a enviar no puede estar vacío."

    client_name_str = ""
    phone_to_send = target

    # Si contiene letras o parece un nombre/código en vez de solo números
    clean_digits = re.sub(r'[^0-9]', '', target)
    if re.search(r'[a-zA-Z]', target) or len(clean_digits) < 8:
        client = database.search_client(target)
        if client:
            phone_reg = client.get("whatsapp")
            if not phone_reg:
                return f"❌ El cliente '{client.get('name')}' ({client.get('client_code')}) está registrado en el CRM pero no tiene número de WhatsApp configurado."
            phone_to_send = phone_reg
            client_name_str = f" a {client.get('name')}"
        else:
            # Fallback inteligente: buscar en la libreta de contactos de Chatwoot
            cw_contacts = await whatsapp_client.search_chatwoot_contacts(target)
            if cw_contacts and cw_contacts[0].get("phone_number"):
                c = cw_contacts[0]
                phone_to_send = c["phone_number"]
                client_name_str = f" a {c.get('name', target)} (Contacto de Chatwoot)"
            else:
                return f"❌ No se encontró ningún cliente en el CRM ni contacto en Chatwoot que coincida con '{target}'."

    res = await whatsapp_client.send_text_message(phone_to_send, mensaje, delay_seconds=delay_segundos)
    if res.get("success"):
        return f"✅ Mensaje de WhatsApp enviado exitosamente{client_name_str} ({res.get('phone')}) (ID: {res.get('message_id')})."
    else:
        return f"❌ Error al enviar WhatsApp{client_name_str} ({phone_to_send}): {res.get('error')}"


@mcp.tool()
async def sincronizar_contactos_chatwoot() -> str:
    """Sincroniza e importa automáticamente todos los contactos de Chatwoot a la base de datos de clientes del CRM."""
    res = await whatsapp_client.sync_chatwoot_contacts_to_crm()
    if not res.get("success"):
        return f"❌ Error al sincronizar contactos de Chatwoot: {res.get('error')}"

    imported = res.get("imported", 0)
    updated = res.get("updated", 0)
    contacts = res.get("contacts", [])

    lines = [
        "🔄 <b>SINCRONIZACIÓN CON CHATWOOT COMPLETADA:</b>",
        f"• Nuevos clientes dados de alta en el CRM: <b>{imported}</b>",
        f"• Clientes actualizados: <b>{updated}</b>",
        f"• Total de contactos procesados: <b>{len(contacts)}</b>\n"
    ]
    if contacts:
        lines.append("👥 <b>Clientes registrados / actualizados:</b>")
        for c in contacts[:10]:
            lines.append(f"• [{c['code']}] {c['name']} (WhatsApp: <code>{c.get('whatsapp') or '-'}</code>)")
        if len(contacts) > 10:
            lines.append(f"  <i>... y {len(contacts) - 10} más.</i>")

    lines.append("\n🎉 Todos los contactos de Chatwoot ahora están disponibles en el CRM para consultar balances, asignar servicios o enviar mensajes.")
    return "\n".join(lines)


@mcp.tool()
def configurar_automatizacion_whatsapp(
    api_url: str = "",
    api_key: str = "",
    instance_name: str = "",
    auto_send_expiry: Optional[int] = None,
    auto_send_sales: Optional[int] = None,
    auto_reply_enabled: Optional[int] = None
) -> str:
    """Configura las opciones de Evolution API WhatsApp y las automatizaciones del sistema:
    - api_url: URL base de Evolution API (ej: http://evolution-api:8080).
    - api_key: Clave API global de autenticación.
    - instance_name: Nombre de la sesión/instancia (ej: streaming-bot).
    - auto_send_expiry: 1 para activar envío 100% automático de cobranzas a las 09:00 AM, 0 para desactivar.
    - auto_send_sales: 1 para despachar credenciales por WhatsApp automáticamente al vender combos, 0 para desactivar.
    - auto_reply_enabled: 1 para activar bot de auto-respuesta a consultas de clientes, 0 para desactivar.
    """
    current = database.get_whatsapp_api_settings()
    new_url = api_url.strip() if api_url else current.get("api_url", "http://evolution-api:8080")
    new_key = api_key.strip() if api_key else current.get("api_key", "mcp-evolution-key-2026")
    new_inst = instance_name.strip() if instance_name else current.get("instance_name", "streaming-bot")
    new_expiry = auto_send_expiry if auto_send_expiry is not None else current.get("auto_send_expiry", 0)
    new_sales = auto_send_sales if auto_send_sales is not None else current.get("auto_send_sales", 0)
    new_reply = auto_reply_enabled if auto_reply_enabled is not None else current.get("auto_reply_enabled", 1)

    database.save_whatsapp_api_settings(
        api_url=new_url,
        api_key=new_key,
        instance_name=new_inst,
        auto_send_expiry=new_expiry,
        auto_send_sales=new_sales,
        auto_reply_enabled=new_reply,
        admin_whatsapp=current.get("admin_whatsapp", ""),
        gemini_api_key=current.get("gemini_api_key", "")
    )
    return (
        f"✅ CONFIGURACIÓN DE WHATSAPP ACTUALIZADA:\n"
        f"• URL: <code>{new_url}</code> | Instancia: <code>{new_inst}</code>\n"
        f"• Auto-Cobro diario (09:00 AM): {'Activado' if new_expiry else 'Desactivado'}\n"
        f"• Auto-Envío en ventas: {'Activado' if new_sales else 'Desactivado'}\n"
        f"• Bot Auto-Respuesta: {'Activado' if new_reply else 'Desactivado'}"
    )

