import os
import logging
from typing import Optional, List, Dict, Any

from db.connection import get_connection
from core.audit import log_audit_event
from infrastructure.external.evolution_whatsapp.client import send_channel_or_group_message

logger = logging.getLogger("application.community.scheduled_broadcast")

MONDAY_RULES_MESSAGE = (
    "🛡️ *NORMAS DE LA COMUNIDAD & CANALES OFICIALES STREAMVAULT* 🛡️\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👋 *¡Feliz inicio de semana a todos!* Les recordamos las pautas clave para mantener nuestra comunidad 100% segura y ordenada:\n\n"
    "📌 *Pautas de Convivencia y Seguridad:*\n"
    "1. 🔒 *Privacidad Total:* Por tu seguridad, nunca envíes correos, contraseñas ni comprobantes bancarios en este grupo público.\n"
    "2. 💬 *Atención Directa:* Para renovaciones, compras o garantía de cuentas caídas, comunícate siempre por chat privado con el administrador.\n"
    "3. 🚫 *Anti-Spam:* No se permite la difusión de enlaces externos no autorizados, cadenas ni publicidad de terceros.\n\n"
    "💳 *Medios de Pago Oficiales Habilitados:*\n"
    "• Transferencias bancarias (CBU / CVU y Alias)\n"
    "• Mercado Pago al instante\n"
    "• Cripto / Binance USDT\n\n"
    "¡Que tengan una excelente y productiva semana! 🍿🚀"
)

FRIDAY_PROMO_MESSAGE = (
    "🍿 *LIQUIDACIÓN DE CASILLEROS LIBRES & COMBOS DE FIN DE SEMANA* 🍿\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ *¡Llegó el fin de semana de maratón y entretenimiento!* ⚡\n\n"
    "Tenemos perfiles y cuentas completas listas con activación inmediata en minutos:\n"
    "• 📺 *Netflix 4K (Casa Extra):* Perfil individual ultra estable sin cortes por hogar.\n"
    "• ⚽ *Disney+ Premium:* Deportes en vivo por ESPN y estrenos en streaming.\n"
    "• 🎬 *Max Platino (HBO):* Cine en 4K Ultra HD.\n"
    "• 🌐 *HTTP Custom VPN:* Internet seguro y alta velocidad vinculado por HWID.\n\n"
    "🎁 *Combos Dúo y Trío Promocionales disponibles con descuento especial.*\n"
    "📲 _Envía un mensaje privado al administrador para asegurar tu perfil antes de que se agote el stock del finde._ 🙌✨"
)


def get_active_community_groups() -> List[str]:
    """Obtiene la lista de JIDs de grupos de WhatsApp habilitados para recibir comunicados comunitarios."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT group_jid FROM whatsapp_groups_config
            WHERE bot_enabled = 1
        """).fetchall()
        groups = [r["group_jid"] for r in rows if r["group_jid"]]
        if not groups:
            def_target = os.getenv("WHATSAPP_BROADCAST_TARGET", "").strip()
            if def_target and def_target.endswith("@g.us"):
                groups = [def_target]
        return groups
    finally:
        conn.close()


async def run_monday_rules_broadcast(
    target_groups: Optional[List[str]] = None,
    actor: str = "scheduler"
) -> Dict[str, Any]:
    """Despacha el comunicado semanal de normas de convivencia y soporte a los grupos activos."""
    groups = target_groups or get_active_community_groups()
    if not groups:
        return {"success": False, "sent_count": 0, "message": "No hay grupos de WhatsApp activos configurados"}

    sent_count = 0
    errors = []

    for g_jid in groups:
        try:
            res = await send_channel_or_group_message(recipient=g_jid, text=MONDAY_RULES_MESSAGE)
            if res.get("success"):
                sent_count += 1
            else:
                errors.append(f"{g_jid}: {res.get('error')}")
        except Exception as e:
            errors.append(f"{g_jid}: {str(e)}")

    try:
        log_audit_event(
            actor=actor,
            action="COMMUNITY_MONDAY_BROADCAST",
            target_type="group_broadcast",
            target_id="monday_rules",
            old_value="",
            new_value=f"sent:{sent_count}/{len(groups)}",
            ip_or_source="scheduled_broadcast_service"
        )
    except Exception as e:
        logger.warning(f"Error en auditoría de broadcast de lunes: {e}")

    return {
        "success": sent_count > 0,
        "broadcast_type": "monday_rules",
        "sent_count": sent_count,
        "total_targets": len(groups),
        "errors": errors
    }


async def run_friday_weekend_promo_broadcast(
    target_groups: Optional[List[str]] = None,
    actor: str = "scheduler"
) -> Dict[str, Any]:
    """Despacha la promoción relámpago de fin de semana y stock disponible a los grupos activos."""
    groups = target_groups or get_active_community_groups()
    if not groups:
        return {"success": False, "sent_count": 0, "message": "No hay grupos de WhatsApp activos configurados"}

    sent_count = 0
    errors = []

    for g_jid in groups:
        try:
            res = await send_channel_or_group_message(recipient=g_jid, text=FRIDAY_PROMO_MESSAGE)
            if res.get("success"):
                sent_count += 1
            else:
                errors.append(f"{g_jid}: {res.get('error')}")
        except Exception as e:
            errors.append(f"{g_jid}: {str(e)}")

    try:
        log_audit_event(
            actor=actor,
            action="COMMUNITY_FRIDAY_BROADCAST",
            target_type="group_broadcast",
            target_id="friday_promo",
            old_value="",
            new_value=f"sent:{sent_count}/{len(groups)}",
            ip_or_source="scheduled_broadcast_service"
        )
    except Exception as e:
        logger.warning(f"Error en auditoría de broadcast de viernes: {e}")

    return {
        "success": sent_count > 0,
        "broadcast_type": "friday_promo",
        "sent_count": sent_count,
        "total_targets": len(groups),
        "errors": errors
    }
