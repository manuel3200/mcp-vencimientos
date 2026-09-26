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
            "admin_whatsapp": "",
            "gemini_api_key": "",
            "expiry_cutoff_hour": 17
        }
        if "admin_whatsapp" not in res or not res["admin_whatsapp"]:
            res["admin_whatsapp"] = os.getenv("ADMIN_WHATSAPP", "")
        if "gemini_api_key" not in res or not res["gemini_api_key"]:
            res["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")
        if "expiry_cutoff_hour" not in res or res["expiry_cutoff_hour"] is None:
            res["expiry_cutoff_hour"] = int(os.getenv("EXPIRY_CUTOFF_HOUR", "17"))
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
    admin_whatsapp: str = "",
    gemini_api_key: str = "",
    expiry_cutoff_hour: int = 17
) -> Dict[str, Any]:
    """Guarda la configuración de conexión de Evolution API y opciones de envío automático."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_api_settings (id, api_url, api_key, instance_name, auto_send_expiry, auto_send_sales, auto_reply_enabled, admin_whatsapp, gemini_api_key, expiry_cutoff_hour, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    api_url = excluded.api_url,
                    api_key = excluded.api_key,
                    instance_name = excluded.instance_name,
                    auto_send_expiry = excluded.auto_send_expiry,
                    auto_send_sales = excluded.auto_send_sales,
                    auto_reply_enabled = excluded.auto_reply_enabled,
                    admin_whatsapp = excluded.admin_whatsapp,
                    gemini_api_key = excluded.gemini_api_key,
                    expiry_cutoff_hour = excluded.expiry_cutoff_hour,
                    updated_at = CURRENT_TIMESTAMP
            """, (api_url.strip(), api_key.strip(), instance_name.strip(), int(auto_send_expiry), int(auto_send_sales), int(auto_reply_enabled), admin_whatsapp.strip(), gemini_api_key.strip(), int(expiry_cutoff_hour)))
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

def _parse_registered_redirects(raw_redirects: str) -> set:
    """Convierte el registro de redirect_uris en un conjunto de URIs exactas (V05)."""
    import json
    raw = (raw_redirects or "").strip()
    if not raw:
        return set()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(u).strip() for u in parsed if str(u).strip()}
        except Exception:
            pass
    import re
    parts = re.split(r"[,;\r\n]+", raw)
    return {p.strip() for p in parts if p.strip()}


def is_registered_redirect_uri(redirect_uri: str, oauth_settings: Optional[Dict[str, Any]] = None) -> bool:
    """Valida que redirect_uri coincida exactamente con una URI registrada (V05: sin startswith)."""
    candidate = (redirect_uri or "").strip()
    if not candidate:
        return False
    cfg = oauth_settings or get_oauth_settings()
    registered = _parse_registered_redirects(cfg.get("redirect_uris", ""))
    return candidate in registered


def validate_pkce_s256(verifier: str, challenge: str, method: str) -> bool:
    """Valida estrictamente PKCE S256 rechazando métodos desconocidos o plain (V05)."""
    import re
    clean_method = (method or "").strip().upper()
    if clean_method != "S256":
        return False
    clean_verifier = (verifier or "").strip()
    clean_challenge = (challenge or "").strip()
    if not clean_challenge or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", clean_verifier):
        return False
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(clean_verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return secrets.compare_digest(expected, clean_challenge)


def create_pending_oauth_request(
    client_id: str,
    redirect_uri: str,
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "mcp",
    ttl_seconds: int = 600,
) -> str:
    """Guarda en servidor una solicitud OAuth pendiente para retomar tras login + 2FA (V04)."""
    req_id = secrets.token_hex(24)
    expires_at = time.time() + max(60, int(ttl_seconds))
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_pending_requests
                (request_id, client_id, redirect_uri, state, code_challenge, code_challenge_method, scope, expires_at, used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                req_id,
                client_id.strip(),
                redirect_uri.strip(),
                (state or "").strip(),
                (code_challenge or "").strip(),
                (code_challenge_method or "S256").strip().upper(),
                (scope or "mcp").strip(),
                expires_at,
            ))
        return req_id
    finally:
        conn.close()


def get_pending_oauth_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene una solicitud OAuth pendiente vigente (V04)."""
    clean_id = (request_id or "").strip()
    if not clean_id:
        return None
    now = time.time()
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM oauth_pending_requests
            WHERE request_id = ? AND used = 0 AND expires_at > ?
        """, (clean_id, now)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_pending_oauth_request_used(request_id: str) -> None:
    clean_id = (request_id or "").strip()
    if not clean_id:
        return
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE oauth_pending_requests SET used = 1 WHERE request_id = ?", (clean_id,))
    finally:
        conn.close()


def validate_oauth_client(client_id: str, client_secret: str) -> bool:
    """Verifica si el Client ID y Client Secret coinciden con los registrados."""
    settings = get_oauth_settings()
    if not settings.get("enabled", 1):
        return False
    valid_id = secrets.compare_digest(str(client_id).strip(), str(settings["client_id"]).strip())
    valid_secret = secrets.compare_digest(str(client_secret).strip(), str(settings["client_secret"]).strip())
    return valid_id and valid_secret


def create_oauth_auth_code(
    client_id: str,
    redirect_uri: str,
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    user_id: str = "admin",
) -> str:
    """Genera un código de autorización OAuth de un solo uso con validez de 5 minutos (V05)."""
    clean_uri = (redirect_uri or "").strip()
    if not is_registered_redirect_uri(clean_uri):
        raise ValueError("Redirección OAuth no registrada")

    clean_method = (code_challenge_method or "S256").strip().upper()
    clean_challenge = (code_challenge or "").strip()
    if clean_challenge and clean_method != "S256":
        raise ValueError("Método PKCE no permitido: se exige S256")

    code = f"auth_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + 300.0  # 5 minutos
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_auth_codes (code, client_id, redirect_uri, code_challenge, code_challenge_method, user_id, expires_at, used)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (code, client_id.strip(), clean_uri, clean_challenge, clean_method, user_id.strip(), expires_at))
        return code
    finally:
        conn.close()


def verify_and_consume_auth_code(
    code: str,
    client_id: str,
    redirect_uri: str = "",
    code_verifier: str = "",
) -> Optional[Dict[str, Any]]:
    """Verifica y consume atómicamente un código OAuth validando redirect_uri exacto y PKCE S256 (V05)."""
    clean_code = (code or "").strip()
    clean_client = (client_id or "").strip()
    clean_redirect = (redirect_uri or "").strip()
    if not clean_code or not clean_client:
        return None

    now = time.time()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM oauth_auth_codes WHERE code = ?", (clean_code,)).fetchone()
        if not row:
            conn.rollback()
            return None
        data = dict(row)
        if data["used"] != 0 or float(data["expires_at"]) <= now:
            conn.rollback()
            return None
        if not secrets.compare_digest(str(data["client_id"]).strip(), clean_client):
            conn.rollback()
            return None

        # Validar coincidencia exacta de redirect_uri contra el almacenado en el código (V05)
        stored_redirect = str(data.get("redirect_uri") or "").strip()
        if stored_redirect:
            if not clean_redirect or not secrets.compare_digest(stored_redirect, clean_redirect):
                conn.rollback()
                return None

        # Validar PKCE S256 estrictamente (rechazar métodos desconocidos o plain) (V05)
        challenge = str(data.get("code_challenge") or "").strip()
        method = str(data.get("code_challenge_method") or "").strip().upper()
        if challenge or code_verifier:
            if not validate_pkce_s256(code_verifier, challenge, method):
                conn.rollback()
                return None

        cur = conn.execute("""
            UPDATE oauth_auth_codes
            SET used = 1
            WHERE code = ? AND used = 0 AND expires_at > ?
        """, (clean_code, now))
        if cur.rowcount != 1:
            conn.rollback()
            return None

        conn.commit()
        return data
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def create_oauth_tokens(
    client_id: str,
    user_id: str = "admin",
    expires_in_seconds: int = 3600,
    scope: str = "mcp",
    client_phone: str = "",
    family_ttl_seconds: int = 86400 * 30,
) -> Dict[str, Any]:
    """Crea y persiste un Access Token y Refresh Token almacenando únicamente sus hashes SHA-256 y familia (V05 / V14)."""
    access_token = f"mcp_at_{secrets.token_urlsafe(32)}"
    refresh_token = f"mcp_rt_{secrets.token_urlsafe(32)}"
    at_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
    rt_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    redacted_at = f"REDACTED_AT_{at_hash[:16]}"
    redacted_rt = f"REDACTED_RT_{rt_hash[:16]}"
    now = time.time()
    expires_at = now + max(60, int(expires_in_seconds))
    family_id = f"fam_{secrets.token_hex(12)}"
    family_expires_at = str(now + max(int(expires_in_seconds), int(family_ttl_seconds)))

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO oauth_tokens (
                    access_token, refresh_token, client_id, user_id, expires_at,
                    access_token_hash, refresh_token_hash, scope, client_phone,
                    family_id, family_expires_at, used_at, revoked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """, (
                redacted_at,
                redacted_rt,
                client_id.strip(),
                user_id.strip(),
                expires_at,
                at_hash,
                rt_hash,
                scope.strip(),
                client_phone.strip(),
                family_id,
                family_expires_at,
            ))
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in_seconds,
            "refresh_token": refresh_token,
            "scope": scope.strip() or "mcp",
            "family_id": family_id,
        }
    finally:
        conn.close()


def refresh_oauth_token(refresh_token: str, client_id: str, expires_in_seconds: int = 3600) -> Optional[Dict[str, Any]]:
    """Renueva atómicamente un Access Token rotando el Refresh Token dentro de su familia (V14).
    Conserva el refresh token anterior como tombstone (used_at) y, si detecta reutilización (replay),
    revoca inmediatamente toda la familia de tokens.
    """
    clean_rt = (refresh_token or "").strip()
    clean_client = (client_id or "").strip()
    if not clean_rt or not clean_client:
        return None
    rt_hash = hashlib.sha256(clean_rt.encode("utf-8")).hexdigest()
    now = time.time()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT * FROM oauth_tokens
            WHERE refresh_token_hash = ? OR refresh_token = ?
        """, (rt_hash, clean_rt)).fetchone()
        if not row:
            conn.rollback()
            return None
        data = dict(row)
        if not secrets.compare_digest(str(data["client_id"]).strip(), clean_client):
            conn.rollback()
            return None

        family_id = str(data.get("family_id") or "").strip() or f"fam_{data['id']}"

        # 1. Si el token ya estaba revocado, rechazar
        if data.get("revoked_at") is not None:
            conn.rollback()
            return None

        # 2. Detección de Replay: si el refresh token ya fue usado previamente, revocar toda la familia (V14)
        if data.get("used_at") is not None:
            conn.execute("""
                UPDATE oauth_tokens
                SET revoked_at = ?
                WHERE family_id = ? AND revoked_at IS NULL
            """, (str(now), family_id))
            conn.commit()
            return None

        # 3. Verificar vencimiento absoluto de la familia (no extender indefinidamente)
        raw_fam_exp = str(data.get("family_expires_at") or "").strip()
        if raw_fam_exp:
            try:
                fam_exp_val = float(raw_fam_exp)
                if now >= fam_exp_val:
                    conn.rollback()
                    return None
            except ValueError:
                pass
        else:
            fam_exp_val = now + 86400 * 30
            raw_fam_exp = str(fam_exp_val)

        # 4. Marcar el refresh token actual como usado (tombstone) verificando rowcount == 1
        cur = conn.execute("""
            UPDATE oauth_tokens
            SET used_at = ?
            WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL
        """, (str(now), data["id"]))
        if cur.rowcount != 1:
            conn.rollback()
            return None

        # 5. Emitir par sucesor dentro de la misma familia y acotado por family_expires_at
        new_access_token = f"mcp_at_{secrets.token_urlsafe(32)}"
        new_refresh_token = f"mcp_rt_{secrets.token_urlsafe(32)}"
        new_at_hash = hashlib.sha256(new_access_token.encode("utf-8")).hexdigest()
        new_rt_hash = hashlib.sha256(new_refresh_token.encode("utf-8")).hexdigest()
        redacted_at = f"REDACTED_AT_{new_at_hash[:16]}"
        redacted_rt = f"REDACTED_RT_{new_rt_hash[:16]}"
        expires_at = min(now + max(60, int(expires_in_seconds)), float(raw_fam_exp))

        conn.execute("""
            INSERT INTO oauth_tokens (
                access_token, refresh_token, client_id, user_id, expires_at,
                access_token_hash, refresh_token_hash, scope, client_phone,
                family_id, family_expires_at, used_at, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        """, (
            redacted_at,
            redacted_rt,
            clean_client,
            str(data.get("user_id") or "admin"),
            expires_at,
            new_at_hash,
            new_rt_hash,
            str(data.get("scope") or "mcp"),
            str(data.get("client_phone") or ""),
            family_id,
            raw_fam_exp,
        ))
        conn.commit()
        return {
            "access_token": new_access_token,
            "token_type": "Bearer",
            "expires_in": int(max(1, expires_at - now)),
            "refresh_token": new_refresh_token,
            "scope": str(data.get("scope") or "mcp"),
            "family_id": family_id,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def revoke_oauth_token_family(token_or_family_id: str) -> int:
    """Revoca inmediatamente un token OAuth o toda su familia asociada (V14)."""
    clean_val = (token_or_family_id or "").strip()
    if not clean_val:
        return 0
    digest = hashlib.sha256(clean_val.encode("utf-8")).hexdigest()
    now_str = str(time.time())
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("""
                SELECT family_id, id FROM oauth_tokens
                WHERE family_id = ? OR access_token_hash = ? OR refresh_token_hash = ?
                LIMIT 1
            """, (clean_val, digest, digest)).fetchone()
            if not row:
                return 0
            fam = str(row["family_id"] or "").strip()
            if fam:
                cur = conn.execute("""
                    UPDATE oauth_tokens SET revoked_at = ?
                    WHERE family_id = ? AND revoked_at IS NULL
                """, (now_str, fam))
            else:
                cur = conn.execute("""
                    UPDATE oauth_tokens SET revoked_at = ?
                    WHERE id = ? AND revoked_at IS NULL
                """, (now_str, row["id"]))
            return int(cur.rowcount or 0)
    finally:
        conn.close()


def verify_oauth_access_token(access_token: str) -> Optional[Dict[str, Any]]:
    """Verifica si un Access Token Bearer es válido, no ha sido rotado/revocado y no ha expirado (V05 / V14)."""
    clean_at = (access_token or "").strip()
    if not clean_at:
        return None
    at_hash = hashlib.sha256(clean_at.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM oauth_tokens
            WHERE access_token_hash = ? OR access_token = ?
        """, (at_hash, clean_at)).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("revoked_at") is not None or data.get("used_at") is not None:
            return None
        if float(data["expires_at"]) < time.time():
            return None
        return data
    finally:
        conn.close()

