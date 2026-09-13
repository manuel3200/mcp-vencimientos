from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union

from db.connection import get_connection
from core.utils import parse_money

def register_customer_payment(
    email_or_id: str,
    amount: Optional[float] = None,
    payment_method: str = "Transferencia",
    new_expiry_date: Optional[str] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Registra el cobro de una mensualidad o renovación a un cliente y actualiza el balance."""
    conn = get_connection()
    q = email_or_id.strip()
    try:
        with conn:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) OR a.id = ?
                LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()

            if not row:
                return {"success": False, "error": f"No se encontró la cuenta '{email_or_id}'"}

            acc = dict(row)
            acc_id = acc["id"]
            client_id = acc.get("client_id")
            
            # Monto cobrado
            final_amount = amount if amount is not None else parse_money(acc.get("price"))
            cost_num = parse_money(acc.get("cost"))
            profit_num = final_amount - cost_num

            # Nueva fecha si se renueva
            if new_expiry_date:
                final_expiry = new_expiry_date.strip()
            else:
                try:
                    curr_exp = datetime.strptime(acc["expiry_date"], "%Y-%m-%d").date()
                    # Si ya estaba vencida, renueva 30 días desde hoy; si no, 30 días desde el vencimiento
                    base_date = max(curr_exp, date.today())
                    final_expiry = (base_date + timedelta(days=30)).isoformat()
                except Exception:
                    final_expiry = (date.today() + timedelta(days=30)).isoformat()

            # Insertar en tabla de pagos
            conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (acc_id, client_id, final_amount, cost_num, profit_num, payment_method.strip(), notes.strip() or "Renovación pagada"))

            # Actualizar cuenta como pagada con nuevo vencimiento
            conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, payment_status = 'pagado', status = 'ocupada',
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_expiry, acc_id))

            return {
                "success": True,
                "client_name": acc.get("client_name"),
                "platform": acc["platform"],
                "email": acc["email"],
                "amount": final_amount,
                "profit": profit_num,
                "new_expiry": final_expiry,
                "payment_method": payment_method
            }
    finally:
        conn.close()

def get_recent_transactions(limit: int = 15) -> List[Dict[str, Any]]:
    """Obtiene el historial reciente de transacciones y pagos."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT p.*, a.platform, a.email, c.name as client_name, c.client_type
            FROM payments p
            LEFT JOIN streaming_accounts a ON p.account_id = a.id
            LEFT JOIN clients c ON p.client_id = c.id
            ORDER BY p.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
