import re
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any

from db.connection import get_connection
import database
from telegram_bot import send_telegram_message

logger = logging.getLogger("services.http_custom")


def parse_date_to_iso(date_str: str) -> Optional[str]:
    """Convierte fechas en formato DD/MM/YYYY o DD-MM-YYYY a ISO YYYY-MM-DD."""
    if not date_str:
        return None
    clean = date_str.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", clean)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        d = date(year, month, day)
        return d.isoformat()
    except ValueError:
        return None


def parse_http_custom_message(text: str) -> Optional[Dict[str, Any]]:
    """Analiza un mensaje enviado al cliente y determina si es un alta o renovación de HTTP Custom (HWID).
    
    Formatos reconocidos:
    1) Venta Nueva:
       USUARIO : kevintj
       HWID    : 00d12f8f5e92c189d8005ddb60614cf9
       VALIDEZ : 18/09/2026

    2) Renovación:
       ID/CLIENTE   : 17 / kevintj 
        📱 PERMITIDOS : HWID 
        VALIDO HASTA : 19/10/2026
        RENUEVA EN 31 DIAS, DISFRUTE SU ESTANCIA!.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()

    # 1. Evaluación de Renovación (ID/CLIENTE + VALIDO HASTA)
    # Detecta "VALIDO HASTA : DD/MM/YYYY"
    m_renov_hasta = re.search(r'VALIDO\s+HASTA\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw, re.IGNORECASE)
    if m_renov_hasta:
        expiry_iso = parse_date_to_iso(m_renov_hasta.group(1))
        # Extraer usuario de ID/CLIENTE : 17 / kevintj o ID/CLIENTE : kevintj
        m_renov_user = re.search(r'(?:ID\s*/\s*CLIENTE|CLIENTE)\s*:\s*(?:(?:\d+)\s*[/|-]\s*)?([^\r\n]+)', raw, re.IGNORECASE)
        username = ""
        if m_renov_user:
            raw_u = m_renov_user.group(1).strip()
            # Si aún quedara un slot numérico separado por barra:
            if "/" in raw_u:
                username = raw_u.split("/")[-1].strip()
            else:
                username = raw_u

        # Validar si tiene indicadores de HTTP Custom
        upper_text = raw.upper()
        is_http_custom = (
            "HWID" in upper_text or
            "PERMITIDOS" in upper_text or
            "RENUEVA EN" in upper_text or
            "DISFRUTE SU ESTANCIA" in upper_text or
            "ID/CLIENTE" in upper_text
        )

        if is_http_custom and expiry_iso:
            return {
                "action": "renewal",
                "username": username,
                "expiry_date": expiry_iso,
                "raw_date": m_renov_hasta.group(1)
            }

    # 2. Evaluación de Venta Nueva (USUARIO + HWID + VALIDEZ)
    m_user = re.search(r'(?:USUARIO|USER)\s*:\s*([^\r\n]+)', raw, re.IGNORECASE)
    # HWID suele ser un hash hexadecimal o alfanumérico (ej: 00d12f8f5e92c189d8005ddb60614cf9)
    m_hwid = re.search(r'HWID\s*:\s*([a-zA-Z0-9_\-]{8,64})', raw, re.IGNORECASE)
    m_validez = re.search(r'(?:VALIDEZ|VALIDO|VENCE|VENCIMIENTO)\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw, re.IGNORECASE)

    if m_user and m_hwid and m_validez:
        hwid_candidate = m_hwid.group(1).strip()
        # Asegurar que no sea la palabra 'HWID' como en 'PERMITIDOS: HWID'
        if hwid_candidate.lower() != "hwid":
            expiry_iso = parse_date_to_iso(m_validez.group(1))
            if expiry_iso:
                return {
                    "action": "new_sale",
                    "username": m_user.group(1).strip(),
                    "hwid": hwid_candidate,
                    "expiry_date": expiry_iso,
                    "raw_date": m_validez.group(1)
                }

    return None


async def process_http_custom_outgoing_message(recipient_phone: str, text: str, source: str = "whatsapp") -> Dict[str, Any]:
    """Procesa un mensaje saliente detectado de HTTP Custom, registrando venta o renovación y actualizando finanzas."""
    parsed = parse_http_custom_message(text)
    if not parsed:
        return {"status": "ignored", "reason": "not_http_custom"}

    action = parsed["action"]
    username = parsed.get("username") or ""
    expiry_date = parsed["expiry_date"]
    hwid = parsed.get("hwid") or ""

    clean_phone = database.clean_whatsapp_phone(recipient_phone) if recipient_phone else ""

    # 1. Localizar o registrar al cliente en el CRM
    client = None
    if clean_phone:
        client = database.search_client(clean_phone)
    if not client and username:
        client = database.search_client(username)

    if not client:
        client_name = username if username else (f"Cliente {clean_phone[-4:]}" if clean_phone else "Cliente HTTP Custom")
        client = database.find_or_create_client(
            name=client_name,
            whatsapp=clean_phone,
            client_type="consumidor_final",
            notes=f"Cliente detectado automáticamente por venta HTTP Custom ({source})"
        )
    elif username and (not client.get("name") or client.get("name").lower().startswith("cliente") or client.get("name").lower().startswith("whatsapp")):
        # Si el cliente tenía un nombre provisorio, actualizarlo con el nombre de usuario
        conn = get_connection()
        try:
            with conn:
                conn.execute("UPDATE clients SET name = ? WHERE id = ?", (username, client["id"]))
                client["name"] = username
        finally:
            conn.close()

    client_id = client["id"]
    client_name = client.get("name") or username or "Cliente"
    client_type = (client.get("client_type") or "consumidor_final").lower()

    # 2. Determinar precio sugerido según tipo de cliente
    s_price, s_cost = database.get_suggested_price("HTTP Custom", "hwid", client_type)
    if s_price > 0.0:
        price = s_price
        cost = s_cost
    else:
        # Fallbacks de tarifas según especificación del usuario
        if "vip" in client_type:
            price = 3500.0
        elif "revend" in client_type:
            price = 4500.0
        else:
            price = 8000.0
        cost = 0.0

    profit = price - cost

    if "vip" in client_type:
        client_type_label = "👑 Revendedor VIP"
    elif "revend" in client_type:
        client_type_label = "💼 Revendedor"
    else:
        client_type_label = "👤 Consumidor Final"

    acc_id = None
    conn = get_connection()

    try:
        with conn:
            if action == "new_sale":
                # Buscar si ya existe una cuenta HTTP Custom para este usuario o cliente
                existing_acc = conn.execute("""
                    SELECT * FROM streaming_accounts
                    WHERE platform = 'HTTP Custom' AND (lower(email) = lower(?) OR client_id = ?)
                    ORDER BY id DESC LIMIT 1
                """, (username, client_id)).fetchone()

                if existing_acc:
                    acc_id = existing_acc["id"]
                    conn.execute("""
                        UPDATE streaming_accounts
                        SET password = ?, expiry_date = ?, client_id = ?,
                            payment_status = 'pagado', status = 'ocupada',
                            price = ?, cost = ?, notes = notes || ? , updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (hwid, expiry_date, client_id, str(price), str(cost), f" | Venta auto {date.today().isoformat()}", acc_id))
                else:
                    cursor = conn.execute("""
                        INSERT INTO streaming_accounts (
                            platform, email, password, profile_name, client_id,
                            status, payment_status, start_date, expiry_date, recurrence,
                            price, cost, notes
                        ) VALUES (?, ?, ?, 'HWID', ?, 'ocupada', 'pagado', ?, ?, 'mensual', ?, ?, ?)
                    """, (
                        "HTTP Custom", username, hwid, client_id,
                        date.today().isoformat(), expiry_date, str(price), str(cost),
                        f"Alta automática por mensaje WhatsApp ({date.today().isoformat()})"
                    ))
                    acc_id = cursor.lastrowid

                # Registrar en pagos / finanzas
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'WhatsApp Auto', ?)
                """, (acc_id, client_id, price, cost, profit, f"Venta HTTP Custom - Usuario: {username}"))

            else:
                # Renovación: Buscar la cuenta del cliente
                target_acc = conn.execute("""
                    SELECT * FROM streaming_accounts
                    WHERE platform = 'HTTP Custom' AND client_id = ?
                    ORDER BY CASE WHEN lower(email) = lower(?) THEN 0 ELSE 1 END, id DESC
                    LIMIT 1
                """, (client_id, username)).fetchone()

                if not target_acc and username:
                    target_acc = conn.execute("""
                        SELECT * FROM streaming_accounts
                        WHERE platform = 'HTTP Custom' AND lower(email) = lower(?)
                        ORDER BY id DESC LIMIT 1
                    """, (username,)).fetchone()

                if target_acc:
                    acc_id = target_acc["id"]
                    # Actualizar cuenta y fecha de vencimiento
                    conn.execute("""
                        UPDATE streaming_accounts
                        SET previous_expiry_date = expiry_date, expiry_date = ?,
                            payment_status = 'pagado', status = 'ocupada',
                            debt_balance = 0.0, last_alert_sent = '', client_id = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (expiry_date, client_id, acc_id))
                else:
                    # Si no existía, crearla directamente
                    cursor = conn.execute("""
                        INSERT INTO streaming_accounts (
                            platform, email, password, profile_name, client_id,
                            status, payment_status, start_date, expiry_date, recurrence,
                            price, cost, notes
                        ) VALUES (?, ?, 'HWID', 'HWID', ?, 'ocupada', 'pagado', ?, ?, 'mensual', ?, ?, ?)
                    """, (
                        "HTTP Custom", username or "Usuario", client_id,
                        date.today().isoformat(), expiry_date, str(price), str(cost),
                        f"Renovación directa auto por WhatsApp ({date.today().isoformat()})"
                    ))
                    acc_id = cursor.lastrowid

                # Registrar pago de renovación
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'WhatsApp Auto', ?)
                """, (acc_id, client_id, price, cost, profit, f"Renovación HTTP Custom - Usuario: {username}"))

            # 3. Auto-aprobar comprobantes pendientes de este cliente si existieran
            cursor_pen = conn.execute("""
                UPDATE pending_payments
                SET status = 'approved', account_id = ?, notes = notes || ' | Aprobado auto por registro HTTP Custom'
                WHERE status = 'pending' AND (client_id = ? OR (length(sender_phone) > 6 AND ? LIKE '%' || substr(sender_phone, -8)))
            """, (acc_id, client_id, clean_phone if clean_phone else "____"))
            approved_pending_count = cursor_pen.rowcount
    finally:
        conn.close()

    # 4. Enviar Notificación a Telegram
    action_title = "🚀 <b>NUEVA VENTA HTTP CUSTOM REGISTRADA</b>" if action == "new_sale" else "🔄 <b>RENOVACIÓN HTTP CUSTOM REGISTRADA</b>"
    hwid_line = f"• <b>HWID:</b> <code>{hwid}</code>\n" if hwid else ""
    receipt_line = f"• <b>Comprobante previo:</b> ✅ Aprobado automáticamente (#{approved_pending_count})\n" if approved_pending_count > 0 else ""

    tg_msg = (
        f"{action_title}\n\n"
        f"• <b>Cliente:</b> {client_name} ({client_type_label})\n"
        f"• <b>WhatsApp:</b> +{clean_phone}\n"
        f"• <b>Usuario:</b> <code>{username}</code>\n"
        f"{hwid_line}"
        f"• <b>Vencimiento:</b> {expiry_date}\n"
        f"• <b>Monto Registrado:</b> {database.format_ars(price)}\n"
        f"• <b>Ganancia Neta:</b> +{database.format_ars(profit)}\n"
        f"{receipt_line}"
        f"• <b>Origen:</b> Detección automática {source}"
    )
    try:
        await send_telegram_message(tg_msg)
    except Exception as e:
        logger.warning(f"No se pudo enviar notificación a Telegram para HTTP Custom: {e}")

    logger.info(f"HTTP Custom {action} procesado con éxito para cliente {client_name} (acc_id={acc_id}, importe={price})")
    return {
        "status": "success",
        "action": action,
        "account_id": acc_id,
        "client_id": client_id,
        "client_name": client_name,
        "client_type": client_type,
        "price": price,
        "profit": profit,
        "expiry_date": expiry_date
    }
