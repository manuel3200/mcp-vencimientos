import os
import re
import sqlite3
import hashlib
import secrets
import time
import urllib.parse
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union

DB_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DB_DIR, "services.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# Helpers de Criptografía y Finanzas
# ==========================================
def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return key.hex(), salt

def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, stored_hash)

def parse_money(val: Union[str, float, int, None]) -> float:
    """Extrae el valor numérico flotante de cadenas como '$10 USD', '15000', '$7.50', etc."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    match = re.search(r'([0-9]+(?:[\.,][0-9]+)?)', str(val))
    if match:
        clean = match.group(1).replace(',', '.')
        try:
            return float(clean)
        except ValueError:
            return 0.0
    return 0.0

# ==========================================
# Inicialización y Esquema Relacional
# ==========================================
def init_db():
    """Inicializa la base de datos y crea las tablas necesarias."""
    conn = get_connection()
    try:
        with conn:
            # 1. Administradores y 2FA
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    totp_secret TEXT DEFAULT '',
                    telegram_otp TEXT DEFAULT '',
                    telegram_otp_expiry REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Clientes (CRM)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_code TEXT UNIQUE,
                    name TEXT NOT NULL,
                    whatsapp TEXT DEFAULT '',
                    telegram TEXT DEFAULT '',
                    client_type TEXT DEFAULT 'consumidor_final',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. Cuentas y Perfiles de Streaming
            conn.execute("""
                CREATE TABLE IF NOT EXISTS streaming_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    profile_name TEXT DEFAULT '',
                    profile_pin TEXT DEFAULT '',
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    status TEXT DEFAULT 'libre', -- 'libre', 'ocupada', 'caida', 'reemplazada_caida', 'vencida'
                    payment_status TEXT DEFAULT 'pagado', -- 'pagado', 'pendiente'
                    start_date TEXT DEFAULT '',
                    expiry_date TEXT DEFAULT '',
                    recurrence TEXT DEFAULT 'mensual',
                    price TEXT DEFAULT '',
                    cost TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    last_alert_sent TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. Tabla de Transacciones y Pagos (Finanzas)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES streaming_accounts(id) ON DELETE SET NULL,
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    amount REAL NOT NULL,
                    cost REAL DEFAULT 0.0,
                    profit REAL DEFAULT 0.0,
                    payment_method TEXT DEFAULT 'Transferencia',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migración: Agregar columna payment_status si no existe en bases de datos previas
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN payment_status TEXT DEFAULT 'pagado'")
            except Exception:
                pass
    finally:
        conn.close()

# ==========================================
# Gestión de Usuarios y 2FA
# ==========================================
def create_or_update_admin(username: str, password: str, totp_secret: str = ""):
    clean_user = username.strip().lower()
    p_hash, salt = hash_password(password.strip())
    conn = get_connection()
    try:
        with conn:
            existing = conn.execute("SELECT id, totp_secret FROM admin_users WHERE lower(username) = ?", (clean_user,)).fetchone()
            if existing:
                secret_to_use = totp_secret if totp_secret else existing["totp_secret"]
                conn.execute("""
                    UPDATE admin_users 
                    SET password_hash = ?, salt = ?, totp_secret = ?
                    WHERE lower(username) = ?
                """, (p_hash, salt, secret_to_use, clean_user))
            else:
                conn.execute("""
                    INSERT INTO admin_users (username, password_hash, salt, totp_secret)
                    VALUES (?, ?, ?, ?)
                """, (clean_user, p_hash, salt, totp_secret))
    finally:
        conn.close()

def get_admin_user(username: str) -> Optional[Dict[str, Any]]:
    clean_user = username.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM admin_users WHERE lower(username) = ?", (clean_user,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def verify_admin_credentials(username: str, password: str) -> bool:
    user = get_admin_user(username)
    if not user:
        return False
    return verify_password(password.strip(), user["password_hash"], user["salt"])

def set_telegram_otp(username: str, otp: str, duration_seconds: int = 300):
    clean_user = username.strip().lower()
    expiry = time.time() + duration_seconds
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE admin_users 
                SET telegram_otp = ?, telegram_otp_expiry = ?
                WHERE lower(username) = ?
            """, (otp.strip(), expiry, clean_user))
    finally:
        conn.close()

def verify_telegram_otp(username: str, otp: str) -> bool:
    clean_user = username.strip().lower()
    user = get_admin_user(clean_user)
    if not user:
        return False
    stored_otp = user.get("telegram_otp")
    expiry = user.get("telegram_otp_expiry", 0)
    
    if not stored_otp or time.time() > expiry:
        return False
        
    if secrets.compare_digest(stored_otp, otp.strip()):
        conn = get_connection()
        try:
            with conn:
                conn.execute("UPDATE admin_users SET telegram_otp = '', telegram_otp_expiry = 0 WHERE lower(username) = ?", (clean_user,))
        finally:
            conn.close()
        return True
    return False

# ==========================================
# CRM de Clientes
# ==========================================
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
    c_type = "revendedor" if "revend" in client_type.lower() else "consumidor_final"

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
                conn.execute("""
                    UPDATE clients 
                    SET whatsapp = CASE WHEN length(?) > 0 THEN ? ELSE whatsapp END,
                        telegram = CASE WHEN length(?) > 0 THEN ? ELSE telegram END,
                        client_type = ?,
                        notes = CASE WHEN length(?) > 0 THEN ? ELSE notes END
                    WHERE id = ?
                """, (clean_wa, clean_wa, clean_tg, clean_tg, c_type, notes.strip(), notes.strip(), client_id))
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

def search_client(query: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = query.strip()
    clean_q = q.replace(" ", "").replace("-", "")
    try:
        with conn:
            row = conn.execute("""
                SELECT * FROM clients 
                WHERE lower(name) LIKE lower(?)
                OR lower(client_code) = lower(?)
                OR lower(telegram) = lower(?)
                OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                ORDER BY id ASC LIMIT 1
            """, (f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()
            
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

# ==========================================
# Gestión de Cuentas y Ventas
# ==========================================
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
    client = find_or_create_client(
        name=client_name, 
        whatsapp=whatsapp, 
        telegram=telegram, 
        client_type=client_type,
        notes=notes
    )
    clean_platform = platform.strip().title()
    s_date = start_date.strip() if start_date else date.today().isoformat()
    
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
            price_num = parse_money(price)
            cost_num = parse_money(cost)
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

# ==========================================
# 5. Módulo de Finanzas, Pagos y Balance
# ==========================================
def register_customer_payment(
    email_or_id: str,
    amount: Optional[float] = None,
    payment_method: str = "Transferencia",
    new_expiry_date: Optional[str] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Registra el cobro de una mensualidad o renovación a un cliente y actualiza el balance."""
    conn = get_connection()
    q = email_or_id.strip()
    try:
        with conn:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) OR a.id = ?
                LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()

            if not row:
                return {"success": False, "error": f"No se encontró la cuenta '{email_or_id}'"}

            acc = dict(row)
            acc_id = acc["id"]
            client_id = acc.get("client_id")
            
            # Monto cobrado
            final_amount = amount if amount is not None else parse_money(acc.get("price"))
            cost_num = parse_money(acc.get("cost"))
            profit_num = final_amount - cost_num

            # Nueva fecha si se renueva
            if new_expiry_date:
                final_expiry = new_expiry_date.strip()
            else:
                try:
                    curr_exp = datetime.strptime(acc["expiry_date"], "%Y-%m-%d").date()
                    # Si ya estaba vencida, renueva 30 días desde hoy; si no, 30 días desde el vencimiento
                    base_date = max(curr_exp, date.today())
                    final_expiry = (base_date + timedelta(days=30)).isoformat()
                except Exception:
                    final_expiry = (date.today() + timedelta(days=30)).isoformat()

            # Insertar en tabla de pagos
            conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (acc_id, client_id, final_amount, cost_num, profit_num, payment_method.strip(), notes.strip() or "Renovación pagada"))

            # Actualizar cuenta como pagada con nuevo vencimiento
            conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, payment_status = 'pagado', status = 'ocupada',
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_expiry, acc_id))

            return {
                "success": True,
                "client_name": acc.get("client_name"),
                "platform": acc["platform"],
                "email": acc["email"],
                "amount": final_amount,
                "profit": profit_num,
                "new_expiry": final_expiry,
                "payment_method": payment_method
            }
    finally:
        conn.close()

def get_financial_balance(period: str = "mes_actual") -> Dict[str, Any]:
    """Calcula el balance completo: ingresos brutos, costos de proveedores, ganancia neta y proyecciones."""
    conn = get_connection()
    today = date.today()
    try:
        with conn:
            # 1. Pagos ya cobrados en el mes actual
            month_str = f"{today.year}-{today.month:02d}%"
            res = conn.execute("""
                SELECT 
                    COALESCE(SUM(amount), 0.0) as total_income,
                    COALESCE(SUM(cost), 0.0) as total_costs,
                    COALESCE(SUM(profit), 0.0) as net_profit,
                    COUNT(*) as total_transactions
                FROM payments
                WHERE created_at LIKE ?
            """, (month_str,)).fetchone()

            total_income = float(res["total_income"])
            total_costs = float(res["total_costs"])
            net_profit = float(res["net_profit"])
            tx_count = int(res["total_transactions"])

            # 2. Dinero por cobrar esta semana (cuentas activas que vencen en los próximos 7 días)
            active_accounts = get_active_accounts()
            pending_receivables_7d = 0.0
            pending_accounts_count = 0
            pending_list = []

            for a in active_accounts:
                days = a.get("days_remaining")
                if days is not None and -5 <= days <= 7:
                    price_val = parse_money(a.get("price"))
                    pending_receivables_7d += price_val
                    pending_accounts_count += 1
                    pending_list.append({
                        "client": a.get("client_name"),
                        "platform": a.get("platform"),
                        "email": a.get("email"),
                        "days_remaining": days,
                        "price": price_val,
                        "whatsapp": a.get("whatsapp"),
                        "telegram": a.get("telegram")
                    })

            # 3. Ganancia estimada mensual total si todos pagan
            monthly_projected_gross = sum(parse_money(a.get("price")) for a in active_accounts)
            monthly_projected_costs = sum(parse_money(a.get("cost")) for a in active_accounts)
            monthly_projected_profit = monthly_projected_gross - monthly_projected_costs

            return {
                "period": f"{today.strftime('%B %Y')}",
                "collected_income": total_income,
                "collected_costs": total_costs,
                "collected_profit": net_profit,
                "transactions_count": tx_count,
                "pending_receivables_7d": pending_receivables_7d,
                "pending_accounts_count": pending_accounts_count,
                "pending_accounts": pending_list,
                "projected_monthly_gross": monthly_projected_gross,
                "projected_monthly_profit": monthly_projected_profit,
                "active_subscriptions_total": len(active_accounts)
            }
    finally:
        conn.close()

def get_recent_transactions(limit: int = 15) -> List[Dict[str, Any]]:
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

# ==========================================
# 6. Módulo de Mensajes y Enlaces de WhatsApp (Paso 2)
# ==========================================
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

def clean_whatsapp_phone(phone: str) -> str:
    """Limpia y estandariza un número telefónico para el protocolo wa.me."""
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return ""
    # Manejo de números de Argentina si vienen en formato local (10 dígitos)
    if len(digits) == 10:
        if digits.startswith("15"):
            digits = "11" + digits[2:]
        digits = "549" + digits
    elif len(digits) == 12 and digits.startswith("54") and not digits.startswith("549"):
        digits = "549" + digits[2:]
    return digits

def generate_whatsapp_message(
    account_or_id: Union[int, str, Dict[str, Any]],
    message_type: str = "entrega",
    payment_methods: str = ""
) -> Dict[str, Any]:
    """Genera plantillas profesionales y enlaces directos con 1 clic para WhatsApp (wa.me)."""
    if isinstance(account_or_id, dict):
        acc = account_or_id
    else:
        acc = get_account_detail(account_or_id)
        if not acc:
            return {"success": False, "error": f"No se encontró la cuenta o servicio '{account_or_id}'"}

    client_name = acc.get("client_name") or "Estimado/a"
    platform = acc.get("platform") or "Streaming"
    email = acc.get("email") or ""
    password = acc.get("password") or ""
    profile = acc.get("profile_name") or ""
    pin = acc.get("profile_pin") or ""
    expiry = acc.get("expiry_date") or ""
    price = acc.get("price") or ""
    raw_phone = acc.get("whatsapp") or ""
    clean_phone = clean_whatsapp_phone(raw_phone)
    days_rem = acc.get("days_remaining")

    m_type = message_type.strip().lower()

    if m_type in ("cobro", "recordatorio", "vencimiento"):
        days_str = ""
        if days_rem is not None:
            if days_rem == 0:
                days_str = " (¡Vence HOY!)"
            elif days_rem > 0:
                days_str = f" (vence en {days_rem} días)"
            else:
                days_str = f" (vencida hace {abs(days_rem)} días)"

        default_pm = (
            "• Transferencia Bancaria / CVU / CBU\n"
            "• Mercado Pago\n"
            "• Binance USDT / Cripto"
        )
        pm_text = payment_methods.strip() if payment_methods else default_pm

        msg = (
            f"👋 *¡Hola {client_name}!* Esperamos que estés disfrutando tu suscripción.\n\n"
            f"Te recordamos que tu servicio está próximo a vencer:\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Cuenta:* `{email}`\n"
            f"📅 *Vencimiento:* {expiry}{days_str}\n"
            f"💰 *Monto a Renovar:* {price or 'Consultar valor'}\n\n"
            f"💳 *Métodos de Pago:*\n{pm_text}\n\n"
            f"Una vez abonado, por favor envíanos tu comprobante por aquí para renovarte inmediatamente sin cortes. ¡Muchas gracias! 🙌"
        )
    elif m_type in ("reemplazo", "soporte", "caida"):
        profile_line = f"\n👤 *Perfil:* {profile}" if profile else ""
        pin_line = f"\n🔒 *PIN de Perfil:* {pin}" if pin else ""
        msg = (
            f"🛠️ *¡Hola {client_name}!* Te informamos que hemos reactivado tu servicio.\n\n"
            f"✨ *Nuevos Datos de Acceso:*\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Nuevo Correo:* `{email}`\n"
            f"🔑 *Nueva Contraseña:* `{password}`"
            f"{profile_line}"
            f"{pin_line}\n"
            f"📅 *Mantiene Vencimiento:* {expiry}\n\n"
            f"📌 *Recomendación:* Recuerda utilizar únicamente el perfil asignado y no modificar la clave.\n\n"
            f"¡Ya puedes continuar disfrutando de tu contenido! 🍿🚀"
        )
    else:  # "entrega", "bienvenida", "activacion"
        profile_line = f"\n👤 *Perfil Asignado:* {profile}" if profile else ""
        pin_line = f"\n🔒 *PIN:* {pin}" if pin else ""
        price_line = f"\n💰 *Valor:* {price}" if price else ""
        msg = (
            f"🍿 *¡Hola {client_name}!* Aquí tienes los datos de acceso a tu suscripción:\n\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Usuario/Correo:* `{email}`\n"
            f"🔑 *Contraseña:* `{password}`"
            f"{profile_line}"
            f"{pin_line}\n"
            f"📅 *Vencimiento:* {expiry}"
            f"{price_line}\n\n"
            f"⚠️ *Reglas de Uso Importantes:*\n"
            f"• No cambiar correo ni contraseña.\n"
            f"• Utilizar únicamente el perfil asignado.\n"
            f"• No ingresar en más dispositivos de los permitidos.\n\n"
            f"¡Que lo disfrutes al máximo! Si tienes alguna duda, estamos a tu disposición ✨"
        )

    encoded_text = urllib.parse.quote(msg)
    if clean_phone:
        wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"

    return {
        "success": True,
        "client_name": client_name,
        "platform": platform,
        "email": email,
        "whatsapp": raw_phone,
        "clean_phone": clean_phone,
        "message_type": m_type,
        "message_text": msg,
        "wa_link": wa_url
    }

# ==========================================
# 7. Gestión de Pantallas Compartidas (Paso 3)
# ==========================================
def create_master_account_with_profiles(
    platform: str,
    email: str,
    password: str,
    profile_count: int = 4,
    pins: Union[str, List[str]] = "",
    cost: str = "",
    notes: str = ""
) -> List[Dict[str, Any]]:
    """Crea una cuenta madre en stock y genera automáticamente sus N casilleros/perfiles libres."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_email = email.strip()
    clean_password = password.strip()
    
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
                        status, payment_status, cost, notes
                    ) VALUES (?, ?, ?, ?, ?, 'libre', 'pagado', ?, ?)
                """, (clean_platform, clean_email, clean_password, prof_name, prof_pin, prof_cost, notes.strip()))
                
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

            today_str = date.today().isoformat()
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                    start_date = ?, expiry_date = ?, price = ?, notes = ?,
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client["id"], today_str, expiry_date.strip(), price.strip(), notes.strip(), slot_id))

            price_num = parse_money(price)
            cost_num = parse_money(slot.get("cost"))
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
