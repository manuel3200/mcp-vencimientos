from datetime import datetime, date
from typing import Optional, Dict, Any, List, Union

from db.connection import get_connection
from core.utils import parse_money, format_ars, clean_whatsapp_phone

def find_or_create_client(
    name: str, 
    whatsapp: str = "", 
    telegram: str = "", 
    client_type: str = "consumidor_final", 
    notes: str = ""
) -> Dict[str, Any]:
    conn = get_connection()
    clean_name = name.strip()
    clean_wa = whatsapp.strip().replace(" ", "").replace("-", "")
    clean_tg = telegram.strip()
    if clean_tg and not clean_tg.startswith("@"):
        clean_tg = "@" + clean_tg
    clean_ctype = client_type.lower()
    if "vip" in clean_ctype:
        c_type = "revendedor_vip"
    elif "revend" in clean_ctype:
        c_type = "revendedor"
    else:
        c_type = "consumidor_final"

    try:
        with conn:
            query = """
                SELECT * FROM clients 
                WHERE lower(name) = lower(?)
                OR (length(?) > 4 AND replace(replace(whatsapp, ' ', ''), '-', '') = ?)
                OR (length(?) > 2 AND lower(telegram) = lower(?))
                LIMIT 1
            """
            existing = conn.execute(query, (clean_name, clean_wa, clean_wa, clean_tg, clean_tg)).fetchone()

            if existing:
                client_id = existing["id"]
                existing_type = (existing["client_type"] or "consumidor_final").lower()
                # Preservar categorías de revendedor si no se especifica explícitamente una nueva
                if "vip" in existing_type and "vip" not in clean_ctype and "revend" not in clean_ctype and "final" not in clean_ctype:
                    final_type = "revendedor_vip"
                elif "revend" in existing_type and "revend" not in clean_ctype and "vip" not in clean_ctype and "final" not in clean_ctype:
                    final_type = "revendedor"
                else:
                    final_type = c_type

                conn.execute("""
                    UPDATE clients 
                    SET whatsapp = CASE WHEN length(?) > 0 THEN ? ELSE whatsapp END,
                        telegram = CASE WHEN length(?) > 0 THEN ? ELSE telegram END,
                        client_type = ?,
                        notes = CASE WHEN length(?) > 0 THEN ? ELSE notes END
                    WHERE id = ?
                """, (clean_wa, clean_wa, clean_tg, clean_tg, final_type, notes.strip(), notes.strip(), client_id))
                row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
                return dict(row)
            else:
                cursor = conn.execute("SELECT count(*) as total FROM clients")
                total = cursor.fetchone()["total"] + 1
                client_code = f"CLI-{total:03d}"

                cursor = conn.execute("""
                    INSERT INTO clients (client_code, name, whatsapp, telegram, client_type, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (client_code, clean_name, clean_wa, clean_tg, c_type, notes.strip()))
                client_id = cursor.lastrowid
                row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
                return dict(row)
    finally:
        conn.close()

def register_or_update_client(
    name: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    notes: str = ""
) -> Dict[str, Any]:
    """Registra un nuevo cliente o actualiza uno existente."""
    return find_or_create_client(
        name=name,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=client_type,
        notes=notes
    )

def search_client(query: Union[str, int]) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = str(query).strip()
    clean_q = q.replace(" ", "").replace("-", "")
    client_id = -1
    if q.isdigit() and len(q) <= 9:
        try:
            client_id = int(q)
        except (ValueError, OverflowError):
            client_id = -1

    try:
        with conn:
            row = conn.execute("""
                SELECT * FROM clients 
                WHERE id = ?
                OR lower(name) LIKE lower(?)
                OR lower(client_code) = lower(?)
                OR lower(telegram) = lower(?)
                OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                ORDER BY id ASC LIMIT 1
            """, (client_id, f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()
            
            if not row:
                return None
            
            client_data = dict(row)
            accounts = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE client_id = ? AND status != 'reemplazada_caida'
                ORDER BY expiry_date ASC
            """, (client_data["id"],)).fetchall()
            
            client_data["accounts"] = [dict(a) for a in accounts]
            return client_data
    finally:
        conn.close()

def list_all_clients() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM clients ORDER BY name ASC").fetchall()
        result = []
        for r in rows:
            c = dict(r)
            accts = conn.execute("SELECT count(*) as count FROM streaming_accounts WHERE client_id = ? AND status = 'ocupada'", (c["id"],)).fetchone()
            c["active_accounts_count"] = accts["count"]
            result.append(c)
        return result
    finally:
        conn.close()

def get_client_360_profile(query_or_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Obtiene la Ficha 360° completa de un cliente: salud, LTV en ARS, cuentas activas, historial de pagos y cobro consolidado."""
    # Importación de servicios de mensajes dentro del método para evitar dependencias circulares
    from services.template_service import generate_whatsapp_message, generate_consolidated_billing_whatsapp

    conn = get_connection()
    try:
        with conn:
            client_row = None
            q = str(query_or_id).strip()
            if (isinstance(query_or_id, int) and query_or_id < 2_000_000_000) or (q.isdigit() and len(q) <= 9):
                try:
                    client_row = conn.execute("SELECT * FROM clients WHERE id = ?", (int(q),)).fetchone()
                except (ValueError, OverflowError):
                    client_row = None

            if not client_row:
                clean_q = q.replace(" ", "").replace("-", "")
                client_row = conn.execute("""
                    SELECT * FROM clients
                    WHERE lower(name) LIKE lower(?)
                    OR lower(client_code) = lower(?)
                    OR lower(telegram) = lower(?)
                    OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                    ORDER BY id ASC LIMIT 1
                """, (f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()

            if not client_row:
                return None

            client = dict(client_row)
            client_id = client["id"]
            client["clean_whatsapp"] = clean_whatsapp_phone(client.get("whatsapp", ""))
            ctype_raw = (client.get("client_type") or "").lower()
            if "vip" in ctype_raw:
                client["client_type_label"] = "👑 Revendedor VIP"
            elif "revend" in ctype_raw:
                client["client_type_label"] = "💼 Revendedor"
            else:
                client["client_type_label"] = "👤 Consumidor Final"

            # 2. Cuentas asociadas
            acc_rows = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE client_id = ?
                ORDER BY expiry_date ASC
            """, (client_id,)).fetchall()

            active_accounts = []
            past_accounts = []
            has_debt = False
            has_expiring_soon = False

            for r in acc_rows:
                a = dict(r)
                price_num = parse_money(a.get("price"))
                cost_num = parse_money(a.get("cost"))
                a["price_num"] = price_num
                a["cost_num"] = cost_num
                a["price_formatted"] = format_ars(price_num)
                a["cost_formatted"] = format_ars(cost_num)

                days_rem = None
                days_lbl = "Sin fecha"
                badge_class = "badge-ok"
                if a.get("expiry_date"):
                    try:
                        exp = datetime.strptime(a["expiry_date"], "%Y-%m-%d").date()
                        diff = (exp - date.today()).days
                        days_rem = diff
                        if diff < 0:
                            days_lbl = f"Vencida hace {abs(diff)}d"
                            badge_class = "badge-danger"
                            has_debt = True
                        elif diff == 0:
                            days_lbl = "¡Vence HOY!"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        elif diff == 1:
                            days_lbl = "Vence mañana"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        elif diff == 2:
                            days_lbl = "Vence en 2 días"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        else:
                            days_lbl = f"Vence en {diff} días"
                            badge_class = "badge-ok"
                    except Exception:
                        pass

                a["days_remaining"] = days_rem
                a["days_label"] = days_lbl
                a["badge_class"] = badge_class

                if a.get("payment_status") == "pendiente":
                    has_debt = True

                # Generar links individuales
                wa_cobro = generate_whatsapp_message(a, message_type="cobro")
                a["wa_cobro_link"] = wa_cobro.get("wa_link", "")
                wa_entrega = generate_whatsapp_message(a, message_type="entrega")
                a["wa_entrega_link"] = wa_entrega.get("wa_link", "")

                if a.get("status") in ("ocupada", "vencida", "caida"):
                    active_accounts.append(a)
                else:
                    past_accounts.append(a)

            # 3. Historial de Pagos y LTV
            pay_rows = conn.execute("""
                SELECT p.*, a.platform as account_platform, a.email as account_email
                FROM payments p
                LEFT JOIN streaming_accounts a ON p.account_id = a.id
                WHERE p.client_id = ?
                ORDER BY p.created_at DESC
            """, (client_id,)).fetchall()

            payments = []
            ltv_amount = 0.0
            total_profit = 0.0
            total_cost = 0.0

            for pr in pay_rows:
                p = dict(pr)
                amt = float(p.get("amount") or 0.0)
                prof = float(p.get("profit") or 0.0)
                cst = float(p.get("cost") or 0.0)
                ltv_amount += amt
                total_profit += prof
                total_cost += cst

                p["amount_formatted"] = format_ars(amt)
                p["profit_formatted"] = f"+{format_ars(prof)}" if prof >= 0 else format_ars(prof)
                p["cost_formatted"] = format_ars(cst)
                payments.append(p)

            monthly_spend = sum(a["price_num"] for a in active_accounts)

            # 4. Semáforo de Salud
            if has_debt:
                health = {
                    "code": "moroso",
                    "label": "🔴 Con Deuda / Vencido",
                    "badge_class": "badge-danger",
                    "summary": "Tiene servicios vencidos o cobros pendientes."
                }
            elif has_expiring_soon:
                health = {
                    "code": "por_vencer",
                    "label": "🟡 Por Vencer (Próximas 48hs)",
                    "badge_class": "badge-warn",
                    "summary": "Tiene servicios próximos a vencer."
                }
            elif active_accounts:
                health = {
                    "code": "al_dia",
                    "label": "🟢 Al Día",
                    "badge_class": "badge-ok",
                    "summary": "Todas sus suscripciones activas están al día."
                }
            else:
                health = {
                    "code": "sin_servicios",
                    "label": "⚪ Sin Servicios Activos",
                    "badge_class": "badge-secondary",
                    "summary": "No tiene servicios activos en este momento."
                }

            profile_data = {
                "client": client,
                "health_status": health,
                "financial_kpis": {
                    "ltv_amount": ltv_amount,
                    "ltv_formatted": format_ars(ltv_amount),
                    "total_profit": total_profit,
                    "total_profit_formatted": f"+{format_ars(total_profit)}" if total_profit >= 0 else format_ars(total_profit),
                    "total_cost": total_cost,
                    "total_cost_formatted": format_ars(total_cost),
                    "payments_count": len(payments),
                    "monthly_committed_spend": monthly_spend,
                    "monthly_committed_spend_formatted": format_ars(monthly_spend),
                    "last_payment": payments[0] if payments else None
                },
                "active_accounts": active_accounts,
                "past_accounts": past_accounts,
                "payments_history": payments[:20]
            }

            # 5. Cobro consolidado
            billing = generate_consolidated_billing_whatsapp(profile_data)
            profile_data["consolidated_billing"] = billing

            return profile_data
    finally:
        conn.close()

def get_client_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """Busca un cliente por su número de teléfono (comparando los últimos 8 dígitos) e incluye su Ficha 360 y cuentas."""
    clean = clean_whatsapp_phone(phone)
    if not clean or len(clean) < 6:
        return None
    suffix = clean[-8:]
    conn = get_connection()
    try:
        clients = conn.execute("SELECT id, whatsapp FROM clients WHERE whatsapp IS NOT NULL AND whatsapp != ''").fetchall()
        for c in clients:
            c_clean = clean_whatsapp_phone(c["whatsapp"] or "")
            if c_clean and (c_clean.endswith(suffix) or clean.endswith(c_clean[-8:])):
                return get_client_360_profile(c["id"])
        return None
    finally:
        conn.close()

def update_client_type(client_id_or_query: Union[int, str], new_type: str = "revendedor") -> Optional[Dict[str, Any]]:
    """Actualiza el tipo de cliente ('revendedor' o 'consumidor_final') por ID, nombre o código."""
    conn = get_connection()
    c_type = "revendedor" if "revend" in new_type.lower() else "consumidor_final"
    ident = str(client_id_or_query).strip()
    try:
        with conn:
            client = None
            if ident.isdigit():
                client = conn.execute("SELECT * FROM clients WHERE id = ?", (int(ident),)).fetchone()
            if not client:
                client = conn.execute("""
                    SELECT * FROM clients 
                    WHERE lower(client_code) = lower(?) OR lower(name) = lower(?)
                    LIMIT 1
                """, (ident, ident)).fetchone()
            if not client:
                return None
            conn.execute("UPDATE clients SET client_type = ? WHERE id = ?", (c_type, client["id"]))
            row = conn.execute("SELECT * FROM clients WHERE id = ?", (client["id"],)).fetchone()
            return dict(row)
    finally:
        conn.close()
