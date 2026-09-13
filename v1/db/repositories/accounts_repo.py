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
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) OR a.id = ?
                ORDER BY a.id DESC LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()
            
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

def replace_fallen_account(email_or_query: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            old_row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type 
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE (lower(a.email) LIKE lower(?) OR a.id = ?)
                AND a.status IN ('caida', 'ocupada')
                ORDER BY a.id DESC LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()

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
                return {
                    "success": False,
                    "error": f"No hay cuentas libres disponibles en inventario para la plataforma '{platform}'",
                    "old_account": old_acc
                }

            new_acc = dict(free_row)

            conn.execute("UPDATE streaming_accounts SET status = 'reemplazada_caida', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (old_acc["id"],))

            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', expiry_date = ?, 
                    price = ?, recurrence = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client_id, expiry, price, recurrence, f"Reemplazo de {old_acc['email']}", new_acc["id"]))

            fresh_new = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (new_acc["id"],)).fetchone()

            return {
                "success": True,
                "platform": platform,
                "old_account": old_acc,
                "new_account": dict(fresh_new)
            }
    finally:
        conn.close()

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
    all_active = get_active_accounts()
    expiring = []
    for a in all_active:
        d = a.get("days_remaining")
        if d is not None and d <= days_window:
            expiring.append(a)
    return expiring

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
        row = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE lower(a.email) LIKE lower(?) OR a.id = ?
            ORDER BY a.id DESC LIMIT 1
        """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()
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
            free_slot = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (clean_platform,)).fetchone()

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
