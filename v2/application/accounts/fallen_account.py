from typing import Dict, Any, Optional
import database

def execute_report_fallen_account(
    account_id: int,
    issue_type: str = "caida",
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """Caso de Uso: Reporte de caída y reemplazo automático de cuenta si hay stock."""
    return database.report_and_auto_replace_account(
        account_id=account_id,
        issue_type=issue_type,
        notes=notes
    )

def execute_reactivate_account(account_id: int) -> Dict[str, Any]:
    """Caso de Uso: Reactivación manual de una cuenta reportada como caída."""
    return database.reactivate_fallen_account(account_id=account_id)
