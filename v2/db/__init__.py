"""
db compatibility package forwarding to infrastructure.persistence
"""
from infrastructure.persistence.connection import get_connection, get_db_cursor
from infrastructure.persistence.schema import init_db, DEFAULT_WHATSAPP_TEMPLATES

__all__ = ["get_connection", "get_db_cursor", "init_db", "DEFAULT_WHATSAPP_TEMPLATES"]
