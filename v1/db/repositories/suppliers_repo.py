from datetime import datetime, date
from typing import Optional, Dict, Any, List, Union, Tuple

from db.connection import get_connection
from core.utils import parse_money, format_ars

def get_suppliers() -> List[Dict[str, Any]]:
    """Devuelve la lista de todos los proveedores mayoristas con resumen de cuentas y gastos."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT s.*,
                   (SELECT COUNT(DISTINCT lower(email)) FROM streaming_accounts WHERE supplier_id = s.id) as master_accounts_count,
                   (SELECT COUNT(*) FROM streaming_accounts WHERE supplier_id = s.id) as profiles_count,
                   (SELECT COALESCE(SUM(amount), 0.0) FROM supplier_expenses WHERE supplier_id = s.id) as total_spent
            FROM suppliers s
            ORDER BY s.name ASC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["total_spent_formatted"] = format_ars(d.get("total_spent", 0.0))
            result.append(d)
        return result
    finally:
        conn.close()

def get_supplier(supplier_id: int) -> Optional[Dict[str, Any]]:
    """Devuelve el detalle de un proveedor por ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        exp = conn.execute("SELECT COALESCE(SUM(amount), 0.0) as s FROM supplier_expenses WHERE supplier_id = ?", (supplier_id,)).fetchone()
        d["total_spent"] = exp["s"] if exp else 0.0
        d["total_spent_formatted"] = format_ars(d["total_spent"])
        return d
    finally:
        conn.close()

def save_supplier(
    supplier_id: Optional[int] = None,
    name: str = "",
    contact: str = "",
    payment_info: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    """Crea o actualiza un proveedor mayorista."""
    conn = get_connection()
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("El nombre del proveedor es obligatorio.")
    try:
        with conn:
            if supplier_id and int(supplier_id) > 0:
                conn.execute("""
                    UPDATE suppliers 
                    SET name = ?, contact = ?, payment_info = ?, notes = ?
                    WHERE id = ?
                """, (clean_name, contact.strip(), payment_info.strip(), notes.strip(), int(supplier_id)))
                s_id = int(supplier_id)
            else:
                cur = conn.execute("""
                    INSERT INTO suppliers (name, contact, payment_info, notes)
                    VALUES (?, ?, ?, ?)
                """, (clean_name, contact.strip(), payment_info.strip(), notes.strip()))
                s_id = cur.lastrowid
            row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (s_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def delete_supplier(supplier_id: int) -> bool:
    """Elimina un proveedor desvinculando sus cuentas asociadas."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE streaming_accounts SET supplier_id = NULL WHERE supplier_id = ?", (supplier_id,))
            conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
            return True
    finally:
        conn.close()

def get_master_accounts_overview() -> List[Dict[str, Any]]:
    """Agrupa las cuentas de streaming por (plataforma, correo) para auditar el estado mayorista y el riesgo de corte."""
    conn = get_connection()
    today = date.today()
    try:
        rows = conn.execute("""
            SELECT a.*, s.name as supplier_name, s.contact as supplier_contact,
                   c.name as client_name
            FROM streaming_accounts a
            LEFT JOIN suppliers s ON a.supplier_id = s.id
            LEFT JOIN clients c ON a.client_id = c.id
            ORDER BY a.platform ASC, a.email ASC, a.profile_name ASC
        """).fetchall()

        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in rows:
            key = (r["platform"].strip().title(), r["email"].strip().lower())
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(dict(r))

        overview = []
        for (plat, em), accs in grouped.items():
            first = accs[0]
            sup_expiry = first.get("supplier_expiry_date") or ""
            sup_cost = parse_money(first.get("supplier_cost") or first.get("cost") or 0.0)
            sup_id = first.get("supplier_id")
            sup_name = first.get("supplier_name") or "Sin asignar"
            sup_contact = first.get("supplier_contact") or ""

            # Días restantes con proveedor
            days_sup = None
            badge_class = "badge-warn"
            status_label = "Sin fecha mayorista"
            if sup_expiry:
                try:
                    exp_dt = date.fromisoformat(sup_expiry[:10])
                    days_sup = (exp_dt - today).days
                    if days_sup < 0:
                        badge_class = "badge-danger"
                        status_label = f"Vencida ante proveedor (-{abs(days_sup)}d)"
                    elif days_sup == 0:
                        badge_class = "badge-danger"
                        status_label = "¡Vence HOY ante proveedor!"
                    elif days_sup <= 3:
                        badge_class = "badge-warn"
                        status_label = f"Vence en {days_sup} días"
                    else:
                        badge_class = "badge-ok"
                        status_label = f"Al día ({days_sup}d)"
                except Exception:
                    status_label = "Fecha mayorista inválida"

            # Estadísticas de perfiles
            total_profs = len(accs)
            occupied = [a for a in accs if a["status"] == "ocupada"]
            free = [a for a in accs if a["status"] == "libre"]
            fallen = [a for a in accs if a["status"] in ("caida", "reemplazada_caida")]
            occ_rate = round((len(occupied) / total_profs) * 100) if total_profs > 0 else 0

            # Fecha máxima de vencimiento entre los clientes ocupados
            client_max_expiry = ""
            risk_mismatch = False
            mismatch_warning = ""

            occupied_expiries = [a.get("expiry_date") for a in occupied if a.get("expiry_date")]
            if occupied_expiries:
                client_max_expiry = max(occupied_expiries)
                if sup_expiry and client_max_expiry > sup_expiry:
                    risk_mismatch = True
                    mismatch_warning = f"¡Riesgo de corte! Clientes vencen el {client_max_expiry} (posterior a cuenta madre {sup_expiry})."

            overview.append({
                "platform": plat,
                "email": first["email"],
                "password": first["password"],
                "supplier_id": sup_id,
                "supplier_name": sup_name,
                "supplier_contact": sup_contact,
                "supplier_expiry_date": sup_expiry,
                "days_remaining_supplier": days_sup,
                "status_badge_class": badge_class,
                "status_label": status_label,
                "supplier_cost": sup_cost,
                "supplier_cost_formatted": format_ars(sup_cost),
                "profiles_total": total_profs,
                "profiles_occupied": len(occupied),
                "profiles_free": len(free),
                "profiles_fallen": len(fallen),
                "occupancy_rate": occ_rate,
                "client_max_expiry": client_max_expiry,
                "risk_mismatch": risk_mismatch,
                "mismatch_warning": mismatch_warning,
                "profiles_list": accs
            })

        def sort_key(item):
            risk_prio = 0 if item["risk_mismatch"] else 1
            days = item["days_remaining_supplier"] if item["days_remaining_supplier"] is not None else 9999
            return (risk_prio, days)

        overview.sort(key=sort_key)
        return overview
    finally:
        conn.close()

def renew_master_account(
    email: str,
    platform: str,
    new_supplier_expiry: str,
    cost: Union[float, str] = 0.0,
    supplier_id: Optional[int] = None,
    payment_method: str = "Transferencia",
    notes: str = ""
) -> Dict[str, Any]:
    """Renueva una cuenta madre actualizando su fecha ante el proveedor en todos sus perfiles y asentando el costo."""
    conn = get_connection()
    clean_email = email.strip()
    clean_plat = platform.strip().title()
    cost_num = parse_money(cost)
    new_exp = new_supplier_expiry.strip()

    try:
        with conn:
            if supplier_id is None:
                cur_row = conn.execute("""
                    SELECT supplier_id FROM streaming_accounts 
                    WHERE lower(email) = lower(?) AND lower(platform) = lower(?) AND supplier_id IS NOT NULL
                    LIMIT 1
                """, (clean_email, clean_plat)).fetchone()
                if cur_row:
                    supplier_id = cur_row["supplier_id"]

            conn.execute("""
                UPDATE streaming_accounts
                SET supplier_expiry_date = ?,
                    supplier_cost = CASE WHEN ? > 0 THEN ? ELSE supplier_cost END,
                    supplier_id = CASE WHEN ? IS NOT NULL THEN ? ELSE supplier_id END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lower(email) = lower(?) AND lower(platform) = lower(?)
            """, (new_exp, cost_num, cost_num, supplier_id, supplier_id, clean_email, clean_plat))

            if cost_num > 0:
                conn.execute("""
                    INSERT INTO supplier_expenses (supplier_id, account_email, platform, amount, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (supplier_id, clean_email, clean_plat, cost_num, payment_method.strip(), notes.strip()))

                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (NULL, NULL, 0.0, ?, ?, ?, ?)
                """, (cost_num, -cost_num, payment_method.strip(), f"Renovación Cuenta Madre {clean_plat} ({clean_email})"))

            return {
                "success": True,
                "email": clean_email,
                "platform": clean_plat,
                "new_supplier_expiry": new_exp,
                "cost": cost_num,
                "cost_formatted": format_ars(cost_num),
                "supplier_id": supplier_id
            }
    finally:
        conn.close()

def get_expiring_master_accounts(days_window: int = 3) -> List[Dict[str, Any]]:
    """Devuelve las cuentas madre que vencen ante el proveedor en <= days_window o que tienen riesgo de corte."""
    all_master = get_master_accounts_overview()
    urgent = []
    for m in all_master:
        days = m.get("days_remaining_supplier")
        if (days is not None and days <= days_window) or m.get("risk_mismatch"):
            urgent.append(m)
    return urgent
