from typing import Dict, Any, Optional
import database

async def execute_rotate_password(
    account_id: int,
    new_password: str,
    notify_clients: bool = True
) -> Dict[str, Any]:
    """Caso de Uso: Rotación segura de contraseña maestra con difusión a clientes activos."""
    return await database.rotate_master_password_and_broadcast(
        account_id=account_id,
        new_password=new_password,
        notify_clients=notify_clients
    )
