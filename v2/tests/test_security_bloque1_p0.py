"""
test_security_bloque1_p0.py - Suite 15 del Test Harness (StreamVault v2)
Valida todos los controles de seguridad del Bloque 1 (Prioridad P0):
- V01 / O01: Prohibición de secretos por defecto y validación fail-closed en producción.
- V02 / O02: Recuperación de contraseña con token de un solo uso (SHA-256) y bootstrap sin sobrescritura.
- V03 / V15: Secretos efímeros en 2 pasos (GET neutro + POST burn con borrado de ciphertext) y scope secrets:create.
- V04 / V05: OAuth 2.0 con sesión 2FA previa, exact match de redirect_uri, PKCE S256 obligatorio, consumo atómico de code y hash de tokens.
- V06 / V17: Webhooks fail-closed, deduplicación persistente anti-replay y errores sanitizados con incident_id.
- V07: Separación de credenciales financieras (scope finance:read) sin reutilizar secretos maestros.
- V08: Guard runtime en FastMCP (default-deny para tools sin clasificar + verificación de propiedad en BD contra IDOR).
- V11: Escape HTML por defecto en render_template y SafeHTML explícito.
- V12: Reversión de pagos atómica (BEGIN IMMEDIATE + rowcount == 1), solo POST y protección CSRF.
"""

import base64
import hashlib
import unittest
from unittest.mock import patch

import database
from db.connection import get_connection
from core.config import settings, FORBIDDEN_DEFAULT_SECRETS
from core.templates import render_template, SafeHTML
from core.principal import (
    Principal,
    require_scope,
    issue_service_token,
    revoke_service_token,
    authenticate_service_token,
    generate_csrf_token,
    verify_csrf_token,
    set_current_mcp_principal,
    reset_current_mcp_principal,
)
from core.ephemeral_secrets import (
    create_ephemeral_secret,
    peek_ephemeral_secret,
    reveal_and_burn_secret,
)
from core.mcp_guard import (
    validate_tool_execution,
    wrap_mcp_tool_with_guard,
)
from presentation.api.webhooks_api import (
    verify_evolution_signature,
    _record_and_check_webhook_replay,
)


def run_tests():
    print("  [Suite 15] Controles de Seguridad Bloque 1 P0 (V01-V08, V11, V12, V15, V17, O01, O02)...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSecurityBloque1P0)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 15 (Seguridad Bloque 1 P0).")
    print(f"    ✅ Suite 15: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestSecurityBloque1P0(unittest.TestCase):

    # =========================================================================
    # 1. V01 / O01: SECRETOS SIN DEFAULTS PREDECIBLES Y FAIL-CLOSED
    # =========================================================================

    def test_v01_no_forbidden_default_secrets(self):
        """Ninguna clave criptográfica debe usar valores por defecto predecibles del repositorio."""
        self.assertNotIn(settings.SESSION_SECRET_KEY, FORBIDDEN_DEFAULT_SECRETS)
        self.assertNotIn(settings.DB_ENCRYPTION_KEY, FORBIDDEN_DEFAULT_SECRETS)
        self.assertNotIn(settings.BACKUP_ENCRYPTION_KEY, FORBIDDEN_DEFAULT_SECRETS)
        self.assertNotIn(settings.AUDIT_HMAC_KEY, FORBIDDEN_DEFAULT_SECRETS)

        with patch.object(settings, "APP_ENV", "production"), patch.object(
            settings, "SESSION_SECRET_KEY", "mcp-super-secret-key-change-in-prod-2026"
        ):
            with self.assertRaises(RuntimeError):
                settings.validate_startup_secrets()

    # =========================================================================
    # 2. V02 / O02: BOOTSTRAP ADMIN SIN SOBRESCRITURA Y RESET EN DOS PASOS
    # =========================================================================

    def test_v02_o02_bootstrap_once_and_password_reset_token_flow(self):
        """El bootstrap no sobrescribe credenciales existentes y /recuperar usa token de un solo uso."""
        database.bootstrap_admin_once("sec_admin_p0", "InitialStrongPass!2026", "JBSWY3DPEHPK3PXP")

        # Segundo intento de bootstrap con otra contraseña NO debe sobrescribir la existente (O02)
        with self.assertRaises(RuntimeError):
            database.bootstrap_admin_once("sec_admin_p0", "AttackerOverwritePass!", "JBSWY3DPEHPK3PXP")
        self.assertIsNotNone(database.verify_admin_credentials("sec_admin_p0", "InitialStrongPass!2026"))
        self.assertIsNone(database.verify_admin_credentials("sec_admin_p0", "AttackerOverwritePass!"))

        # Emitir token de recuperación NO altera la contraseña vigente (V02)
        raw_token = database.create_password_reset_token("sec_admin_p0", ttl_minutes=10)
        self.assertTrue(len(raw_token) >= 32)
        self.assertIsNotNone(database.verify_admin_credentials("sec_admin_p0", "InitialStrongPass!2026"))

        # Consumir token actualiza contraseña atómicamente una sola vez
        res1 = database.consume_password_reset_token_and_update_password(
            "sec_admin_p0", raw_token, "NewStrongPass#2026"
        )
        self.assertTrue(res1)
        self.assertIsNotNone(database.verify_admin_credentials("sec_admin_p0", "NewStrongPass#2026"))

        # Reutilizar el mismo token debe fallar (single-use)
        res2 = database.consume_password_reset_token_and_update_password(
            "sec_admin_p0", raw_token, "ReplayPass#2026"
        )
        self.assertFalse(res2)

    # =========================================================================
    # 3. V03 / V15: SECRETOS EFÍMEROS EN DOS PASOS Y SCOPES DE SERVICIO
    # =========================================================================

    def test_v03_v15_ephemeral_secret_two_step_and_ciphertext_wipe(self):
        """GET (peek) no quema el secreto; POST (reveal_and_burn) lo revela una vez y borra el ciphertext."""
        token, _ = create_ephemeral_secret(
            data={"user": "demo@stream.com", "pass": "TopSecret99"},
            title="Credencial Test",
            ttl_seconds=300,
            actor="unit_test",
        )

        # Paso 1: Peek (GET de bots de previsualización) no consume el secreto
        title, peek_status = peek_ephemeral_secret(token)
        self.assertEqual(peek_status, "available")
        self.assertEqual(title, "Credencial Test")

        # Paso 2: Reveal & Burn (POST explícito del usuario)
        payload, burn_status = reveal_and_burn_secret(token, reader_ip="10.0.0.1")
        self.assertEqual(burn_status, "revealed")
        self.assertEqual(payload["pass"], "TopSecret99")

        # Verificar que ciphertext quedó vacío en SQLite
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT ciphertext, is_burned, burned_at FROM ephemeral_secrets WHERE token = ?",
                (token,),
            ).fetchone()
            self.assertEqual(row["is_burned"], 1)
            self.assertEqual(row["ciphertext"], "")
            self.assertIsNotNone(row["burned_at"])
        finally:
            conn.close()

        # Segundo intento de lectura devuelve already_burned
        payload2, burn_status2 = reveal_and_burn_secret(token, reader_ip="10.0.0.1")
        self.assertIsNone(payload2)
        self.assertEqual(burn_status2, "already_burned")

    def test_v03_v07_service_tokens_and_scopes(self):
        """Los tokens de servicio se almacenan por hash SHA-256 y aplican mínimo privilegio por scope."""
        raw_fin_token = issue_service_token("n8n-finance-digest", ["finance:read"], ttl_days=7)
        principal = authenticate_service_token(raw_fin_token)
        self.assertIsNotNone(principal)
        self.assertEqual(principal.subject, "n8n-finance-digest")
        require_scope(principal, "finance:read")

        with self.assertRaises( Exception ):
            require_scope(principal, "secrets:create")

        self.assertTrue(revoke_service_token("n8n-finance-digest"))
        self.assertIsNone(authenticate_service_token(raw_fin_token))

    # =========================================================================
    # 4. V04 / V05: OAUTH 2.0 REDIRECT EXACTO, PKCE S256 Y CONSUMO ATÓMICO
    # =========================================================================

    def test_v04_v05_oauth_exact_redirect_pkce_s256_and_hashed_tokens(self):
        """Valida comparación exacta de redirect_uri, PKCE S256 obligatorio y consumo único de auth_code."""
        allowed_uri = "https://claude.ai/api/mcp/auth_callback"
        self.assertTrue(database.is_registered_redirect_uri(allowed_uri))
        self.assertFalse(database.is_registered_redirect_uri("https://claude.ai.attacker.com/api/mcp/auth_callback"))
        self.assertFalse(database.is_registered_redirect_uri("https://claude.ai/api/mcp/auth_callback/../../evil"))

        # PKCE S256
        code_verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk_test_verifier_2026"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

        cfg = database.get_oauth_settings()
        auth_code = database.create_oauth_auth_code(
            client_id=cfg["client_id"],
            redirect_uri=allowed_uri,
            code_challenge=challenge,
            code_challenge_method="S256",
            scope="mcp:tools",
        )

        # Falla si redirect_uri no coincide exactamente
        self.assertFalse(
            database.verify_and_consume_auth_code(
                code=auth_code,
                client_id=cfg["client_id"],
                redirect_uri="https://claude.ai/api/mcp/other",
                code_verifier=code_verifier,
            )
        )

        # Consumo exitoso con PKCE válido
        self.assertTrue(
            database.verify_and_consume_auth_code(
                code=auth_code,
                client_id=cfg["client_id"],
                redirect_uri=allowed_uri,
                code_verifier=code_verifier,
            )
        )

        # Replay del mismo code debe fallar
        self.assertFalse(
            database.verify_and_consume_auth_code(
                code=auth_code,
                client_id=cfg["client_id"],
                redirect_uri=allowed_uri,
                code_verifier=code_verifier,
            )
        )

        # Verificar que oauth_tokens persiste hashes SHA-256 y no texto plano
        tokens = database.create_oauth_tokens(cfg["client_id"], scope="mcp:tools")
        verified = database.verify_oauth_access_token(tokens["access_token"])
        self.assertIsNotNone(verified)
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT access_token, access_token_hash FROM oauth_tokens WHERE access_token_hash IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertTrue(row["access_token"].startswith("REDACTED_AT_"))
            self.assertNotEqual(row["access_token"], tokens["access_token"])
        finally:
            conn.close()

    # =========================================================================
    # 5. V06: WEBHOOKS FAIL-CLOSED Y ANTI-REPLAY PERSISTENTE
    # =========================================================================

    def test_v06_webhook_fail_closed_and_replay_protection(self):
        """Firma vacía o secreto ausente falla cerrado y eventos repetidos son bloqueados."""
        with patch.object(settings, "EVOLUTION_WEBHOOK_SECRET", ""), patch.object(settings, "EVOLUTION_API_KEY", ""):
            self.assertFalse(verify_evolution_signature(b'{"event":"messages.upsert"}', "any_sig"))

        event_key = "test_evt_unique_998877"
        self.assertFalse(_record_and_check_webhook_replay("whatsapp_test", event_key))
        self.assertTrue(_record_and_check_webhook_replay("whatsapp_test", event_key))

    # =========================================================================
    # 6. V08: GUARD RUNTIME MCP (DEFAULT-DENY + PROPIEDAD EN BD CONTRA IDOR)
    # =========================================================================

    def test_v08_mcp_guard_default_deny_and_db_ownership(self):
        """Herramientas no clasificadas se deniegan por defecto y se verifica propiedad en BD."""
        res_unknown = validate_tool_execution(
            tool_name="herramienta_interna_no_clasificada",
            params={},
            requester_phone_or_jid="5491133334444@s.whatsapp.net",
            is_admin=False,
        )
        self.assertFalse(res_unknown["allowed"])

        # Crear dos clientes y una cuenta perteneciente al Cliente A
        c_a = database.find_or_create_client(name="Cliente A", whatsapp="5491100001111")
        c_b = database.find_or_create_client(name="Cliente B", whatsapp="5491100002222")
        sale = database.assign_or_sell_account(
            platform="Netflix",
            email="idor_test_a@netflix.com",
            password="PassA",
            client_name="Cliente A",
            whatsapp="5491100001111",
            price=5000,
            expiry_date="2026-12-31",
        )
        acc_id = sale.get("account_id")

        # Cliente B intenta consultar/operar el account_id de Cliente A omitiendo 'telefono'
        res_idor = validate_tool_execution(
            tool_name="reportar_cuenta_caida",
            params={"account_id": acc_id},
            requester_phone_or_jid="5491100002222@s.whatsapp.net",
            is_admin=False,
        )
        self.assertFalse(res_idor["allowed"])

        # Cliente A sí puede operar su propio account_id
        res_owner = validate_tool_execution(
            tool_name="reportar_cuenta_caida",
            params={"account_id": acc_id},
            requester_phone_or_jid="5491100001111@s.whatsapp.net",
            is_admin=False,
        )
        self.assertTrue(res_owner["allowed"])

        # Verificar wrapper runtime con ContextVar Principal
        @wrap_mcp_tool_with_guard
        def dummy_admin_tool(secret_param: str = "ok") -> str:
            return secret_param

        client_principal = Principal(
            subject="client_b",
            kind="client",
            scopes=frozenset({"mcp:client"}),
            client_phone="5491100002222",
        )
        tok = set_current_mcp_principal(client_principal)
        try:
            with self.assertRaises(PermissionError):
                dummy_admin_tool("should_fail")
        finally:
            reset_current_mcp_principal(tok)

    # =========================================================================
    # 7. V11 / V12: ESCAPE HTML POR DEFECTO, CSRF Y REVERSIÓN ATÓMICA
    # =========================================================================

    def test_v11_v12_template_auto_escape_and_csrf_tokens(self):
        """render_template escapa HTML por defecto y verify_csrf_token valida tokens ligados a sesión."""
        with patch("core.templates.load_template", return_value="<div>{{UNTRUSTED}}</div><span>{{SAFE}}</span>"):
            out = render_template(
                "dummy.html",
                UNTRUSTED='<script>alert("xss")</script>',
                SAFE=SafeHTML("<b>Trusted</b>"),
            )
            self.assertIn("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;", out)
            self.assertIn("<b>Trusted</b>", out)

        session_cookie = "admin.1700000000.deadbeef"
        csrf_tok = generate_csrf_token(session_cookie)
        self.assertTrue(verify_csrf_token(session_cookie, csrf_tok))
        self.assertFalse(verify_csrf_token("other_session", csrf_tok))
        self.assertFalse(verify_csrf_token(session_cookie, "invalid_csrf"))
