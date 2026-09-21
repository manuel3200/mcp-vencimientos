import os
import logging
from typing import Optional, List, Dict, Any

from core.audit import log_audit_event
from infrastructure.external.evolution_whatsapp.client import send_channel_or_group_message
from infrastructure.external.telegram.bot_app import broadcast_to_telegram_channel

logger = logging.getLogger("application.channels.broadcast")

CATEGORY_CONFIG = {
    "promo": {
        "badge_wa": "🔥 *¡PROMOCIÓN FLASH STREAMVAULT!* 🔥",
        "badge_tg": "🔥 <b>¡PROMOCIÓN FLASH STREAMVAULT!</b> 🔥",
        "default_cta": "📲 _Responde a este mensaje o contáctanos para asegurar tu cupo antes de que se agote._"
    },
    "stock": {
        "badge_wa": "📦 *¡NUEVO STOCK Y DISPONIBILIDAD INMEDIATA!* 📦",
        "badge_tg": "📦 <b>¡NUEVO STOCK Y DISPONIBILIDAD INMEDIATA!</b> 📦",
        "default_cta": "⚡ _Activación y entrega al instante en minutos. ¡Consulta disponibilidad de tu plataforma favorita!_"
    },
    "mantenimiento": {
        "badge_wa": "⚠️ *AVISO DE MANTENIMIENTO Y ACTUALIZACIÓN TÉCNICA* ⚠️",
        "badge_tg": "⚠️ <b>AVISO DE MANTENIMIENTO Y ACTUALIZACIÓN TÉCNICA</b> ⚠️",
        "default_cta": "🛡️ _Nuestro equipo está optimizando la infraestructura para garantizar la máxima estabilidad._"
    },
    "comunicado": {
        "badge_wa": "📢 *COMUNICADO OFICIAL DE LA COMUNIDAD* 📢",
        "badge_tg": "📢 <b>COMUNICADO OFICIAL DE LA COMUNIDAD</b> 📢",
        "default_cta": "✨ _Gracias por ser parte de StreamVault. Tu preferencia es nuestra mayor motivación._"
    }
}


def format_whatsapp_broadcast(
    title: str,
    message: str,
    category: str = "promo",
    platforms: Optional[List[str]] = None,
    custom_cta: Optional[str] = None
) -> str:
    """Construye un mensaje corporativo optimizado para canales y grupos de WhatsApp."""
    cat_cfg = CATEGORY_CONFIG.get(category.lower(), CATEGORY_CONFIG["comunicado"])
    badge = cat_cfg["badge_wa"]
    cta = custom_cta or cat_cfg["default_cta"]

    lines = [
        badge,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 *{title.strip()}*\n",
        message.strip()
    ]

    if platforms:
        plat_list = ", ".join(f"`{p.strip()}`" for p in platforms if p.strip())
        if plat_list:
            lines.append(f"\n📺 *Servicios Incluidos:* {plat_list}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(cta)
    return "\n".join(lines)


def format_telegram_broadcast(
    title: str,
    message: str,
    category: str = "promo",
    platforms: Optional[List[str]] = None,
    custom_cta: Optional[str] = None
) -> str:
    """Construye un mensaje corporativo optimizado con HTML para Canales de Telegram."""
    cat_cfg = CATEGORY_CONFIG.get(category.lower(), CATEGORY_CONFIG["comunicado"])
    badge = cat_cfg["badge_tg"]
    cta = custom_cta or cat_cfg["default_cta"].replace("_", "<i>").replace("_", "</i>")

    lines = [
        badge,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>{title.strip()}</b>\n",
        message.strip()
    ]

    if platforms:
        plat_list = ", ".join(f"<code>{p.strip()}</code>" for p in platforms if p.strip())
        if plat_list:
            lines.append(f"\n📺 <b>Servicios Incluidos:</b> {plat_list}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(cta)
    return "\n".join(lines)


async def broadcast_announcement(
    title: str,
    message: str,
    category: str = "promo",
    platforms: Optional[List[str]] = None,
    send_whatsapp: bool = True,
    whatsapp_target: Optional[str] = None,
    send_telegram: bool = True,
    telegram_target: Optional[str] = None,
    actor: str = "admin",
    custom_cta: Optional[str] = None
) -> Dict[str, Any]:
    """Publica y difunde un anuncio oficial en Canales de WhatsApp y Telegram simultáneamente con 1 clic."""
    clean_title = str(title or "").strip()
    clean_msg = str(message or "").strip()
    clean_cat = str(category or "promo").lower()

    if not clean_title:
        return {"success": False, "error": "El título del anuncio es obligatorio"}
    if not clean_msg:
        return {"success": False, "error": "El cuerpo del mensaje es obligatorio"}

    # Resolver destinos predeterminados si no se especifican
    target_wa = whatsapp_target or os.getenv("WHATSAPP_BROADCAST_TARGET", "").strip()
    target_tg = telegram_target or os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    wa_sent = False
    tg_sent = False
    errors = []

    # 1. Despacho a WhatsApp Channel o Grupo
    if send_whatsapp:
        if not target_wa:
            errors.append("No se configuró destino de WhatsApp (Canal @newsletter o Grupo @g.us)")
        else:
            wa_text = format_whatsapp_broadcast(
                title=clean_title,
                message=clean_msg,
                category=clean_cat,
                platforms=platforms,
                custom_cta=custom_cta
            )
            try:
                wa_res = await send_channel_or_group_message(recipient=target_wa, text=wa_text)
                wa_sent = bool(wa_res.get("success"))
                if not wa_sent:
                    errors.append(f"Fallo WhatsApp: {wa_res.get('error', 'Error desconocido')}")
            except Exception as e:
                errors.append(f"Excepción WhatsApp: {str(e)}")

    # 2. Despacho a Canal de Telegram
    if send_telegram:
        if not target_tg:
            errors.append("No se configuró canal de Telegram (@canal o ID)")
        else:
            tg_text = format_telegram_broadcast(
                title=clean_title,
                message=clean_msg,
                category=clean_cat,
                platforms=platforms,
                custom_cta=custom_cta
            )
            try:
                tg_ok = await broadcast_to_telegram_channel(channel_target=target_tg, text=tg_text)
                tg_sent = tg_ok
                if not tg_sent:
                    errors.append("Fallo Telegram: Error en API de Telegram")
            except Exception as e:
                errors.append(f"Excepción Telegram: {str(e)}")

    overall_success = wa_sent or tg_sent

    # 3. Registrar auditoría inmutable
    try:
        log_audit_event(
            actor=actor,
            action="BROADCAST_CHANNEL",
            target_type="channels",
            target_id=clean_cat,
            old_value="",
            new_value=f"wa:{wa_sent}({target_wa}),tg:{tg_sent}({target_tg}),title:{clean_title}",
            ip_or_source="broadcast_service"
        )
    except Exception as e:
        logger.warning(f"Error registrando auditoría de broadcast: {e}")

    return {
        "success": overall_success,
        "title": clean_title,
        "category": clean_cat,
        "whatsapp_sent": wa_sent,
        "whatsapp_target": target_wa,
        "telegram_sent": tg_sent,
        "telegram_target": target_tg,
        "errors": errors
    }
