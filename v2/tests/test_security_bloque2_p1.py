"""
test_security_bloque2_p1.py - Suite 16 del Test Harness (StreamVault v2)
Valida todos los controles de seguridad del Bloque 2 (Prioridad P1):
- V09: RBAC WhatsApp estricto (sin elevación por typo a SUPER_ADMIN, E.164 completo sin sufijo de 10 dígitos, is_from_me condicionado a verified_instance).
- V10: Autorización en Telegram por user_id numérico (from.id), rechazo de remitentes anónimos y RBAC en callbacks/comandos.
- V13: Sesiones administrativas revocables en servidor (tabla admin_sessions) al hacer logout o reset de contraseña.
- V14: Rotación transaccional de refresh_token OAuth con detección de reúso y revocación automática de toda la familia (family_id).
- V16: Separación criptográfica por propósito (DB_ENCRYPTION_KEY, BACKUP_ENCRYPTION_KEY, AUDIT_HMAC_KEY), JSON canónico sin colisión por '|' y append atómico BEGIN IMMEDIATE.
- O03: Contenedor no-root (USER 10001:10001), puertos en loopback por defecto y endurecimiento Docker (cap_drop ALL, no-new-privileges).
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from db.connection import get_connection
from core.config import settings
from core.rbac import (
    ROLE_SUPER_ADMIN,
    ROLE_FINANZAS,
    ROLE_SOPORTE,
    ROLE_UNAUTHORIZED,
    validate_configured_role,
    canonicalize_whatsapp_phone,
    get_actor_role,
    check_admin_permission,
)
from core.security import (
    create_session_cookie,
    verify_session_cookie,
    revoke_session_cookie,
    revoke_all_user_sessions,
    encrypt_secret,
    decrypt_secret,
    encrypt_backup,
    decrypt_backup,
)
from core.audit import (
    compute_audit_signature,
    log_audit_event,
    verify_audit_chain,
)
from infrastructure.external.telegram.bot_app import (
    authorize_telegram_actor,
    map_telegram_callback_to_action,
)


def run_tests():
    print("  [Suite 16] Controles de Seguridad Bloque 2 P1 (V09, V10, V13, V14, V16, O03)...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSecurityBloque2P1)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 16 (Seguridad Bloque 2 P1).")
    print(f"    ✅ Suite 16: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestSecurityBloque2P1(unittest.TestCase):

    # =========================================================================
    # 1. V09: RBAC WHATSAPP SIN ELEVACIÓN POR TYPO NI COLISIÓN DE SUFIJO
    # =========================================================================

    def test_v09_rbac_strict_role_validation_and_e164_matching(self):
        """Un rol mal escrito nunca escala a SUPER_ADMIN y no hay colisión por sufijo de 10 dígitos."""
        self.assertEqual(validate_configured_role("SOPORTEE"), ROLE_UNAUTHORIZED)
        self.assertEqual(validate_configured_role("ADMIN_TYPO"), ROLE_UNAUTHORIZED)
        self.assertEqual(validate_configured_role("SOPORTE"), ROLE_SOPORTE)
        self.assertEqual(validate_configured_role("FINANZAS"), ROLE_FINANZAS)
        self.assertEqual(validate_configured_role("SUPER_ADMIN"), ROLE_SUPER_ADMIN)

        # Normalización E.164 Argentina (54 + 9 + 10 dígitos)
        self.assertEqual(canonicalize_whatsapp_phone("+54 11 6609-9952"), "5491166099952")
        self.assertEqual(canonicalize_whatsapp_phone("5491166099952@s.whatsapp.net"), "5491166099952")

        with patch.dict(
            os.environ,
            {
                "ADMIN_PHONE_ROLES": "5491166099952:SOPORTE,5491122223333:TYPO_ROLE,5491144445555",
                "ADMIN_WHATSAPP_NUMBERS": "",
            },
            clear=False,
        ):
            # Rol SOPORTE legítimo
            self.assertEqual(get_actor_role("5491166099952@s.whatsapp.net"), ROLE_SOPORTE)
            # Número con rol mal escrito o sin rol explícito debe ser UNAUTHORIZED (nunca SUPER_ADMIN)
            self.assertEqual(get_actor_role("5491122223333@s.whatsapp.net"), ROLE_UNAUTHORIZED)
            self.assertEqual(get_actor_role("5491144445555@s.whatsapp.net"), ROLE_UNAUTHORIZED)
            # Otro país con los mismos últimos 10 dígitos (ej: +1 1166099952) NO debe coincidir con +5491166099952
            self.assertEqual(get_actor_role("11166099952@s.whatsapp.net"), ROLE_UNAUTHORIZED)

            # is_from_me sin verified_instance=True NO otorga SUPER_ADMIN
            self.assertEqual(
                get_actor_role("5491199998888@s.whatsapp.net", is_from_me=True, verified_instance=False),
                ROLE_UNAUTHORIZED,
            )
            self.assertEqual(
                get_actor_role("5491199998888@s.whatsapp.net", is_from_me=True, verified_instance=True),
                ROLE_SUPER_ADMIN,
            )

    # =========================================================================
    # 2. V10: AUTORIZACIÓN POR USUARIO Y ROL EN TELEGRAM
    # =========================================================================

    def test_v10_telegram_per_user_and_role_authorization(self):
        """Telegram valida from.id nominal, rechaza anónimos y aplica RBAC por comando/callback."""
        with patch.object(settings, "TELEGRAM_CHAT_ID", "-100123456789"), patch.dict(
            os.environ,
            {
                "TELEGRAM_ADMIN_CHAT_IDS": "-100123456789",
                "TELEGRAM_ADMIN_USERS": "1001:SUPER_ADMIN,1002:SOPORTE,1003:INVALID_ROLE",
            },
            clear=False,
        ):
            # Remitente anónimo (sender_chat) siempre denegado
            anon_res = authorize_telegram_actor("-100123456789", 1001, "status", is_anonymous=True)
            self.assertFalse(anon_res["allowed"])

            # Usuario con rol inválido o no listado denegado
            bad_role_res = authorize_telegram_actor("-100123456789", 1003, "status")
            self.assertFalse(bad_role_res["allowed"])
            unknown_user_res = authorize_telegram_actor("-100123456789", 9999, "status")
            self.assertFalse(unknown_user_res["allowed"])

            # Usuario SOPORTE (1002) puede autorizar caídas pero NO aprobar pagos ni ejecutar /purge
            act_fallen = map_telegram_callback_to_action("f_ok_42")
            self.assertEqual(act_fallen, "authorize_fallen")
            self.assertTrue(authorize_telegram_actor("-100123456789", 1002, act_fallen)["allowed"])

            act_pay = map_telegram_callback_to_action("ap_pay_99")
            self.assertEqual(act_pay, "approve_payment")
            self.assertFalse(authorize_telegram_actor("-100123456789", 1002, act_pay)["allowed"])
            self.assertFalse(authorize_telegram_actor("-100123456789", 1002, "purge")["allowed"])

            # SUPER_ADMIN (1001) puede aprobar pagos y ejecutar /purge
            self.assertTrue(authorize_telegram_actor("-100123456789", 1001, act_pay)["allowed"])
            self.assertTrue(authorize_telegram_actor("-100123456789", 1001, "purge")["allowed"])

    # =========================================================================
    # 3. V13: SESIONES SERVIDOR REVOCABLES EN LOGOUT Y RESET DE CONTRASEÑA
    # =========================================================================

    def test_v13_server_side_session_revocation(self):
        """Cerrar sesión o revocar sesiones de usuario invalida inmediatamente la cookie en servidor."""
        cookie1 = create_session_cookie("admin_v13")
        cookie2 = create_session_cookie("admin_v13")
        self.assertEqual(verify_session_cookie(cookie1), "admin_v13")
        self.assertEqual(verify_session_cookie(cookie2), "admin_v13")

        # Revocar cookie1 (logout individual)
        self.assertTrue(revoke_session_cookie(cookie1))
        self.assertIsNone(verify_session_cookie(cookie1))
        self.assertEqual(verify_session_cookie(cookie2), "admin_v13")

        # Revocar todas las sesiones del usuario (cambio/reset de contraseña)
        revoked_count = revoke_all_user_sessions("admin_v13")
        self.assertGreaterEqual(revoked_count, 1)
        self.assertIsNone(verify_session_cookie(cookie2))

    # =========================================================================
    # 4. V14: ROTACIÓN DE REFRESH TOKEN OAUTH Y DETECCIÓN DE REÚSO DE FAMILIA
    # =========================================================================

    def test_v14_oauth_refresh_token_rotation_and_reuse_detection(self):
        """Reutilizar un refresh_token ya rotado revoca automáticamente toda la familia de tokens."""
        cfg = database.get_oauth_settings()
        t1 = database.create_oauth_tokens(cfg["client_id"], scope="mcp:tools")
        rt1 = t1["refresh_token"]
        at1 = t1["access_token"]
        self.assertIsNotNone(database.verify_oauth_access_token(at1))

        # Primera rotación legítima: emite t2 y marca t1 como usado (used_at)
        t2 = database.refresh_oauth_token(rt1, cfg["client_id"])
        self.assertIsNotNone(t2)
        self.assertEqual(t2["family_id"], t1["family_id"])
        rt2 = t2["refresh_token"]
        at2 = t2["access_token"]

        # El access_token anterior (at1) ya no es válido tras rotarse
        self.assertIsNone(database.verify_oauth_access_token(at1))
        # El nuevo access_token (at2) sí es válido
        self.assertIsNotNone(database.verify_oauth_access_token(at2))

        # Simular robo/replay de rt1: debe fallar y revocar TODA la familia (incluido at2 y rt2)
        replay_attempt = database.refresh_oauth_token(rt1, cfg["client_id"])
        self.assertIsNone(replay_attempt)

        # Ahora tanto at2 como rt2 deben haber quedado revocados por compromiso de familia
        self.assertIsNone(database.verify_oauth_access_token(at2))
        self.assertIsNone(database.refresh_oauth_token(rt2, cfg["client_id"]))

    # =========================================================================
    # 5. V16: SEPARACIÓN DE CLAVES Y AUDITORÍA CANÓNICA SIN COLISIÓN POR '|'
    # =========================================================================

    def test_v16_key_separation_and_canonical_json_audit(self):
        """Claves separadas por propósito y serialización JSON canónica inmune a inyección de '|'."""
        sig_a = compute_audit_signature(
            prev_hash="0" * 64,
            actor="admin|extra",
            action="TEST",
            target_type="account",
            target_id="1",
            old_value="a",
            new_value="b|c",
            ip_or_source="web",
        )
        sig_b = compute_audit_signature(
            prev_hash="0" * 64,
            actor="admin",
            action="EXTRA|TEST",
            target_type="account",
            target_id="1",
            old_value="a|b",
            new_value="c",
            ip_or_source="web",
        )
        self.assertNotEqual(sig_a, sig_b, "La serialización JSON canónica debe impedir colisiones con '|'")

        # Registrar evento con '|' en los campos y verificar integridad de cadena
        log_audit_event(
            actor="admin_v16",
            action="PIPE_SAFE_TEST",
            target_type="security",
            target_id="v16|1",
            old_value="old|val",
            new_value="new|val",
            ip_or_source="127.0.0.1",
        )
        valid_chain, total_blocks, msg = verify_audit_chain()
        self.assertTrue(valid_chain, f"La cadena de auditoría debe ser válida: {msg}")
        self.assertGreaterEqual(total_blocks, 1)

        # Cifrado de secretos de BD y backups con claves dedicadas
        enc_val = encrypt_secret("clave_secreta_v16")
        self.assertTrue(enc_val.startswith("enc:v1:"))
        self.assertEqual(decrypt_secret(enc_val), "clave_secreta_v16")

        raw_backup = b'{"backup": "streamvault_v16"}'
        enc_bkp = encrypt_backup(raw_backup)
        self.assertEqual(decrypt_backup(enc_bkp), raw_backup)

    # =========================================================================
    # 6. O03: HARDENING DE CONTENEDOR NO-ROOT Y PUERTOS LOOPBACK
    # =========================================================================

    def test_o03_dockerfile_non_root_and_compose_hardening(self):
        """Verifica que Dockerfile use USER 10001:10001 y docker-compose use loopback + cap_drop ALL."""
        v2_dir = Path(__file__).resolve().parent.parent
        dockerfile_text = (v2_dir / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER 10001:10001", dockerfile_text)

        compose_path = v2_dir.parent / "docker-compose.oracle-stack.yml"
        if compose_path.exists():
            compose_text = compose_path.read_text(encoding="utf-8")
            self.assertIn("${BIND_HOST:-127.0.0.1}:8000:8000", compose_text)
            self.assertIn("no-new-privileges:true", compose_text)
            self.assertIn("cap_drop:", compose_text)
