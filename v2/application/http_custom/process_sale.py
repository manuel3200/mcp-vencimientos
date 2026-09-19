from typing import Dict, Any
from domain.rules.http_custom_rules import parse_http_custom_message, parse_date_to_iso, is_valid_hwid
from domain.rules.pricing_rules import resolve_http_custom_price
from services.http_custom_service import process_http_custom_outgoing_message

async def execute_process_http_custom_sale(phone: str, message_text: str, source: str = "WhatsApp") -> Dict[str, Any]:
    """Caso de Uso: Procesa alta o venta de servidor HTTP Custom por HWID."""
    return await process_http_custom_outgoing_message(phone, message_text, source=source)
