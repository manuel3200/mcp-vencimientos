"""
outbound_queue_service.py - Cola Persistente de Envío con Ritmo por Instancia (O05)
Garantiza:
1. Recepción rápida (202 Accepted) tras persistencia idempotente por (producer, idempotency_key).
2. Reclamo atómico con lease/expiración (BEGIN IMMEDIATE) y reserva de un único slot temporal por instancia.
3. Reintentos con backoff exponencial solo en errores recuperables.
4. Protección ante timeout ambiguo (estado 'ambiguous_review') para evitar reenvíos ciegos duplicados.
5. Consulta de estado individual y cola de fallos (dead_letter / ambiguous_review).
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from core.config import settings
from db.connection import get_connection

logger = logging.getLogger("services.outbound_queue")

FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "serverurl", "apiurl", "baseurl", "apikey", "api_key", "token", "webhookurl", "authorization"
})
MAX_OUTBOUND_PAYLOAD_BYTES = 16 * 1024  # 16 KB


def _allowed_instances() -> set:
    configured = (
        getattr(settings, "EVOLUTION_INSTANCE_NAME", "")
        or os.getenv("EVOLUTION_INSTANCE_NAME", "")
        or "streamvault"
    ).strip()
    extra = os.getenv("ALLOWED_WHATSAPP_INSTANCES", "").strip()
    instances = {configured}
    if extra:
        for item in extra.split(","):
            if item.strip():
                instances.add(item.strip())
    return instances


def _validate_recipient(recipient: str) -> str:
    clean = (recipient or "").strip()
    if not clean:
        raise ValueError("El destinatario ('recipient') es obligatorio.")
    if clean.endswith("@g.us") or clean.endswith("@newsletter") or clean.endswith("@s.whatsapp.net"):
        return clean
    digits = re.sub(r"\D", "", clean)
    if len(digits) < 10 or len(digits) > 15:
        raise ValueError("El destinatario debe ser un número E.164 válido (10-15 dígitos) o JID autorizado.")
    return digits


def enqueue_outbound_job(
    producer: str,
    idempotency_key: str,
    recipient: str,
    payload: Dict[str, Any],
    instance: Optional[str] = None,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    """Persiste una tarea de envío de forma idempotente antes de responder 202 Accepted (O05)."""
    clean_producer = (producer or "").strip()
    clean_key = (idempotency_key or "").strip()
    if not clean_producer or len(clean_producer) > 64:
        raise ValueError("El identificador 'producer' es obligatorio (máx. 64 caracteres).")
    if not clean_key or len(clean_key) < 6 or len(clean_key) > 128:
        raise ValueError("La clave 'idempotency_key' es obligatoria (6-128 caracteres).")

    allowed = _allowed_instances()
    default_inst = (
        getattr(settings, "EVOLUTION_INSTANCE_NAME", "")
        or os.getenv("EVOLUTION_INSTANCE_NAME", "")
        or "streamvault"
    ).strip()
    target_instance = (instance or default_inst).strip()
    if target_instance not in allowed:
        raise PermissionError(f"Instancia '{target_instance}' no autorizada para este emisor.")

    clean_recipient = _validate_recipient(recipient)

    if not isinstance(payload, dict):
        raise ValueError("El campo 'payload' debe ser un objeto JSON.")

    for key in payload.keys():
        if str(key).strip().lower() in FORBIDDEN_PAYLOAD_KEYS:
            raise PermissionError(f"Campo prohibido en payload de despacho: '{key}'.")

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_OUTBOUND_PAYLOAD_BYTES:
        raise ValueError("El payload excede el tamaño máximo permitido de 16 KB.")

    now = time.time()
    safe_max_attempts = max(1, min(int(max_attempts or 3), 10))

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM outbound_jobs WHERE producer = ? AND idempotency_key = ?",
            (clean_producer, clean_key),
        ).fetchone()
        if existing:
            conn.commit()
            result = dict(existing)
            result["duplicate"] = True
            return result

        cursor = conn.execute(
            """
            INSERT INTO outbound_jobs (
                producer, idempotency_key, instance, recipient, payload,
                status, attempts, max_attempts, next_attempt_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                clean_producer,
                clean_key,
                target_instance,
                clean_recipient,
                serialized,
                safe_max_attempts,
                now,
            ),
        )
        job_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (job_id,)).fetchone()
        conn.commit()
        result = dict(row) if row else {"id": job_id}
        result["duplicate"] = False
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_next_outbound_job(
    instance: Optional[str] = None,
    worker_id: str = "worker-main",
    min_interval_seconds: float = 15.0,
    lease_seconds: float = 60.0,
    now_override: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Reclama atómicamente la siguiente tarea lista respetando el slot único de cadencia por instancia (O05)."""
    target_instance = (
        instance
        or getattr(settings, "EVOLUTION_INSTANCE_NAME", "")
        or os.getenv("EVOLUTION_INSTANCE_NAME", "")
        or "streamvault"
    ).strip()
    now = float(now_override) if now_override is not None else time.time()
    interval = max(0.0, float(min_interval_seconds))
    lease_dur = max(5.0, float(lease_seconds))

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        # 1. Verificar el slot global de cadencia para esta instancia
        pacing_row = conn.execute(
            "SELECT next_slot_at FROM outbound_instance_pacing WHERE instance = ?",
            (target_instance,),
        ).fetchone()
        if pacing_row and float(pacing_row["next_slot_at"] or 0.0) > now:
            conn.commit()
            return None

        # 2. Buscar la tarea más antigua elegible (pendiente o con lease vencido)
        job_row = conn.execute(
            """
            SELECT * FROM outbound_jobs
            WHERE instance = ?
              AND attempts < max_attempts
              AND (
                  (status = 'pending' AND next_attempt_at <= ?)
                  OR (status = 'leased' AND lease_until IS NOT NULL AND lease_until <= ?)
              )
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT 1
            """,
            (target_instance, now, now),
        ).fetchone()

        if not job_row:
            conn.commit()
            return None

        job_id = job_row["id"]
        new_attempts = int(job_row["attempts"] or 0) + 1
        lease_until = now + lease_dur
        next_slot = now + interval

        # 3. Reservar el slot de la instancia y el lease de la tarea en la misma transacción
        conn.execute(
            """
            INSERT INTO outbound_instance_pacing (instance, next_slot_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(instance) DO UPDATE SET
                next_slot_at = excluded.next_slot_at,
                updated_at = excluded.updated_at
            """,
            (target_instance, next_slot, now),
        )

        conn.execute(
            """
            UPDATE outbound_jobs
            SET status = 'leased',
                attempts = ?,
                lease_until = ?,
                lease_owner = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_attempts, lease_until, worker_id, job_id),
        )
        updated = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (job_id,)).fetchone()
        conn.commit()
        return dict(updated) if updated else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_outbound_job(job_id: int, provider_response: Optional[Dict[str, Any]] = None) -> bool:
    """Marca una tarea como enviada exitosamente y guarda la respuesta del proveedor."""
    resp_json = json.dumps(provider_response or {"ok": True}, ensure_ascii=False)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE outbound_jobs
            SET status = 'sent',
                provider_response = ?,
                lease_until = NULL,
                lease_owner = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (resp_json, int(job_id)),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fail_outbound_job(
    job_id: int,
    error: str,
    recoverable: bool = True,
    ambiguous_timeout: bool = False,
    backoff_base_seconds: float = 30.0,
    now_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Registra fallo de envío.
    - Si ambiguous_timeout=True: pasa a 'ambiguous_review' y NO reenvía ciegamente (O05).
    - Si recoverable=True y quedan intentos: reprograma con backoff exponencial.
    - En caso contrario: mueve a 'dead_letter'.
    """
    now = float(now_override) if now_override is not None else time.time()
    clean_err = (error or "dispatch_error")[:500]

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (int(job_id),)).fetchone()
        if not row:
            conn.commit()
            return {}

        attempts = int(row["attempts"] or 1)
        max_attempts = int(row["max_attempts"] or 3)

        if ambiguous_timeout:
            new_status = "ambiguous_review"
            next_at = float(row["next_attempt_at"] or now)
        elif recoverable and attempts < max_attempts:
            new_status = "pending"
            delay = float(backoff_base_seconds) * (2 ** max(0, attempts - 1))
            next_at = now + delay
        else:
            new_status = "dead_letter"
            next_at = float(row["next_attempt_at"] or now)

        conn.execute(
            """
            UPDATE outbound_jobs
            SET status = ?,
                last_error = ?,
                next_attempt_at = ?,
                lease_until = NULL,
                lease_owner = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, clean_err, next_at, int(job_id)),
        )
        updated = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (int(job_id),)).fetchone()
        conn.commit()
        return dict(updated) if updated else {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_outbound_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Consulta el estado actual de una tarea de la cola de envío."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (int(job_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_failed_outbound_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retorna la cola de fallos ('dead_letter' y 'ambiguous_review') para revisión operativa."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM outbound_jobs
            WHERE status IN ('dead_letter', 'ambiguous_review')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
