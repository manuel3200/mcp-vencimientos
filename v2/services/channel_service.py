"""
services/channel_service.py - Fachada de servicio para difusión y emisión en canales.
Re-exporta la lógica desde application.channels.broadcast_service.
"""
from application.channels.broadcast_service import (
    broadcast_announcement,
    format_whatsapp_broadcast,
    format_telegram_broadcast,
    CATEGORY_CONFIG
)

__all__ = [
    "broadcast_announcement",
    "format_whatsapp_broadcast",
    "format_telegram_broadcast",
    "CATEGORY_CONFIG"
]
