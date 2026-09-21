"""
ephemeral_secrets.py - Enlaces Efímeros y Secretos de Un Solo Uso (Anti-SIM Swap)
Genera credenciales temporales autodestructibles para prevenir el almacenamiento
persistente de contraseñas y PINs en el historial de mensajería (WhatsApp / Telegram).
"""

import os
import json
import secrets
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from core.config import settings
from core.security import encrypt_secret, decrypt_secret
from core.audit import log_audit_event
from db.connection import get_connection

logger = logging.getLogger("core.ephemeral_secrets")


def create_ephemeral_secret(
    data: Dict[str, Any],
    title: str = "Credenciales Seguras",
    ttl_seconds: int = 600,
    max_views: int = 1,
    actor: str = "system"
) -> Tuple[str, str]:
    """Cifra un diccionario de datos sensibles y genera un enlace efímero de un solo uso.
    
    Retorna:
    (token: str, full_url: str)
    """
    token = f"sec_{secrets.token_urlsafe(18)}"
    payload_json = json.dumps(data, ensure_ascii=False)
    ciphertext = encrypt_secret(payload_json)

    # Cálculo de expiración en formato ISO UTC
    expires_dt = datetime.utcnow() + timedelta(seconds=ttl_seconds)
    expires_at_str = expires_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO ephemeral_secrets (token, ciphertext, title, expires_at, max_views, view_count)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (token, ciphertext, title.strip(), expires_at_str, max_views))
    finally:
        conn.close()

    base_url = (
        getattr(settings, "PUBLIC_BASE_URL", "") or
        os.getenv("PUBLIC_BASE_URL", "") or
        getattr(settings, "APP_BASE_URL", "") or
        os.getenv("APP_BASE_URL", "http://localhost:8000")
    ).strip().rstrip("/")
    full_url = f"{base_url}/v/{token}"

    try:
        log_audit_event(
            actor=actor,
            action="GENERATE_EPHEMERAL_SECRET",
            target_type="ephemeral_secret",
            target_id=token,
            old_value="",
            new_value=f"title:{title},ttl:{ttl_seconds}s,max_views:{max_views}",
            ip_or_source="ephemeral_secrets"
        )
    except Exception as e:
        logger.warning(f"Error registrando auditoría de secreto efímero: {e}")

    logger.info(f"🔑 Secreto efímero creado: {token} (válido por {ttl_seconds}s, max_views={max_views})")
    return token, full_url



def reveal_and_burn_secret(token: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Accede a un secreto efímero y lo autodestruye en el servidor.
    
    Estados posibles:
    - ('revealed', dict): Éxito, credenciales reveladas y destruidas.
    - ('not_found', None): El enlace no existe.
    - ('already_burned', None): El enlace ya fue consumido y destruido previamente.
    - ('expired', None): El enlace superó su tiempo de vida útil (TTL).
    """
    clean_token = token.strip()
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("""
                SELECT * FROM ephemeral_secrets WHERE token = ?
            """, (clean_token,)).fetchone()

            if not row:
                return None, "not_found"

            # Verificar si ya fue consumido
            if row["burned_at"] is not None or row["view_count"] >= row["max_views"]:
                return None, "already_burned"

            # Verificar si ya expiró por tiempo
            now_utc = datetime.utcnow()
            try:
                expires_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                expires_dt = now_utc

            if now_utc > expires_dt:
                conn.execute("""
                    UPDATE ephemeral_secrets
                    SET burned_at = CURRENT_TIMESTAMP
                    WHERE token = ?
                """, (clean_token,))
                return None, "expired"

            # Consumir y quemar el secreto de forma atómica condicional
            cursor = conn.execute("""
                UPDATE ephemeral_secrets
                SET view_count = view_count + 1,
                    burned_at = CASE WHEN view_count + 1 >= max_views THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE token = ? AND burned_at IS NULL AND view_count < max_views
            """, (clean_token,))

            if cursor.rowcount == 0:
                # Otra solicitud concurrente consumió el secreto una fracción de ms antes
                return None, "already_burned"

            # Descifrar contenido
            raw_ciphertext = row["ciphertext"]
            decrypted_json = decrypt_secret(raw_ciphertext)
            payload = json.loads(decrypted_json)
    except Exception as e:
        logger.error(f"Error procesando secreto efímero {clean_token}: {e}")
        return None, "error"
    finally:
        conn.close()

    try:
        log_audit_event(
            actor="client_web",
            action="CONSUME_EPHEMERAL_SECRET",
            target_type="ephemeral_secret",
            target_id=clean_token,
            old_value="active",
            new_value="burned",
            ip_or_source="ephemeral_secrets"
        )
    except Exception as e:
        logger.warning(f"Error en auditoría al consumir secreto: {e}")

    logger.info(f"🔥 Secreto efímero consumido y quemado atómicamente: {clean_token}")
    return payload, "revealed"


def burn_secret_immediately(token: str, actor: str = "admin") -> bool:
    """Quema inmediatamente un secreto efímero para forzar su expiración anticipada."""
    clean_token = token.strip()
    conn = get_connection()
    burned = False
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE ephemeral_secrets
                SET burned_at = CURRENT_TIMESTAMP
                WHERE token = ? AND burned_at IS NULL
            """, (clean_token,))
            burned = cursor.rowcount > 0
    finally:
        conn.close()

    if burned:
        try:
            log_audit_event(
                actor=actor,
                action="BURN_EPHEMERAL_SECRET",
                target_type="ephemeral_secret",
                target_id=clean_token,
                old_value="active",
                new_value="forced_burn",
                ip_or_source="ephemeral_secrets"
            )
        except Exception as e:
            logger.warning(f"Error en auditoría al forzar quemado: {e}")
    return burned
