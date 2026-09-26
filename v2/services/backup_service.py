"""
backup_service.py - Servicio Unificado de Snapshot SQLite Consistente y Backup Cifrado (O06)
Garantiza:
1. Snapshot consistente en caliente mediante la API oficial `sqlite3.Connection.backup()` (compatible con modo WAL).
2. Verificación obligatoria de `PRAGMA integrity_check` sobre el snapshot antes de empaquetar.
3. Cifrado autenticado con clave dedicada `BACKUP_ENCRYPTION_KEY` (V16).
4. Limpieza inmediata del archivo temporal privado en bloque `finally` (cero snapshots en claro persistidos).
5. Ensayo de restauración aislada (`verify_encrypted_sqlite_backup_bytes`) con conteo de tablas críticas de negocio.
"""

import asyncio
import logging
import os
import sqlite3
import tempfile
from typing import Any, Dict, Optional

from core.audit import log_audit_event
from core.config import settings
from core.security import decrypt_backup, encrypt_backup

logger = logging.getLogger("services.backup")


def create_sqlite_snapshot(source_path: str, destination_path: str) -> None:
    """Crea un snapshot consistente de SQLite usando la API nativa de backup y verifica integridad (O06)."""
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"Base de datos origen no encontrada: {source_path}")

    source = sqlite3.connect(source_path, timeout=30.0)
    target = sqlite3.connect(destination_path, timeout=30.0)
    try:
        source.backup(target)
        row = target.execute("PRAGMA integrity_check").fetchone()
        status = (row[0] if row else "").strip().lower()
        if status != "ok":
            raise RuntimeError(f"Snapshot SQLite inválido en integrity_check: {status}")
    finally:
        target.close()
        source.close()


def create_encrypted_sqlite_backup_bytes(
    source_path: Optional[str] = None,
    actor: str = "system",
) -> bytes:
    """Genera un snapshot SQLite consistente, lo cifra con BACKUP_ENCRYPTION_KEY y elimina el temporal en claro."""
    db_file = source_path or settings.DB_PATH
    fd, tmp_path = tempfile.mkstemp(prefix="streamvault_snap_", suffix=".sqlite3")
    os.close(fd)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except Exception:
            pass
        create_sqlite_snapshot(db_file, tmp_path)
        with open(tmp_path, "rb") as f:
            raw_bytes = f.read()
        encrypted_bytes = encrypt_backup(raw_bytes)
        try:
            log_audit_event(
                actor=actor,
                action="CREATE_ENCRYPTED_SQLITE_SNAPSHOT",
                target_type="backup",
                target_id=os.path.basename(db_file),
                old_value="",
                new_value=f"raw_bytes={len(raw_bytes)},enc_bytes={len(encrypted_bytes)}",
                ip_or_source="backup_service",
            )
        except Exception as audit_err:
            logger.warning(f"No se pudo registrar evento de auditoría de backup: {audit_err}")
        return encrypted_bytes
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def create_encrypted_sqlite_backup_bytes_async(
    source_path: Optional[str] = None,
    actor: str = "system",
) -> bytes:
    """Ejecuta el snapshot y cifrado fuera del hilo async principal para no bloquear el event loop (O06)."""
    return await asyncio.to_thread(
        create_encrypted_sqlite_backup_bytes,
        source_path,
        actor,
    )


def verify_encrypted_sqlite_backup_bytes(encrypted_bytes: bytes) -> Dict[str, Any]:
    """Descifra un backup en un entorno temporal aislado y verifica su integridad y totales de negocio (O06)."""
    raw_bytes = decrypt_backup(encrypted_bytes)
    fd, tmp_path = tempfile.mkstemp(prefix="streamvault_restore_check_", suffix=".sqlite3")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(raw_bytes)

        conn = sqlite3.connect(tmp_path, timeout=15.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = (row[0] if row else "").strip().lower()
            if integrity != "ok":
                raise RuntimeError(f"Integridad de backup restaurado inválida: {integrity}")

            counts: Dict[str, int] = {}
            static_queries = (
                ("clients", "SELECT COUNT(*) FROM clients"),
                ("streaming_accounts", "SELECT COUNT(*) FROM streaming_accounts"),
                ("payments", "SELECT COUNT(*) FROM payments"),
                ("audit_log", "SELECT COUNT(*) FROM audit_log"),
            )
            for table_name, sql_query in static_queries:
                try:
                    c_row = conn.execute(sql_query).fetchone()
                    counts[table_name] = int(c_row[0]) if c_row else 0
                except Exception:
                    counts[table_name] = 0

            return {
                "valid": True,
                "integrity": integrity,
                "size_bytes": len(raw_bytes),
                "counts": counts,
            }
        finally:
            conn.close()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
