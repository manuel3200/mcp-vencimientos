from typing import Dict, Any, Optional
import database

def execute_sell_account(
    account_id: int,
    client_name: str,
    client_whatsapp: str,
    price: Optional[float] = None,
    client_type: str = "consumidor_final",
    profile_name: Optional[str] = None,
    expiry_date: Optional[str] = None
) -> Dict[str, Any]:
    """Caso de Uso: Vender o asignar una cuenta libre a un cliente."""
    return database.assign_or_sell_account(
        account_id=account_id,
        client_name=client_name,
        client_whatsapp=client_whatsapp,
        price=price,
        client_type=client_type,
        profile_name=profile_name,
        expiry_date=expiry_date
    )
