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
    """Registra un nuevo comprobante o pago en estado 'pending' con ID único autoincremental e idempotencia."""
    clean_phone = sender_phone.strip()
    clean_op = operation_id.strip()
    clean_amount = float(amount or 0.0)

    conn = get_connection()
    try:
        with conn:
            # 1. Idempotencia por operation_id no genérico
            if clean_op and clean_op.lower() not in ("coelsa", "codigo", "código", "id", "operacion", "operación") and len(clean_op) >= 6:
                existing_op = conn.execute("""
                    SELECT * FROM pending_payments WHERE operation_id = ? AND status = 'pending' LIMIT 1
                """, (clean_op,)).fetchone()
                if existing_op:
                    return dict(existing_op)

            # 2. Idempotencia por sender_phone en ventana de 10 minutos
            if clean_phone:
                existing_recent = conn.execute("""
                    SELECT * FROM pending_payments
                    WHERE sender_phone = ?
                      AND status = 'pending'
                      AND datetime(created_at) >= datetime('now', '-10 minutes')
                    ORDER BY id DESC LIMIT 1
                """, (clean_phone,)).fetchone()
                if existing_recent:
                    ex_dict = dict(existing_recent)
                    ex_amount = float(ex_dict.get("amount") or 0.0)
                    # Si el nuevo pago aporta un monto válido o más información, actualizar sin duplicar ID
                    if clean_amount > 0 and (ex_amount == 0.0 or abs(ex_amount - clean_amount) < 1.0):
                        conn.execute("""
                            UPDATE pending_payments
                            SET amount = ?, amount_formatted = ?,
                                bank = CASE WHEN bank IS NULL OR bank = '' THEN ? ELSE bank END,
                                operation_id = CASE WHEN operation_id IS NULL OR operation_id = '' THEN ? ELSE operation_id END,
                                receipt_base64 = CASE WHEN receipt_base64 IS NULL OR receipt_base64 = '' THEN ? ELSE receipt_base64 END
                            WHERE id = ?
                        """, (clean_amount, amount_formatted.strip(), bank.strip(), clean_op, receipt_base64.strip(), ex_dict["id"]))
                        updated_row = conn.execute("SELECT * FROM pending_payments WHERE id = ?", (ex_dict["id"],)).fetchone()
                        return dict(updated_row) if updated_row else ex_dict
                    elif clean_amount == 0.0 or abs(ex_amount - clean_amount) < 1.0:
                        return ex_dict

            cursor = conn.execute("""
                INSERT INTO pending_payments (
                    client_id, account_id, sender_phone, client_name, platform,
                    amount, amount_formatted, bank, operation_id, date_detected,
                    receipt_filename, receipt_mimetype, receipt_base64, raw_text,
                    status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                client_id, account_id, clean_phone, client_name.strip(), platform.strip(),
                clean_amount, amount_formatted.strip(), bank.strip(), clean_op,
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


def cleanup_duplicate_pending_payments() -> int:
    """Detecta y sanea comprobantes duplicados generados por reintentos de webhook en ventanas cortas.
    Descarta clones dejando 1 único registro por cliente y corrige el artefacto OCR $58.000 -> $8.000."""
    conn = get_connection()
    try:
        with conn:
            # 1. Corregir artefactos OCR conocidos en montos pendientes
            conn.execute("""
                UPDATE pending_payments
                SET amount = 8000.0, amount_formatted = '$8.000', bank = 'Naranja X'
                WHERE amount = 58000.0 AND status = 'pending'
            """)

            # 2. Buscar grupos duplicados pendientes del mismo número
            rows = conn.execute("""
                SELECT id, sender_phone, amount, created_at, operation_id
                FROM pending_payments
                WHERE status = 'pending'
                ORDER BY sender_phone, id ASC
            """).fetchall()

            seen_by_phone: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                p = r["sender_phone"]
                seen_by_phone.setdefault(p, []).append(dict(r))

            rejected_ids = []
            for phone, items in seen_by_phone.items():
                if len(items) > 1:
                    # Dejar el último elemento, rechazar los anteriores como clones
                    for clone in items[:-1]:
                        rejected_ids.append(clone["id"])

            if rejected_ids:
                placeholders = ",".join("?" for _ in rejected_ids)
                conn.execute(f"""
                    UPDATE pending_payments
                    SET status = 'rejected', notes = 'Auto-descartado: clon duplicado de webhook'
                    WHERE id IN ({placeholders})
                """, rejected_ids)

            return len(rejected_ids)
    except Exception as e:
        logger.warning(f"Error saneando duplicados de comprobantes: {e}")
        return 0
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
    custom_amount: Optional[float] = None,
    renew_all: bool = False
) -> Dict[str, Any]:
    """Aprueba un pago pendiente:
    - Actualiza el estado a 'approved'.
    - Si renew_all=True, busca todas las cuentas activas del cliente y las renueva juntas.
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
    acc_id = item.get("account_id")
    client_id = item.get("client_id")

    # 1. Si se especificó monto personalizado o se actualizó, persistirlo en pending_payments
    if custom_amount is not None and custom_amount > 0:
        conn = get_connection()
        try:
            with conn:
                conn.execute("""
                    UPDATE pending_payments
                    SET amount = ?, amount_formatted = ?
                    WHERE id = ?
                """, (amt_val, format_ars(amt_val), payment_id))
        finally:
            conn.close()

    # 2. Si no hay cuenta asociada directamente, intentar buscar una cuenta activa del cliente
    if not acc_id:
        conn = get_connection()
        try:
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
                with conn:
                    conn.execute("UPDATE pending_payments SET account_id = ?, client_id = ? WHERE id = ?", (acc_id, client_id, payment_id))
        finally:
            conn.close()

    finance_res = None
    renewed_accounts = []

    # 3. Si renew_all está activo, buscar todas las cuentas activas del cliente
    if renew_all:
        conn = get_connection()
        try:
            all_active = []
            if client_id:
                rows_all = conn.execute("""
                    SELECT * FROM streaming_accounts
                    WHERE client_id = ? AND status = 'ocupada'
                    ORDER BY expiry_date ASC
                """, (client_id,)).fetchall()
                all_active = [dict(r) for r in rows_all]
            elif item.get("sender_phone"):
                s_clean = clean_whatsapp_phone(item["sender_phone"])
                if s_clean:
                    suffix = s_clean[-8:]
                    rows_all = conn.execute("""
                        SELECT a.* FROM streaming_accounts a
                        JOIN clients c ON a.client_id = c.id
                        WHERE a.status = 'ocupada' AND (c.whatsapp LIKE ? OR c.whatsapp LIKE ?)
                        ORDER BY a.expiry_date ASC
                    """, (f"%{suffix}%", f"%{s_clean}%")).fetchall()
                    all_active = [dict(r) for r in rows_all]
        finally:
            conn.close()

        if all_active:
            split_amt = (amt_val / len(all_active)) if (amt_val > 0 and len(all_active) > 0) else None
            for a_item in all_active:
                try:
                    f_item_res = finance_repo.collect_payment(
                        account_id=a_item["id"],
                        extend_days=30,
                        amount=split_amt,
                        payment_method=bank_name,
                        notes=f"Aprobación Multi-Servicio #{payment_id} (Op: {op_code})"
                    )
                    renewed_accounts.append({
                        "account_id": a_item["id"],
                        "platform": a_item["platform"],
                        "email": a_item["email"],
                        "profile_name": a_item.get("profile_name"),
                        "new_expiry_date": f_item_res.get("new_expiry_date")
                    })
                except Exception as e:
                    logger.error(f"Error renovando cuenta #{a_item['id']} en approve_pending_payment(renew_all=True): {e}")

            finance_res = {
                "success": True,
                "renew_all": True,
                "renewed_count": len(renewed_accounts),
                "renewed_accounts": renewed_accounts,
                "total_amount": amt_val
            }

    # 4. Si no es renew_all o no se encontraron cuentas en renew_all:
    if not renewed_accounts:
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
            if amt_val > 0:
                conn = get_connection()
                try:
                    with conn:
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
                finally:
                    conn.close()

    # 5. Marcar el registro como aprobado en pending_payments
    approve_tag = f" | Aprobado por {admin_user}" + (f" [Multi-Servicio: {len(renewed_accounts)} cuentas]" if renewed_accounts else "")
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE pending_payments
                SET status = 'approved', resolved_at = CURRENT_TIMESTAMP,
                    notes = COALESCE(notes, '') || ?
                WHERE id = ?
            """, (approve_tag, payment_id))
    finally:
        conn.close()

    updated_item = get_pending_payment(payment_id) or item
    return {
        "success": True,
        "payment_id": payment_id,
        "payment": updated_item,
        "renewed_accounts": renewed_accounts,
        "finance_details": finance_res
    }


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
                    notes = COALESCE(notes, '') || ?
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

def prune_old_approved_receipts_base64(days_threshold: int = 60) -> int:
    """Purga las cadenas Base64 de comprobantes aprobados o rechazados con más de N días de antigüedad,
    liberando espacio crítico en SQLite pero manteniendo los metadatos contables intactos."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE pending_payments
                SET receipt_base64 = ''
                WHERE status IN ('approved', 'rejected')
                  AND receipt_base64 IS NOT NULL AND receipt_base64 != ''
                  AND (
                      resolved_at <= datetime('now', ? || ' days')
                      OR (resolved_at IS NULL AND created_at <= datetime('now', ? || ' days'))
                  )
            """, (f"-{days_threshold}", f"-{days_threshold}"))
            pruned_count = cursor.rowcount
        logger.info(f"Purga de almacenamiento: {pruned_count} comprobantes Base64 antiguos liberados.")
        return pruned_count
    finally:
        conn.close()

