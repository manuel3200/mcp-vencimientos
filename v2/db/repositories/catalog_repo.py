import logging
import urllib.parse
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple

from db.connection import get_connection
from db.repositories.clients_repo import find_or_create_client
from core.utils import parse_money, format_ars, clean_whatsapp_phone

logger = logging.getLogger("db.catalog_repo")

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
            price_vip = float(d.get("price_reseller_vip") or 0.0)
            profit_reseller_vip = (price_vip - d["cost_price"]) if price_vip > 0 else 0.0
            d["profit_final"] = profit_final
            d["profit_reseller"] = profit_reseller
            d["price_reseller_vip"] = price_vip
            d["profit_reseller_vip"] = profit_reseller_vip
            d["cost_price_formatted"] = format_ars(d["cost_price"])
            d["price_final_formatted"] = format_ars(d["price_final"])
            d["price_reseller_formatted"] = format_ars(d["price_reseller"])
            d["price_reseller_vip_formatted"] = format_ars(price_vip) if price_vip > 0 else "-"
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
    price_reseller_vip: Union[float, str] = 0.0,
    notes: str = ""
) -> Dict[str, Any]:
    """Crea o actualiza un precio sugerido en el catálogo para una plataforma y tipo de servicio."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_stype = service_type.strip().lower()
    if clean_stype not in ("pantalla", "cuenta_completa", "hwid"):
        clean_stype = "pantalla"
    
    cost_val = parse_money(cost_price)
    final_val = parse_money(price_final)
    reseller_val = parse_money(price_reseller)
    reseller_vip_val = parse_money(price_reseller_vip)
    
    try:
        with conn:
            existing_row = conn.execute("""
                SELECT cost_price, price_final FROM price_catalog
                WHERE platform = ? AND service_type = ?
            """, (clean_platform, clean_stype)).fetchone()
            old_cost = float(existing_row["cost_price"]) if existing_row else 0.0

            conn.execute("""
                INSERT INTO price_catalog (platform, service_type, cost_price, price_final, price_reseller, price_reseller_vip, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, service_type) DO UPDATE SET
                    cost_price = excluded.cost_price,
                    price_final = excluded.price_final,
                    price_reseller = excluded.price_reseller,
                    price_reseller_vip = excluded.price_reseller_vip,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_platform, clean_stype, cost_val, final_val, reseller_val, reseller_vip_val, notes.strip()))
            
            row = conn.execute("""
                SELECT * FROM price_catalog
                WHERE platform = ? AND service_type = ?
            """, (clean_platform, clean_stype)).fetchone()

            if old_cost > 0 and cost_val > 0 and old_cost != cost_val:
                try:
                    import asyncio
                    from application.suppliers.cost_variance_service import evaluate_cost_variance
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(evaluate_cost_variance(
                            platform=clean_platform,
                            service_type=clean_stype,
                            old_cost=old_cost,
                            new_cost=cost_val,
                            current_sale_price=final_val,
                            notify_telegram=True,
                            actor="catalog_repo"
                        ))
                    except RuntimeError:
                        asyncio.run(evaluate_cost_variance(
                            platform=clean_platform,
                            service_type=clean_stype,
                            old_cost=old_cost,
                            new_cost=cost_val,
                            current_sale_price=final_val,
                            notify_telegram=True,
                            actor="catalog_repo"
                        ))
                except Exception as e:
                    logger.warning(f"Error evaluando variación de costo: {e}")

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
    is_vip = "vip" in client_type.lower()
    is_reseller = "revend" in client_type.lower() or is_vip
    
    # Detección inteligente de tipo de servicio si viene en el texto de la plataforma
    if any(k in clean_plat for k in ["custom", "hwid", "vpn", "ssh"]):
        clean_stype = "hwid"
    elif any(k in clean_plat for k in ["casa extra", "pantalla", "perfil", "miembro extra"]):
        clean_stype = "pantalla"
    elif any(k in clean_plat for k in ["completa", "full hd", "4k", "cuenta entera", "4 pantallas"]):
        clean_stype = "cuenta_completa"
        
    try:
        # 1. Búsqueda exacta por plataforma y service_type
        row = conn.execute("""
            SELECT cost_price, price_final, price_reseller, price_reseller_vip
            FROM price_catalog
            WHERE lower(platform) = ? AND service_type = ?
            LIMIT 1
        """, (clean_plat, clean_stype)).fetchone()
        
        # 2. Si es Netflix y busca pantalla -> buscar 'Netflix (Casa Extra)' o similar
        if not row and "netflix" in clean_plat:
            target_plat = "%casa extra%" if clean_stype == "pantalla" else "%completa%"
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller, price_reseller_vip
                FROM price_catalog
                WHERE lower(platform) LIKE ? AND service_type = ?
                LIMIT 1
            """, (target_plat, clean_stype)).fetchone()

        # 3. Búsqueda parcial por plataforma
        if not row:
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller, price_reseller_vip
                FROM price_catalog
                WHERE (lower(platform) LIKE ? OR ? LIKE '%' || lower(platform) || '%')
                  AND service_type = ?
                LIMIT 1
            """, (f"%{clean_plat}%", clean_plat, clean_stype)).fetchone()

        # 4. Fallback a cualquier registro de la plataforma
        if not row:
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller, price_reseller_vip
                FROM price_catalog
                WHERE lower(platform) LIKE ? OR ? LIKE '%' || lower(platform) || '%'
                ORDER BY CASE WHEN service_type = ? THEN 0 ELSE 1 END
                LIMIT 1
            """, (f"%{clean_plat}%", clean_plat, clean_stype)).fetchone()
            
        if row:
            row_dict = dict(row)
            p_vip = float(row_dict.get("price_reseller_vip") or 0.0)
            if is_vip and p_vip > 0:
                sale_price = p_vip
            elif is_reseller:
                sale_price = row["price_reseller"]
            else:
                sale_price = row["price_final"]
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


def generate_catalog_message(
    client_type: str = "consumidor_final",
    platform_filter: Optional[str] = None,
    include_payment_methods: bool = True
) -> str:
    """Genera un catálogo interactivo y profesional para WhatsApp con servicios, combos,
    precios vigentes y disponibilidad de stock en tiempo real.
    - client_type: 'consumidor_final' o 'revendedor' (ajusta tarifas automáticamente).
    - platform_filter: Si se pasa (ej: 'netflix'), genera una respuesta enfocada en esa plataforma.
    - include_payment_methods: Si incluye resumen de medios de pago al pie.
    """
    conn = get_connection()
    try:
        is_reseller = "revend" in str(client_type).lower()

        # 1. Obtener conteo de stock libre por plataforma
        free_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'libre'
            GROUP BY platform
        """).fetchall()
        free_stock_map: Dict[str, int] = {}
        for r in free_rows:
            p_name = r["platform"].strip().lower()
            free_stock_map[p_name] = r["count"]

        # 2. Obtener catálogo de precios
        cat_rows = conn.execute("""
            SELECT * FROM price_catalog
            ORDER BY platform ASC, service_type ASC
        """).fetchall()

        # Mapeo de emojis para plataformas
        def get_platform_emoji(name: str) -> str:
            n = name.lower()
            if "netflix" in n: return "🔴"
            if "disney" in n: return "🏰"
            if "max" in n or "hbo" in n: return "🟣"
            if "prime" in n or "amazon" in n: return "📦"
            if "spotify" in n: return "🟢"
            if "youtube" in n: return "▶️"
            if "paramount" in n: return "⛰️"
            if "crunchyroll" in n: return "🟠"
            if "apple" in n: return "🍏"
            if "star" in n: return "🌟"
            if "iptv" in n: return "📺"
            return "📺"

        # Función auxiliar para chequear stock libre
        def check_stock(plat_name: str) -> int:
            p_low = plat_name.strip().lower()
            if p_low in free_stock_map:
                return free_stock_map[p_low]
            total = 0
            for k, cnt in free_stock_map.items():
                if k in p_low or p_low in k:
                    total += cnt
            return total

        clean_filter = platform_filter.strip().lower() if platform_filter else None

        # CASO A: Filtro por plataforma específica (ej: 'netflix', 'disney', 'max')
        if clean_filter:
            matched_items = []
            for r in cat_rows:
                p_curr = r["platform"].strip().lower()
                st_curr = r["service_type"].strip().lower()
                if clean_filter in p_curr or clean_filter in st_curr or p_curr in clean_filter:
                    matched_items.append(dict(r))

            if matched_items:
                lines = []
                lines.append("🍿 *PLANES Y TARIFAS DISPONIBLES:*\n")
                if is_reseller:
                    lines.append("👔 _(Lista con precios mayoristas de Revendedor)_\n")

                for it in matched_items:
                    emoji = get_platform_emoji(it["platform"])
                    stype_label = "1 Pantalla / Perfil" if it["service_type"] == "pantalla" else "Cuenta Completa"
                    price_val = it["price_reseller"] if is_reseller else it["price_final"]
                    price_str = format_ars(price_val)
                    stock_qty = check_stock(it["platform"])
                    stock_badge = "✅ *Disponible* _(Entrega inmediata)_" if stock_qty > 0 else "⏳ *A pedido* _(Entrega rápida)_"

                    lines.append(f"{emoji} *{it['platform']}* ({stype_label})")
                    lines.append(f"• Tarifa: *{price_str}* / mes")
                    lines.append(f"• Stock: {stock_badge}")
                    if it.get("notes"):
                        lines.append(f"• Detalle: _{it['notes']}_")
                    lines.append("")

                if include_payment_methods:
                    lines.append("💳 *Medios de Pago:* Transferencia, Mercado Pago, Ualá, Naranja X.")
                    lines.append("\n👉 *¿Deseas activarlo?* Responde a este mensaje y te enviamos los datos para disfrutar de inmediato. ✨")
                return "\n".join(lines).strip()

        # CASO B: Catálogo Completo General
        lines = []
        lines.append("✨ *CATÁLOGO OFICIAL DE STREAMING* ✨")
        lines.append("🍿 _Cuentas y perfiles premium con garantía total de 30 días._\n")

        if is_reseller:
            lines.append("👔 *LISTA MAYORISTA PARA REVENDEDORES:*\n")
        else:
            lines.append("📺 *SERVICIOS DISPONIBLES:*\n")

        if cat_rows:
            for r in cat_rows:
                emoji = get_platform_emoji(r["platform"])
                stype_label = "1 Pantalla" if r["service_type"] == "pantalla" else "Completa"
                price_val = r["price_reseller"] if is_reseller else r["price_final"]
                price_str = format_ars(price_val)
                stock_qty = check_stock(r["platform"])
                stock_icon = "✅" if stock_qty > 0 else "⏳"
                lines.append(f"• {emoji} *{r['platform']}* ({stype_label}): *{price_str}* {stock_icon}")
            lines.append("\n_Referencias: ✅ Entrega inmediata | ⏳ A pedido_")
        else:
            lines.append("• 🔴 *Netflix*: $6.500 ✅")
            lines.append("• 🏰 *Disney+ Premium*: $4.500 ✅")
            lines.append("• 🟣 *Max Estándar*: $4.000 ✅")
            lines.append("• 📦 *Prime Video*: $3.500 ✅")
            lines.append("• 🟢 *Spotify Premium*: $3.800 ✅")

        # Combos Activos
        combos_list = get_combos(only_active=True)
        if combos_list:
            lines.append("\n🔥 *COMBOS Y PROMOS EXCLUSIVAS:*")
            for c in combos_list:
                c_price = c["price_reseller"] if is_reseller else c["price_final"]
                lines.append(f"• ⭐ *{c['name']}* ({c['platforms_str']}): *{format_ars(c_price)}*")
                if c.get("description"):
                    lines.append(f"  _{c['description']}_")

        if include_payment_methods:
            lines.append("\n💳 *MEDIOS DE PAGO:*")
            lines.append("• Mercado Pago, Transferencia Bancaria (CBU/CVU), Ualá, Naranja X y más.")

        lines.append("\n👉 *¿CÓMO CONTRATAR?*")
        lines.append("Escribe el nombre del servicio o combo que deseas contratar y te enviamos los datos de pago al instante para activarlo. 🚀")

        return "\n".join(lines).strip()
    finally:
        conn.close()
