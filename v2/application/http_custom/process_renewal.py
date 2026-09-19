from typing import Dict, Any
from services.http_custom_service import process_http_custom_outgoing_message

async def execute_process_http_custom_renewal(phone: str, message_text: str, source: str = "WhatsApp") -> Dict[str, Any]:
    """Caso de Uso: Procesa renovación de servidor HTTP Custom por HWID / Nombre de usuario."""
    return await process_http_custom_outgoing_message(phone, message_text, source=source)
