import logging
from typing import List, Dict, Any, Optional
import database

logger = logging.getLogger("application.payments.bulk")

def execute_bulk_approve_payments(
    payment_ids: List[int],
    admin_user: str = "Admin",
    notify_client: Optional[bool] = None
) -> Dict[str, Any]:
    """Caso de Uso: Aprobación masiva de múltiples comprobantes pendientes."""
    results = []
    approved_count = 0
    failed_count = 0

    for pid in payment_ids:
        try:
            res = database.approve_pending_payment(
                payment_id=pid,
                admin_user=admin_user,
                renew_all=False,
                notify_client=notify_client
            )
            if res.get("success"):
                approved_count += 1
            else:
                failed_count += 1
            results.append({"payment_id": pid, "result": res})
        except Exception as e:
            failed_count += 1
            logger.error(f"Error aprobando pago masivo {pid}: {e}")
            results.append({"payment_id": pid, "error": str(e), "success": False})

    return {
        "success": failed_count == 0,
        "total": len(payment_ids),
        "approved": approved_count,
        "failed": failed_count,
        "details": results
    }
