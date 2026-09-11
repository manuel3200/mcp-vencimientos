import os
import re
import httpx
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple, Union

import database

logger = logging.getLogger("telegram_bot")

_polling_task: Optional[asyncio.Task] = None
_polling_active: bool = False

def get_telegram_config():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id

async def send_telegram_message(
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    chat_id: Optional[str] = None
) -> bool:
    """Envía un mensaje con soporte para botones inline (reply_markup)."""
    token, default_chat_id = get_telegram_config()
    target_chat = chat_id or default_chat_id
    
    if not token or not target_chat:
        logger.warning("Telegram Bot Token o Chat ID no configurados.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            if response.status_code == 200 and data.get("ok"):
                return True
            else:
                logger.error(f"Error de Telegram API (sendMessage): {data.get('description', response.text)}")
                return False
    except Exception as e:
        logger.error(f"Excepción al enviar mensaje de Telegram: {e}")
        return False

async def edit_telegram_message(
    chat_id: str,
    message_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None
) -> bool:
    """Edita el texto y botones de un mensaje existente en Telegram."""
    token, _ = get_telegram_config()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            data = res.json()
            return bool(res.status_code == 200 and data.get("ok"))
    except Exception as e:
        logger.error(f"Error al editar mensaje de Telegram: {e}")
        return False

async def answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False) -> bool:
    """Responde a un callback de botón en Telegram para cerrar el estado de carga o mostrar popup."""
    token, _ = get_telegram_config()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            return res.status_code == 200
    except Exception as e:
        logger.error(f"Error al responder callback query: {e}")
        return False

async def send_telegram_document(
    filename: str,
    content: bytes,
    caption: str = "",
    chat_id: str = ""
) -> bool:
    """Envía un archivo adjunto descargable (CSV, Excel, etc.) al chat administrativo de Telegram."""
    token, default_chat = get_telegram_config()
    target_chat = chat_id if chat_id else default_chat
    if not token or not target_chat:
        return False

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data = {
        "chat_id": target_chat,
        "caption": caption,
        "parse_mode": "HTML"
    }
    files = {
        "document": (filename, content, "text/csv")
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, data=data, files=files)
            data_resp = res.json()
            return bool(res.status_code == 200 and data_resp.get("ok"))
    except Exception as e:
        logger.error(f"Error al enviar documento por Telegram: {e}")
        return False

async def send_full_backup_to_telegram(chat_id: str = "") -> bool:
    """Genera y envía los reportes CSV de cuentas activas, stock y balance financiero a Telegram."""
    date_str = date.today().strftime("%Y%m%d")
    await send_telegram_message("📦 <b>Generando copia de seguridad de tu CRM en Excel/CSV...</b>", chat_id=chat_id)

    # 1. Cuentas Activas
    csv_active = database.export_active_accounts_csv().encode("utf-8-sig")
    await send_telegram_document(
        filename=f"crm_cuentas_activas_{date_str}.csv",
        content=csv_active,
        caption="📋 <b>Cuentas y Clientes Activos (Excel/CSV)</b>",
        chat_id=chat_id
    )

    # 2. Stock Libre
    csv_stock = database.export_free_stock_csv().encode("utf-8-sig")
    await send_telegram_document(
        filename=f"crm_stock_libre_{date_str}.csv",
        content=csv_stock,
        caption="📦 <b>Inventario de Stock Libre (Excel/CSV)</b>",
        chat_id=chat_id
    )

    # 3. Transacciones y Finanzas
    csv_tx = database.export_transactions_csv().encode("utf-8-sig")
    await send_telegram_document(
        filename=f"crm_balance_transacciones_{date_str}.csv",
        content=csv_tx,
        caption="💵 <b>Historial Financiero y Ganancias (Excel/CSV)</b>",
        chat_id=chat_id
    )

    await send_telegram_message("✅ <b>Copia de seguridad enviada con éxito.</b> Archivos listos para abrir en Microsoft Excel.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
    return True

def get_main_menu_keyboard() -> Dict[str, Any]:
    """Teclado inline del menú principal."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Balance Financiero", "callback_data": "menu_balance"},
                {"text": "⏳ Por Cobrar (7d)", "callback_data": "menu_cobros"}
            ],
            [
                {"text": "👤 Ficha de Cliente", "callback_data": "menu_client_prompt"},
                {"text": "🏷️ Precios ARS", "callback_data": "menu_catalog"}
            ],
            [
                {"text": "📦 Combos Activos", "callback_data": "menu_combos"},
                {"text": "📺 Pantallas", "callback_data": "menu_screens"}
            ],
            [
                {"text": "🏢 Proveedores & Cuentas", "callback_data": "menu_master_accounts"},
                {"text": "📦 Stock & Alertas", "callback_data": "menu_stock"}
            ],
            [
                {"text": "💳 Datos de Cobro & CBU", "callback_data": "menu_datos_pago"},
                {"text": "🚨 Cuentas Caídas", "callback_data": "menu_fallen"}
            ],
            [
                {"text": "📋 Diagnóstico & Logs", "callback_data": "menu_logs"},
                {"text": "💾 Backup CSV", "callback_data": "menu_backup"}
            ]
        ]
    }

def get_alert_keyboard(account_id: int, wa_url: str = "", client_id: Optional[int] = None, wa_consolidated_url: str = "", multiple_count: int = 0) -> Dict[str, Any]:
    """Botones para las alertas de vencimiento."""
    kb = []
    if wa_consolidated_url and multiple_count > 1:
        kb.append([{"text": f"🧾 Cobro Consolidado ({multiple_count} servicios - 1 Clic)", "url": wa_consolidated_url}])
    if wa_url:
        kb.append([{"text": "💬 Cobrar por WhatsApp (1 Clic)", "url": wa_url}])
    
    actions_row = [
        {"text": "💵 Pagó (Renovar 30d)", "callback_data": f"pay_{account_id}"},
        {"text": "🚨 Marcar Caída", "callback_data": f"fall_{account_id}"}
    ]
    kb.append(actions_row)

    if client_id:
        kb.append([{"text": "👤 Ver Ficha 360° del Cliente", "callback_data": f"client_{client_id}"}])

    return {"inline_keyboard": kb}

def format_client_360_telegram(profile: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Genera el texto HTML formateado y el teclado interactivo para la Ficha 360° en Telegram."""
    c = profile["client"]
    health = profile["health_status"]
    kpis = profile["financial_kpis"]
    act = profile.get("active_accounts", [])
    billing = profile.get("consolidated_billing", {})

    lines = [
        f"👤 <b>FICHA 360°: {c['name']}</b> (<code>{c['client_code']}</code>)",
        f"• Tipo: {c['client_type_label']}",
        f"• Estado de Salud: <b>{health['label']}</b>",
        f"• Diagnóstico: <i>{health['summary']}</i>",
        "──────────────────────"
    ]

    # Contacto
    contact_parts = []
    if c.get("whatsapp"):
        clean_num = c.get("clean_whatsapp") or re.sub(r'\D', '', c['whatsapp'])
        contact_parts.append(f"📱 <a href=\"https://wa.me/{clean_num}\">{c['whatsapp']}</a>")
    if c.get("telegram"):
        clean_tg = c['telegram'].lstrip('@')
        contact_parts.append(f"💬 <a href=\"https://t.me/{clean_tg}\">@{clean_tg}</a>")
    if contact_parts:
        lines.append("📞 " + " | ".join(contact_parts))
    if c.get("notes"):
        lines.append(f"📝 <i>Notas: {c['notes']}</i>")

    lines.append("──────────────────────")
    lines.append("💰 <b>MÉTRICAS & LTV (Pesos Argentinos):</b>")
    lines.append(f"• Total Facturado (LTV): <b>{kpis['ltv_formatted']}</b> ({kpis['payments_count']} cobros)")
    lines.append(f"• Ganancia Neta Real: <b>{kpis['total_profit_formatted']}</b>")
    lines.append(f"• Gasto Mensual Activo: <b>{kpis['monthly_committed_spend_formatted']}</b>")
    if kpis.get("last_payment"):
        lp = kpis["last_payment"]
        dt = str(lp.get("created_at", ""))[:10]
        lines.append(f"• Último Cobro: {lp['amount_formatted']} ({dt}) vía {lp.get('payment_method')}")

    lines.append("──────────────────────")
    lines.append(f"📺 <b>SUSCRIPCIONES ACTIVAS ({len(act)}):</b>")
    if not act:
        lines.append("<i>(No posee suscripciones activas asignadas actualmente)</i>")
    else:
        for a in act:
            perf = f" (Perf: {a['profile_name']})" if a.get("profile_name") else ""
            pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
            lines.append(
                f"• <b>{a['platform']}</b>{perf}: <code>{a['email']}</code>{pin}\n"
                f"  Vence: <code>{a.get('expiry_date')}</code> ({a.get('days_label')}) | Cobro: <b>{a.get('price_formatted')}</b>"
            )

    # Teclado Inline
    kb = []
    if billing.get("success") and billing.get("wa_link"):
        kb.append([{"text": f"📲 Cobro Consolidado WhatsApp ({billing['accounts_count']} servicios)", "url": billing["wa_link"]}])
    elif c.get("clean_whatsapp"):
        kb.append([{"text": "💬 Abrir Chat de WhatsApp", "url": f"https://wa.me/{c['clean_whatsapp']}"}])

    if len(act) == 1:
        kb.append([{"text": f"💵 Cobrar {act[0]['platform']} (Renovar 30d)", "callback_data": f"pay_{act[0]['id']}"}])

    kb.append([{"text": "🔙 Menú Principal", "callback_data": "menu_main"}])

    return "\n".join(lines), {"inline_keyboard": kb}

async def format_and_send_alert(account: Dict[str, Any]) -> bool:
    """Formatea una alerta de vencimiento para cuentas de streaming con datos del cliente."""
    client_name = account.get("client_name") or "Cliente"
    client_type = account.get("client_type") or "consumidor_final"
    type_badge = "👔 Revendedor" if "revend" in client_type.lower() else "👤 Consumidor Final"
    
    whatsapp = account.get("whatsapp", "").strip()
    telegram = account.get("telegram", "").strip()
    
    platform = account.get("platform", "Streaming")
    email = account.get("email", "")
    profile_name = account.get("profile_name", "")
    expiry = account.get("expiry_date", "")
    price = account.get("price", "")
    days = account.get("days_remaining", 0)
    client_id = account.get("client_id")
    
    if days is not None and days < 0:
        icon = "🚨"
        header = f"<b>¡SERVICIO VENCIDO HACE {abs(days)} DÍA(S)!</b>"
    elif days == 0:
        icon = "⚠️"
        header = "<b>¡EL SERVICIO VENCE HOY!</b>"
    elif days == 1:
        icon = "⚠️"
        header = "<b>¡EL SERVICIO VENCE MAÑANA!</b>"
    else:
        icon = "🔔"
        header = f"<b>¡AVISO: VENCE EN {days} DÍAS!</b>"

    lines = [
        f"{icon} {header}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>Cliente:</b> {client_name} ({type_badge})",
    ]

    # Links directos de contacto
    if whatsapp:
        clean_num = re.sub(r'[^0-9]', '', whatsapp)
        lines.append(f"📱 <b>WhatsApp:</b> <a href=\"https://wa.me/{clean_num}\">{whatsapp}</a>")
    if telegram:
        clean_tg = telegram.lstrip('@')
        lines.append(f"💬 <b>Telegram:</b> <a href=\"https://t.me/{clean_tg}\">@{clean_tg}</a>")

    lines.append("──────────────────────")
    service_label = f"{platform} (Perfil: {profile_name})" if profile_name else platform
    lines.append(f"📺 <b>Plataforma:</b> {service_label}")
    lines.append(f"📧 <b>Correo:</b> <code>{email}</code>")
    lines.append(f"📅 <b>Vence:</b> <code>{expiry}</code>")
    
    if price:
        lines.append(f"💰 <b>A cobrar:</b> {price}")

    # Enlace de 1 Clic para cobrar por WhatsApp
    wa_url = ""
    try:
        from database import generate_whatsapp_message
        wa_data = generate_whatsapp_message(account, message_type="cobro")
        wa_url = wa_data.get("wa_link", "")
        if wa_url:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📲 <a href=\"{wa_url}\"><b>👉 ENVIAR RECORDATORIO POR WHATSAPP (1 Clic)</b></a>")
    except Exception as e:
        logger.error(f"Error generando link de WhatsApp en alerta: {e}")

    # Chequear si el cliente tiene múltiples servicios para cobro consolidado
    wa_consolidated_url = ""
    multiple_count = 0
    if client_id:
        try:
            profile = database.get_client_360_profile(client_id)
            if profile and profile.get("consolidated_billing", {}).get("success"):
                billing = profile["consolidated_billing"]
                multiple_count = billing.get("accounts_count", 0)
                if multiple_count > 1:
                    wa_consolidated_url = billing.get("wa_link", "")
                    lines.append(f"🧾 <i>Este cliente tiene {multiple_count} servicios activos (Total: {billing.get('total_amount_formatted')}).</i>")
        except Exception as e:
            logger.error(f"Error calculando cobro consolidado en alerta: {e}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>👇 Gestiona esta suscripción con los botones:</i>")
    
    message_text = "\n".join(lines)
    reply_markup = get_alert_keyboard(
        account["id"], 
        wa_url=wa_url, 
        client_id=client_id, 
        wa_consolidated_url=wa_consolidated_url, 
        multiple_count=multiple_count
    )
    return await send_telegram_message(message_text, reply_markup=reply_markup)

async def format_and_send_stock_alert(chat_id: str = "") -> bool:
    """Envía un reporte interactivo de alerta de stock bajo o crítico por Telegram."""
    summary = database.get_stock_health_summary()
    platforms = summary.get("platforms", [])

    if not platforms:
        msg = "📦 <b>Control de Inventario:</b>\n\nNo hay cuentas o plataformas registradas en el sistema."
        return await send_telegram_message(msg, chat_id=chat_id)

    lines = [
        "📦 <b>SALUD DEL INVENTARIO & ALERTAS DE STOCK</b>",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]

    has_alerts = summary.get("has_alerts", False)
    if has_alerts:
        lines.append("⚠️ <b>¡ATENCIÓN! Plataformas en nivel crítico o agotadas:</b>\n")
    else:
        lines.append("✅ <b>¡Todo en orden! Stock saludable en todas las plataformas:</b>\n")

    for p in platforms:
        plat_name = p["platform"]
        free = p["free_count"]
        thresh = p["min_threshold"]
        occupied = p["occupied_count"]

        if p["status"] == "agotado":
            lines.append(f"🔴 <b>{plat_name}:</b> ¡AGOTADO! (0 libres | Mín: {thresh} | Activas: {occupied})")
        elif p["status"] == "bajo":
            lines.append(f"🟡 <b>{plat_name}:</b> {free} libre(s) (STOCK BAJO | Mín: {thresh} | Activas: {occupied})")
        else:
            lines.append(f"🟢 <b>{plat_name}:</b> {free} libre(s) (Mín: {thresh})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 <b>Resumen:</b> {summary['total_free_units']} cuentas libres en {summary['total_platforms']} plataformas.")
    if summary['out_of_stock_count'] > 0:
        lines.append(f"🚨 <b>{summary['out_of_stock_count']} plataforma(s) con stock CERO (Agotadas).</b>")
    if summary['low_stock_count'] > 0:
        lines.append(f"⚠️ <b>{summary['low_stock_count']} plataforma(s) en umbral crítico.</b>")

    kb = {
        "inline_keyboard": [
            [
                {"text": "📦 Ver Cuentas Libres", "callback_data": "menu_stock_list"},
                {"text": "🔄 Re-verificar", "callback_data": "check_stock_alert"}
            ],
            [
                {"text": "🔙 Menú Principal", "callback_data": "menu_main"}
            ]
        ]
    }
    return await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

async def format_and_send_supplier_alert(item: Dict[str, Any], chat_id: str = "") -> bool:
    """Envía una alerta prioritaria de vencimiento de cuenta madre ante el proveedor mayorista."""
    plat = item.get("platform", "")
    email = item.get("email", "")
    days = item.get("days_remaining_supplier")
    sup_name = item.get("supplier_name") or "Sin asignar"
    sup_contact = item.get("supplier_contact") or ""
    sup_exp = item.get("supplier_expiry_date") or "-"
    risk = item.get("risk_mismatch", False)
    occ = item.get("profiles_occupied", 0)
    tot = item.get("profiles_total", 0)

    lines = [
        "🏢 <b>ALERTA DE CUENTA MADRE (PROVEEDOR)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📺 <b>Servicio:</b> {plat}",
        f"📧 <b>Cuenta Principal:</b> <code>{email}</code>",
        f"👔 <b>Proveedor:</b> {sup_name}",
        f"📅 <b>Vencimiento Mayorista:</b> <code>{sup_exp}</code>"
    ]

    if days is not None:
        if days < 0:
            lines.append(f"🚨 <b>ESTADO: ¡VENCIDA HACE {abs(days)} DÍAS!</b>")
        elif days == 0:
            lines.append("🚨 <b>ESTADO: ¡VENCE HOY ANTE EL PROVEEDOR!</b>")
        else:
            lines.append(f"⏳ <b>ESTADO: Vence en {days} días</b>")

    lines.append(f"👥 <b>Perfiles Asignados:</b> {occ} de {tot} activos")

    if risk:
        lines.append(
            f"\n⚠️ <b>¡RIESGO DE CORTE INMINENTE!</b>\n"
            f"Hay clientes con perfiles que vencen después de la cuenta madre (hasta {item.get('client_max_expiry')}). "
            f"Renueva la cuenta con tu proveedor para que tus clientes no sufran cortes."
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    kb_buttons = []
    clean_sup_phone = re.sub(r'[^0-9]', '', sup_contact)
    if clean_sup_phone:
        kb_buttons.append([{"text": f"💬 Contactar a {sup_name} (WhatsApp)", "url": f"https://wa.me/{clean_sup_phone}"}])
    kb_buttons.append([{"text": "🔙 Menú Principal", "callback_data": "menu_main"}])

    return await send_telegram_message("\n".join(lines), reply_markup={"inline_keyboard": kb_buttons}, chat_id=chat_id)

# ==========================================
# Despachadores de Mensajes y Callbacks
# ==========================================
async def handle_telegram_message(msg: Dict[str, Any]):
    """Procesa mensajes entrantes y comandos del chat administrativo."""
    _, authorized_chat = get_telegram_config()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    # Filtro de seguridad
    if authorized_chat and chat_id != authorized_chat:
        logger.warning(f"Mensaje ignorado de usuario no autorizado ID: {chat_id}")
        return

    text = (msg.get("text") or "").strip()
    cmd = text.lower()

    if cmd in ("/start", "/menu", "/ayuda", "menu"):
        menu_text = (
            "🤖 <b>Streaming CRM - Panel de Control Telegram</b>\n\n"
            "Bienvenido al panel rápido. Selecciona una acción para gestionar tu negocio:"
        )
        await send_telegram_message(menu_text, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/balance", "balance"):
        b = database.get_financial_balance()
        txt = (
            f"📊 <b>BALANCE FINANCIERO ({b['period']}):</b>\n\n"
            f"💰 <b>Ingresos Cobrados:</b> {database.format_ars(b['collected_income'])} ({b['transactions_count']} cobros)\n"
            f"📉 <b>Costo Proveedores:</b> {database.format_ars(b['collected_costs'])}\n"
            f"💵 <b>GANANCIA NETA:</b> +{database.format_ars(b['collected_profit'])}\n\n"
            f"⏳ <b>Por Cobrar (7d):</b> {database.format_ars(b['pending_receivables_7d'])} ({b['pending_accounts_count']} cuentas)\n"
            f"🎯 <b>Proyección Mensual:</b> +{database.format_ars(b['projected_monthly_profit'])}\n"
            f"📱 Total Suscripciones Activas: {b['active_subscriptions_total']}"
        )
        await send_telegram_message(txt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/precios", "/catalogo", "precios", "catalogo"):
        cat = database.get_price_catalog()
        if not cat:
            await send_telegram_message("🏷️ El catálogo de precios está vacío actualmente.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏷️ <b>LISTA OFICIAL DE PRECIOS (ARS):</b>\n"]
            for c in cat:
                st = "📱" if c["service_type"] == "pantalla" else "👑"
                lines.append(
                    f"{st} <b>{c['platform']}</b>\n"
                    f"   👤 Final: <b>{c['price_final_formatted']}</b> | 👔 Rev: <b>{c['price_reseller_formatted']}</b>\n"
                    f"   📉 Costo: {c['cost_price_formatted']}\n"
                )
            lines.append("<i>Precios actualizados automáticamente.</i>")
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/combos", "combos", "/packs", "packs"):
        combos = database.get_combos(only_active=True)
        if not combos:
            await send_telegram_message("📦 No hay combos o packs promocionales activos.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["📦 <b>PACKS Y COMBOS ACTIVOS (ARS):</b>\n"]
            for cb in combos:
                lines.append(
                    f"🔹 <b>{cb['name']}</b>\n"
                    f"   📺 Incluye: <code>{cb['platforms_str']}</code>\n"
                    f"   💰 Final: <b>{cb['price_final_formatted']}</b> | 👔 Rev: <b>{cb['price_reseller_formatted']}</b>\n"
                )
            lines.append("<i>Puedes vender un combo desde el panel web o pidiéndoselo a Gemini.</i>")
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/stock", "stock", "/alerta_stock", "/stock_bajo", "/alertas_stock", "/inventario"):
        await format_and_send_stock_alert(chat_id=chat_id)

    elif cmd in ("/escanear", "/scan"):
        from scheduler import check_and_send_alerts
        sent = await check_and_send_alerts(days_window=7, force=True)
        if sent == 0:
            sent = await check_and_send_alerts(days_window=30, force=True)
        await send_telegram_message(f"🔍 Escaneo completado. Se enviaron {sent} alerta(s) interactivas.", chat_id=chat_id)

    elif cmd in ("/alerta", "/alerta_demo", "/test_alerta"):
        accounts = database.get_active_accounts()
        if accounts:
            await format_and_send_alert(accounts[0])
            await send_telegram_message("👆 ¡Arriba tienes la alerta interactiva con botones de 1 toque!", chat_id=chat_id)
        else:
            await send_telegram_message("No hay cuentas activas registradas para enviar alerta.", chat_id=chat_id)

    elif cmd.startswith("/cliente") or cmd.startswith("/ficha") or cmd.startswith("/buscar"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            msg_prompt = (
                "👤 <b>CONSULTA DE FICHA 360° DE CLIENTE</b>\n\n"
                "Para consultar el perfil completo, salud de pagos, LTV en ARS y suscripciones activas, escribe el comando seguido de su nombre, código o WhatsApp:\n\n"
                "👉 <code>/cliente Juan</code>\n"
                "👉 <code>/cliente CLI-001</code>\n"
                "👉 <code>/cliente 54911...</code>\n"
                "👉 <code>/cliente @usuario</code>"
            )
            await send_telegram_message(msg_prompt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            query_val = parts[1].strip()
            profile = database.get_client_360_profile(query_val)
            if not profile:
                await send_telegram_message(
                    f"❌ No se encontró ningún cliente que coincida con '<b>{query_val}</b>'.\n\n"
                    f"Verifica el nombre, número de WhatsApp o código e inténtalo nuevamente.",
                    reply_markup=get_main_menu_keyboard(),
                    chat_id=chat_id
                )
            else:
                card_text, kb = format_client_360_telegram(profile)
                await send_telegram_message(card_text, reply_markup=kb, chat_id=chat_id)

    elif cmd in ("/backup", "backup", "/exportar", "exportar"):
        await send_full_backup_to_telegram(chat_id=chat_id)

    elif cmd in ("/datos_pago", "/cbu", "/alias", "datos_pago", "cbu", "alias", "/pago", "pago", "/pagos", "pagos"):
        s = database.get_payment_settings()
        pm_block = database.get_formatted_payment_methods()
        txt = (
            "💳 <b>DATOS DE COBRO CONFIGURADOS (1 TOQUE PARA COPIAR):</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Alias Mercado Pago:</b> <code>{s.get('alias_mp') or 'No configurado'}</code>\n"
            f"• <b>CBU / CVU:</b> <code>{s.get('cvu_cbu') or 'No configurado'}</code>\n"
            f"• <b>Titular:</b> <b>{s.get('account_holder') or 'No configurado'}</b>\n"
            f"• <b>Banco / Entidad:</b> {s.get('bank_name') or 'Mercado Pago'}\n"
        )
        if s.get("usdt_address"):
            txt += f"• <b>USDT / Cripto:</b> <code>{s.get('usdt_address')}</code>\n"
        txt += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>Bloque formateado que se envía al cliente:</b>\n\n"
            f"{pm_block}\n\n"
            "<i>💡 Toca sobre cualquier número o Alias en recuadro para copiarlo al instante.</i>"
        )
        await send_telegram_message(txt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/logs", "logs", "/diagnostico", "diagnostico", "/salud", "salud"):
        import system_logger
        rep = system_logger.get_system_health_report()
        db = rep["database"]
        ls = rep["logs_summary"]
        status_emoji = "🟢" if rep["status"] == "OK" else ("🟡" if rep["status"] == "WARNING" else "🔴")
        lines = [
            f"{status_emoji} <b>DIAGNÓSTICO DEL SISTEMA ({rep['status']}):</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"⏱️ <b>Uptime:</b> {rep['uptime']}",
            f"🗄️ <b>Base de Datos:</b> {db['status']} ({db['size']} | {db['total_accounts']} cuentas)",
            f"🤖 <b>Telegram Polling:</b> {rep['telegram']['status']}",
            f"⏰ <b>Scheduler:</b> {rep['scheduler']['status']}",
            f"📊 <b>Buffer de Logs:</b> {ls['total_buffered']} eventos",
            f"⚠️ <b>Advertencias:</b> {ls['warnings_count']} | 🚨 <b>Errores:</b> {ls['errors_count']}",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]
        if ls.get("last_error"):
            le = ls["last_error"]
            lines.append(f"🚨 <b>Último Error ({le['timestamp']}) en [{le['module']}]:</b>")
            lines.append(f"<code>{le['message'][:300]}</code>")
        else:
            lines.append("✨ <i>¡Sin errores recientes registrados en el sistema!</i>")

        kb = {
            "inline_keyboard": [
                [{"text": "🔄 Actualizar Diagnóstico", "callback_data": "menu_logs"}],
                [{"text": "🔙 Menú Principal", "callback_data": "menu_main"}]
            ]
        }
        await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

    elif cmd in ("/proveedores", "proveedores", "/mayoristas", "mayoristas"):
        sups = database.get_suppliers()
        if not sups:
            await send_telegram_message("🏢 No hay proveedores mayoristas registrados aún. Puedes agregarlos desde el panel web.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏢 <b>PROVEEDORES MAYORISTAS REGISTRADOS:</b>\n"]
            for s in sups:
                c = f" | 📱 {s['contact']}" if s.get("contact") else ""
                lines.append(
                    f"🔹 <b>{s['name']}</b>{c}\n"
                    f"  Cuentas Madre: <b>{s['master_accounts_count']}</b> ({s['profiles_count']} perfiles)\n"
                    f"  Total Pagado: <b>{s['total_spent_formatted']}</b>\n"
                    f"  Datos de Pago: <code>{s.get('payment_info') or 'Sin datos'}</code>\n"
                )
            lines.append("<i>Gestiona altas y pagos desde la pestaña 'Proveedores' del Panel Web.</i>")
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif cmd in ("/cuentas_madre", "cuentas_madre", "/vencimientos_madre", "vencimientos_madre"):
        masters = database.get_master_accounts_overview()
        if not masters:
            await send_telegram_message("📺 No hay cuentas registradas en el sistema.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏢 <b>MONITOR DE CUENTAS MADRE & PROVEEDORES:</b>\n"]
            for m in masters[:10]:
                risk_ico = "⚠️ " if m["risk_mismatch"] else ""
                lines.append(
                    f"{risk_ico}📺 <b>{m['platform']}</b> - <code>{m['email']}</code>\n"
                    f"  Proveedor: <b>{m['supplier_name']}</b>\n"
                    f"  Vence Proveedor: <code>{m['supplier_expiry_date'] or 'Sin fecha'}</code> ({m['status_label']})\n"
                    f"  Perfiles: {m['profiles_occupied']} ocupados / {m['profiles_total']} tot\n"
                )
                if m["risk_mismatch"]:
                    lines.append(f"  🔴 <i>{m['mismatch_warning']}</i>\n")
            if len(masters) > 10:
                lines.append(f"<i>... y {len(masters) - 10} más en el Panel Web.</i>")
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

async def handle_telegram_callback(query: Dict[str, Any]):
    """Procesa pulsaciones de botones inline."""
    _, authorized_chat = get_telegram_config()
    query_id = query.get("id")
    chat_id = str(query.get("message", {}).get("chat", {}).get("id", ""))
    data = query.get("data", "")

    if authorized_chat and chat_id != authorized_chat:
        await answer_callback_query(query_id, "No autorizado", show_alert=True)
        return

    # 1. Menú Principal
    if data == "menu_balance":
        await answer_callback_query(query_id)
        b = database.get_financial_balance()
        txt = (
            f"📊 <b>BALANCE FINANCIERO ({b['period']}):</b>\n\n"
            f"💰 <b>Ingresos Cobrados:</b> {database.format_ars(b['collected_income'])}\n"
            f"📉 <b>Costo Proveedores:</b> {database.format_ars(b['collected_costs'])}\n"
            f"💵 <b>GANANCIA NETA:</b> +{database.format_ars(b['collected_profit'])}\n\n"
            f"⏳ <b>Por Cobrar (7d):</b> {database.format_ars(b['pending_receivables_7d'])} ({b['pending_accounts_count']} cuentas)\n"
            f"🎯 <b>Proyección Mensual:</b> +{database.format_ars(b['projected_monthly_profit'])}"
        )
        await send_telegram_message(txt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data == "menu_cobros":
        await answer_callback_query(query_id)
        b = database.get_financial_balance()
        pending = b.get("pending_accounts", [])
        if not pending:
            await send_telegram_message("🎉 ¡Al día! No hay cuentas por cobrar en los próximos 7 días.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = [f"⏳ <b>CUENTAS POR COBRAR ({len(pending)}):</b>\n"]
            for p in pending[:8]:
                d_str = "HOY" if p['days_remaining'] == 0 else f"en {p['days_remaining']}d"
                lines.append(f"• <b>{p['client']}</b> - {p['platform']}: <b>{database.format_ars(p['price'])}</b> ({d_str})")
            txt = "\n".join(lines)
            kb = {
                "inline_keyboard": [
                    [{"text": "🔔 Enviar Tarjetas de Cobro (1-Toque)", "callback_data": "menu_scan"}],
                    [{"text": "🔙 Volver al Menú", "callback_data": "menu_main"}]
                ]
            }
            await send_telegram_message(txt, reply_markup=kb, chat_id=chat_id)

    elif data == "menu_catalog":
        await answer_callback_query(query_id)
        cat = database.get_price_catalog()
        if not cat:
            await send_telegram_message("🏷️ El catálogo de precios está vacío actualmente.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏷️ <b>LISTA OFICIAL DE PRECIOS (ARS):</b>\n"]
            for c in cat:
                st = "📱" if c["service_type"] == "pantalla" else "👑"
                lines.append(
                    f"{st} <b>{c['platform']}</b>\n"
                    f"   👤 Final: <b>{c['price_final_formatted']}</b> | 👔 Rev: <b>{c['price_reseller_formatted']}</b>\n"
                    f"   📉 Costo: {c['cost_price_formatted']}\n"
                )
            lines.append("<i>Precios actualizados automáticamente.</i>")
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data == "menu_combos":
        await answer_callback_query(query_id)
        combos = database.get_combos(only_active=True)
        if not combos:
            await send_telegram_message("📦 No hay combos o packs promocionales activos.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["📦 <b>PACKS Y COMBOS ACTIVOS (ARS):</b>\n"]
            for cb in combos:
                lines.append(
                    f"🔹 <b>{cb['name']}</b>\n"
                    f"   📺 Incluye: <code>{cb['platforms_str']}</code>\n"
                    f"   💰 Final: <b>{cb['price_final_formatted']}</b> | 👔 Rev: <b>{cb['price_reseller_formatted']}</b>\n"
                )
            await send_telegram_message("\n".join(lines), reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data == "menu_client_prompt":
        await answer_callback_query(query_id)
        msg_prompt = (
            "👤 <b>CONSULTAR FICHA 360° DE CLIENTE</b>\n\n"
            "Escribe en el chat el comando <code>/cliente</code> seguido del nombre, teléfono o @telegram.\n\n"
            "Ejemplo:\n"
            "👉 <code>/cliente Lucas</code>\n"
            "👉 <code>/cliente CLI-002</code>\n"
            "👉 <code>/cliente 54911...</code>"
        )
        await send_telegram_message(msg_prompt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data.startswith("client_"):
        await answer_callback_query(query_id)
        client_id_val = data.replace("client_", "").strip()
        profile = database.get_client_360_profile(client_id_val)
        if not profile:
            await send_telegram_message("❌ No se encontró la ficha del cliente.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            card_text, kb = format_client_360_telegram(profile)
            await send_telegram_message(card_text, reply_markup=kb, chat_id=chat_id)

    elif data == "menu_main":
        await answer_callback_query(query_id)
        menu_text = (
            "🤖 <b>Streaming CRM - Panel de Control Telegram</b>\n\n"
            "Bienvenido al panel rápido. Selecciona una acción para gestionar tu negocio:"
        )
        await send_telegram_message(menu_text, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data == "menu_datos_pago":
        await answer_callback_query(query_id)
        s = database.get_payment_settings()
        pm_block = database.get_formatted_payment_methods()
        txt = (
            "💳 <b>DATOS DE COBRO CONFIGURADOS (1 TOQUE PARA COPIAR):</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Alias Mercado Pago:</b> <code>{s.get('alias_mp') or 'No configurado'}</code>\n"
            f"• <b>CBU / CVU:</b> <code>{s.get('cvu_cbu') or 'No configurado'}</code>\n"
            f"• <b>Titular:</b> <b>{s.get('account_holder') or 'No configurado'}</b>\n"
            f"• <b>Banco / Entidad:</b> {s.get('bank_name') or 'Mercado Pago'}\n"
        )
        if s.get("usdt_address"):
            txt += f"• <b>USDT / Cripto:</b> <code>{s.get('usdt_address')}</code>\n"
        txt += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>Bloque formateado para el cliente:</b>\n\n"
            f"{pm_block}\n\n"
            "<i>💡 Toca sobre cualquier número o Alias en recuadro para copiarlo al instante.</i>"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "🔙 Volver al Menú", "callback_data": "menu_main"}]
            ]
        }
        await send_telegram_message(txt, reply_markup=kb, chat_id=chat_id)

    elif data == "menu_logs":
        await answer_callback_query(query_id)
        import system_logger
        rep = system_logger.get_system_health_report()
        db = rep["database"]
        ls = rep["logs_summary"]
        status_emoji = "🟢" if rep["status"] == "OK" else ("🟡" if rep["status"] == "WARNING" else "🔴")
        lines = [
            f"{status_emoji} <b>DIAGNÓSTICO DEL SISTEMA ({rep['status']}):</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"⏱️ <b>Uptime:</b> {rep['uptime']}",
            f"🗄️ <b>Base de Datos:</b> {db['status']} ({db['size']} | {db['total_accounts']} cuentas)",
            f"🤖 <b>Telegram Polling:</b> {rep['telegram']['status']}",
            f"⏰ <b>Scheduler:</b> {rep['scheduler']['status']}",
            f"📊 <b>Buffer de Logs:</b> {ls['total_buffered']} eventos",
            f"⚠️ <b>Advertencias:</b> {ls['warnings_count']} | 🚨 <b>Errores:</b> {ls['errors_count']}",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]
        if ls.get("last_error"):
            le = ls["last_error"]
            lines.append(f"🚨 <b>Último Error ({le['timestamp']}) en [{le['module']}]:</b>")
            lines.append(f"<code>{le['message'][:300]}</code>")
        else:
            lines.append("✨ <i>¡Sin errores recientes registrados en el sistema!</i>")

        kb = {
            "inline_keyboard": [
                [{"text": "🔄 Actualizar Diagnóstico", "callback_data": "menu_logs"}],
                [{"text": "🔙 Menú Principal", "callback_data": "menu_main"}]
            ]
        }
        await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

    elif data == "menu_master_accounts":
        await answer_callback_query(query_id)
        masters = database.get_master_accounts_overview()
        if not masters:
            await send_telegram_message("📺 No hay cuentas registradas en el sistema.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏢 <b>MONITOR DE CUENTAS MADRE & PROVEEDORES:</b>\n"]
            for m in masters[:8]:
                risk_ico = "⚠️ " if m["risk_mismatch"] else ""
                lines.append(
                    f"{risk_ico}📺 <b>{m['platform']}</b> - <code>{m['email']}</code>\n"
                    f"  Proveedor: <b>{m['supplier_name']}</b>\n"
                    f"  Vence Proveedor: <code>{m['supplier_expiry_date'] or 'Sin fecha'}</code> ({m['status_label']})\n"
                    f"  Perfiles: {m['profiles_occupied']} ocupados / {m['profiles_total']} tot\n"
                )
                if m["risk_mismatch"]:
                    lines.append(f"  🔴 <i>{m['mismatch_warning']}</i>\n")
            kb = {
                "inline_keyboard": [
                    [{"text": "🏢 Ver Proveedores", "callback_data": "menu_suppliers"}],
                    [{"text": "🔙 Menú Principal", "callback_data": "menu_main"}]
                ]
            }
            await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

    elif data == "menu_suppliers":
        await answer_callback_query(query_id)
        sups = database.get_suppliers()
        if not sups:
            await send_telegram_message("🏢 No hay proveedores mayoristas registrados aún.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = ["🏢 <b>PROVEEDORES MAYORISTAS REGISTRADOS:</b>\n"]
            for s in sups:
                c = f" | 📱 {s['contact']}" if s.get("contact") else ""
                lines.append(
                    f"🔹 <b>{s['name']}</b>{c}\n"
                    f"  Cuentas Madre: <b>{s['master_accounts_count']}</b> ({s['profiles_count']} perfiles)\n"
                    f"  Total Pagado: <b>{s['total_spent_formatted']}</b>\n"
                    f"  Datos de Pago: <code>{s.get('payment_info') or 'Sin datos'}</code>\n"
                )
            kb = {
                "inline_keyboard": [
                    [{"text": "📺 Ver Cuentas Madre", "callback_data": "menu_master_accounts"}],
                    [{"text": "🔙 Menú Principal", "callback_data": "menu_main"}]
                ]
            }
            await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

    elif data == "menu_stock":
        await answer_callback_query(query_id)
        await format_and_send_stock_alert(chat_id=chat_id)

    elif data == "menu_stock_list":
        await answer_callback_query(query_id)
        stock = database.get_free_stock()
        if not stock:
            await send_telegram_message("📦 No hay cuentas libres en stock actualmente.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = [f"📦 <b>CUENTAS LIBRES EN INVENTARIO ({len(stock)}):</b>\n"]
            for s in stock[:15]:
                perf = f" ({s['profile_name']})" if s.get('profile_name') else ""
                lines.append(f"• <b>{s['platform']}</b>{perf}: <code>{s['email']}</code>")
            if len(stock) > 15:
                lines.append(f"\n<i>... y {len(stock) - 15} más en el panel web.</i>")
            kb = {
                "inline_keyboard": [
                    [{"text": "📊 Ver Semáforo de Stock", "callback_data": "menu_stock"}],
                    [{"text": "🔙 Menú Principal", "callback_data": "menu_main"}]
                ]
            }
            await send_telegram_message("\n".join(lines), reply_markup=kb, chat_id=chat_id)

    elif data == "check_stock_alert":
        await answer_callback_query(query_id, "Comprobando niveles de inventario...", show_alert=False)
        await format_and_send_stock_alert(chat_id=chat_id)

    elif data == "menu_screens":
        await answer_callback_query(query_id)
        screens = database.get_shared_screens_overview()
        if not screens:
            await send_telegram_message("📺 No hay cuentas registradas con pantallas múltiples.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            lines = [f"📺 <b>PANTALLAS COMPARTIDAS ({len(screens)} Cuentas Madre):</b>\n"]
            for s in screens[:5]:
                lines.append(f"• <b>{s['platform']}</b>: <code>{s['email']}</code>\n  Ocupación: {s['occupied_count']}/{s['total_profiles']} ({s['free_count']} libres)")
            txt = "\n".join(lines)
            await send_telegram_message(txt, reply_markup=get_main_menu_keyboard(), chat_id=chat_id)

    elif data == "menu_fallen":
        await answer_callback_query(query_id)
        fallen = database.get_fallen_accounts()
        if not fallen:
            await send_telegram_message("🎉 ¡Excelente! No hay cuentas caídas reportadas.", reply_markup=get_main_menu_keyboard(), chat_id=chat_id)
        else:
            for f in fallen[:3]:
                f_id = f["id"]
                c_name = f.get("client_name") or "Sin cliente"
                msg = (
                    f"🚨 <b>Cuenta Caída Pendiente:</b>\n"
                    f"• {f['platform']}: <code>{f['email']}</code>\n"
                    f"• Cliente: {c_name}\n"
                    f"• Detalle: {f.get('notes') or '-'}"
                )
                kb = {"inline_keyboard": [[{"text": "🔄 Reemplazar Automáticamente", "callback_data": f"repl_{f_id}"}]]}
                await send_telegram_message(msg, reply_markup=kb, chat_id=chat_id)

    elif data == "menu_scan":
        await answer_callback_query(query_id, "Iniciando escaneo...", show_alert=False)
        from scheduler import check_and_send_alerts
        sent = await check_and_send_alerts(days_window=7, force=True)
        if sent == 0:
            sent = await check_and_send_alerts(days_window=30, force=True)
            if sent == 0:
                accounts = database.get_active_accounts()
                if accounts:
                    await format_and_send_alert(accounts[0])
                    sent = 1
                    await send_telegram_message(
                        "ℹ️ No había cuentas por vencer en 30 días, pero te enviamos la primera cuenta activa para que pruebes los botones interactivos.",
                        reply_markup=get_main_menu_keyboard(),
                        chat_id=chat_id
                    )
                else:
                    await send_telegram_message(
                        "🔍 Escaneo completado: No tienes cuentas activas registradas.",
                        reply_markup=get_main_menu_keyboard(),
                        chat_id=chat_id
                    )
            else:
                await send_telegram_message(
                    f"✅ Se enviaron {sent} alerta(s) de los próximos 30 días con botones interactivos.",
                    reply_markup=get_main_menu_keyboard(),
                    chat_id=chat_id
                )
        else:
            await send_telegram_message(
                f"✅ Escaneo completado. Se enviaron {sent} alerta(s) interactivas con botones de acción rápida de 1 toque.",
                reply_markup=get_main_menu_keyboard(),
                chat_id=chat_id
            )

    elif data == "menu_backup":
        await answer_callback_query(query_id, "Generando copia de seguridad...", show_alert=False)
        await send_full_backup_to_telegram(chat_id=chat_id)

    # 2. Acciones de Cuenta (Cobro y Caída)
    elif data.startswith("pay_"):
        acc_id = data.split("_")[1]
        res = database.register_customer_payment(email_or_id=acc_id, payment_method="Telegram Bot")
        if res.get("success"):
            await answer_callback_query(query_id, f"✅ ¡Cobro registrado! Vence el {res['new_expiry']}", show_alert=True)
            wa_data = database.generate_whatsapp_message(acc_id, message_type="entrega")
            wa_link = wa_data.get("wa_link", "")
            kb = None
            if wa_link:
                kb = {"inline_keyboard": [[{"text": "📲 Enviar Recibo por WhatsApp", "url": wa_link}]]}
            await send_telegram_message(
                f"💵 <b>¡Cobro Registrado y Renovado con Éxito!</b>\n\n"
                f"• Cliente: <b>{res['client_name']}</b>\n"
                f"• Servicio: {res['platform']} ({res['email']})\n"
                f"• Cobrado: +{database.format_ars(res['amount'])}\n"
                f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
                f"• Nuevo Vencimiento: <code>{res['new_expiry']}</code> (30 días extendidos)",
                reply_markup=kb,
                chat_id=chat_id
            )
        else:
            await answer_callback_query(query_id, f"Error: {res.get('error')}", show_alert=True)

    elif data.startswith("fall_"):
        acc_id = data.split("_")[1]
        acc = database.mark_account_fallen(acc_id, reason="Reportada desde botón de Telegram")
        if acc:
            await answer_callback_query(query_id, "Cuenta marcada como caída.", show_alert=True)
            kb = {"inline_keyboard": [[{"text": "🔄 Reemplazar por una libre de inmediato", "callback_data": f"repl_{acc_id}"}]]}
            await send_telegram_message(
                f"🚨 <b>Cuenta marcada como CAÍDA:</b>\n"
                f"• {acc['platform']}: <code>{acc['email']}</code>\n"
                f"• Cliente: {acc.get('client_name') or 'Cliente'}\n\n"
                f"¿Deseas buscar un reemplazo libre y asignárselo ahora?",
                reply_markup=kb,
                chat_id=chat_id
            )
        else:
            await answer_callback_query(query_id, "No se encontró la cuenta", show_alert=True)

    elif data.startswith("repl_"):
        acc_id = data.split("_")[1]
        res = database.replace_fallen_account(acc_id)
        if res and res.get("success"):
            new_a = res["new_account"]
            client_name = new_a.get("client_name") or "Cliente"
            wa_data = database.generate_whatsapp_message(new_a, message_type="reemplazo")
            wa_url = wa_data.get("wa_link", "")
            await answer_callback_query(query_id, "¡Reemplazo exitoso realizado!", show_alert=True)
            kb = None
            if wa_url:
                kb = {"inline_keyboard": [[{"text": "📲 Enviar Nueva Cuenta por WhatsApp", "url": wa_url}]]}
            await send_telegram_message(
                f"🎉 <b>REEMPLAZO REALIZADO CON ÉXITO:</b>\n\n"
                f"👤 <b>Cliente:</b> {client_name}\n"
                f"📺 <b>Plataforma:</b> {new_a['platform']}\n"
                f"✨ <b>NUEVAS CREDENCIALES:</b>\n"
                f"• Correo: <code>{new_a['email']}</code>\n"
                f"• Clave: <code>{new_a['password']}</code>" + (f"\n• Perfil: {new_a['profile_name']}" if new_a.get('profile_name') else "") + (f" [PIN: {new_a['profile_pin']}]" if new_a.get('profile_pin') else "") + "\n"
                f"📅 Mantiene vencimiento: <code>{new_a['expiry_date']}</code>",
                reply_markup=kb,
                chat_id=chat_id
            )
        else:
            await answer_callback_query(query_id, "No hay stock libre disponible para esa plataforma.", show_alert=True)

# ==========================================
# Motor de Long Polling Asíncrono
# ==========================================
async def telegram_polling_loop():
    """Bucle de recepción de eventos en tiempo real mediante long polling."""
    global _polling_active
    token, chat_id = get_telegram_config()
    if not token:
        logger.warning("Telegram Bot Token no configurado. Polling desactivado.")
        return

    logger.info("Iniciando Telegram Long Polling interactivo...")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": False})
    except Exception as e:
        logger.warning(f"Error al limpiar webhook previo: {e}")

    offset = 0
    _polling_active = True

    while _polling_active:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            payload = {
                "offset": offset,
                "timeout": 15,
                "allowed_updates": ["message", "callback_query"]
            }
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("ok"):
                        updates = data.get("result", [])
                        for u in updates:
                            offset = u["update_id"] + 1
                            if "message" in u:
                                asyncio.create_task(handle_telegram_message(u["message"]))
                            elif "callback_query" in u:
                                asyncio.create_task(handle_telegram_callback(u["callback_query"]))
                elif res.status_code == 409:
                    logger.warning("Conflicto de Telegram (otra instancia activa). Reintentando en 5s...")
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(2)
        except httpx.TimeoutException:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en telegram polling loop: {e}")
            await asyncio.sleep(3)

def start_telegram_polling():
    """Inicia el long polling en segundo plano."""
    global _polling_task, _polling_active
    if _polling_task is None or _polling_task.done():
        _polling_active = True
        _polling_task = asyncio.create_task(telegram_polling_loop())
        logger.info("Tarea de Telegram Polling iniciada.")

def stop_telegram_polling():
    """Detiene el long polling."""
    global _polling_task, _polling_active
    _polling_active = False
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        logger.info("Tarea de Telegram Polling cancelada.")
