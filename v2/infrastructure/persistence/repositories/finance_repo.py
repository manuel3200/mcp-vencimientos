import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union

from db.connection import get_connection
from core.utils import parse_money

logger = logging.getLogger("infrastructure.persistence.finance_repo")

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
            if q.isdigit():
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                    LIMIT 1
                """, (int(q),)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) = lower(?)
                        LIMIT 1
                    """, (q,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) = lower(?)
                    LIMIT 1
                """, (q,)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) LIKE lower(?)
                        ORDER BY a.id DESC LIMIT 1
                    """, (f"%{q}%",)).fetchone()

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

            # Comprobar si la cuenta ya registra pagos previos en el libro financiero
            prior_payments = conn.execute("SELECT COUNT(*) as cnt FROM payments WHERE account_id = ?", (acc_id,)).fetchone()
            has_prior_payments = bool(prior_payments and prior_payments["cnt"] > 0)

            # Es PAGO INICIAL solo si se pide explícitamente (extend_expiry=False)
            # o si extend_expiry es None, la cuenta NO tiene pagos previos y vence en más de 15 días.
            is_initial = False
            if extend_expiry is False:
                is_initial = True
            elif extend_expiry is True:
                is_initial = False
            elif extend_expiry is None:
                if not has_prior_payments and days_left is not None and days_left > 15:
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
                SET previous_expiry_date = expiry_date, expiry_date = ?, payment_status = 'pagado', status = 'ocupada',
                    debt_balance = 0.0, last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_expiry, acc_id))

            return {
                "success": True,
                "account_id": acc_id,
                "client_id": client_id,
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

def register_partial_payment(
    email_or_id: str,
    amount: float,
    payment_method: str = "Transferencia",
    notes: str = ""
) -> Dict[str, Any]:
    """Registra un pago parcial / seña, calculando el saldo restante adeudado por el cliente."""
    conn = get_connection()
    q = str(email_or_id).strip()
    paid_amt = float(amount)
    try:
        with conn:
            if q.isdigit():
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                    LIMIT 1
                """, (int(q),)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) = lower(?)
                        LIMIT 1
                    """, (q,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) = lower(?)
                    LIMIT 1
                """, (q,)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) LIKE lower(?)
                        ORDER BY a.id DESC LIMIT 1
                    """, (f"%{q}%",)).fetchone()

            if not row:
                return {"success": False, "error": f"No se encontró la cuenta '{email_or_id}'"}

            acc = dict(row)
            acc_id = acc["id"]
            client_id = acc.get("client_id")
            price_total = parse_money(acc.get("price")) or paid_amt
            cost_num = parse_money(acc.get("cost"))
            profit_num = max(0.0, paid_amt - cost_num)

            # Saldo pendiente
            current_debt = acc.get("debt_balance") or 0.0
            total_due = current_debt if current_debt > 0 else price_total
            new_remaining_debt = max(0.0, total_due - paid_amt)

            action_label = f"Pago parcial (Saldo rest: {format_ars(new_remaining_debt)})"
            final_notes = notes.strip() or action_label

            # Registrar en payments
            cur = conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes, is_partial, remaining_balance)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (acc_id, client_id, paid_amt, cost_num, profit_num, payment_method.strip(), final_notes, new_remaining_debt))
            tx_id = cur.lastrowid

            # Actualizar cuenta con el saldo pendiente
            new_payment_st = "pagado" if new_remaining_debt == 0 else "parcial"
            if new_remaining_debt == 0:
                conn.execute("""
                    UPDATE streaming_accounts
                    SET debt_balance = 0.0, payment_status = 'pagado', status = 'ocupada', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (acc_id,))
            else:
                conn.execute("""
                    UPDATE streaming_accounts
                    SET debt_balance = ?, payment_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (new_remaining_debt, new_payment_st, acc_id))

            c_name = acc.get("client_name") or "Cliente"
            plat = acc.get("platform") or "Streaming"
            exp_date = acc.get("expiry_date") or "fin de ciclo"
            amt_fmt = format_ars(paid_amt)
            debt_fmt = format_ars(new_remaining_debt)

            wa_msg = (
                f"💵 *¡Hola {c_name}!* Hemos registrado tu pago parcial de *{amt_fmt}* para tu servicio de *{plat}*.\n\n"
                f"📊 *Estado del Pago:*\n"
                f"• Monto abonado: *{amt_fmt}*\n"
                f"• Saldo restante pendiente: *{debt_fmt}*\n"
                f"• Fecha límite de vencimiento: `{exp_date}`\n\n"
                f"Por favor recuerda cancelar el saldo restante antes de la fecha para mantener tu servicio sin interrupciones. ¡Muchas gracias! 🙌✨"
            )

            return {
                "success": True,
                "payment_id": tx_id,
                "account_id": acc_id,
                "client_name": c_name,
                "clean_phone": re.sub(r'\\D', '', str(acc.get("whatsapp") or "")),
                "platform": plat,
                "paid_amount": paid_amt,
                "remaining_debt": new_remaining_debt,
                "paid_formatted": amt_fmt,
                "debt_formatted": debt_fmt,
                "expiry_date": exp_date,
                "whatsapp_message": wa_msg
            }
    finally:
        conn.close()

def reverse_customer_payment(payment_id: int, reason: str = "Error de aprobación", admin_user: str = "Admin") -> Dict[str, Any]:
    """Revierte un pago aprobado por error: restaura el vencimiento anterior y anula la transacción contable."""
    conn = get_connection()
    try:
        # Migración defensiva en caliente para asegurar columnas necesarias
        for col_stmt in (
            "ALTER TABLE payments ADD COLUMN status TEXT DEFAULT 'completed'",
            "ALTER TABLE payments ADD COLUMN is_partial INTEGER DEFAULT 0",
            "ALTER TABLE payments ADD COLUMN remaining_balance REAL DEFAULT 0.0"
        ):
            try:
                conn.execute(col_stmt)
            except Exception:
                pass

        with conn:
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
            if not row:
                return {"success": False, "error": f"No se encontró la transacción de pago #{payment_id}"}

            p = dict(row)
            if p.get("status") == "reversed":
                return {"success": False, "error": f"El pago #{payment_id} ya fue revertido con anterioridad."}

            acc_id = p.get("account_id")
            restored_expiry = None

            # Si la transacción estaba asociada a una cuenta de streaming:
            if acc_id:
                # Comprobar si existen otros pagos activos/completados para esta cuenta
                other_active_row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM payments
                    WHERE account_id = ? AND id != ? AND (status IS NULL OR status != 'reversed')
                """, (acc_id, payment_id)).fetchone()
                other_active = int(other_active_row["cnt"]) if other_active_row else 0

                acc_row = conn.execute("SELECT * FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
                if acc_row:
                    acc = dict(acc_row)
                    prev_exp = acc.get("previous_expiry_date")

                    if other_active > 0:
                        # Caso duplicado: aún queda al menos un pago válido activo
                        # Si la duplicación extendió la fecha de vencimiento, restaurar el vencimiento previo
                        # pero CONSERVAR payment_status = 'pagado' ya que el cliente pagó su servicio
                        if prev_exp and prev_exp != acc.get("expiry_date"):
                            conn.execute("""
                                UPDATE streaming_accounts
                                SET expiry_date = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (prev_exp, acc_id))
                            restored_expiry = prev_exp
                    else:
                        # Caso normal (único pago): restaurar vencimiento y marcar como 'pendiente'
                        if prev_exp and prev_exp != acc.get("expiry_date"):
                            conn.execute("""
                                UPDATE streaming_accounts
                                SET expiry_date = ?, payment_status = 'pendiente', updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (prev_exp, acc_id))
                            restored_expiry = prev_exp
                        else:
                            conn.execute("""
                                UPDATE streaming_accounts
                                SET payment_status = 'pendiente', updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (acc_id,))

            # Anular el pago en payments de forma segura contra NULL
            rev_note = f"REVERTIDO por {admin_user}: {reason}"
            conn.execute("""
                UPDATE payments
                SET status = 'reversed',
                    notes = CASE WHEN notes IS NULL OR notes = '' THEN ? ELSE notes || ' | ' || ? END
                WHERE id = ?
            """, (rev_note, rev_note, payment_id))

            try:
                from core.audit import log_audit_event
                log_audit_event(
                    actor=admin_user,
                    action="REVERT_PAYMENT",
                    target_type="payment",
                    target_id=str(payment_id),
                    old_value=f"amount:{p.get('amount')},account_id:{acc_id}",
                    new_value=f"status:reversed,reason:{reason}",
                    ip_or_source="finance_repo"
                )
            except Exception as e:
                logger.warning(f"No se pudo registrar log de auditoría para reversión de pago #{payment_id}: {e}")

            return {
                "success": True,
                "payment_id": payment_id,
                "amount": p["amount"],
                "reversed_amount": p["amount"],
                "profit": p.get("profit", 0.0),
                "account_id": acc_id,
                "restored_expiry": restored_expiry,
                "reason": reason
            }
    except Exception as e:
        logger.error(f"Error revirtiendo pago #{payment_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
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
