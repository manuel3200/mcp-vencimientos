"""
audit_repo.py - Repositorio SQLite para Log de Auditoría Inmutable (Append-Only)
Persiste y consulta registros de auditoría protegidos con firmas criptográficas encadenadas.
"""

import logging
from typing import Dict, Any, List, Optional
from db.connection import get_connection

logger = logging.getLogger("database.audit")


def insert_audit_entry(
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old_value: str,
    new_value: str,
    ip_or_source: str,
    prev_hash: str,
    signature_hmac: str
) -> Dict[str, Any]:
    """Inserta una entrada de auditoría inmutable de forma atómica en SQLite."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO audit_log (
                    actor, action, target_type, target_id,
                    old_value, new_value, ip_or_source,
                    prev_hash, signature_hmac
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                actor.strip(),
                action.strip().upper(),
                target_type.strip().lower(),
                str(target_id).strip(),
                old_value.strip() if old_value else "",
                new_value.strip() if new_value else "",
                ip_or_source.strip() if ip_or_source else "",
                prev_hash.strip(),
                signature_hmac.strip()
            ))
            new_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (new_id,)).fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def get_latest_audit_entry() -> Optional[Dict[str, Any]]:
    """Obtiene el registro de auditoría más reciente para encadenar el siguiente bloque."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM audit_log ORDER BY id DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_audit_entries_asc() -> List[Dict[str, Any]]:
    """Obtiene todas las entradas en orden cronológico ascendente para verificación de cadena."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM audit_log ORDER BY id ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_audit_history(
    target_id: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Consulta el historial de auditoría con filtros opcionales."""
    conn = get_connection()
    try:
        query = "SELECT * FROM audit_log"
        params: List[Any] = []
        conditions = []

        if target_id:
            conditions.append("target_id = ?")
            params.append(str(target_id).strip())
        if target_type:
            conditions.append("target_type = ?")
            params.append(str(target_type).strip().lower())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
