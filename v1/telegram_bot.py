import os
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

async def format_and_send_alert(service: Dict[str, Any]) -> bool:
    """Formatea una alerta atractiva de vencimiento y la envía por Telegram."""
    name = service.get("name", "Servicio")
    category = service.get("category", "General")
    expiry = service.get("expiry_date", "Sin fecha")
    cost = service.get("cost", "")
    recurrence = service.get("recurrence", "mensual")
    notes = service.get("notes", "")
    days = service.get("days_remaining", 0)
    
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
        "━━━━━━━━━━━━━━━━━━",
        f"📌 <b>Servicio:</b> {name}",
        f"🏷️ <b>Categoría:</b> {category}",
        f"📅 <b>Vence:</b> <code>{expiry}</code>",
        f"🔄 <b>Recurrencia:</b> {recurrence}",
    ]
    
    if cost:
        lines.append(f"💰 <b>Costo:</b> {cost}")
    if notes:
        lines.append(f"📝 <b>Notas:</b> {notes}")
        
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Generado automáticamente por Gemini MCP Bot</i>")
    
    message_text = "\n".join(lines)
    return await send_telegram_message(message_text)
