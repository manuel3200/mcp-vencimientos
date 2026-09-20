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
    nombre: str = "",
    auto_registrar_cliente: bool = True,
    delay_segundos: float = 2.0
) -> str:
    """Envía un mensaje de texto por WhatsApp directamente a un cliente o contacto usando Evolution API:
    - destinatario: Nombre o alias del cliente en el CRM, contacto en Chatwoot, o número de teléfono (ej: '+54 9 11 2358-6964 Marco Antonio' o '5491123586964').
    - mensaje: Texto del mensaje a enviar.
    - telefono: (Opcional) Número telefónico directo si se especifica por separado.
    - nombre: (Opcional) Nombre del cliente para registrarlo en el CRM si no existía.
    - auto_registrar_cliente: (Por defecto True) Si el cliente no está en el CRM y se conoce su nombre, lo registra automáticamente con su código CLI-XXX. Si no hay nombre, solicita al administrador con qué nombre darlo de alta.
    - delay_segundos: Simulación de escritura anti-baneo en segundos (por defecto 2.0).
    """
    raw_dest = (destinatario or "").strip()
    raw_phone = (telefono or "").strip()
    raw_name = (nombre or "").strip()
    if not raw_dest and not raw_phone:
        return "❌ Error: Debes indicar el nombre del cliente o su número de teléfono."
    if not (mensaje or "").strip():
        return "❌ Error: El mensaje a enviar no puede estar vacío."

    phone_to_send = ""
    client_name_str = ""

    # 1. Prioridad: ¿Se especificó un parámetro 'telefono' con dígitos válidos?
    if raw_phone:
        clean_p = database.clean_whatsapp_phone(raw_phone)
        if len(clean_p) >= 8:
            phone_to_send = clean_p
            client_name_str = f" a {raw_name or raw_dest}" if (raw_name or raw_dest) else ""

    # 2. ¿El destinatario contiene un número de teléfono (al menos 8 dígitos)?
    if not phone_to_send and raw_dest:
        digits = re.sub(r'\D', '', raw_dest)
        if len(digits) >= 8:
            phone_to_send = database.clean_whatsapp_phone(raw_dest)
            name_part = re.sub(r'[\+\d\-\(\)\.]+', ' ', raw_dest).strip()
            if name_part:
                client_name_str = f" a {name_part}"
                if not raw_name:
                    raw_name = name_part

    # 3. Si no hay dígitos suficientes en el destino, buscar por nombre o código en el CRM o Chatwoot
    if not phone_to_send:
        lookup_target = raw_dest or raw_phone
        client = database.search_client(lookup_target)
        if client:
            phone_reg = client.get("whatsapp")
            if not phone_reg:
                return f"❌ El cliente '{client.get('name')}' ({client.get('client_code')}) está registrado en el CRM pero no tiene número de WhatsApp configurado."
            phone_to_send = database.clean_whatsapp_phone(phone_reg)
            client_name_str = f" a {client.get('name')}"
            if not raw_name:
                raw_name = client.get("name")
        else:
            # Fallback inteligente: buscar en la libreta de contactos de Chatwoot
            try:
                cw_contacts = await whatsapp_client.search_chatwoot_contacts(lookup_target)
                if cw_contacts and cw_contacts[0].get("phone_number"):
                    c = cw_contacts[0]
                    phone_to_send = database.clean_whatsapp_phone(c["phone_number"])
                    client_name_str = f" a {c.get('name', lookup_target)} (Contacto de Chatwoot)"
                    if not raw_name:
                        raw_name = c.get('name', '')
            except Exception as e:
                logger.warning(f"Error consultando Chatwoot contacts: {e}")

    if not phone_to_send or len(phone_to_send) < 8:
        return f"❌ No se encontró ningún número de teléfono válido ni cliente registrado que coincida con '{raw_dest or raw_phone}'."

    # 4. Auto-registro inteligente en CRM si no está dado de alta
    auto_reg_note = ""
    if auto_registrar_cliente:
        clean_d = re.sub(r'\D', '', phone_to_send)
        existing = database.search_client(clean_d[-8:] if len(clean_d) >= 8 else clean_d)
        if not existing:
            # Si tenemos nombre, lo registramos inmediatamente
            if raw_name and len(raw_name) >= 2:
                try:
                    new_c = database.find_or_create_client(name=raw_name, whatsapp=phone_to_send)
                    auto_reg_note = f"\n👤 <b>Cliente registrado en el CRM:</b> {new_c['name']} (Código: <code>{new_c['client_code']}</code>)"
                    client_name_str = f" a {new_c['name']}"
                except Exception as ex:
                    logger.warning(f"Error auto-registrando cliente en envío WhatsApp: {ex}")
            else:
                # No hay nombre: avisar y consultar al administrador
                auto_reg_note = f"\n💡 <i>Aviso: El número {phone_to_send} no está registrado como cliente en el CRM. Pregunta al usuario: '¿Con qué nombre deseas registrar al cliente de este número?' o usa 'registrar_cliente'.</i>"

    res = await whatsapp_client.send_text_message(phone_to_send, mensaje, delay_seconds=delay_segundos)
    if res.get("success"):
        return f"✅ Mensaje de WhatsApp enviado exitosamente{client_name_str} ({res.get('phone', phone_to_send)}) (ID: {res.get('message_id', 'ok')}).{auto_reg_note}"
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


@mcp.tool()
async def resumen_ejecutivo_negocio(detallado: bool = False) -> str:
    """Genera una RADIOGRAFÍA EJECUTIVA 360° integral del negocio en tiempo real:
    - 💰 Finanzas: Ingresos cobrados este mes, costos, ganancias netas y dinero a cobrar en los próximos 7 días.
    - 🧾 Comprobantes: Cantidad de comprobantes pendientes de aprobación por OCR/WhatsApp.
    - ⏳ Vencimientos Críticos: Cuentas que vencen hoy o están vencidas sin cobrar.
    - 🚨 Cuentas Caídas: Casilleros o cuentas caídas que requieren reemplazo urgente.
    - 📦 Stock & Inventario: Salud del inventario, cuentas libres y alertas de plataformas en nivel bajo/crítico.
    - 🟢 Conectividad WhatsApp: Estado de la sesión de Evolution API.
    - detallado: Si es True, lista los nombres de clientes y correos de las cuentas críticas.
    """
    # 1. Finanzas
    try:
        b = database.get_financial_balance(period="mes_actual")
        inc_str = database.format_ars(b.get("collected_income", 0.0))
        cost_str = database.format_ars(b.get("collected_costs", 0.0))
        prof_str = database.format_ars(b.get("collected_profit", 0.0))
        pend_7d = database.format_ars(b.get("pending_receivables_7d", 0.0))
        proj_prof = database.format_ars(b.get("projected_monthly_profit", 0.0))
        active_subs = b.get("active_subscriptions_total", 0)
    except Exception as e:
        logger.warning(f"Error consultando balance financiero: {e}")
        inc_str = cost_str = prof_str = pend_7d = proj_prof = "Error"
        active_subs = 0

    # 2. Comprobantes pendientes
    try:
        pending_receipts = database.count_pending_payments()
    except Exception:
        pending_receipts = 0

    # 3. Vencimientos de hoy / impagas
    try:
        due_today_accs = database.get_due_today_unpaid_accounts()
    except Exception:
        due_today_accs = []

    # 4. Cuentas caídas
    try:
        fallen_accs = database.get_fallen_accounts()
    except Exception:
        fallen_accs = []

    # 5. Salud de Stock
    try:
        stock_health = database.get_stock_health_summary()
        total_free_accs = stock_health.get("total_free_accounts", 0)
        total_free_profiles = stock_health.get("total_free_profiles", 0)
        low_stock_platforms = stock_health.get("low_stock_platforms", [])
    except Exception:
        total_free_accs = total_free_profiles = 0
        low_stock_platforms = []

    # 6. Conectividad WhatsApp
    try:
        wa_status = await whatsapp_client.check_connection_status()
        wa_connected = bool(wa_status.get("connected"))
        wa_label = "🟢 Conectado (En línea)" if wa_connected else "🔴 Desconectado (Requiere QR)"
    except Exception:
        wa_label = "⚠️ No consultable"

    # Construcción del informe ejecutivo
    lines = [
        "🏢 <b>RADIOGRAFÍA EJECUTIVA 360° DE STREAMVAULT:</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>FINANZAS & RENTABILIDAD (Mes Actual):</b>",
        f"• Ingresos Cobrados: <b>{inc_str}</b>",
        f"• Costos de Proveedores: {cost_str}",
        f"• 💵 <b>GANANCIA NETA REAL: {prof_str}</b>",
        f"• ⏳ A cobrar en próximos 7 días: <b>{pend_7d}</b>",
        f"• 🎯 Proyección de ganancia mensual: <b>{proj_prof}</b>",
        f"• Suscripciones activas totales: <b>{active_subs}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🚨 <b>ESTADO OPERATIVO & ALERTAS CRÍTICAS:</b>",
        f"• 🧾 Comprobantes pendientes de revisión: <b>{pending_receipts}</b>" + (" ⚠️ <i>(Revisar con listar_comprobantes_pendientes)</i>" if pending_receipts > 0 else " ✅ (Al día)"),
        f"• ⏳ Cuentas que vencen HOY sin pagar: <b>{len(due_today_accs)}</b>",
        f"• ⚡ Cuentas caídas que requieren cambio: <b>{len(fallen_accs)}</b>" + (" 🚨 <i>(Reemplazar urgente)</i>" if fallen_accs else " ✅ (Cero caídas)"),
        f"• 💬 WhatsApp Evolution API: <b>{wa_label}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📦 <b>INVENTARIO & STOCK DISPONIBLE:</b>",
        f"• Cuentas completas libres: <b>{total_free_accs}</b>",
        f"• Perfiles de pantalla libres: <b>{total_free_profiles}</b>"
    ]

    if low_stock_platforms:
        lsp_str = ", ".join([f"{p['platform']} ({p['available']} disp.)" for p in low_stock_platforms])
        lines.append(f"• ⚠️ <b>Alerta de stock bajo en:</b> {lsp_str}")
    else:
        lines.append("• ✨ Stock en niveles saludables en todas las plataformas.")

    # Detalle expandido opcional
    if detallado:
        if due_today_accs:
            lines.append("\n📋 <b>Cuentas que vencen HOY:</b>")
            for a in due_today_accs[:5]:
                lines.append(f"  • {a.get('client_name') or 'Cliente'} - {a.get('platform')}: <code>{a.get('email')}</code> ({database.format_ars(a.get('price'))})")
            if len(due_today_accs) > 5:
                lines.append(f"  <i>... y {len(due_today_accs) - 5} cuentas más.</i>")

        if fallen_accs:
            lines.append("\n🚨 <b>Cuentas caídas pendientes:</b>")
            for f_acc in fallen_accs[:5]:
                lines.append(f"  • ID #{f_acc['id']} - {f_acc.get('platform')}: <code>{f_acc.get('email')}</code> (Cliente: {f_acc.get('client_name') or '-'})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


