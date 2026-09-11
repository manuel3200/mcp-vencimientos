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

    return sent_count

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
    tz_str = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")
    try:
        tz = timezone(tz_str)
    except Exception:
        tz = timezone("UTC")

    # Hora de comprobación diaria (por defecto 09:00 AM)
    check_hour = int(os.getenv("ALERT_HOUR", "9"))
    check_minute = int(os.getenv("ALERT_MINUTE", "0"))

    trigger = CronTrigger(hour=check_hour, minute=check_minute, timezone=tz)
    scheduler.add_job(
        check_and_send_alerts,
        trigger=trigger,
        id="daily_expiry_check",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"Scheduler iniciado. Verificación diaria programada a las {check_hour:02d}:{check_minute:02d} ({tz_str})")

def stop_scheduler():
    """Detiene el programador de tareas."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler detenido.")
