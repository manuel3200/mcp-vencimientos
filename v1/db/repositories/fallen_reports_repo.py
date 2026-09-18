import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from db.connection import get_connection
from core.utils import clean_whatsapp_phone
from db.repositories.accounts_repo import report_and_auto_replace_account

logger = logging.getLogger("database.fallen_reports")


def create_fallen_report(
    sender_phone: str,
    client_name: str,
    client_id: Optional[int] = None,
    account_id: Optional[int] = None,
    platform: str = "",
    account_email: str = "",
    profile_name: str = "",
    issue_type: str = "caida",
    raw_message: str = ""
) -> Dict[str, Any]:
    """Registra un nuevo reporte de cuenta caída o problema de credenciales en estado 'pending'."""
    conn = get_connection()
    clean_p = clean_whatsapp_phone(sender_phone)
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO fallen_reports (
                    client_id, account_id, sender_phone, client_name,
                    platform, account_email, profile_name, issue_type,
                    raw_message, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                client_id, account_id, clean_p, client_name.strip(),
                platform.strip(), account_email.strip(), profile_name.strip(),
                issue_type.strip(), raw_message.strip()
            ))
            report_id = cur.lastrowid
            row = conn.execute("SELECT * FROM fallen_reports WHERE id = ?", (report_id,)).fetchone()
            logger.info(f"Reporte de cuenta caída creado #C{report_id} para {client_name} ({platform})")
            return dict(row)
    finally:
        conn.close()


def get_fallen_report(report_id: int) -> Optional[Dict[str, Any]]:
    """Obtiene el detalle completo de un reporte por ID."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT r.*, 
                   c.whatsapp as client_whatsapp, c.telegram as client_telegram, c.client_type,
                   a.password as account_password, a.expiry_date as account_expiry, a.price as account_price
            FROM fallen_reports r
            LEFT JOIN clients c ON r.client_id = c.id
            LEFT JOIN streaming_accounts a ON r.account_id = a.id
            WHERE r.id = ?
        """, (report_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_fallen_reports(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Lista los reportes de cuentas caídas filtrados opcionalmente por estado."""
    conn = get_connection()
    try:
        if status:
            rows = conn.execute("""
                SELECT r.*, c.whatsapp as client_whatsapp, c.telegram as client_telegram
                FROM fallen_reports r
                LEFT JOIN clients c ON r.client_id = c.id
                WHERE r.status = ?
                ORDER BY r.id DESC
                LIMIT ?
            """, (status, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT r.*, c.whatsapp as client_whatsapp, c.telegram as client_telegram
                FROM fallen_reports r
                LEFT JOIN clients c ON r.client_id = c.id
                ORDER BY CASE 
                    WHEN r.status = 'pending' THEN 0 
                    WHEN r.status = 'waiting' THEN 1 
                    ELSE 2 
                END, r.id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_pending_fallen_reports() -> int:
    """Retorna la cantidad de reportes pendientes o en espera de acción."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as count FROM fallen_reports WHERE status IN ('pending', 'waiting')").fetchone()
        return row["count"] if row else 0
    finally:
        conn.close()


def authorize_fallen_report(report_id: int, admin_user: str = "Admin") -> Dict[str, Any]:
    """
    Autoriza el reporte y ejecuta el reemplazo inmediato de la cuenta caída.
    - Asigna una cuenta libre en inventario de la misma plataforma.
    - Conserva el vencimiento y tarifa del cliente.
    - Marca el reporte como 'resolved'.
    """
    item = get_fallen_report(report_id)
    if not item:
        return {"success": False, "error": f"No se encontró el reporte #C{report_id}"}

    if item["status"] == "resolved":
        return {
            "success": True,
            "already_resolved": True,
            "message": f"El reporte #C{report_id} ya fue resuelto previamente.",
            "report": item
        }

    # Identificador para la búsqueda de reemplazo
    target_id = str(item.get("account_id") or item.get("account_email") or item.get("sender_phone"))
    platform = item.get("platform")

    repl_res = report_and_auto_replace_account(
        identifier=target_id,
        reason=f"Autorizado por {admin_user} (Reporte #C{report_id})",
        platform_filter=platform
    )

    conn = get_connection()
    try:
        with conn:
            if repl_res.get("replaced"):
                new_acc = repl_res["new_account"]
                new_acc_id = new_acc.get("id")
                admin_note = f"Autorizado por {admin_user}. Reemplazo #{new_acc_id} ({new_acc.get('email')})"
                conn.execute("""
                    UPDATE fallen_reports
                    SET status = 'resolved', admin_notes = ?, resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (admin_note, report_id))

                item["status"] = "resolved"
                item["admin_notes"] = admin_note
                return {
                    "success": True,
                    "replaced": True,
                    "report_id": report_id,
                    "report": item,
                    "client_name": repl_res.get("client_name") or item["client_name"],
                    "clean_phone": repl_res.get("clean_phone") or item["sender_phone"],
                    "platform": repl_res.get("platform") or platform,
                    "old_account": repl_res.get("old_account"),
                    "new_account": new_acc,
                    "whatsapp_message": repl_res.get("whatsapp_message", ""),
                    "wa_link": repl_res.get("wa_link", "")
                }
            elif repl_res.get("out_of_stock"):
                admin_note = f"Intento de reemplazo por {admin_user}: Sin stock libre disponible para {platform}"
                conn.execute("""
                    UPDATE fallen_reports
                    SET admin_notes = ?
                    WHERE id = ?
                """, (admin_note, report_id))
                return {
                    "success": True,
                    "replaced": False,
                    "out_of_stock": True,
                    "report_id": report_id,
                    "report": item,
                    "error": f"No hay cuentas libres disponibles en inventario para la plataforma '{platform}'"
                }
            else:
                return {
                    "success": False,
                    "error": repl_res.get("error", "Error desconocido al intentar reemplazar la cuenta.")
                }
    finally:
        conn.close()


def put_fallen_report_on_wait(report_id: int, admin_user: str = "Admin") -> Dict[str, Any]:
    """
    Pone el reporte en estado 'waiting' y genera el mensaje de espera para el cliente.
    Indica que el pedido está en cola y que se le entregará la cuenta en breve.
    """
    item = get_fallen_report(report_id)
    if not item:
        return {"success": False, "error": f"No se encontró el reporte #C{report_id}"}

    c_name = item.get("client_name") or "Cliente"
    plat = item.get("platform") or "Streaming"

    wait_msg = (
        f"🛠️ *¡Hola {c_name}!* Te informamos que tu solicitud sobre el inconveniente con tu servicio de *{plat}* "
        f"(Reporte #C{report_id}) se encuentra en nuestra *cola de atención prioritaria*.\n\n"
        f"Nuestro equipo técnico ya se encuentra gestionando tus nuevos datos de acceso y te los enviaremos por este mismo chat en cuanto estén listos.\n\n"
        f"¡Muchas gracias por tu comprensión y paciencia! 🙌"
    )

    admin_note = f"Puesto en espera por {admin_user} ({datetime.now().strftime('%d/%m %H:%M')})"

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE fallen_reports
                SET status = 'waiting', admin_notes = ?
                WHERE id = ?
            """, (admin_note, report_id))
            item["status"] = "waiting"
            item["admin_notes"] = admin_note
            return {
                "success": True,
                "status": "waiting",
                "report_id": report_id,
                "report": item,
                "client_name": c_name,
                "clean_phone": item["sender_phone"],
                "whatsapp_message": wait_msg
            }
    finally:
        conn.close()


def dismiss_fallen_report(report_id: int, reason: str = "Desestimado", admin_user: str = "Admin") -> Dict[str, Any]:
    """Descarta o cierra un reporte de caída."""
    conn = get_connection()
    try:
        with conn:
            admin_note = f"Desestimado por {admin_user}: {reason}"
            conn.execute("""
                UPDATE fallen_reports
                SET status = 'dismissed', admin_notes = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (admin_note, report_id))
            return {"success": True, "report_id": report_id, "status": "dismissed"}
    finally:
        conn.close()
