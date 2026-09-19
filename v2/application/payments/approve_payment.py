import logging
from typing import Dict, Any, Optional
import database

logger = logging.getLogger("application.payments")

def execute_approve_payment(
    payment_id: int,
    admin_user: str = "Admin",
    renew_all: bool = False,
    notify_client: Optional[bool] = None
) -> Dict[str, Any]:
    """Caso de Uso: Aprobación de pago pendiente y renovación de servicios."""
    return database.approve_pending_payment(
        payment_id=payment_id,
        admin_user=admin_user,
        renew_all=renew_all,
        notify_client=notify_client
    )

def execute_reject_payment(
    payment_id: int,
    admin_user: str = "Admin",
    reason: str = "Comprobante no válido o no acreditado."
) -> Dict[str, Any]:
    """Caso de Uso: Rechazo de pago pendiente con motivo."""
    return database.reject_pending_payment(
        payment_id=payment_id,
        admin_user=admin_user,
        reason=reason
    )
