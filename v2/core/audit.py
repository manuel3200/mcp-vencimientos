"""
audit.py - Log de Auditoría Inmutable con Firma Criptográfica Encadenada (Blockchain Liviana)
Garantiza no repudio, trazabilidad forense y detección inmediata de tampering
para operaciones críticas (cambio de claves maestras, pagos, bajas de cuentas, etc.).
"""

import os
import json
import hmac
import hashlib
import secrets
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from core.config import settings

logger = logging.getLogger("core.audit")

GENESIS_HASH = "0" * 64


def _get_audit_repo():
    """Importación perezosa para desacoplar el núcleo de persistencia y evitar ciclos."""
    from db.repositories import audit_repo
    return audit_repo


def get_latest_audit_entry() -> Optional[Dict[str, Any]]:
    """Consulta diferida del último bloque de auditoría para compatibilidad de endpoints."""
    return _get_audit_repo().get_latest_audit_entry()


def get_audit_hmac_key() -> str:
    """Obtiene la clave simétrica dedicada para la firma HMAC del log de auditoría (V16)."""
    explicit_key = (
        getattr(settings, "AUDIT_HMAC_KEY", "") or
        os.getenv("AUDIT_HMAC_KEY", "")
    ).strip()
    if explicit_key:
        return explicit_key

    app_env = (getattr(settings, "APP_ENV", "") or os.getenv("APP_ENV", "")).strip().lower()
    if app_env == "production":
        raise RuntimeError("AUDIT_HMAC_KEY dedicada es obligatoria en producción (V16).")

    # En desarrollo/tests derivar una clave con separación criptográfica de dominio respecto a SESSION_SECRET_KEY
    base_secret = getattr(settings, "SESSION_SECRET_KEY", "") or os.getenv("SESSION_SECRET_KEY", "") or "dev-fallback"
    return hashlib.sha256(f"streamvault-audit-hmac-v2:{base_secret}".encode("utf-8")).hexdigest()


def _build_canonical_audit_json(
    prev_hash: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str,
    new_value: str,
    ip_or_source: str,
) -> str:
    """Construye un payload JSON canónico determinista inmune a colisiones por delimitador '|' (V16)."""
    payload = {
        "v": 2,
        "prev_hash": (prev_hash or "").strip(),
        "actor": (actor or "").strip(),
        "action": (action or "").strip().upper(),
        "target_type": (target_type or "").strip().lower(),
        "target_id": str(target_id if target_id is not None else "").strip(),
        "old_value": (old_value or "").strip(),
        "new_value": (new_value or "").strip(),
        "ip_or_source": (ip_or_source or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compute_legacy_pipe_signature(
    prev_hash: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str,
    new_value: str,
    ip_or_source: str,
    key: str,
) -> str:
    """Compatibilidad de solo verificación para bloques históricos previos a V16 sin caracteres '|'."""
    canonical_payload = (
        f"{(prev_hash or '').strip()}|{(actor or '').strip()}|{(action or '').strip().upper()}|"
        f"{(target_type or '').strip().lower()}|{str(target_id if target_id is not None else '').strip()}|"
        f"{(old_value or '').strip()}|"
        f"{(new_value or '').strip()}|"
        f"{(ip_or_source or '').strip()}"
    )
    return hmac.new(
        key.encode("utf-8"),
        canonical_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def compute_audit_signature(
    prev_hash: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str,
    new_value: str,
    ip_or_source: str,
    key: Optional[str] = None
) -> str:
    """Calcula la firma HMAC-SHA256 del bloque de auditoría sobre JSON canónico determinista (V16)."""
    hmac_key = key or get_audit_hmac_key()
    canonical_payload = _build_canonical_audit_json(
        prev_hash=prev_hash,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        ip_or_source=ip_or_source,
    )
    return hmac.new(
        hmac_key.encode("utf-8"),
        canonical_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def log_audit_event(
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str = "",
    new_value: str = "",
    ip_or_source: str = ""
) -> Dict[str, Any]:
    """Registra de forma inmutable y atómica (BEGIN IMMEDIATE) una operación crítica en la bitácora criptográfica (V16)."""
    repo = _get_audit_repo()

    def _sign_with_prev(prev_hash: str) -> str:
        return compute_audit_signature(
            prev_hash=prev_hash,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_value=old_value,
            new_value=new_value,
            ip_or_source=ip_or_source,
        )

    if hasattr(repo, "append_audit_entry_atomic"):
        entry = repo.append_audit_entry_atomic(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_value=old_value,
            new_value=new_value,
            ip_or_source=ip_or_source,
            signature_builder=_sign_with_prev,
        )
    else:
        latest = repo.get_latest_audit_entry()
        prev_hash = latest["signature_hmac"] if latest and latest.get("signature_hmac") else GENESIS_HASH
        sig = _sign_with_prev(prev_hash)
        entry = repo.insert_audit_entry(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_value=old_value,
            new_value=new_value,
            ip_or_source=ip_or_source,
            prev_hash=prev_hash,
            signature_hmac=sig
        )

    sig_logged = entry.get("signature_hmac", "")
    logger.info(
        f"🔒 AUDIT_LOG #{entry.get('id')}: [{action}] por '{actor}' en {target_type}#{target_id} "
        f"(sig: {sig_logged[:12]}...)"
    )
    return entry


def verify_audit_chain(key: Optional[str] = None) -> Tuple[bool, int, str]:
    """Verifica la integridad criptográfica de extremo a extremo de la bitácora.
    
    Retorna:
    (is_valid: bool, total_checked: int, details: str)
    Si algún registro fue modificado o borrado manualmente en SQLite, la función
    detecta la inconsistencia e identifica el ID del registro corrompido.
    """
    repo = _get_audit_repo()
    entries = repo.get_all_audit_entries_asc()
    if not entries:
        return True, 0, "Bitácora vacía: sin registros que auditar."

    hmac_key = key or get_audit_hmac_key()
    legacy_fallback_key = getattr(settings, "SESSION_SECRET_KEY", "") or hmac_key
    expected_prev = GENESIS_HASH

    for idx, entry in enumerate(entries):
        entry_id = entry.get("id")
        prev_hash = entry.get("prev_hash") or ""
        sig_stored = entry.get("signature_hmac") or ""

        # 1. Validar encadenamiento de hash con el bloque previo
        if not secrets.compare_digest(prev_hash, expected_prev):
            msg = (
                f"🚨 CORRUPCIÓN DE CADENA en registro #{entry_id}: "
                f"prev_hash '{prev_hash[:16]}...' no coincide con hash esperado '{expected_prev[:16]}...'."
            )
            logger.error(msg)
            return False, idx, msg

        # 2. Recomputar firma HMAC del bloque actual (JSON canónico V16)
        actor_val = entry.get("actor") or ""
        action_val = entry.get("action") or ""
        target_type_val = entry.get("target_type") or ""
        target_id_val = entry.get("target_id") or ""
        old_val = entry.get("old_value") or ""
        new_val = entry.get("new_value") or ""
        source_val = entry.get("ip_or_source") or ""

        computed_sig = compute_audit_signature(
            prev_hash=prev_hash,
            actor=actor_val,
            action=action_val,
            target_type=target_type_val,
            target_id=target_id_val,
            old_value=old_val,
            new_value=new_val,
            ip_or_source=source_val,
            key=hmac_key
        )

        matched = secrets.compare_digest(sig_stored, computed_sig)
        if not matched and not any("|" in str(v) for v in (actor_val, action_val, target_type_val, target_id_val, old_val, new_val, source_val)):
            for candidate_key in (hmac_key, legacy_fallback_key):
                if candidate_key:
                    legacy_sig = _compute_legacy_pipe_signature(
                        prev_hash=prev_hash,
                        actor=actor_val,
                        action=action_val,
                        target_type=target_type_val,
                        target_id=target_id_val,
                        old_value=old_val,
                        new_value=new_val,
                        ip_or_source=source_val,
                        key=candidate_key,
                    )
                    if secrets.compare_digest(sig_stored, legacy_sig):
                        matched = True
                        break

        if not matched:
            msg = (
                f"🚨 MANIPULACIÓN DETECTADA (TAMPERING) en registro #{entry_id}: "
                f"firma almacenada '{sig_stored[:16]}...' difiere de la firma calculada '{computed_sig[:16]}...'."
            )
            logger.error(msg)
            return False, idx, msg

        expected_prev = sig_stored

    total = len(entries)
    msg = f"✨ Cadena de auditoría 100% íntegra: {total} registros verificados criptográficamente."
    return True, total, msg


def get_audit_history(
    target_id: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Consulta el historial de auditoría para un objetivo específico o global."""
    repo = _get_audit_repo()
    return repo.list_audit_history(target_id=target_id, target_type=target_type, limit=limit)


def format_audit_report(records: List[Dict[str, Any]], target_query: Optional[str] = None) -> str:
    """Genera un reporte legible de trazabilidad con sellos temporales y firmas."""
    is_valid, count, status_msg = verify_audit_chain()
    status_icon = "🟢" if is_valid else "🔴"

    header = (
        f"🛡️ *BITÁCORA INMUTABLE DE AUDITORÍA*\n"
        f"• Estado Criptográfico: {status_icon} *{'Cadena Íntegra' if is_valid else 'Alerta de Tampering'}*\n"
        f"• Total Verificado: `{count}` registros analizados\n"
    )
    if target_query:
        header += f"• Filtro Objetivo: `{target_query}`\n"

    if not records:
        return header + "\nℹ️ _No se registran eventos para la consulta indicada._"

    lines = [header, "📋 *Últimos Eventos Registrados:*"]
    for r in records[:10]:
        rid = r.get("id")
        ts = r.get("timestamp") or ""
        act = r.get("action") or ""
        actor = r.get("actor") or ""
        ttype = r.get("target_type") or ""
        tid = r.get("target_id") or ""
        sig_short = (r.get("signature_hmac") or "")[:8]

        detail_parts = []
        if r.get("old_value"):
            detail_parts.append(f"prev: {r['old_value'][:20]}")
        if r.get("new_value"):
            detail_parts.append(f"nuevo: {r['new_value'][:20]}")
        detail_str = f" ({', '.join(detail_parts)})" if detail_parts else ""

        lines.append(
            f"• `#{rid}` *[{act}]* {ttype}#{tid}{detail_str}\n"
            f"  👤 Por: `{actor}` | 📅 `{ts}` | 🔒 `sig:{sig_short}...`"
        )

    return "\n".join(lines)


async def anchor_audit_root_to_telegram(chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Publica el hash raíz acumulado de la bitácora inmutable en Telegram como testigo externo inmutable (CRIT-04)."""
    from db.connection import get_connection
    from telegram_bot import send_telegram_message

    repo = _get_audit_repo()
    latest = repo.get_latest_audit_entry()
    latest_sig = latest.get("signature_hmac", GENESIS_HASH) if latest else GENESIS_HASH
    latest_id = latest.get("id", 0) if latest else 0

    conn = get_connection()
    try:
        count_row = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()
        total_count = count_row["c"] if count_row else 0
    finally:
        conn.close()

    utc_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"🔒 <b>[AUDIT ANCHOR — TESTIGO EXTERNO INMUTABLE]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Timestamp:</b> <code>{utc_now}</code>\n"
        f"• <b>Bloques Auditados:</b> <code>{total_count}</code> (Último ID: <code>#{latest_id}</code>)\n"
        f"• <b>Root Hash HMAC:</b>\n<code>sha256:{latest_sig}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <i>Sello criptográfico publicado de forma inmutable para verificación forense externa.</i>"
    )

    ok = await send_telegram_message(text=msg, chat_id=chat_id)
    logger.info(f"Ancla de auditoría publicada en Telegram: Root sha256:{latest_sig[:16]}... ({'OK' if ok else 'FALLO'})")
    return {
        "success": ok,
        "total_blocks": total_count,
        "latest_id": latest_id,
        "root_hash": latest_sig,
        "timestamp": utc_now
    }

