import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from db.connection import get_connection
import db.repositories.finance_repo as finance_repo

logger = logging.getLogger("database.payments_approval")


def create_pending_payment(
    sender_phone: str,
    client_name: str,
    client_id: Optional[int] = None,
    account_id: Optional[int] = None,
    platform: str = "",
    amount: float = 0.0,
    amount_formatted: str = "",
    bank: str = "",
    operation_id: str = "",
    date_detected: str = "",
    receipt_filename: str = "",
    receipt_mimetype: str = "",
    receipt_base64: str = "",
    raw_text: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    """Registra un nuevo comprobante o pago en estado 'pending' con ID único autoincremental."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO pending_payments (
                    client_id, account_id, sender_phone, client_name, platform,
                    amount, amount_formatted, bank, operation_id, date_detected,
                    receipt_filename, receipt_mimetype, receipt_base64, raw_text,
                    status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                client_id, account_id, sender_phone.strip(), client_name.strip(), platform.strip(),
                float(amount or 0.0), amount_formatted.strip(), bank.strip(), operation_id.strip(),
                date_detected.strip(), receipt_filename.strip(), receipt_mimetype.strip(),
                receipt_base64.strip(), raw_text.strip(), notes.strip()
            ))
            payment_id = cursor.lastrowid

            row = conn.execute("""
                SELECT * FROM pending_payments WHERE id = ?
            """, (payment_id,)).fetchone()
            return dict(row) if row else {"id": payment_id, "status": "pending"}
    finally:
        conn.close()


def get_pending_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Obtiene un registro de pago pendiente por su ID."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT p.*, c.client_code, c.whatsapp as client_whatsapp, c.client_type,
                   a.email as account_email, a.expiry_date as account_expiry,
                   a.price as account_price, a.profile_name, a.payment_status as account_payment_status
            FROM pending_payments p
            LEFT JOIN clients c ON p.client_id = c.id
            LEFT JOIN streaming_accounts a ON p.account_id = a.id
            WHERE p.id = ?
        """, (payment_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_pending_payments(status: str = "pending", limit: int = 50) -> List[Dict[str, Any]]:
    """Lista pagos según su estado ('pending', 'approved', 'rejected' o 'all')."""
    conn = get_connection()
    try:
        if status == "all":
            rows = conn.execute("""
                SELECT p.*, c.client_code, c.whatsapp as client_whatsapp, c.client_type,
                       a.email as account_email, a.expiry_date as account_expiry,
                       a.price as account_price, a.profile_name
                FROM pending_payments p
                LEFT JOIN clients c ON p.client_id = c.id
                LEFT JOIN streaming_accounts a ON p.account_id = a.id
                ORDER BY p.id DESC LIMIT ?
            """, (limit,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT p.*, c.client_code, c.whatsapp as client_whatsapp, c.client_type,
                       a.email as account_email, a.expiry_date as account_expiry,
                       a.price as account_price, a.profile_name
                FROM pending_payments p
                LEFT JOIN clients c ON p.client_id = c.id
                LEFT JOIN streaming_accounts a ON p.account_id = a.id
                WHERE p.status = ?
                ORDER BY p.id DESC LIMIT ?
            """, (status, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_pending_payments() -> int:
    """Devuelve la cantidad de pagos actualmente en estado 'pending'."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) as count FROM pending_payments WHERE status = 'pending'
        """).fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def approve_pending_payment(
    payment_id: int,
    admin_user: str = "admin",
    custom_amount: Optional[float] = None
) -> Dict[str, Any]:
    """Aprueba un pago pendiente:
    - Actualiza el estado a 'approved'.
    - Si tiene una cuenta vinculada o encontrada para el cliente, registra el cobro en finanzas y extiende el servicio.
    - Si no tiene cuenta asociada (compra inicial sin asignar aún), registra el ingreso directamente en el libro de pagos (Finanzas & Cobros).
    """
    item = get_pending_payment(payment_id)
    if not item:
        return {"success": False, "error": f"Pago #{payment_id} no encontrado."}

    if item["status"] != "pending":
        return {
            "success": False,
            "error": f"El pago #{payment_id} ya fue procesado anteriormente (Estado: {item['status']}).",
            "payment": item
        }

    from core.utils import clean_whatsapp_phone, format_ars

    amt_val = custom_amount if (custom_amount is not None and custom_amount > 0) else (float(item.get("amount") or 0.0))
    bank_name = item.get("bank") or "Mercado Pago"
    op_code = item.get("operation_id") or "-"

    conn = get_connection()
    try:
        with conn:
            # Si se especificó monto personalizado o se actualizó, persistirlo
            if custom_amount is not None and custom_amount > 0:
                conn.execute("""
                    UPDATE pending_payments
                    SET amount = ?, amount_formatted = ?
                    WHERE id = ?
                """, (amt_val, format_ars(amt_val), payment_id))

            acc_id = item.get("account_id")
            client_id = item.get("client_id")
            finance_res = None

            # 1. Si no hay cuenta asociada directamente, intentar buscar una cuenta activa del cliente
            if not acc_id:
                found_acc = None
                if client_id:
                    row_acc = conn.execute("""
                        SELECT * FROM streaming_accounts
                        WHERE client_id = ? AND status = 'ocupada'
                        ORDER BY expiry_date ASC LIMIT 1
                    """, (client_id,)).fetchone()
                    if row_acc:
                        found_acc = dict(row_acc)
                elif item.get("sender_phone"):
                    s_clean = clean_whatsapp_phone(item["sender_phone"])
                    if s_clean:
                        suffix = s_clean[-8:]
                        row_acc = conn.execute("""
                            SELECT a.* FROM streaming_accounts a
                            JOIN clients c ON a.client_id = c.id
                            WHERE a.status = 'ocupada' AND (c.whatsapp LIKE ? OR c.whatsapp LIKE ?)
                            ORDER BY a.expiry_date ASC LIMIT 1
                        """, (f"%{suffix}%", f"%{s_clean}%")).fetchone()
                        if row_acc:
                            found_acc = dict(row_acc)

                if found_acc:
                    acc_id = found_acc["id"]
                    if not client_id and found_acc.get("client_id"):
                        client_id = found_acc["client_id"]
                    conn.execute("UPDATE pending_payments SET account_id = ?, client_id = ? WHERE id = ?", (acc_id, client_id, payment_id))

            # 2. Si tenemos cuenta (vinculada u obtenida), impactar renovación en finanzas
            if acc_id:
                try:
                    curr_exp = item.get("account_expiry")
                    is_initial = False
                    if curr_exp:
                        try:
                            from datetime import date
                            exp_d = datetime.strptime(curr_exp, "%Y-%m-%d").date()
                            days_left = (exp_d - date.today()).days
                            if days_left > 15:
                                is_initial = True
                        except Exception:
                            pass

                    finance_res = finance_repo.collect_payment(
                        account_id=acc_id,
                        extend_days=0 if is_initial else 30,
                        amount=amt_val if amt_val > 0 else None,
                        payment_method=bank_name,
                        notes=f"Aprobado desde Pago #{payment_id} (Op: {op_code})"
                    )
                except Exception as e:
                    logger.error(f"Error al impactar cobro financiero para cuenta #{acc_id}: {e}")
            else:
                # 3. Si NO hay cuenta activa (compra nueva que aún no tiene cuenta asignada), ASENTAR INGRESO EN FINANZAS DIRECTAMENTE
                if amt_val > 0:
                    try:
                        conn.execute("""
                            INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                            VALUES (NULL, ?, ?, 0.0, ?, ?, ?)
                        """, (client_id, amt_val, amt_val, bank_name, f"Cobro aprobado #{payment_id} (Cliente: {item.get('client_name')}, Op: {op_code})"))
                        finance_res = {
                            "success": True,
                            "amount": amt_val,
                            "profit": amt_val,
                            "payment_method": bank_name,
                            "action_label": "Cobro sin cuenta vinculada"
                        }
                    except Exception as e:
                        logger.error(f"Error al asentar cobro en payments: {e}")

            # Marcar el registro como aprobado
            conn.execute("""
                UPDATE pending_payments
                SET status = 'approved', resolved_at = CURRENT_TIMESTAMP,
                    notes = notes || ' | Aprobado por ' || ?
                WHERE id = ?
            """, (admin_user, payment_id))

        updated_item = get_pending_payment(payment_id)
        return {
            "success": True,
            "payment_id": payment_id,
            "payment": updated_item,
            "finance_details": finance_res
        }
    finally:
        conn.close()


def reject_pending_payment(payment_id: int, reason: str = "", admin_user: str = "admin") -> Dict[str, Any]:
    """Rechaza un pago pendiente marcando su estado como 'rejected'."""
    item = get_pending_payment(payment_id)
    if not item:
        return {"success": False, "error": f"Pago #{payment_id} no encontrado."}

    if item["status"] != "pending":
        return {
            "success": False,
            "error": f"El pago #{payment_id} ya fue procesado anteriormente (Estado: {item['status']}).",
            "payment": item
        }

    conn = get_connection()
    try:
        with conn:
            rejection_note = f" | Rechazado por {admin_user}: {reason.strip()}" if reason else f" | Rechazado por {admin_user}"
            conn.execute("""
                UPDATE pending_payments
                SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP,
                    notes = notes || ?
                WHERE id = ?
            """, (rejection_note, payment_id))

        updated_item = get_pending_payment(payment_id)
        return {
            "success": True,
            "payment_id": payment_id,
            "payment": updated_item,
            "reason": reason
        }
    finally:
        conn.close()
