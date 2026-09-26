"""
test_security_bloque3_p2.py - Suite 17 del Test Harness (StreamVault v2)
Valida todos los controles de operación y verificación reproducible del Bloque 3 (Prioridad P2):
- O05: Cola persistente de envío con idempotencia (producer, idempotency_key), slot único por instancia, backoff y protección ante timeout ambiguo.
- O06: Snapshot SQLite consistente mediante sqlite3.backup() + PRAGMA integrity_check, cifrado con BACKUP_ENCRYPTION_KEY y verificación de restauración aislada.
- O07: Pre-filtro de longitud base64 antes de decodificar, límites de dimensiones/píxeles, IP de proxy confiable y cuotas compartidas en SQLite.
- Q02 / Q03 / Q04 / Q05: Lockfile reproducible (requirements.lock con ==), runner unificado (harness_runner -> harness_verify) y acciones CI fijadas por SHA.
"""

import asyncio
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import database
from core.config import settings
from core.rate_limiter import (
    check_shared_sqlite_quota,
    get_trusted_client_ip,
)
from services.backup_service import (
    create_encrypted_sqlite_backup_bytes,
    verify_encrypted_sqlite_backup_bytes,
)
from services.outbound_queue_service import (
    claim_next_outbound_job,
    complete_outbound_job,
    enqueue_outbound_job,
    fail_outbound_job,
    get_outbound_job,
    list_failed_outbound_jobs,
)
from services.receipt_service import analyze_image_with_gemini


def run_tests():
    print("  [Suite 17] Controles de Operación y Verificación Reproducible Bloque 3 P2 (O05-O07, Q02-Q05)...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSecurityBloque3P2)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 17 (Seguridad Bloque 3 P2).")
    print(f"    ✅ Suite 17: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestSecurityBloque3P2(unittest.TestCase):

    # =========================================================================
    # 1. O05: COLA PERSISTENTE CON RITMO POR INSTANCIA E IDEMPOTENCIA
    # =========================================================================

    def test_o05_outbound_queue_idempotency_pacing_and_ambiguous_timeout(self):
        """Valida idempotencia por (producer, idempotency_key), cadencia global por instancia y estado ambiguous_review."""
        j1 = enqueue_outbound_job(
            producer="streamvault-core",
            idempotency_key="op-unique-001",
            recipient="+5491166099952",
            payload={"message": "Hola cliente 1"},
        )
        self.assertFalse(j1["duplicate"])

        # Reintento HTTP con misma idempotency_key no duplica la tarea
        j1_dup = enqueue_outbound_job(
            producer="streamvault-core",
            idempotency_key="op-unique-001",
            recipient="+5491166099952",
            payload={"message": "Hola cliente 1"},
        )
        self.assertTrue(j1_dup["duplicate"])
        self.assertEqual(j1["id"], j1_dup["id"])

        # Segunda tarea distinta lista para enviar
        j2 = enqueue_outbound_job(
            producer="streamvault-core",
            idempotency_key="op-unique-002",
            recipient="+5491166099953",
            payload={"message": "Hola cliente 2"},
        )
        self.assertFalse(j2["duplicate"])

        # Rechazar campos prohibidos que intenten redirigir URL/API key del proveedor (O04/O05)
        with self.assertRaises(PermissionError):
            enqueue_outbound_job(
                producer="streamvault-core",
                idempotency_key="op-evil-003",
                recipient="+5491166099952",
                payload={"message": "test", "serverUrl": "https://evil.example.com"},
            )

        # Reclamo con ritmo por instancia: t=1000 reclama j1 y reserva slot hasta t=1020
        base_t = 2_000_000_000.0
        claimed1 = claim_next_outbound_job(worker_id="w1", min_interval_seconds=20.0, now_override=base_t)
        self.assertIsNotNone(claimed1)
        self.assertEqual(claimed1["id"], j1["id"])
        self.assertEqual(claimed1["status"], "leased")

        # En t=1005 (dentro de la ventana de 20s) otro worker NO puede reclamar j2 (evita ráfaga concurrente)
        claimed_early = claim_next_outbound_job(worker_id="w2", min_interval_seconds=20.0, now_override=base_t + 5.0)
        self.assertIsNone(claimed_early)

        # Completar j1
        self.assertTrue(complete_outbound_job(j1["id"], {"provider_id": "MSG-1"}))
        self.assertEqual(get_outbound_job(j1["id"])["status"], "sent")

        # En t=1021 (vencido el slot de 20s) ahora sí se puede reclamar j2
        claimed2 = claim_next_outbound_job(worker_id="w2", min_interval_seconds=20.0, now_override=base_t + 21.0)
        self.assertIsNotNone(claimed2)
        self.assertEqual(claimed2["id"], j2["id"])

        # Simular timeout ambiguo en j2: pasa a 'ambiguous_review' y NO se reenvía ciegamente
        failed_amb = fail_outbound_job(
            j2["id"],
            error="ReadTimeout waiting for Evolution response",
            ambiguous_timeout=True,
            now_override=base_t + 25.0,
        )
        self.assertEqual(failed_amb["status"], "ambiguous_review")

        # No debe volver a reclamarse automáticamente mientras esté en ambiguous_review
        claimed_after_amb = claim_next_outbound_job(worker_id="w1", min_interval_seconds=20.0, now_override=base_t + 100.0)
        self.assertIsNone(claimed_after_amb)

        failed_list = list_failed_outbound_jobs(limit=10)
        self.assertTrue(any(item["id"] == j2["id"] and item["status"] == "ambiguous_review" for item in failed_list))

    # =========================================================================
    # 2. O06: SNAPSHOT SQLITE CONSISTENTE Y BACKUP CIFRADO VERIFICABLE
    # =========================================================================

    def test_o06_consistent_sqlite_snapshot_and_isolated_restore_verification(self):
        """Genera un snapshot SQLite consistente, lo cifra con clave dedicada y verifica su restauración."""
        database.find_or_create_client(name="Cliente Snapshot O06", whatsapp="5491177778888")

        encrypted_blob = create_encrypted_sqlite_backup_bytes(actor="test_suite_17")
        self.assertTrue(len(encrypted_blob) > 100)
        self.assertFalse(encrypted_blob.startswith(b"SQLite format 3"), "El backup entregado nunca debe estar en claro")

        restore_report = verify_encrypted_sqlite_backup_bytes(encrypted_blob)
        self.assertTrue(restore_report["valid"])
        self.assertEqual(restore_report["integrity"], "ok")
        self.assertGreaterEqual(restore_report["counts"]["clients"], 1)

        # Clave incorrecta debe fallar al intentar restaurar/verificar
        with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "wrong-key-for-backup-restore-test"), patch.object(
            settings, "SESSION_SECRET_KEY", "wrong-session-key-for-restore-test"
        ):
            with self.assertRaises(ValueError):
                verify_encrypted_sqlite_backup_bytes(encrypted_blob)

    # =========================================================================
    # 3. O07: PRE-FILTRO BASE64, PROXY CONFIABLE Y CUOTAS COMPARTIDAS SQLITE
    # =========================================================================

    def test_o07_base64_precheck_trusted_proxy_and_shared_sqlite_quota(self):
        """Rechaza base64 gigante antes de b64decode, valida X-Forwarded-For por proxy confiable y persiste cuotas."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_test_key"}, clear=False):
            huge_b64 = "A" * (11 * 1024 * 1024 + 64)
            res = asyncio.run(analyze_image_with_gemini(huge_b64))
            self.assertIsNotNone(res)
            self.assertFalse(res["is_receipt"])
            self.assertIn("base64 excesivo", res["summary"])

        # X-Forwarded-For desde IP NO confiable debe ignorarse (usa peer IP directa)
        untrusted_req = SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.77"),
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        self.assertEqual(get_trusted_client_ip(untrusted_req), "198.51.100.77")

        # X-Forwarded-For desde proxy confiable (127.0.0.1) sí extrae el primer salto
        trusted_req = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"X-Forwarded-For": "203.0.113.50, 127.0.0.1"},
        )
        self.assertEqual(get_trusted_client_ip(trusted_req), "203.0.113.50")

        # Cuota compartida en SQLite
        bucket = "test_o07_bucket_ip_203.0.113.50"
        ok1, _ = check_shared_sqlite_quota(bucket, limit=2, window_seconds=60.0, cooldown_seconds=120.0)
        ok2, _ = check_shared_sqlite_quota(bucket, limit=2, window_seconds=60.0, cooldown_seconds=120.0)
        ok3, retry_after = check_shared_sqlite_quota(bucket, limit=2, window_seconds=60.0, cooldown_seconds=120.0)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertFalse(ok3)
        self.assertGreater(retry_after, 0)

    # =========================================================================
    # 4. Q02 / Q04 / Q05: RUNNER UNIFICADO, LOCKFILE Y ACCIONES CI CON SHA
    # =========================================================================

    def test_q02_q04_q05_reproducible_lockfile_unified_runner_and_pinned_ci(self):
        """Verifica que harness_runner delegue a harness_verify, requirements.lock use '==' y workflows usen SHAs."""
        import harness_verify
        from tests import harness_runner

        self.assertEqual(harness_runner.CANONICAL_SUITES, harness_verify.CANONICAL_SUITES)

        v2_dir = Path(__file__).resolve().parent.parent
        lock_path = v2_dir / "requirements.lock"
        self.assertTrue(lock_path.exists(), "Debe existir v2/requirements.lock")
        lock_lines = [
            ln.strip()
            for ln in lock_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.assertGreaterEqual(len(lock_lines), 13)
        for ln in lock_lines:
            self.assertIn("==", ln, f"Dependencia no fijada con '==' en requirements.lock: {ln}")

        workflows_dir = v2_dir.parent / ".github" / "workflows"
        if workflows_dir.exists():
            for wf_file in ("docker.yml", "security_pipeline.yml"):
                wf_path = workflows_dir / wf_file
                if wf_path.exists():
                    wf_text = wf_path.read_text(encoding="utf-8")
                    uses_matches = re.findall(r"uses:\s*([^\s#]+)", wf_text)
                    self.assertGreater(len(uses_matches), 0)
                    for u in uses_matches:
                        self.assertRegex(
                            u,
                            r"@[0-9a-f]{40}$",
                            f"La acción CI '{u}' en {wf_file} debe estar fijada a un commit SHA de 40 caracteres (Q05).",
                        )
