import urllib.parse
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple

from db.connection import get_connection
from db.repositories.clients_repo import find_or_create_client
from core.utils import parse_money, format_ars, clean_whatsapp_phone

def get_price_catalog() -> List[Dict[str, Any]]:
    """Devuelve la lista completa de precios oficiales por plataforma en Pesos Argentinos (ARS)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM price_catalog
            ORDER BY platform ASC, service_type ASC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            profit_final = d["price_final"] - d["cost_price"]
            profit_reseller = d["price_reseller"] - d["cost_price"]
            d["profit_final"] = profit_final
            d["profit_reseller"] = profit_reseller
            d["cost_price_formatted"] = format_ars(d["cost_price"])
            d["price_final_formatted"] = format_ars(d["price_final"])
            d["price_reseller_formatted"] = format_ars(d["price_reseller"])
            result.append(d)
        return result
    finally:
        conn.close()

def upsert_catalog_price(
    platform: str,
    service_type: str = "pantalla",
    cost_price: Union[float, str] = 0.0,
    price_final: Union[float, str] = 0.0,
    price_reseller: Union[float, str] = 0.0,
    notes: str = ""
) -> Dict[str, Any]:
    """Crea o actualiza un precio sugerido en el catálogo para una plataforma y tipo de servicio."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_stype = service_type.strip().lower()
    if clean_stype not in ("pantalla", "cuenta_completa"):
        clean_stype = "pantalla"
    
    cost_val = parse_money(cost_price)
    final_val = parse_money(price_final)
    reseller_val = parse_money(price_reseller)
    
    try:
        with conn:
            conn.execute("""
                INSERT INTO price_catalog (platform, service_type, cost_price, price_final, price_reseller, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, service_type) DO UPDATE SET
                    cost_price = excluded.cost_price,
                    price_final = excluded.price_final,
                    price_reseller = excluded.price_reseller,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_platform, clean_stype, cost_val, final_val, reseller_val, notes.strip()))
            
            row = conn.execute("""
                SELECT * FROM price_catalog
                WHERE platform = ? AND service_type = ?
            """, (clean_platform, clean_stype)).fetchone()
            return dict(row)
    finally:
        conn.close()

def delete_catalog_price(price_id: int) -> bool:
    """Elimina una entrada del catálogo de precios."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM price_catalog WHERE id = ?", (price_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_suggested_price(
    platform: str,
    service_type: str = "pantalla",
    client_type: str = "consumidor_final"
) -> Tuple[float, float]:
    """Obtiene el precio de venta sugerido y el costo del catálogo para una plataforma y tipo de servicio.
    Retorna (precio_venta, costo) en Pesos Argentinos (ARS)."""
    conn = get_connection()
    clean_plat = platform.strip().lower()
    clean_stype = service_type.strip().lower()
    is_reseller = "revend" in client_type.lower()
    
    # Detección inteligente de tipo de servicio si viene en el texto de la plataforma
    if any(k in clean_plat for k in ["casa extra", "pantalla", "perfil", "miembro extra"]):
        clean_stype = "pantalla"
    elif any(k in clean_plat for k in ["completa", "full hd", "4k", "cuenta entera", "4 pantallas"]):
        clean_stype = "cuenta_completa"
        
    try:
        # 1. Búsqueda exacta por plataforma y service_type
        row = conn.execute("""
            SELECT cost_price, price_final, price_reseller
            FROM price_catalog
            WHERE lower(platform) = ? AND service_type = ?
            LIMIT 1
        """, (clean_plat, clean_stype)).fetchone()
        
        # 2. Si es Netflix y busca pantalla -> buscar 'Netflix (Casa Extra)' o similar
        if not row and "netflix" in clean_plat:
            target_plat = "%casa extra%" if clean_stype == "pantalla" else "%completa%"
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller
                FROM price_catalog
                WHERE lower(platform) LIKE ? AND service_type = ?
                LIMIT 1
            """, (target_plat, clean_stype)).fetchone()

        # 3. Búsqueda parcial por plataforma
        if not row:
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller
                FROM price_catalog
                WHERE (lower(platform) LIKE ? OR ? LIKE '%' || lower(platform) || '%')
                  AND service_type = ?
                LIMIT 1
            """, (f"%{clean_plat}%", clean_plat, clean_stype)).fetchone()

        # 4. Fallback a cualquier registro de la plataforma
        if not row:
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller
                FROM price_catalog
                WHERE lower(platform) LIKE ? OR ? LIKE '%' || lower(platform) || '%'
                ORDER BY CASE WHEN service_type = ? THEN 0 ELSE 1 END
                LIMIT 1
            """, (f"%{clean_plat}%", clean_plat, clean_stype)).fetchone()
            
        if row:
            sale_price = row["price_reseller"] if is_reseller else row["price_final"]
            cost = row["cost_price"]
            return float(sale_price), float(cost)
        return 0.0, 0.0
    finally:
        conn.close()


def get_combos(only_active: bool = False) -> List[Dict[str, Any]]:
    """Lista todos los combos configurados con sus plataformas asociadas y precios en ARS."""
    conn = get_connection()
    try:
        query = "SELECT * FROM combos"
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY name ASC"
        
        combos_rows = conn.execute(query).fetchall()
        result = []
        for c in combos_rows:
            combo = dict(c)
            items_rows = conn.execute("""
                SELECT * FROM combo_items WHERE combo_id = ?
            """, (combo["id"],)).fetchall()
            combo["items"] = [dict(it) for it in items_rows]
            combo["platforms_list"] = [it["platform"] for it in combo["items"]]
            combo["platforms_str"] = " + ".join(combo["platforms_list"])
            combo["price_final_formatted"] = format_ars(combo["price_final"])
            combo["price_reseller_formatted"] = format_ars(combo["price_reseller"])
            result.append(combo)
        return result
    finally:
        conn.close()

def create_or_update_combo(
    name: str,
    description: str,
    price_final: Union[float, str],
    price_reseller: Union[float, str],
    platforms: List[Union[str, Dict[str, str]]],
    combo_id: Optional[int] = None
) -> Dict[str, Any]:
    """Crea o actualiza un combo promocional compuesto por varias plataformas."""
    conn = get_connection()
    clean_name = name.strip()
    p_final = parse_money(price_final)
    p_reseller = parse_money(price_reseller)
    
    try:
        with conn:
            if combo_id:
                conn.execute("""
                    UPDATE combos
                    SET name = ?, description = ?, price_final = ?, price_reseller = ?
                    WHERE id = ?
                """, (clean_name, description.strip(), p_final, p_reseller, combo_id))
                cid = combo_id
                conn.execute("DELETE FROM combo_items WHERE combo_id = ?", (cid,))
            else:
                cursor = conn.execute("""
                    INSERT INTO combos (name, description, price_final, price_reseller)
                    VALUES (?, ?, ?, ?)
                """, (clean_name, description.strip(), p_final, p_reseller))
                cid = cursor.lastrowid
                
            for p in platforms:
                if isinstance(p, dict):
                    plat_name = p.get("platform", "").strip().title()
                    stype = p.get("service_type", "pantalla").strip().lower()
                else:
                    plat_name = str(p).strip().title()
                    stype = "pantalla"
                
                if plat_name:
                    conn.execute("""
                        INSERT INTO combo_items (combo_id, platform, service_type)
                        VALUES (?, ?, ?)
                    """, (cid, plat_name, stype))
                    
            return {"success": True, "combo_id": cid, "name": clean_name}
    finally:
        conn.close()

def delete_combo(combo_id: int) -> bool:
    """Elimina un combo y sus elementos asociados."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM combo_items WHERE combo_id = ?", (combo_id,))
            cursor = conn.execute("DELETE FROM combos WHERE id = ?", (combo_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def sell_combo(
    combo_name_or_id: Union[str, int],
    client_name: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    payment_method: str = "Transferencia",
    duration_days: int = 30,
    notes: str = ""
) -> Dict[str, Any]:
    """Vende un combo completo en un solo paso."""
    conn = get_connection()
    try:
        c_query = str(combo_name_or_id).strip()
        if c_query.isdigit():
            combo_row = conn.execute("SELECT * FROM combos WHERE id = ?", (int(c_query),)).fetchone()
        else:
            combo_row = conn.execute("SELECT * FROM combos WHERE lower(name) = lower(?)", (c_query,)).fetchone()
            
        if not combo_row:
            return {"success": False, "error": f"No se encontró el combo '{combo_name_or_id}'"}
            
        combo = dict(combo_row)
        combo_id = combo["id"]
        
        items = conn.execute("SELECT * FROM combo_items WHERE combo_id = ?", (combo_id,)).fetchall()
        if not items:
            return {"success": False, "error": f"El combo '{combo['name']}' no tiene plataformas asociadas."}
            
        missing_stock = []
        needed_slots = []
        for it in items:
            p_name = it["platform"]
            slot = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (p_name,)).fetchone()
            
            if not slot:
                missing_stock.append(p_name)
            else:
                needed_slots.append((it, dict(slot)))
                
        if missing_stock:
            return {
                "success": False,
                "error": f"Stock insuficiente para completar el combo '{combo['name']}'. Cuentas faltantes: {', '.join(missing_stock)}"
            }
            
        client = find_or_create_client(
            name=client_name,
            whatsapp=whatsapp,
            telegram=telegram,
            client_type=client_type,
            notes=notes
        )
        
        today = date.today()
        expiry = today + timedelta(days=duration_days)
        expiry_str = expiry.isoformat()
        today_str = today.isoformat()
        
        is_reseller = "revend" in client_type.lower()
        combo_sale_price = combo["price_reseller"] if is_reseller else combo["price_final"]
        
        assigned_accounts = []
        total_costs = 0.0
        
        with conn:
            for item, slot in needed_slots:
                slot_id = slot["id"]
                slot_cost = parse_money(slot.get("cost"))
                if slot_cost == 0.0:
                    _, cat_cost = get_suggested_price(item["platform"], item.get("service_type", "pantalla"), client_type)
                    slot_cost = cat_cost
                total_costs += slot_cost
                
                conn.execute("""
                    UPDATE streaming_accounts
                    SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                        start_date = ?, expiry_date = ?, price = ?, notes = ?,
                        last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    client["id"],
                    today_str,
                    expiry_str,
                    format_ars(combo_sale_price / len(needed_slots)),
                    f"Venta Combo: {combo['name']}. {notes}".strip(),
                    slot_id
                ))
                
                fresh = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                """, (slot_id,)).fetchone()
                assigned_accounts.append(dict(fresh))
                
            profit = combo_sale_price - total_costs
            cursor = conn.execute("""
                INSERT INTO payments (client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                client["id"],
                combo_sale_price,
                total_costs,
                profit,
                payment_method,
                f"Combo {combo['name']} ({len(assigned_accounts)} servicios)"
            ))
            payment_id = cursor.lastrowid

        clean_phone = clean_whatsapp_phone(whatsapp)
        
        msg_lines = [
            f"🍿 *¡Hola {client['name']}!* ¡Gracias por tu compra!",
            f"Aquí tienes los accesos a tu *{combo['name']}*:\n"
        ]
        
        for idx, acc in enumerate(assigned_accounts, 1):
            prof = f" | 👤 *Perfil:* {acc['profile_name']}" if acc.get('profile_name') else ""
            pin = f" | 🔒 *PIN:* {acc['profile_pin']}" if acc.get('profile_pin') else ""
            msg_lines.append(
                f"📺 *{idx}. {acc['platform']}*\n"
                f"📧 *Correo:* `{acc['email']}`\n"
                f"🔑 *Clave:* `{acc['password']}`"
                f"{prof}{pin}\n"
            )
            
        msg_lines.append(f"📅 *Vencimiento del Combo:* {expiry_str}")
        msg_lines.append(f"💰 *Total Abonado:* {format_ars(combo_sale_price)}")
        msg_lines.append(f"💳 *Medio de Pago:* {payment_method}\n")
        msg_lines.append("⚠️ *Reglas:* No modificar contraseñas ni perfiles ajenos para conservar la garantía activa.\n")
        msg_lines.append("¡Que disfrutes de tus series y películas! 🚀✨")
        
        full_msg = "\n".join(msg_lines)
        encoded_text = urllib.parse.quote(full_msg)
        wa_link = f"https://wa.me/{clean_phone}?text={encoded_text}" if clean_phone else f"https://wa.me/?text={encoded_text}"
        
        return {
            "success": True,
            "combo_name": combo["name"],
            "client_name": client["name"],
            "accounts": assigned_accounts,
            "amount": combo_sale_price,
            "cost": total_costs,
            "profit": profit,
            "expiry_date": expiry_str,
            "whatsapp_message": full_msg,
            "wa_link": wa_link,
            "payment_id": payment_id
        }
    finally:
        conn.close()
