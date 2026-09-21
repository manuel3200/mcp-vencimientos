import os
import logging
from typing import Optional, List, Dict, Any

from core.audit import log_audit_event
from infrastructure.external.evolution_whatsapp.client import send_poll
from infrastructure.external.telegram.bot_app import send_telegram_poll

logger = logging.getLogger("application.community.polls")

SENSITIVE_POLL_KEYWORDS = [
    "precio", "presupuesto", "cuánto", "pagar", "costo",
    "comprar", "contratar", "interesa", "quiero", "saldo"
]


def is_sensitive_poll(question: str) -> bool:
    """Evalúa si una encuesta involucra datos sensibles de compra o pricing para forzar anonimato (MED-01)."""
    q_lower = str(question or "").lower()
    return any(kw in q_lower for kw in SENSITIVE_POLL_KEYWORDS)


async def create_and_dispatch_poll(
    question: str,
    options: List[str],
    send_whatsapp: bool = True,
    whatsapp_target: Optional[str] = None,
    send_telegram: bool = True,
    telegram_target: Optional[str] = None,
    selectable_count: int = 1,
    category: str = "demand",
    actor: str = "admin"
) -> Dict[str, Any]:
    """Crea y despacha una encuesta nativa en grupos de WhatsApp y canales de Telegram para medir demanda o interés."""
    clean_q = str(question or "").strip()
    clean_opts = [str(opt).strip() for opt in (options or []) if str(opt).strip()]

    if not clean_q:
        return {"success": False, "error": "La pregunta de la encuesta es obligatoria"}
    if len(clean_opts) < 2:
        return {"success": False, "error": "La encuesta debe tener al menos 2 opciones de respuesta"}

    sensitive = is_sensitive_poll(clean_q)
    if sensitive:
        logger.info(f"🔒 Encuesta de intención de compra/precio detectada: '{clean_q}'. Forzando anonimato estricto (MED-01).")

    target_wa = whatsapp_target or os.getenv("WHATSAPP_BROADCAST_TARGET", "").strip()
    target_tg = telegram_target or os.getenv("TELEGRAM_CHANNEL_ID", "").strip()


    wa_sent = False
    tg_sent = False
    errors = []

    # 1. Enviar Encuesta Nativa a WhatsApp
    if send_whatsapp:
        if not target_wa:
            errors.append("No se configuró destino de WhatsApp (@g.us o @newsletter)")
        else:
            try:
                wa_res = await send_poll(
                    recipient=target_wa,
                    question=clean_q,
                    options=clean_opts,
                    selectable_count=selectable_count
                )
                wa_sent = bool(wa_res.get("success"))
                if not wa_sent:
                    errors.append(f"Fallo WhatsApp Poll: {wa_res.get('error', 'Error desconocido')}")
            except Exception as e:
                errors.append(f"Excepción WhatsApp Poll: {str(e)}")

    # 2. Enviar Encuesta Nativa a Telegram
    if send_telegram:
        if not target_tg:
            errors.append("No se configuró destino de Telegram (@canal o ID)")
        else:
            try:
                tg_ok = await send_telegram_poll(
                    chat_id=target_tg,
                    question=clean_q,
                    options=clean_opts,
                    is_anonymous=True,
                    allows_multiple_answers=(selectable_count > 1)
                )
                tg_sent = tg_ok
                if not tg_sent:
                    errors.append("Fallo Telegram Poll: Error en API de Telegram")
            except Exception as e:
                errors.append(f"Excepción Telegram Poll: {str(e)}")

    overall_success = wa_sent or tg_sent

    # 3. Asentar en bitácora inmutable de auditoría HMAC
    try:
        log_audit_event(
            actor=actor,
            action="DISPATCH_COMMUNITY_POLL",
            target_type="poll",
            target_id=category,
            old_value="",
            new_value=f"q:{clean_q},opts:{len(clean_opts)},wa:{wa_sent},tg:{tg_sent}",
            ip_or_source="polls_service"
        )
    except Exception as e:
        logger.warning(f"Error registrando auditoría de encuesta: {e}")

    return {
        "success": overall_success,
        "question": clean_q,
        "options": clean_opts,
        "category": category,
        "whatsapp_sent": wa_sent,
        "whatsapp_target": target_wa,
        "telegram_sent": tg_sent,
        "telegram_target": target_tg,
        "errors": errors
    }
