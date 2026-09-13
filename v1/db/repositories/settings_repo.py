import os
import time
import base64
import hashlib
import secrets
from typing import Optional, Dict, Any

from db.connection import get_connection
from db.schema import DEFAULT_WHATSAPP_TEMPLATES

# ==========================================
# Medios de Cobro y CBU
# ==========================================
def get_payment_settings() -> Dict[str, Any]:
    """Obtiene la configuración actual de medios de cobro y datos bancarios."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM payment_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO payment_settings (id) VALUES (1)")
            conn.commit()
            row = conn.execute("SELECT * FROM payment_settings WHERE id = 1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()

def save_payment_settings(
    alias_mp: str = "",
    cvu_cbu: str = "",
    account_holder: str = "",
    bank_name: str = "",
    usdt_address: str = "",
    extra_instructions: str = ""
) -> bool:
    """Guarda o actualiza los datos bancarios y métodos de cobro."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO payment_settings (id, alias_mp, cvu_cbu, account_holder, bank_name, usdt_address, extra_instructions, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    alias_mp = excluded.alias_mp,
                    cvu_cbu = excluded.cvu_cbu,
                    account_holder = excluded.account_holder,
                    bank_name = excluded.bank_name,
                    usdt_address = excluded.usdt_address,
                    extra_instructions = excluded.extra_instructions,
                    updated_at = CURRENT_TIMESTAMP
            """, (alias_mp.strip(), cvu_cbu.strip(), account_holder.strip(), bank_name.strip(), usdt_address.strip(), extra_instructions.strip()))
            return True
    finally:
        conn.close()

# ==========================================
# Plantillas Personalizables de WhatsApp
# ==========================================
def get_whatsapp_templates() -> Dict[str, Dict[str, Any]]:
    """Devuelve todas las plantillas registradas combinando base de datos y predeterminadas."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM whatsapp_templates ORDER BY template_key ASC").fetchall()
        result = {}
        for r in rows:
            result[r["template_key"]] = dict(r)
        
        for k, def_data in DEFAULT_WHATSAPP_TEMPLATES.items():
            if k not in result:
                result[k] = {
                    "template_key": k,
                    "title": def_data["title"],
                    "content": def_data["content"],
                    "description": def_data["description"]
                }
        return result
    finally:
        conn.close()

def get_whatsapp_template(template_key: str) -> Dict[str, Any]:
    """Obtiene una plantilla individual por su clave."""
    conn = get_connection()
    k = template_key.strip().lower()
    try:
        row = conn.execute("SELECT * FROM whatsapp_templates WHERE template_key = ?", (k,)).fetchone()
        if row:
            return dict(row)
        if k in DEFAULT_WHATSAPP_TEMPLATES:
            d = DEFAULT_WHATSAPP_TEMPLATES[k]
            return {"template_key": k, "title": d["title"], "content": d["content"], "description": d["description"]}
        return {"template_key": k, "title": k.title(), "content": "", "description": ""}
    finally:
        conn.close()

def save_whatsapp_template(template_key: str, content: str, title: str = "", description: str = "") -> bool:
    """Guarda o actualiza una plantilla de WhatsApp."""
    conn = get_connection()
    k = template_key.strip().lower()
    final_title = title.strip() or DEFAULT_WHATSAPP_TEMPLATES.get(k, {}).get("title", k.title())
    final_desc = description.strip() or DEFAULT_WHATSAPP_TEMPLATES.get(k, {}).get("description", "")
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_templates (template_key, title, content, description, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_key) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    description = excluded.description,
                    updated_at = CURRENT_TIMESTAMP
            """, (k, final_title, content.strip(), final_desc))
            return True
    finally:
        conn.close()

def reset_whatsapp_template(template_key: str) -> bool:
    """Restaura una plantilla a su valor predeterminado del sistema."""
    k = template_key.strip().lower()
    if k not in DEFAULT_WHATSAPP_TEMPLATES:
        return False
    d = DEFAULT_WHATSAPP_TEMPLATES[k]
    return save_whatsapp_template(k, d["content"], d["title"], d["description"])

# ==========================================
# Configuración Evolution API WhatsApp
# ==========================================
def get_whatsapp_api_settings() -> Dict[str, Any]:
    """Obtiene la configuración de conexión y automatización de Evolution API WhatsApp."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM whatsapp_api_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO whatsapp_api_settings (id) VALUES (1)")
            conn.commit()
            row = conn.execute("SELECT * FROM whatsapp_api_settings WHERE id = 1").fetchone()
        res = dict(row) if row else {
            "id": 1,
            "api_url": "http://evolution-api:8080",
            "api_key": "mcp-evolution-key-2026",
            "instance_name": "streaming-bot",
            "auto_send_expiry": 0,
            "auto_send_sales": 0,
            "auto_reply_enabled": 1,
            "admin_whatsapp": ""
        }
        if "admin_whatsapp" not in res or not res["admin_whatsapp"]:
            res["admin_whatsapp"] = os.getenv("ADMIN_WHATSAPP", "")
        return res
    finally:
        conn.close()

def save_whatsapp_api_settings(
    api_url: str = "http://evolution-api:8080",
    api_key: str = "mcp-evolution-key-2026",
    instance_name: str = "streaming-bot",
    auto_send_expiry: int = 0,
    auto_send_sales: int = 0,
    auto_reply_enabled: int = 1,
    admin_whatsapp: str = ""
) -> Dict[str, Any]:
    """Guarda la configuración de conexión de Evolution API y opciones de envío automático."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_api_settings (id, api_url, api_key, instance_name, auto_send_expiry, auto_send_sales, auto_reply_enabled, admin_whatsapp, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    api_url = excluded.api_url,
                    api_key = excluded.api_key,
                    instance_name = excluded.instance_name,
                    auto_send_expiry = excluded.auto_send_expiry,
                    auto_send_sales = excluded.auto_send_sales,
                    auto_reply_enabled = excluded.auto_reply_enabled,
                    admin_whatsapp = excluded.admin_whatsapp,
                    updated_at = CURRENT_TIMESTAMP
            """, (api_url.strip(), api_key.strip(), instance_name.strip(), int(auto_send_expiry), int(auto_send_sales), int(auto_reply_enabled), admin_whatsapp.strip()))
        return get_whatsapp_api_settings()
    finally:
        conn.close()

# ==========================================
# Integración Chatwoot API
# ==========================================
def get_chatwoot_settings() -> Dict[str, Any]:
    """Obtiene la configuración activa de conexión con Chatwoot."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM chatwoot_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute("""
                INSERT OR IGNORE INTO chatwoot_settings (id, url, token, account_id, enabled, auto_sync)
                VALUES (1, 'https://chat.joif.net', '', '1', 1, 1)
            """)
            conn.commit()
            row = conn.execute("SELECT * FROM chatwoot_settings WHERE id = 1").fetchone()
        
        data = dict(row) if row else {}
        url = (data.get("url") or os.getenv("CHATWOOT_URL") or "https://chat.joif.net").strip().rstrip("/")
        token = (data.get("token") or os.getenv("CHATWOOT_TOKEN") or "ZRzCpt75vxkyiUC7H1otEoog").strip()
        if not token:
            token = "ZRzCpt75vxkyiUC7H1otEoog"
        account_id = str(data.get("account_id") or os.getenv("CHATWOOT_ACCOUNT_ID") or "1").strip()
        return {
            "id": 1,
            "url": url,
            "token": token,
            "account_id": account_id,
            "enabled": int(data.get("enabled", 1)),
            "auto_sync": int(data.get("auto_sync", 1))
        }
    finally:
        conn.close()

def save_chatwoot_settings(
    url: str = "https://chat.joif.net",
    token: str = "",
    account_id: str = "1",
    enabled: int = 1,
    auto_sync: int = 1
) -> Dict[str, Any]:
    """Guarda o actualiza las credenciales y URL de Chatwoot."""
    conn = get_connection()
    try:
        clean_url = (url or "https://chat.joif.net").strip().rstrip("/")
        clean_token = token.strip() or "ZRzCpt75vxkyiUC7H1otEoog"
        clean_acc = str(account_id or "1").strip()
        with conn:
            conn.execute("""
                INSERT INTO chatwoot_settings (id, url, token, account_id, enabled, auto_sync, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    token = CASE WHEN length(excluded.token) > 0 THEN excluded.token ELSE chatwoot_settings.token END,
                    account_id = excluded.account_id,
                    enabled = excluded.enabled,
                    auto_sync = excluded.auto_sync,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_url, clean_token, clean_acc, int(enabled), int(auto_sync)))
        return get_chatwoot_settings()
    finally:
        conn.close()

# ==========================================
# Autenticación y Tokens OAuth 2.0 (Gemini Spark)
# ==========================================
def get_oauth_settings() -> Dict[str, Any]:
    """Obtiene la configuración del cliente OAuth (Client ID y Client Secret)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM oauth_clients WHERE id = 1").fetchone()
        if not row:
            def_client_id = "gemini-spark-joif"
            def_client_secret = f"sec_{secrets.token_hex(20)}"
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO oauth_clients (id, client_id, client_secret, client_name, redirect_uris, enabled)
                    VALUES (1, ?, ?, 'Gemini Spark Connected App', 'https://gemini.google.com', 1)
                """, (def_client_id, def_client_secret))
            row = conn.execute("SELECT * FROM oauth_clients WHERE id = 1").fetchone()
        return dict(row)
    finally:
        conn.close()

def save_oauth_settings(client_id: str, client_secret: str, redirect_uris: str = "", enabled: int = 1) -> Dict[str, Any]:
    """Actualiza las credenciales OAuth en la base de datos."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_clients (id, client_id, client_secret, redirect_uris, enabled, updated_at)
                VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    client_id = excluded.client_id,
                    client_secret = excluded.client_secret,
                    redirect_uris = excluded.redirect_uris,
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
            """, (client_id.strip(), client_secret.strip(), redirect_uris.strip(), int(enabled)))
        return get_oauth_settings()
    finally:
        conn.close()

def regenerate_oauth_secret() -> Dict[str, Any]:
    """Regenera un nuevo Client Secret aleatorio y seguro."""
    new_secret = f"sec_{secrets.token_hex(20)}"
    current = get_oauth_settings()
    return save_oauth_settings(current["client_id"], new_secret, current.get("redirect_uris", ""), current.get("enabled", 1))

def validate_oauth_client(client_id: str, client_secret: str) -> bool:
    """Verifica si el Client ID y Client Secret coinciden con los registrados."""
    settings = get_oauth_settings()
    if not settings.get("enabled", 1):
        return False
    valid_id = secrets.compare_digest(str(client_id).strip(), str(settings["client_id"]).strip())
    valid_secret = secrets.compare_digest(str(client_secret).strip(), str(settings["client_secret"]).strip())
    return valid_id and valid_secret

def create_oauth_auth_code(client_id: str, redirect_uri: str, code_challenge: str = "", code_challenge_method: str = "", user_id: str = "admin") -> str:
    """Genera un código de autorización OAuth de un solo uso con validez de 5 minutos."""
    code = f"auth_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + 300.0  # 5 minutos
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_auth_codes (code, client_id, redirect_uri, code_challenge, code_challenge_method, user_id, expires_at, used)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (code, client_id.strip(), redirect_uri.strip(), code_challenge.strip(), code_challenge_method.strip(), user_id.strip(), expires_at))
        return code
    finally:
        conn.close()

def verify_and_consume_auth_code(code: str, client_id: str, redirect_uri: str = "", code_verifier: str = "") -> Optional[Dict[str, Any]]:
    """Verifica un código de autorización OAuth, valida PKCE si aplica, y lo marca como usado."""
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("SELECT * FROM oauth_auth_codes WHERE code = ?", (code.strip(),)).fetchone()
            if not row:
                return None
            data = dict(row)
            if data["used"] != 0 or data["expires_at"] < time.time():
                return None
            if not secrets.compare_digest(data["client_id"], client_id.strip()):
                return None

            challenge = data.get("code_challenge", "").strip()
            method = (data.get("code_challenge_method") or "plain").strip().upper()
            if challenge:
                if not code_verifier:
                    return None
                if method == "S256":
                    expected = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                    if not secrets.compare_digest(expected, challenge):
                        return None
                elif method == "PLAIN":
                    if not secrets.compare_digest(code_verifier.strip(), challenge):
                        return None

            conn.execute("UPDATE oauth_auth_codes SET used = 1 WHERE code = ?", (code.strip(),))
            return data
    finally:
        conn.close()

def create_oauth_tokens(client_id: str, user_id: str = "admin", expires_in_seconds: int = 31536000) -> Dict[str, Any]:
    """Crea y persiste un Access Token y Refresh Token para el cliente autenticado."""
    access_token = f"mcp_at_{secrets.token_urlsafe(32)}"
    refresh_token = f"mcp_rt_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + expires_in_seconds
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (access_token, refresh_token, client_id.strip(), user_id.strip(), expires_at))
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in_seconds,
            "refresh_token": refresh_token,
            "scope": "mcp"
        }
    finally:
        conn.close()

def refresh_oauth_token(refresh_token: str, client_id: str, expires_in_seconds: int = 31536000) -> Optional[Dict[str, Any]]:
    """Renueva un Access Token usando un Refresh Token válido."""
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("SELECT * FROM oauth_tokens WHERE refresh_token = ?", (refresh_token.strip(),)).fetchone()
            if not row:
                return None
            data = dict(row)
            if not secrets.compare_digest(data["client_id"], client_id.strip()):
                return None
            new_access_token = f"mcp_at_{secrets.token_urlsafe(32)}"
            new_refresh_token = f"mcp_rt_{secrets.token_urlsafe(32)}"
            expires_at = time.time() + expires_in_seconds
            conn.execute("""
                UPDATE oauth_tokens SET access_token = ?, refresh_token = ?, expires_at = ? WHERE refresh_token = ?
            """, (new_access_token, new_refresh_token, expires_at, refresh_token.strip()))
            return {
                "access_token": new_access_token,
                "token_type": "Bearer",
                "expires_in": expires_in_seconds,
                "refresh_token": new_refresh_token,
                "scope": "mcp"
            }
    finally:
        conn.close()

def verify_oauth_access_token(access_token: str) -> Optional[Dict[str, Any]]:
    """Verifica si un Access Token Bearer es válido y no ha expirado."""
    if not access_token:
        return None
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM oauth_tokens WHERE access_token = ?", (access_token.strip(),)).fetchone()
        if not row:
            return None
        data = dict(row)
        if data["expires_at"] < time.time():
            return None
        return data
    finally:
        conn.close()
