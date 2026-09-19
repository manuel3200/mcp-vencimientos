import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional

import database
from db.connection import get_connection

logger = logging.getLogger("application.http_custom.renew")

def execute_renew_http_custom(account_id: int, extend_days: int = 30, admin_user: str = "admin") -> Dict[str, Any]:
    """Renueva un servidor HTTP Custom por N días (por defecto 30), impactando el pago en finanzas."""
    conn = get_connection()
    try:
        acc = conn.execute("""
            SELECT a.*, c.name as client_name, c.client_type, c.whatsapp as client_phone
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.id = ? AND a.platform = 'HTTP Custom'
        """, (account_id,)).fetchone()

        if not acc:
            return {"success": False, "error": f"Servidor HTTP Custom #{account_id} no encontrado."}

        acc_dict = dict(acc)
        curr_exp = acc_dict.get("expiry_date")
        today = date.today()

        # Calcular nueva fecha de vencimiento
        if curr_exp:
            try:
                base_d = datetime.strptime(curr_exp, "%Y-%m-%d").date()
                new_date = (base_d + timedelta(days=extend_days)) if base_d >= today else (today + timedelta(days=extend_days))
            except Exception:
                new_date = today + timedelta(days=extend_days)
        else:
            new_date = today + timedelta(days=extend_days)

        new_expiry_str = new_date.isoformat()
        client_type = (acc_dict.get("client_type") or "consumidor_final").lower()
        price, cost = database.get_suggested_price("HTTP Custom", "hwid", client_type)
        if price <= 0.0:
            if "vip" in client_type:
                price = 3500.0
            elif "revend" in client_type:
                price = 4500.0
            else:
                price = 8000.0
            cost = 0.0

        profit = price - cost
        client_id = acc_dict.get("client_id")
        username = acc_dict.get("email") or "Usuario"

        with conn:
            conn.execute("""
                UPDATE streaming_accounts
                SET previous_expiry_date = expiry_date, expiry_date = ?,
                    status = 'ocupada', payment_status = 'pagado', debt_balance = 0.0,
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_expiry_str, account_id))

            conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, 'Dashboard Manual', ?)
            """, (account_id, client_id, price, cost, profit, f"Renovación rápida Dashboard ({admin_user}) - Usuario: {username}"))

        logger.info(f"Servidor HTTP Custom #{account_id} renovado hasta {new_expiry_str} por {admin_user}")
        return {
            "success": True,
            "account_id": account_id,
            "username": username,
            "new_expiry_date": new_expiry_str,
            "price": price,
            "profit": profit
        }
    finally:
        conn.close()
