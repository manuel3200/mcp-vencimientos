from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union

from db.connection import get_connection
from core.utils import parse_money

def register_customer_payment(
    email_or_id: str,
    amount: Optional[float] = None,
    payment_method: str = "Transferencia",
    new_expiry_date: Optional[str] = None,
    extend_expiry: Optional[bool] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Registra el cobro de una cuenta (compra inicial o renovación mensual) y actualiza el balance."""
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

            # Determinar si es confirmación de compra inicial o renovación
            curr_exp_str = acc.get("expiry_date") or ""
            days_left = None
            curr_exp_date = None
            if curr_exp_str:
                try:
                    curr_exp_date = datetime.strptime(curr_exp_str, "%Y-%m-%d").date()
                    days_left = (curr_exp_date - date.today()).days
                except Exception:
                    pass

            # Si extend_expiry es False o si la cuenta vence en más de 15 días (recién creada a 30d):
            # Es PAGO INICIAL -> Mantiene la fecha de vencimiento ya otorgada al cliente.
            is_initial = False
            if extend_expiry is False:
                is_initial = True
            elif extend_expiry is None and days_left is not None and days_left > 15:
                is_initial = True

            if new_expiry_date:
                final_expiry = new_expiry_date.strip()
            elif is_initial and curr_exp_str:
                final_expiry = curr_exp_str
            else:
                try:
                    base_date = max(curr_exp_date, date.today()) if curr_exp_date else date.today()
                    final_expiry = (base_date + timedelta(days=30)).isoformat()
                except Exception:
                    final_expiry = (date.today() + timedelta(days=30)).isoformat()

            action_label = "Pago inicial de compra" if is_initial else "Renovación mensual"
            final_notes = notes.strip() or action_label

            # Insertar en tabla de pagos
            conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (acc_id, client_id, final_amount, cost_num, profit_num, payment_method.strip(), final_notes))

            # Actualizar cuenta como pagada
            conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, payment_status = 'pagado', status = 'ocupada',
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_expiry, acc_id))

            return {
                "success": True,
                "client_name": acc.get("client_name"),
                "whatsapp": acc.get("whatsapp"),
                "client_type": acc.get("client_type"),
                "platform": acc["platform"],
                "email": acc["email"],
                "amount": final_amount,
                "profit": profit_num,
                "new_expiry": final_expiry,
                "is_initial": is_initial,
                "action_label": action_label,
                "payment_method": payment_method
            }
    finally:
        conn.close()

def collect_payment(
    account_id: Union[int, str],
    amount: Optional[float] = None,
    payment_method: str = "Transferencia",
    extend_days: Optional[int] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Cobra y asienta el pago de una cuenta de streaming, actualizando vencimiento y finanzas."""
    extend_exp = False if (extend_days == 0) else (True if extend_days else None)
    return register_customer_payment(
        email_or_id=str(account_id),
        amount=amount,
        payment_method=payment_method,
        extend_expiry=extend_exp,
        notes=notes
    )

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
