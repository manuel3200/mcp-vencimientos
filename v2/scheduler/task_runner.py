import os
import logging
from datetime import date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from database import get_expiring_streaming_accounts, mark_streaming_alert_sent
from telegram_bot import format_and_send_alert

logger = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()

import asyncio

async def check_and_send_alerts(days_window: int = None, force: bool = False) -> int:
    """Verifica servicios y cuentas de streaming por vencer y envía notificaciones por Telegram."""
    if days_window is None:
        try:
            days_window = int(os.getenv("DAYS_BEFORE_ALERT", "7"))
        except ValueError:
            days_window = 7

    logger.info(f"Iniciando escaneo de vencimientos (ventana: {days_window} días, force={force})...")
    expiring = get_expiring_streaming_accounts(days_window=days_window)
    today_str = date.today().isoformat()
    sent_count = 0

    for item in expiring:
        # Evitar re-enviar la misma alerta el mismo día a menos que sea forzado
        if not force and item.get("last_alert_sent") == today_str:
            logger.info(f"Alerta ya enviada hoy para {item.get('email')}, omitiendo...")
            continue

        success = await format_and_send_alert(item)
        if success:
            mark_streaming_alert_sent(item["id"], today_str)
            sent_count += 1
            logger.info(f"Alerta enviada con éxito para cliente {item.get('client_name')} ({item.get('platform')})")
            await asyncio.sleep(0.1)

    logger.info(f"Escaneo finalizado. Alertas enviadas: {sent_count}")

    # Verificar salud de stock en cada comprobación diaria
    try:
        import database
        from telegram_bot import format_and_send_stock_alert
        summary = database.get_stock_health_summary()
        if summary.get("has_alerts") and not force:
            logger.info("Plataformas con stock crítico detectadas durante escaneo diario. Notificando...")
            await format_and_send_stock_alert()
    except Exception as e:
        logger.error(f"Error verificando alertas de stock en scheduler: {e}")

    # Verificar vencimiento de cuentas madre ante mayoristas
    try:
        await check_and_send_supplier_expiry_alerts(days_window=3, force=force)
    except Exception as e:
        logger.error(f"Error verificando vencimiento de cuentas madre en scheduler: {e}")

    # Despacho automático de cobros/recordatorios por WhatsApp si Evolution API está activa
    try:
        import whatsapp_client
        import random
        wa_settings = database.get_whatsapp_api_settings()
        if wa_settings.get("auto_send_expiry") and wa_settings["auto_send_expiry"] == 1:
            wa_status = await whatsapp_client.check_connection_status()
            if wa_status.get("connected"):
                logger.info("Evolution API conectada. Iniciando envío automático de recordatorios por WhatsApp...")
                wa_sent_count = 0
                for item in expiring:
                    client_phone = item.get("whatsapp") or ""
                    if client_phone:
                        wa_data = database.generate_whatsapp_message(item, "cobro")
                        msg_text = wa_data.get("message_text", "")
                        if msg_text:
                            typing_delay = random.uniform(4.0, 7.0)
                            res = await whatsapp_client.send_text_message(
                                client_phone,
                                msg_text,
                                delay_seconds=typing_delay,
                                simulate_typing=True
                            )
                            if res.get("success"):
                                wa_sent_count += 1
                                # Cadencia anti-spam humana entre chat y chat: 15 a 30 segundos
                                between_chat_delay = random.uniform(15.0, 30.0)
                                logger.info(f"Recordatorio WhatsApp enviado a {client_phone}. Pausa humana de {between_chat_delay:.1f}s...")
                                await asyncio.sleep(between_chat_delay)
                if wa_sent_count > 0:
                    from telegram_bot import send_telegram_message
                    await send_telegram_message(
                        f"📲 <b>Auto-Cobro WhatsApp Activo:</b>\n"
                        f"Se enviaron automáticamente <b>{wa_sent_count}</b> recordatorios de vencimiento por WhatsApp a tus clientes con pausas anti-spam."
                    )
    except Exception as e:
        logger.error(f"Error en envío automático de recordatorios WhatsApp: {e}")

    return sent_count

async def check_and_send_evening_cutoff_alerts() -> int:
    """Aviso de corte vespertino (a partir de las 17:00 hs) para cuentas que vencen HOY y siguen impagas."""
    import database
    import whatsapp_client
    import random
    from telegram_bot import send_telegram_message

    wa_settings = database.get_whatsapp_api_settings()
    if not wa_settings.get("auto_send_expiry") or wa_settings["auto_send_expiry"] != 1:
        return 0

    status = await whatsapp_client.check_connection_status()
    if not status.get("connected"):
        return 0

    unpaid_today = database.get_due_today_unpaid_accounts()
    today_str = date.today().isoformat()
    sent_count = 0

    logger.info(f"Iniciando aviso vespertino de corte a las 17hs ({len(unpaid_today)} cuentas hoy)...")
    for item in unpaid_today:
        client_phone = item.get("whatsapp") or ""
        # Evitar re-enviar aviso de corte vespertino dos veces el mismo día
        if item.get("last_alert_sent") == f"{today_str}_evening":
            continue

        c_name = item.get("client_name") or "Cliente"
        plat = item.get("platform") or "Streaming"
        exp_date = item.get("expiry_date") or today_str
        price_str = item.get("price") or ""

        msg_cutoff = (
            f"⚠️ *¡Hola {c_name}!* Te recordamos que hoy *{exp_date}* finaliza el periodo de tu servicio de *{plat}*.\n\n"
            f"A partir de las 20:00 hs los accesos serán actualizados automáticamente por el sistema. "
            f"Si deseas continuar disfrutando de tu cuenta sin cortes ni cambios de clave, por favor envíanos tu comprobante de pago por este chat a la brevedad.\n\n"
            + (f"💰 *Importe a renovar:* {price_str}\n\n" if price_str else "") +
            f"¡Muchas gracias por tu atención! 🙌✨"
        )

        typing_sec = random.uniform(4.0, 7.0)
        res = await whatsapp_client.send_text_message(client_phone, msg_cutoff, delay_seconds=typing_sec, simulate_typing=True)
        if res.get("success"):
            database.mark_streaming_alert_sent(item["id"], f"{today_str}_evening")
            sent_count += 1
            between_chat_delay = random.uniform(15.0, 30.0)
            logger.info(f"Aviso de corte vespertino enviado a {client_phone}. Pausa humana de {between_chat_delay:.1f}s...")
            await asyncio.sleep(between_chat_delay)

    if sent_count > 0:
        await send_telegram_message(
            f"⚠️ <b>AVISO DE CORTE VESPERTINO ENVIADO (17:00 hs)</b>\n\n"
            f"Se notificó a <b>{sent_count}</b> cliente(s) cuyo servicio vence hoy y aún no han registrado pago."
        )
    return sent_count

async def cleanup_overdue_accounts() -> int:
    """Detecta cuentas vencidas sin pago y las pasa al estado 'por_cambiar_clave' para frenar avisos zombie."""
    import database
    from telegram_bot import send_telegram_message
    changed = database.mark_overdue_accounts_for_password_change(overdue_days_threshold=1)
    if changed:
        count = len(changed)
        logger.info(f"Se pasaron {count} cuentas impagas al estado 'por_cambiar_clave'")
        await send_telegram_message(
            f"🔒 <b>CUENTAS CON CORTE PENDIENTE (POR CAMBIAR CLAVE)</b>\n\n"
            f"Se detectaron <b>{count}</b> cuenta(s) que vencieron y no renovaron.\n"
            f"Pasaron al estado <b>'Por Cambiar Clave'</b> en el panel web para rotación de credenciales y liberación de casilleros."
        )
        return count
    return 0

_LAST_WA_STATE = {"connected": True, "alerted_disconnected": False}

async def check_whatsapp_heartbeat():
    """Monitor de vida de WhatsApp (cada 15 min): Notifica a Telegram si WhatsApp se desconecta."""
    global _LAST_WA_STATE
    import whatsapp_client
    from telegram_bot import send_telegram_message
    try:
        status = await whatsapp_client.check_connection_status()
        is_conn = bool(status.get("connected"))
        state = status.get("state", "desconocido")

        if not is_conn:
            if not _LAST_WA_STATE["alerted_disconnected"]:
                _LAST_WA_STATE["alerted_disconnected"] = True
                _LAST_WA_STATE["connected"] = False
                logger.warning(f"Heartbeat: WhatsApp desconectado (estado: {state}). Notificando a Telegram...")
                await send_telegram_message(
                    f"🚨 <b>¡ALERTA CRÍTICA: WHATSAPP DESCONECTADO!</b>\n\n"
                    f"• Estado reportado: <code>{state}</code>\n"
                    f"• La sesión de WhatsApp en Evolution API se ha cerrado o requiere escanear el código QR.\n"
                    f"• Los recordatorios automáticos y la recepción de comprobantes están <b>DETENIDOS</b>.\n\n"
                    f"👉 Ingresa a la sección <b>Integraciones</b> en el Panel Web para re-escanear el código QR."
                )
        else:
            if _LAST_WA_STATE.get("alerted_disconnected"):
                _LAST_WA_STATE["alerted_disconnected"] = False
                _LAST_WA_STATE["connected"] = True
                logger.info("Heartbeat: WhatsApp reconectado exitosamente. Notificando a Telegram...")
                await send_telegram_message(
                    f"🟢 <b>¡WHATSAPP RECONECTADO CON ÉXITO!</b>\n\n"
                    f"La sesión de WhatsApp volvió a estar en línea. El bot de atención y recepción opera normalmente."
                )
            else:
                _LAST_WA_STATE["connected"] = True
    except Exception as e:
        logger.debug(f"Error en heartbeat de WhatsApp: {e}")

_ALERTED_STALE_REPORTS = set()

async def check_stale_fallen_reports():
    """Watchdog de SLA: detecta clientes en espera (#C) por más de 4 horas sin resolver."""
    global _ALERTED_STALE_REPORTS
    import database
    import whatsapp_client
    from telegram_bot import send_telegram_message

    try:
        stale_list = database.get_stale_waiting_reports(hours_threshold=4.0)
        for rep in stale_list:
            rid = rep["id"]
            if rid in _ALERTED_STALE_REPORTS:
                continue

            _ALERTED_STALE_REPORTS.add(rid)
            c_name = rep.get("client_name") or "Cliente"
            plat = rep.get("platform") or "Streaming"
            phone = rep.get("sender_phone") or ""

            kb = {
                "inline_keyboard": [
                    [
                        {"text": f"🔄 Autorizar y Cambiar (#C{rid})", "callback_data": f"fallapp_{rid}"},
                        {"text": "💬 Abrir WhatsApp", "url": f"https://wa.me/{phone}"}
                    ]
                ]
            }
            await send_telegram_message(
                f"⏳ <b>ALERTA DE SLA: CLIENTE EN ESPERA PROLONGADA</b>\n\n"
                f"• Reporte: <b>#C{rid}</b>\n"
                f"• Cliente: <b>{c_name}</b> (<code>+{phone}</code>)\n"
                f"• Servicio: <b>{plat}</b>\n"
                f"• Estado: Lleva <b>más de 4 horas en espera</b> sin nueva cuenta asignada.\n\n"
                f"👉 Por favor carga stock o autoriza el reemplazo para no perder al cliente.",
                reply_markup=kb
            )

            # También avisar por WhatsApp privado al admin si está configurado
            wa_settings = database.get_whatsapp_api_settings()
            admin_phone = wa_settings.get("admin_whatsapp")
            clean_admin = database.clean_whatsapp_phone(admin_phone) if admin_phone else ""
            if clean_admin and clean_admin != phone:
                await whatsapp_client.send_text_message(
                    clean_admin,
                    f"⏳ *ALERTA DE SLA:* El cliente *{c_name}* (Reporte #C{rid}, {plat}) lleva *más de 4 horas en espera*.\n\n"
                    f"👉 Para asignarle la cuenta ahora: `/cambiar_{rid}`"
                )
    except Exception as e:
        logger.debug(f"Error en watchdog de reportes de caída: {e}")

async def check_and_send_stock_alerts(force: bool = False) -> bool:
    """Verifica si existen plataformas con stock agotado o bajo su umbral y emite alerta si es necesario."""
    import database
    from telegram_bot import format_and_send_stock_alert
    summary = database.get_stock_health_summary()
    if summary.get("has_alerts") or force:
        logger.info("Emitiendo alerta de stock por Telegram...")
        return await format_and_send_stock_alert()
    return False

async def check_and_send_supplier_expiry_alerts(days_window: int = 3, force: bool = False) -> int:
    """Verifica cuentas madre que vencen ante proveedores mayoristas o con riesgo de corte y notifica a Telegram."""
    import database
    from telegram_bot import format_and_send_supplier_alert
    expiring_masters = database.get_expiring_master_accounts(days_window=days_window)
    sent_count = 0

    for item in expiring_masters:
        try:
            success = await format_and_send_supplier_alert(item)
            if success:
                sent_count += 1
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error enviando alerta de cuenta madre para {item.get('email')}: {e}")

    return sent_count

def start_scheduler():
    """Inicia el programador de tareas en segundo plano."""
    from apscheduler.triggers.interval import IntervalTrigger
    import database

    tz_str = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")
    try:
        tz = timezone(tz_str)
    except Exception:
        tz = timezone("UTC")

    # 1. Escaneo Matutino Diario (por defecto 09:00 AM)
    check_hour = int(os.getenv("ALERT_HOUR", "9"))
    check_minute = int(os.getenv("ALERT_MINUTE", "0"))

    trigger_morning = CronTrigger(hour=check_hour, minute=check_minute, timezone=tz)
    scheduler.add_job(
        check_and_send_alerts,
        trigger=trigger_morning,
        id="daily_expiry_morning_check",
        replace_existing=True
    )

    # 2. Escaneo Vespertino de Corte (por defecto 17:00 hs o configurable desde el panel)
    try:
        wa_settings = database.get_whatsapp_api_settings()
        cutoff_hour = int(wa_settings.get("expiry_cutoff_hour") or 17)
    except Exception:
        cutoff_hour = 17

    trigger_cutoff = CronTrigger(hour=cutoff_hour, minute=0, timezone=tz)
    scheduler.add_job(
        check_and_send_evening_cutoff_alerts,
        trigger=trigger_cutoff,
        id="daily_expiry_evening_cutoff_check",
        replace_existing=True
    )

    # 3. Limpieza nocturna de cuentas vencidas a 'por_cambiar_clave' (23:55 hs)
    trigger_cleanup = CronTrigger(hour=23, minute=55, timezone=tz)
    scheduler.add_job(
        cleanup_overdue_accounts,
        trigger=trigger_cleanup,
        id="daily_overdue_cleanup",
        replace_existing=True
    )

    # 4. Heartbeat Monitor de WhatsApp a Telegram (cada 15 minutos)
    scheduler.add_job(
        check_whatsapp_heartbeat,
        trigger=IntervalTrigger(minutes=15),
        id="whatsapp_heartbeat_check",
        replace_existing=True
    )

    # 5. Watchdog de SLA para clientes en espera (#C) (cada 30 minutos)
    scheduler.add_job(
        check_stale_fallen_reports,
        trigger=IntervalTrigger(minutes=30),
        id="stale_fallen_reports_check",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"Scheduler iniciado. Mañana: {check_hour:02d}:{check_minute:02d}, Corte vespertino: {cutoff_hour:02d}:00, Heartbeat: cada 15m ({tz_str})")

def stop_scheduler():
    """Detiene el programador de tareas."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler detenido.")
