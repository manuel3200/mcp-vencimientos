from typing import Dict, Any, Optional
import database

def execute_renew_account(
    account_id: int,
    days: int = 30,
    new_expiry_date: Optional[str] = None
) -> Dict[str, Any]:
    """Caso de Uso: Renovar una cuenta activa."""
    return database.renew_account(account_id=account_id, days=days, new_expiry_date=new_expiry_date)
