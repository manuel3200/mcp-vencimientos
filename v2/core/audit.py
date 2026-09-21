"""
audit.py - Log de Auditoría Inmutable con Firma Criptográfica Encadenada (Blockchain Liviana)
Garantiza no repudio, trazabilidad forense y detección inmediata de tampering
para operaciones críticas (cambio de claves maestras, pagos, bajas de cuentas, etc.).
"""

import os
import hmac
import hashlib
import secrets
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from core.config import settings
from db.repositories.audit_repo import (
    insert_audit_entry,
    get_latest_audit_entry,
    get_all_audit_entries_asc,
    list_audit_history
)

logger = logging.getLogger("core.audit")

GENESIS_HASH = "0" * 64


def get_audit_hmac_key() -> str:
    """Obtiene la clave simétrica para la firma HMAC del log de auditoría."""
    return (
        getattr(settings, "AUDIT_HMAC_KEY", "") or
        os.getenv("AUDIT_HMAC_KEY", "") or
        settings.SESSION_SECRET_KEY
    )


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
    """Calcula la firma HMAC-SHA256 del bloque de auditoría uniendo sus campos canónicos."""
    hmac_key = key or get_audit_hmac_key()
    canonical_payload = (
        f"{prev_hash}|{actor.strip()}|{action.strip().upper()}|"
        f"{target_type.strip().lower()}|{str(target_id).strip()}|"
        f"{old_value.strip() if old_value else ''}|"
        f"{new_value.strip() if new_value else ''}|"
        f"{ip_or_source.strip() if ip_or_source else ''}"
    )
    sig = hmac.new(
        hmac_key.encode("utf-8"),
        canonical_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return sig


def log_audit_event(
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str = "",
    new_value: str = "",
    ip_or_source: str = ""
) -> Dict[str, Any]:
    """Registra de forma inmutable una operación crítica en la bitácora criptográfica.
    
    Toma el signature_hmac del último bloque insertado como prev_hash y genera
    una nueva firma HMAC-SHA256 encadenada.
    """
    latest = get_latest_audit_entry()
    prev_hash = latest["signature_hmac"] if latest and latest.get("signature_hmac") else GENESIS_HASH

    sig = compute_audit_signature(
        prev_hash=prev_hash,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        ip_or_source=ip_or_source
    )

    entry = insert_audit_entry(
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
    logger.info(
        f"🔒 AUDIT_LOG #{entry.get('id')}: [{action}] por '{actor}' en {target_type}#{target_id} "
        f"(sig: {sig[:12]}...)"
    )
    return entry


def verify_audit_chain(key: Optional[str] = None) -> Tuple[bool, int, str]:
    """Verifica la integridad criptográfica de extremo a extremo de la bitácora.
    
    Retorna:
    (is_valid: bool, total_checked: int, details: str)
    Si algún registro fue modificado o borrado manualmente en SQLite, la función
    detecta la inconsistencia e identifica el ID del registro corrompido.
    """
    entries = get_all_audit_entries_asc()
    if not entries:
        return True, 0, "Bitácora vacía: sin registros que auditar."

    hmac_key = key or get_audit_hmac_key()
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

        # 2. Recomputar firma HMAC del bloque actual
        computed_sig = compute_audit_signature(
            prev_hash=prev_hash,
            actor=entry.get("actor") or "",
            action=entry.get("action") or "",
            target_type=entry.get("target_type") or "",
            target_id=entry.get("target_id") or "",
            old_value=entry.get("old_value") or "",
            new_value=entry.get("new_value") or "",
            ip_or_source=entry.get("ip_or_source") or "",
            key=hmac_key
        )

        if not secrets.compare_digest(sig_stored, computed_sig):
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
    return list_audit_history(target_id=target_id, target_type=target_type, limit=limit)


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

    latest = get_latest_audit_entry()
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

