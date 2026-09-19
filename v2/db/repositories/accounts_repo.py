import re
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Union, Tuple

from db.connection import get_connection
from db.repositories.clients_repo import find_or_create_client
from core.utils import parse_money, format_ars

logger = logging.getLogger("database.accounts")

def add_free_account(
    platform: str, 
    email: str, 
    password: str, 
    profile_name: str = "", 
    profile_pin: str = "", 
    cost: str = "", 
    notes: str = ""
) -> Dict[str, Any]:
    conn = get_connection()
    clean_platform = platform.strip().title()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO streaming_accounts (platform, email, password, profile_name, profile_pin, status, cost, notes)
                VALUES (?, ?, ?, ?, ?, 'libre', ?, ?)
            """, (clean_platform, email.strip(), password.strip(), profile_name.strip(), profile_pin.strip(), cost.strip(), notes.strip()))
            acc_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def assign_or_sell_account(
    client_name: str,
    platform: str,
    email: str,
    password: str,
    expiry_date: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    profile_name: str = "",
    profile_pin: str = "",
    start_date: str = "",
    recurrence: str = "mensual",
    price: str = "",
    cost: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    from db.repositories.catalog_repo import get_suggested_price

    client = find_or_create_client(
        name=client_name, 
        whatsapp=whatsapp, 
        telegram=telegram, 
        client_type=client_type,
        notes=notes
    )
    clean_platform = platform.strip().title()
    s_date = start_date.strip() if start_date else date.today().isoformat()
    
    actual_client_type = client.get("client_type") or client_type
    
    price_num = parse_money(price)
    cost_num = parse_money(cost)
    stype = "pantalla" if profile_name else "cuenta_completa"
    if any(k in clean_platform.lower() for k in ["casa extra", "pantalla", "perfil", "miembro extra"]):
        stype = "pantalla"
    elif any(k in clean_platform.lower() for k in ["completa", "full hd", "4k", "cuenta entera", "4 pantallas"]):
        stype = "cuenta_completa"
    
    s_price, s_cost = get_suggested_price(clean_platform, stype, actual_client_type)
    if "revend" in actual_client_type.lower():
        norm_price, _ = get_suggested_price(clean_platform, stype, "consumidor_final")
        # Si no se pasó precio o el precio pasado coincide con el precio de consumidor final, aplicar tarifa revendedor
        if price_num == 0.0 or (norm_price > 0 and price_num == norm_price):
            if s_price > 0.0:
                price_num = s_price
                price = format_ars(price_num)
    else:
        if price_num == 0.0 and s_price > 0.0:
            price_num = s_price
            price = format_ars(price_num)

    if cost_num == 0.0 and s_cost > 0.0:
        cost_num = s_cost
        cost = format_ars(cost_num)

    conn = get_connection()
    try:
        with conn:
            existing_acc = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(email) = lower(?) AND status = 'libre'
                LIMIT 1
            """, (email.strip(),)).fetchone()

            if existing_acc:
                acc_id = existing_acc["id"]
                conn.execute("""
                    UPDATE streaming_accounts
                    SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                        platform = ?, password = ?, profile_name = ?, profile_pin = ?,
                        start_date = ?, expiry_date = ?, recurrence = ?, price = ?,
                        cost = CASE WHEN length(?) > 0 THEN ? ELSE cost END,
                        notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (client["id"], clean_platform, password.strip(), profile_name.strip(), 
                      profile_pin.strip(), s_date, expiry_date.strip(), recurrence.strip(), 
                      price.strip(), cost.strip(), cost.strip(), notes.strip(), acc_id))
            else:
                cursor = conn.execute("""
                    INSERT INTO streaming_accounts (
                        platform, email, password, profile_name, profile_pin,
                        client_id, status, payment_status, start_date, expiry_date, recurrence,
                        price, cost, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ocupada', 'pagado', ?, ?, ?, ?, ?, ?)
                """, (clean_platform, email.strip(), password.strip(), profile_name.strip(),
                      profile_pin.strip(), client["id"], s_date, expiry_date.strip(),
                      recurrence.strip(), price.strip(), cost.strip(), notes.strip()))
                acc_id = cursor.lastrowid

            # Registrar la venta en la tabla de pagos / balance
            profit_num = price_num - cost_num
            if price_num > 0:
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'Inicial', 'Venta registrada')
                """, (acc_id, client["id"], price_num, cost_num, profit_num))

            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def mark_account_fallen(email_or_query: str, reason: str = "Suscripción caída") -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            if q.isdigit():
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                    LIMIT 1
                """, (int(q),)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) = lower(?)
                        LIMIT 1
                    """, (q,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) = lower(?)
                    LIMIT 1
                """, (q,)).fetchone()
                if not row:
                    row = conn.execute("""
                        SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                        FROM streaming_accounts a
                        LEFT JOIN clients c ON a.client_id = c.id
                        WHERE lower(a.email) LIKE lower(?)
                        ORDER BY a.id DESC LIMIT 1
                    """, (f"%{q}%",)).fetchone()
            
            if not row:
                return None
            
            acc_id = row["id"]
            existing_notes = row["notes"] or ""
            updated_notes = f"{existing_notes} | CAÍDA: {reason} ({date.today().isoformat()})".strip(" |")
            
            conn.execute("""
                UPDATE streaming_accounts 
                SET status = 'caida', notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (updated_notes, acc_id))
            
            updated = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()
            return dict(updated)
    finally:
        conn.close()

def reactivate_fallen_account(email_or_id_or_client: str) -> Optional[Dict[str, Any]]:
    """Reactiva una cuenta que fue marcada por error como caída, devolviéndola al estado 'ocupada' (o 'libre' si no tenía cliente)."""
    conn = get_connection()
    q = email_or_id_or_client.strip()
    try:
        with conn:
            # Buscar por ID numérico, email exacto o parcial, o por nombre del cliente
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE (a.id = ? OR lower(a.email) LIKE lower(?) OR lower(c.name) LIKE lower(?))
                  AND a.status = 'caida'
                ORDER BY a.id DESC LIMIT 1
            """, (int(q) if q.isdigit() else -1, f"%{q}%", f"%{q}%")).fetchone()

            if not row:
                # Si no encuentra caída, verificar si ya está ocupada
                return None

            acc_id = row["id"]
            client_id = row["client_id"]
            new_status = "ocupada" if client_id else "libre"
            
            # Limpiar nota de caída
            curr_notes = row["notes"] or ""
            # Remover marcas de CAÍDA
            cleaned_notes = " | ".join([part for part in curr_notes.split(" | ") if not part.startswith("CAÍDA:") and not part.startswith("Caída")]).strip(" |")

            conn.execute("""
                UPDATE streaming_accounts
                SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_status, cleaned_notes, acc_id))

            updated = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()
            return dict(updated)
    finally:
        conn.close()


def replace_fallen_account(
    email_or_query: str,
    reason: str = "Reporte de caída",
    platform_filter: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            clean_digits = re.sub(r'\D', '', q)
            is_small_id = clean_digits.isdigit() and len(clean_digits) <= 6 and q.isdigit()
            is_phone = len(clean_digits) >= 7

            conditions = []
            params: List[Any] = []

            if is_small_id:
                conditions.append("a.id = ?")
                params.append(int(clean_digits))

            if is_phone:
                conditions.append("c.whatsapp LIKE ?")
                params.append(f"%{clean_digits[-8:]}%")

            conditions.append("lower(a.email) LIKE lower(?)")
            params.append(f"%{q}%")

            conditions.append("lower(c.name) LIKE lower(?)")
            params.append(f"%{q}%")

            where_clause = " OR ".join(conditions)

            extra_plat_sql = ""
            if platform_filter:
                extra_plat_sql = " AND lower(a.platform) LIKE lower(?) "
                params.append(f"%{platform_filter.strip()}%")

            # Priorizar cuentas caídas (status='caida'), luego ocupadas (status='ocupada')
            sql = f"""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type 
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE ({where_clause})
                  {extra_plat_sql}
                  AND a.status IN ('caida', 'ocupada')
                ORDER BY CASE WHEN a.status = 'caida' THEN 0 ELSE 1 END, a.updated_at DESC, a.id DESC
                LIMIT 1
            """
            old_row = conn.execute(sql, params).fetchone()

            if not old_row:
                return None
            
            old_acc = dict(old_row)
            platform = old_acc["platform"]
            client_id = old_acc["client_id"]
            expiry = old_acc["expiry_date"]
            price = old_acc["price"]
            recurrence = old_acc["recurrence"]

            free_row = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (platform,)).fetchone()

            if not free_row:
                curr_notes = old_acc.get("notes") or ""
                updated_notes = f"{curr_notes} | CAÍDA: {reason} ({date.today().isoformat()})".strip(" |")
                conn.execute("""
                    UPDATE streaming_accounts 
                    SET status = 'caida', notes = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (updated_notes, old_acc["id"]))

                old_acc["status"] = "caida"
                old_acc["notes"] = updated_notes
                return {
                    "success": True,
                    "replaced": False,
                    "out_of_stock": True,
                    "platform": platform,
                    "error": f"No hay cuentas libres disponibles en inventario para la plataforma '{platform}'",
                    "old_account": old_acc
                }

            new_acc = dict(free_row)

            curr_notes = old_acc.get("notes") or ""
            updated_old_notes = f"{curr_notes} | CAÍDA REEMPLAZADA: {reason} ({date.today().isoformat()}) -> Reemplazo #{new_acc['id']}".strip(" |")
            conn.execute("""
                UPDATE streaming_accounts 
                SET status = 'reemplazada_caida', notes = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (updated_old_notes, old_acc["id"]))

            repl_notes = f"Reemplazo por caída de cuenta #{old_acc['id']} ({old_acc['email']})"
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', expiry_date = ?, 
                    price = ?, recurrence = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client_id, expiry, price, recurrence, repl_notes, new_acc["id"]))

            fresh_new = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (new_acc["id"],)).fetchone()

            return {
                "success": True,
                "replaced": True,
                "platform": platform,
                "old_account": old_acc,
                "new_account": dict(fresh_new)
            }
    finally:
        conn.close()

def report_and_auto_replace_account(
    identifier: str,
    reason: str = "Reporte de caída",
    platform_filter: Optional[str] = None
) -> Dict[str, Any]:
    """
    Gestiona el reporte y reemplazo inmediato en 1 clic de una cuenta.
    Si hay stock libre, asigna la nueva cuenta y genera el mensaje de WhatsApp con credenciales.
    Si no hay stock libre, marca la cuenta como caída y genera un mensaje tranquilizador para el cliente.
    """
    from services.template_service import generate_whatsapp_message
    from core.utils import clean_whatsapp_phone

    res = replace_fallen_account(identifier, reason=reason, platform_filter=platform_filter)
    if not res:
        return {
            "success": False,
            "replaced": False,
            "error": f"No se encontró ninguna cuenta activa o registrada que coincida con '{identifier}'."
        }

    if res.get("replaced"):
        new_acc = res["new_account"]
        wa_data = generate_whatsapp_message(new_acc, message_type="reemplazo")
        return {
            "success": True,
            "replaced": True,
            "platform": res["platform"],
            "client_name": new_acc.get("client_name") or "Cliente",
            "old_account": res["old_account"],
            "new_account": new_acc,
            "whatsapp_message": wa_data.get("message_text", ""),
            "wa_link": wa_data.get("wa_link", ""),
            "clean_phone": wa_data.get("clean_phone") or clean_whatsapp_phone(new_acc.get("whatsapp") or "")
        }
    else:
        old_acc = res["old_account"]
        c_name = old_acc.get("client_name") or "Cliente"
        plat = res["platform"] or "Streaming"
        c_phone = clean_whatsapp_phone(old_acc.get("whatsapp") or "")
        apology_msg = (
            f"🛠️ *¡Hola {c_name}!* Hemos registrado tu reporte sobre el inconveniente con tu servicio de *{plat}*.\n\n"
            f"Nuestro equipo técnico ya se encuentra gestionando la reposición de tu cuenta con el proveedor. "
            f"Te enviaremos tus nuevos datos de acceso por este mismo chat a la brevedad posible.\n\n"
            f"¡Te pedimos sinceras disculpas por las molestias y muchas gracias por tu paciencia! 🙌"
        )
        return {
            "success": True,
            "replaced": False,
            "out_of_stock": True,
            "platform": plat,
            "client_name": c_name,
            "old_account": old_acc,
            "whatsapp_message": apology_msg,
            "clean_phone": c_phone,
            "error": res.get("error")
        }

def get_free_stock(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if platform:
            rows = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE status = 'libre' AND lower(platform) = lower(?)
                ORDER BY platform ASC, id ASC
            """, (platform.strip(),)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE status = 'libre'
                ORDER BY platform ASC, id ASC
            """,).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_fallen_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.status = 'caida'
            ORDER BY a.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_active_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    today = date.today()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.status = 'ocupada'
            ORDER BY a.expiry_date ASC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
                diff = (exp - today).days
                d["days_remaining"] = diff
            except Exception:
                d["days_remaining"] = None
            result.append(d)
        return result
    finally:
        conn.close()

def get_expiring_streaming_accounts(days_window: int = 2) -> List[Dict[str, Any]]:
    """
    Retorna cuentas activas que vencen en la ventana especificada.
    PROTECCIÓN ANTI-ZOMBIE: Solo incluye cuentas cuyo vencimiento esté entre HOY (0) y days_window.
    Excluye cuentas con vencimiento negativo (ya pasadas) para no spamear a ex-clientes.
    """
    all_active = get_active_accounts()
    expiring = []
    for a in all_active:
        d = a.get("days_remaining")
        if d is not None and 0 <= d <= days_window:
            expiring.append(a)
    return expiring

def get_due_today_unpaid_accounts() -> List[Dict[str, Any]]:
    """Retorna cuentas activas que vencen estrictamente HOY (days_remaining == 0) y que siguen impagas."""
    all_active = get_active_accounts()
    due_today = []
    for a in all_active:
        if a.get("days_remaining") == 0 and a.get("payment_status") != "pagado":
            due_today.append(a)
    return due_today

def mark_overdue_accounts_for_password_change(overdue_days_threshold: int = 1) -> List[Dict[str, Any]]:
    """
    Pone en estado 'por_cambiar_clave' las cuentas que vencieron hace más de overdue_days_threshold días y no pagaron.
    Frena definitivamente los mensajes diarios al cliente y coloca la cuenta en la lista prioritaria de cambio de clave.
    """
    conn = get_connection()
    all_active = get_active_accounts()
    changed = []
    try:
        with conn:
            for a in all_active:
                d = a.get("days_remaining")
                if d is not None and d <= -overdue_days_threshold and a.get("payment_status") != "pagado":
                    acc_id = a["id"]
                    conn.execute("""
                        UPDATE streaming_accounts
                        SET status = 'por_cambiar_clave', updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (acc_id,))
                    a["status"] = "por_cambiar_clave"
                    changed.append(a)
        return changed
    finally:
        conn.close()

def get_accounts_pending_password_change() -> List[Dict[str, Any]]:
    """Retorna cuentas con corte pendiente o en estado 'por_cambiar_clave'."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.status = 'por_cambiar_clave'
            ORDER BY a.expiry_date ASC, a.id ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def mark_account_for_password_change(account_id_or_email: str) -> Dict[str, Any]:
    """Pone una cuenta individual en estado 'por_cambiar_clave' para cortar alertas y proceder a baja/cambio de contraseña."""
    conn = get_connection()
    q = str(account_id_or_email).strip()
    try:
        with conn:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) = lower(?) OR a.id = ?
                LIMIT 1
            """, (q, int(q) if q.isdigit() else -1)).fetchone()
            if not row:
                return {"success": False, "error": f"Cuenta '{q}' no encontrada."}
            acc = dict(row)
            conn.execute("""
                UPDATE streaming_accounts
                SET status = 'por_cambiar_clave', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (acc["id"],))
            return {
                "success": True,
                "account_id": acc["id"],
                "email": acc["email"],
                "platform": acc["platform"],
                "client_name": acc.get("client_name"),
                "whatsapp": acc.get("whatsapp")
            }
    finally:
        conn.close()

def rotate_master_password_and_broadcast(
    email_or_account_id: str,
    new_password: str,
    unpaid_account_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Actualiza la contraseña de la cuenta madre y, si es compartida por perfiles:
    1. Si se especifica unpaid_account_id, libera ese perfil a stock libre ('libre').
    2. Actualiza la contraseña en todos los perfiles de ese correo.
    3. Genera los datos de los demás clientes activos para notificarles su nueva contraseña por WhatsApp.
    """
    conn = get_connection()
    q = str(email_or_account_id).strip()
    clean_pwd = new_password.strip()
    if not clean_pwd:
        return {"success": False, "error": "La nueva contraseña no puede estar vacía."}

    try:
        with conn:
            # 1. Identificar la cuenta base
            base_row = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(email) = lower(?) OR id = ?
                LIMIT 1
            """, (q, int(q) if q.isdigit() else -1)).fetchone()

            if not base_row:
                return {"success": False, "error": f"No se encontró ninguna cuenta con '{q}'"}

            target_email = base_row["email"].strip()
            target_platform = base_row["platform"].strip()

            # 2. Obtener todos los perfiles asociados a este correo y plataforma
            all_profiles = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp as client_whatsapp
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) = lower(?) AND lower(a.platform) = lower(?)
            """, (target_email, target_platform)).fetchall()

            profile_list = [dict(p) for p in all_profiles]
            is_shared = len(profile_list) > 1 or any(p.get("profile_name") for p in profile_list)

            # 3. Actualizar la contraseña en todas las filas de esa cuenta madre
            conn.execute("""
                UPDATE streaming_accounts
                SET password = ?, updated_at = CURRENT_TIMESTAMP
                WHERE lower(email) = lower(?) AND lower(platform) = lower(?)
            """, (clean_pwd, target_email, target_platform))

            # 4. Si se especificó el perfil impago que no renovó, liberarlo
            freed_profile = None
            if unpaid_account_id:
                conn.execute("""
                    UPDATE streaming_accounts
                    SET status = 'libre', client_id = NULL, payment_status = 'pagado',
                        debt_balance = 0.0, expiry_date = '', last_alert_sent = '',
                        notes = 'Liberada por falta de pago (Rotación de contraseña)',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (unpaid_account_id,))
                for p in profile_list:
                    if p["id"] == unpaid_account_id:
                        p["status"] = "libre"
                        p["client_id"] = None
                        freed_profile = p
                        break

            # 5. Detectar co-usuarios activos que deben recibir la nueva credencial
            active_co_users = []
            for p in profile_list:
                # Omitir el perfil que fue dado de baja/liberado
                if unpaid_account_id and p["id"] == unpaid_account_id:
                    continue
                # Si está ocupada y tiene cliente y teléfono
                if p.get("status") == "ocupada" and p.get("client_id"):
                    c_name = p.get("client_name") or "Cliente"
                    raw_phone = p.get("client_whatsapp") or p.get("whatsapp") or ""
                    clean_phone = re.sub(r'\\D', '', str(raw_phone))
                    pin_info = f" [PIN: {p.get('profile_pin')}]" if p.get("profile_pin") else ""

                    wa_msg = (
                        f"🔐 *¡Hola {c_name}!* Te informamos que por mantenimiento y seguridad hemos actualizado la contraseña de tu cuenta de *{target_platform}*.\n\n"
                        f"✨ *Tus Nuevos Accesos:*\n"
                        f"📺 *Servicio:* {target_platform}\n"
                        f"📧 *Correo:* `{target_email}`\n"
                        f"🔑 *Nueva Contraseña:* `{clean_pwd}`\n"
                        f"👤 *Tu Perfil:* {p.get('profile_name') or 'Principal'}{pin_info}\n"
                        f"📅 *Tu servicio continúa activo normalmente hasta el:* {p.get('expiry_date') or 'fin de tu ciclo'}\n\n"
                        f"📌 *Importante:* Recuerda no modificar la clave ni el correo dentro de la aplicación. ¡Muchas gracias por tu preferencia! 🙌🍿"
                    )
                    active_co_users.append({
                        "account_id": p["id"],
                        "client_name": c_name,
                        "clean_phone": clean_phone,
                        "platform": target_platform,
                        "email": target_email,
                        "profile_name": p.get("profile_name", ""),
                        "profile_pin": p.get("profile_pin", ""),
                        "expiry_date": p.get("expiry_date", ""),
                        "whatsapp_message": wa_msg
                    })

            return {
                "success": True,
                "email": target_email,
                "platform": target_platform,
                "new_password": clean_pwd,
                "total_profiles": len(profile_list),
                "is_shared": is_shared,
                "freed_profile": freed_profile,
                "active_co_users": active_co_users
            }
    finally:
        conn.close()

def renew_account(account_id_or_email: str, new_expiry_date: str) -> bool:
    conn = get_connection()
    q = account_id_or_email.strip()
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, status = 'ocupada', payment_status = 'pagado', 
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE (lower(email) = lower(?) OR id = ?)
            """, (new_expiry_date.strip(), q, int(q) if q.isdigit() else -1))
            return cursor.rowcount > 0
    finally:
        conn.close()

def delete_account(account_id: int) -> bool:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM streaming_accounts WHERE id = ?", (account_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def purge_accounts_except_client(client_name_query: str = "samuel martin") -> Dict[str, Any]:
    """Elimina todas las cuentas de streaming excepto las asignadas al cliente indicado."""
    conn = get_connection()
    try:
        with conn:
            # Identificar los IDs de clientes protegidos
            protected_clients = conn.execute("""
                SELECT id, name FROM clients WHERE lower(name) LIKE lower(?)
            """, (f"%{client_name_query.strip()}%",)).fetchall()
            
            protected_client_ids = [r["id"] for r in protected_clients]
            
            # Cuentas que se van a eliminar
            if protected_client_ids:
                placeholders = ",".join("?" for _ in protected_client_ids)
                to_delete = conn.execute(f"""
                    SELECT id, email, platform, status, client_id
                    FROM streaming_accounts
                    WHERE client_id IS NULL OR client_id NOT IN ({placeholders})
                """, protected_client_ids).fetchall()
                
                del_cursor = conn.execute(f"""
                    DELETE FROM streaming_accounts
                    WHERE client_id IS NULL OR client_id NOT IN ({placeholders})
                """, protected_client_ids)
            else:
                to_delete = conn.execute("""
                    SELECT id, email, platform, status, client_id
                    FROM streaming_accounts
                """).fetchall()
                del_cursor = conn.execute("DELETE FROM streaming_accounts")

            deleted_count = del_cursor.rowcount
            
            # Cuentas preservadas
            kept = conn.execute("""
                SELECT a.id, a.email, a.platform, a.profile_name, c.name as client_name
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
            """).fetchall()

            return {
                "success": True,
                "deleted_count": deleted_count,
                "protected_client": client_name_query,
                "kept_accounts": [dict(k) for k in kept]
            }
    finally:
        conn.close()


def mark_streaming_alert_sent(account_id: int, alert_date: str):
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE streaming_accounts SET last_alert_sent = ? WHERE id = ?", (alert_date, account_id))
    finally:
        conn.close()

def get_account_detail(email_or_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Obtiene el detalle completo de una cuenta y su cliente asociado."""
    conn = get_connection()
    q = str(email_or_id).strip()
    try:
        if q.isdigit():
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
                LIMIT 1
            """, (int(q),)).fetchone()
            if not row:
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) = lower(?)
                    LIMIT 1
                """, (q,)).fetchone()
        else:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) = lower(?)
                LIMIT 1
            """, (q,)).fetchone()
            if not row:
                row = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) LIKE lower(?)
                    ORDER BY a.id DESC LIMIT 1
                """, (f"%{q}%",)).fetchone()
        if not row:
            return None
        d = dict(row)
        today = date.today()
        try:
            exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
            d["days_remaining"] = (exp - today).days
        except Exception:
            d["days_remaining"] = None
        return d
    finally:
        conn.close()

def create_master_account_with_profiles(
    platform: str,
    email: str,
    password: str,
    profile_count: int = 4,
    pins: Union[str, List[str]] = "",
    cost: str = "",
    notes: str = "",
    supplier_id: Optional[int] = None,
    supplier_expiry_date: str = "",
    supplier_cost: Union[float, str] = 0.0
) -> List[Dict[str, Any]]:
    """Crea una cuenta madre en stock y genera automáticamente sus N casilleros/perfiles libres vinculados a su proveedor."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_email = email.strip()
    clean_password = password.strip()
    s_exp = supplier_expiry_date.strip()
    s_cost_num = parse_money(supplier_cost) if supplier_cost else parse_money(cost)
    
    pin_list = []
    if isinstance(pins, list):
        pin_list = [str(p).strip() for p in pins]
    elif isinstance(pins, str) and pins.strip():
        parts = re.split(r'[,;\s]+', pins.strip())
        pin_list = [p for p in parts if p]

    created = []
    try:
        with conn:
            for i in range(profile_count):
                prof_name = f"Perfil {i + 1}"
                prof_pin = pin_list[i] if i < len(pin_list) else (pin_list[0] if len(pin_list) == 1 else "")
                prof_cost = cost if i == 0 else ""

                cursor = conn.execute("""
                    INSERT INTO streaming_accounts (
                        platform, email, password, profile_name, profile_pin,
                        status, payment_status, cost, notes,
                        supplier_id, supplier_expiry_date, supplier_cost
                    ) VALUES (?, ?, ?, ?, ?, 'libre', 'pagado', ?, ?, ?, ?, ?)
                """, (clean_platform, clean_email, clean_password, prof_name, prof_pin, prof_cost, notes.strip(),
                      supplier_id, s_exp, s_cost_num))
                
                acc_id = cursor.lastrowid
                row = conn.execute("SELECT * FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
                created.append(dict(row))
        return created
    finally:
        conn.close()

def assign_next_free_profile(
    client_name: str,
    platform: str,
    expiry_date: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    price: str = "",
    notes: str = ""
) -> Optional[Dict[str, Any]]:
    """Busca el primer perfil libre disponible de una plataforma y lo asigna a un cliente."""
    from db.repositories.catalog_repo import get_suggested_price

    conn = get_connection()
    clean_platform = platform.strip().title()
    try:
        with conn:
            # 1. Búsqueda exacta
            free_slot = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (clean_platform,)).fetchone()

            # 2. Búsqueda inteligente por modalidad si es Netflix
            if not free_slot and "netflix" in clean_platform.lower():
                if any(k in clean_platform.lower() for k in ["casa extra", "pantalla", "perfil"]):
                    free_slot = conn.execute("""
                        SELECT * FROM streaming_accounts
                        WHERE (lower(platform) LIKE '%casa extra%' OR lower(platform) = 'netflix')
                          AND status = 'libre'
                        ORDER BY CASE WHEN lower(platform) LIKE '%casa extra%' THEN 0 ELSE 1 END, id ASC
                        LIMIT 1
                    """).fetchone()
                elif any(k in clean_platform.lower() for k in ["completa", "full hd", "4k", "4 pantallas"]):
                    free_slot = conn.execute("""
                        SELECT * FROM streaming_accounts
                        WHERE (lower(platform) LIKE '%completa%' OR lower(platform) LIKE '%full hd%')
                          AND status = 'libre'
                        ORDER BY id ASC
                        LIMIT 1
                    """).fetchone()
                else:
                    free_slot = conn.execute("""
                        SELECT * FROM streaming_accounts
                        WHERE lower(platform) LIKE '%netflix%' AND status = 'libre'
                        ORDER BY id ASC
                        LIMIT 1
                    """).fetchone()

            # 3. Búsqueda flexible por substring
            if not free_slot:
                free_slot = conn.execute("""
                    SELECT * FROM streaming_accounts
                    WHERE (lower(platform) LIKE ? OR ? LIKE '%' || lower(platform) || '%')
                      AND status = 'libre'
                    ORDER BY id ASC LIMIT 1
                """, (f"%{clean_platform.lower()}%", clean_platform.lower())).fetchone()

            if not free_slot:
                return None

            slot = dict(free_slot)
            slot_id = slot["id"]

            client = find_or_create_client(
                name=client_name,
                whatsapp=whatsapp,
                telegram=telegram,
                client_type=client_type,
                notes=notes
            )

            actual_client_type = client.get("client_type") or client_type

            price_num = parse_money(price)
            cost_num = parse_money(slot.get("cost"))
            
            s_price, s_cost = get_suggested_price(clean_platform, "pantalla", actual_client_type)
            if "revend" in actual_client_type.lower():
                norm_price, _ = get_suggested_price(clean_platform, "pantalla", "consumidor_final")
                if price_num == 0.0 or (norm_price > 0 and price_num == norm_price):
                    if s_price > 0.0:
                        price_num = s_price
                        price = format_ars(price_num)
            else:
                if price_num == 0.0 and s_price > 0.0:
                    price_num = s_price
                    price = format_ars(price_num)

            if cost_num == 0.0 and s_cost > 0.0:
                cost_num = s_cost

            today_str = date.today().isoformat()
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                    start_date = ?, expiry_date = ?, price = ?, notes = ?,
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client["id"], today_str, expiry_date.strip(), price.strip(), notes.strip(), slot_id))

            profit_num = price_num - cost_num
            if price_num > 0:
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'Inicial', 'Venta perfil compartido')
                """, (slot_id, client["id"], price_num, cost_num, profit_num))

            fresh = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (slot_id,)).fetchone()
            return dict(fresh)
    finally:
        conn.close()

def get_shared_screens_overview(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """Agrupa las cuentas por correo madre y muestra la ocupación de cada una."""
    conn = get_connection()
    today = date.today()
    try:
        query = """
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
        """
        params = []
        if platform:
            query += " WHERE lower(a.platform) = lower(?)"
            params.append(platform.strip())
        query += " ORDER BY a.platform ASC, a.email ASC, a.id ASC"

        rows = conn.execute(query, params).fetchall()

        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in rows:
            d = dict(r)
            try:
                exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
                d["days_remaining"] = (exp - today).days
            except Exception:
                d["days_remaining"] = None
            key = (d["platform"], d["email"])
            grouped.setdefault(key, []).append(d)

        overview = []
        for (plat, mail), profs in grouped.items():
            occupied = [p for p in profs if p["status"] == "ocupada"]
            free = [p for p in profs if p["status"] == "libre"]
            fallen = [p for p in profs if p["status"] == "caida"]
            passw = profs[0]["password"] if profs else ""
            
            overview.append({
                "platform": plat,
                "email": mail,
                "password": passw,
                "total_profiles": len(profs),
                "occupied_count": len(occupied),
                "free_count": len(free),
                "fallen_count": len(fallen),
                "occupancy_rate": round((len(occupied) / len(profs)) * 100, 1) if profs else 0.0,
                "profiles": profs
            })
        return overview
    finally:
        conn.close()

def mark_entire_master_account_fallen(email_or_query: str, reason: str = "Caída de cuenta completa") -> Dict[str, Any]:
    """Marca como caídas todas las pantallas asociadas a un correo madre y lista los clientes afectados."""
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            rows = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) AND a.status IN ('ocupada', 'libre')
            """, (f"%{q}%",)).fetchall()

            if not rows:
                return {"success": False, "error": f"No se encontraron cuentas activas con el correo '{q}'"}

            affected_clients = []
            for r in rows:
                d = dict(r)
                if d["status"] == "ocupada":
                    affected_clients.append({
                        "account_id": d["id"],
                        "client_name": d.get("client_name"),
                        "profile_name": d.get("profile_name"),
                        "whatsapp": d.get("whatsapp"),
                        "telegram": d.get("telegram"),
                        "platform": d.get("platform")
                    })
                conn.execute("""
                    UPDATE streaming_accounts 
                    SET status = 'caida', notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (reason.strip(), d["id"]))

            return {
                "success": True,
                "email": q,
                "total_profiles_affected": len(rows),
                "affected_clients": affected_clients
            }
    finally:
        conn.close()

def set_platform_min_stock(platform: str, min_stock: int) -> bool:
    """Establece o actualiza el umbral mínimo de stock para una plataforma."""
    conn = get_connection()
    plat = platform.strip()
    try:
        with conn:
            conn.execute("""
                INSERT INTO stock_thresholds (platform, min_stock, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform) DO UPDATE SET min_stock = excluded.min_stock, updated_at = CURRENT_TIMESTAMP
            """, (plat, max(0, min_stock)))
            return True
    except Exception as e:
        logger.error(f"Error estableciendo umbral de stock: {e}")
        return False
    finally:
        conn.close()

def get_stock_thresholds() -> Dict[str, int]:
    """Obtiene el diccionario de umbrales mínimos configurados por plataforma."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT platform, min_stock FROM stock_thresholds").fetchall()
        return {r["platform"]: r["min_stock"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()

def get_stock_health_summary(default_min_stock: int = 2) -> Dict[str, Any]:
    """
    Analiza todo el catálogo y devuelve un diagnóstico de salud del stock por plataforma:
    - Agotadas (0 disponibles) -> 🔴
    - Stock Bajo (<= umbral mínimo) -> 🟡
    - Stock Óptimo (> umbral mínimo) -> 🟢
    """
    conn = get_connection()
    try:
        thresholds_map = {}
        try:
            th_rows = conn.execute("SELECT platform, min_stock FROM stock_thresholds").fetchall()
            thresholds_map = {r["platform"].strip().lower(): r["min_stock"] for r in th_rows}
        except Exception:
            pass

        plat_rows = conn.execute("""
            SELECT DISTINCT platform FROM streaming_accounts
            WHERE platform != ''
            ORDER BY platform ASC
        """).fetchall()

        all_platforms = [r["platform"].strip() for r in plat_rows if r["platform"].strip()]

        free_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'libre'
            GROUP BY platform
        """).fetchall()
        free_map = {r["platform"].strip().lower(): r["count"] for r in free_rows}

        occupied_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'ocupada'
            GROUP BY platform
        """).fetchall()
        occupied_map = {r["platform"].strip().lower(): r["count"] for r in occupied_rows}

        fallen_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'caida'
            GROUP BY platform
        """).fetchall()
        fallen_map = {r["platform"].strip().lower(): r["count"] for r in fallen_rows}

        platforms_summary = []
        out_of_stock_count = 0
        low_stock_count = 0
        optimal_count = 0
        total_free_units = 0

        for p_name in all_platforms:
            key = p_name.lower()
            free_cnt = free_map.get(key, 0)
            occupied_cnt = occupied_map.get(key, 0)
            fallen_cnt = fallen_map.get(key, 0)
            min_thresh = thresholds_map.get(key, thresholds_map.get("default", default_min_stock))
            total_free_units += free_cnt

            if free_cnt == 0:
                status = "agotado"
                badge = "🔴 Agotado"
                out_of_stock_count += 1
            elif free_cnt <= min_thresh:
                status = "bajo"
                badge = "🟡 Stock Bajo"
                low_stock_count += 1
            else:
                status = "optimo"
                badge = "🟢 Óptimo"
                optimal_count += 1

            platforms_summary.append({
                "platform": p_name,
                "free_count": free_cnt,
                "occupied_count": occupied_cnt,
                "fallen_count": fallen_cnt,
                "min_threshold": min_thresh,
                "status": status,
                "badge": badge,
                "needs_alert": status in ("agotado", "bajo")
            })

        status_order = {"agotado": 0, "bajo": 1, "optimo": 2}
        platforms_summary.sort(key=lambda x: (status_order.get(x["status"], 3), -x["occupied_count"], x["platform"]))

        alert_platforms = [p for p in platforms_summary if p["needs_alert"]]

        return {
            "total_platforms": len(all_platforms),
            "total_free_units": total_free_units,
            "out_of_stock_count": out_of_stock_count,
            "low_stock_count": low_stock_count,
            "optimal_count": optimal_count,
            "has_alerts": len(alert_platforms) > 0,
            "alert_platforms": alert_platforms,
            "platforms": platforms_summary
        }
    finally:
        conn.close()

def update_account_price(
    identifier: Union[str, int],
    new_price: Union[str, float],
    platform: Optional[str] = None,
    mark_as_reseller: bool = False,
    notes: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Modifica o corrige el precio cobrado por una cuenta activa y actualiza el libro contable de pagos."""
    conn = get_connection()
    new_price_num = parse_money(new_price)
    new_price_str = format_ars(new_price_num)
    ident_str = str(identifier).strip()

    try:
        with conn:
            target = None
            if ident_str.isdigit():
                target = conn.execute("""
                    SELECT a.*, c.name as client_name, c.client_code, c.client_type, c.whatsapp, c.id as cid
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                """, (int(ident_str),)).fetchone()

            if not target:
                target = conn.execute("""
                    SELECT a.*, c.name as client_name, c.client_code, c.client_type, c.whatsapp, c.id as cid
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE lower(a.email) = lower(?) AND a.status = 'ocupada'
                    LIMIT 1
                """, (ident_str,)).fetchone()

            if not target:
                plat_filter = "AND lower(a.platform) = lower(?)" if platform else ""
                plat_args = (f"%{ident_str}%", f"%{ident_str}%", platform.strip()) if platform else (f"%{ident_str}%", f"%{ident_str}%")
                
                target = conn.execute(f"""
                    SELECT a.*, c.name as client_name, c.client_code, c.client_type, c.whatsapp, c.id as cid
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.status = 'ocupada'
                      AND (lower(c.name) LIKE lower(?) OR replace(replace(c.whatsapp, ' ', ''), '-', '') LIKE ?)
                      {plat_filter}
                    ORDER BY a.id DESC
                    LIMIT 1
                """, plat_args).fetchone()

            if not target:
                return None

            acc_id = target["id"]
            client_id = target["cid"]
            old_price = target["price"]
            cost_num = parse_money(target["cost"])
            new_profit_num = new_price_num - cost_num

            # 1. Actualizar streaming_accounts
            conn.execute("""
                UPDATE streaming_accounts
                SET price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_price_str, acc_id))

            # 2. Si se marcó como revendedor, actualizar cliente
            if mark_as_reseller and client_id:
                conn.execute("""
                    UPDATE clients
                    SET client_type = 'revendedor'
                    WHERE id = ?
                """, (client_id,))

            # 3. Actualizar registro en payments
            last_payment = conn.execute("""
                SELECT id, cost FROM payments
                WHERE account_id = ?
                ORDER BY id DESC LIMIT 1
            """, (acc_id,)).fetchone()

            if last_payment:
                p_cost = float(last_payment["cost"] or cost_num)
                p_profit = new_price_num - p_cost
                conn.execute("""
                    UPDATE payments
                    SET amount = ?, cost = ?, profit = ?, notes = notes || ' [Precio corregido a ' || ? || ']'
                    WHERE id = ?
                """, (new_price_num, p_cost, p_profit, new_price_str, last_payment["id"]))
            else:
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'Corrección', 'Ajuste de precio manual')
                """, (acc_id, client_id, new_price_num, cost_num, new_profit_num))

            fresh = conn.execute("""
                SELECT a.*, c.name as client_name, c.client_code, c.client_type, c.whatsapp
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()

            res = dict(fresh)
            res["old_price"] = old_price
            res["new_price"] = new_price_str
            res["new_price_num"] = new_price_num
            res["cost_num"] = cost_num
            res["profit_num"] = new_profit_num
            return res
    finally:
        conn.close()

def get_http_custom_accounts() -> List[Dict[str, Any]]:
    """Obtiene todas las cuentas y servidores HTTP Custom registrados con sus clientes y días restantes."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.client_type, c.whatsapp as client_phone
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.platform = 'HTTP Custom'
            ORDER BY a.expiry_date ASC, a.id DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

