import os
import re
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("telegram_bot")

def get_telegram_config():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id

async def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Envía un mensaje a través del bot de Telegram configurado."""
    token, chat_id = get_telegram_config()
    
    if not token or not chat_id:
        logger.warning("Telegram Bot Token o Chat ID no configurados.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            if response.status_code == 200 and data.get("ok"):
                logger.info(f"Mensaje de Telegram enviado con éxito a {chat_id}")
                return True
            else:
                logger.error(f"Error de Telegram API: {data.get('description', response.text)}")
                return False
    except Exception as e:
        logger.error(f"Excepción al enviar mensaje de Telegram: {e}")
        return False

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

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>🔔 Avisa a tu cliente para cobrar la renovación</i>")
    
    message_text = "\n".join(lines)
    return await send_telegram_message(message_text)
