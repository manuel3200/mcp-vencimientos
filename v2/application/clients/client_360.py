from typing import Dict, Any, Optional
import database

def get_client_360(client_id: int) -> Optional[Dict[str, Any]]:
    """Caso de Uso: Obtiene la ficha 360 del cliente, cuentas activas, deudas y balance."""
    return database.get_client_360_profile(client_id)
