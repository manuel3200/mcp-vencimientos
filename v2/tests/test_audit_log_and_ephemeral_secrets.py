"""
test_audit_log_and_ephemeral_secrets.py - Suite 9 del Harness de Calidad StreamVault v2
Valida:
1. Cifrado a nivel de columna (AES-256-GCM, prefijo enc:v1:, retrocompatibilidad en claro y anti-tampering).
2. Transparencia en repositorios de cuentas (almacenamiento cifrado en SQLite y descifrado en memoria).
3. Log de auditoría inmutable con encadenamiento HMAC estilo blockchain (bloques, firmas y detección de manipulación).
4. Enlaces efímeros de credenciales Anti-SIM Swap (One-Time Secrets, autodestrucción inmediata y expiración TTL).
5. Control de Acceso Basado en Roles (RBAC: SUPER_ADMIN, FINANZAS, SOPORTE) y comando /auditoria.
"""

import os
import time
import sqlite3
from typing import Dict, Any

from core.security import (
    encrypt_secret,
    decrypt_secret,
    is_encrypted_secret
)
from core.audit import (
    log_audit_event,
    verify_audit_chain,
    get_audit_history,
    compute_audit_signature,
    GENESIS_HASH
)
from core.ephemeral_secrets import (
    create_ephemeral_secret,
    reveal_and_burn_secret,
    burn_secret_immediately
)
from core.rbac import (
    ROLE_SUPER_ADMIN,
    ROLE_FINANZAS,
    ROLE_SOPORTE,
    ROLE_UNAUTHORIZED,
    get_actor_role,
    has_permission,
    check_admin_permission,
    normalize_command_action
)
import database
from db.connection import get_connection


def test_column_level_encryption():
    """1. Valida el motor criptográfico a nivel de columna (AES-256-GCM)."""
    print("  [1/5] Probando Cifrado a Nivel de Columna (AES-256-GCM, prefijo enc:v1: y retrocompatibilidad)...")
    
    secret_pass = "SuperClaveSecreta_2026!#"
    enc = encrypt_secret(secret_pass)

    assert is_encrypted_secret(enc), f"Debe comenzar con 'enc:v1:': {enc}"
    assert enc != secret_pass, "El texto cifrado no debe ser igual al texto plano"

    # Descifrado
    dec = decrypt_secret(enc)
    assert dec == secret_pass, f"Error descifrando secreto: esperado '{secret_pass}', obtenido '{dec}'"

    # Idempotencia: cifrar dos veces no produce doble cifrado
    enc2 = encrypt_secret(enc)
    assert enc2 == enc, "encrypt_secret debe ser idempotente"

    # Retrocompatibilidad con texto plano histórico sin prefijo
    plain_legacy = "contrasena_vieja_en_claro"
    dec_legacy = decrypt_secret(plain_legacy)
    assert dec_legacy == plain_legacy, "decrypt_secret debe retornar texto plano intacto si no tiene enc:v1:"

    # Vacíos y None
    assert encrypt_secret("") == "", "String vacío debe retornar vacío"
    assert encrypt_secret(None) == "", "None debe retornar vacío"
    assert decrypt_secret("") == "", "String vacío descifrado debe ser vacío"
    assert decrypt_secret(None) == "", "None descifrado debe ser vacío"

    # Anti-tampering: alteración de bytes del payload cifrado
    parts = enc.split(":")
    tampered = f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3][:-2]}00"
    tamper_caught = False
    try:
        decrypt_secret(tampered)
    except ValueError:
        tamper_caught = True
    assert tamper_caught, "La manipulación de bits debe disparar ValueError por falla en tag AES-GCM"

    print("    ✅ Cifrado simétrico de columnas, idempotencia, retrocompatibilidad y detección de manipulación validados.")


def test_accounts_repo_transparent_encryption():
    """2. Valida la persistencia transparente y migración en streaming_accounts."""
    print("  [2/5] Probando Cifrado Transparente en Repositorios y Migración de Cuentas...")
    
    # 1. Agregar cuenta libre con contraseña y PIN
    acc = database.add_free_account(
        platform="Netflix 4K Suite9",
        email="suite9_test@streamvault.app",
        password="PassWordSegura456!",
        profile_name="Perfil 1",
        profile_pin="9876",
        cost="2000",
        notes="Prueba Suite 9"
    )
    acc_id = acc["id"]

    # Al leer desde la función de repositorio, las credenciales deben estar descifradas
    assert acc["password"] == "PassWordSegura456!", f"El repositorio debe devolver la clave descifrada: {acc['password']}"
    assert acc["profile_pin"] == "9876", f"El repositorio debe devolver el PIN descifrado: {acc['profile_pin']}"

    # Verificación en crudo en SQLite: En el disco debe estar cifrado con enc:v1:
    conn = get_connection()
    try:
        raw_row = conn.execute("SELECT password, profile_pin FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
        raw_pwd = raw_row["password"]
        raw_pin = raw_row["profile_pin"]
        assert raw_pwd.startswith("enc:v1:"), f"En SQLite la clave DEBE almacenarse cifrada con 'enc:v1:': {raw_pwd}"
        assert raw_pin.startswith("enc:v1:"), f"En SQLite el PIN DEBE almacenarse cifrado con 'enc:v1:': {raw_pin}"
    finally:
        conn.close()

    # Lectura a través de get_account_detail
    detail = database.get_account_detail(acc_id)
    assert detail is not None
    assert detail["password"] == "PassWordSegura456!"
    assert detail["profile_pin"] == "9876"

    # Prueba de migración en lote de cuentas en texto plano
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO streaming_accounts (platform, email, password, profile_name, profile_pin, status)
                VALUES ('Max HBO Suite9', 'legacy_suite9@streamvault.app', 'plano123', 'Perfil 2', '4321', 'libre')
            """)
    finally:
        conn.close()

    migrated = database.migrate_encrypt_plaintext_accounts()
    assert migrated >= 1, f"Debió migrar al menos 1 cuenta en claro: {migrated}"

    # Confirmar que ahora quedó cifrada
    conn = get_connection()
    try:
        mig_row = conn.execute("SELECT password, profile_pin FROM streaming_accounts WHERE email = 'legacy_suite9@streamvault.app'").fetchone()
        assert mig_row["password"].startswith("enc:v1:"), f"La cuenta migrada debe tener clave cifrada: {mig_row['password']}"
        assert mig_row["profile_pin"].startswith("enc:v1:"), f"El PIN migrado debe tener cifrado: {mig_row['profile_pin']}"
    finally:
        conn.close()

    print("    ✅ Almacenamiento cifrado en SQLite, lectura transparente y migración automática validados.")


def test_immutable_audit_log_blockchain():
    """3. Valida la bitácora inmutable estilo blockchain liviana con HMAC encadenado."""
    print("  [3/5] Probando Bitácora Inmutable (Append-Only) y Detección de Tampering...")

    # Registrar eventos auditables
    e1 = log_audit_event(
        actor="admin:+5491166099952",
        action="ROTATE_MASTER_PASSWORD",
        target_type="account",
        target_id="netflix_madre@streamvault.app",
        old_value="pass_v1",
        new_value="pass_v2_rotated",
        ip_or_source="WhatsApp"
    )
    assert e1["prev_hash"] == GENESIS_HASH or len(e1["prev_hash"]) == 64
    assert len(e1["signature_hmac"]) == 64

    e2 = log_audit_event(
        actor="admin:+5491166099952",
        action="APPROVE_PAYMENT",
        target_type="payment",
        target_id="101",
        old_value="status:pending",
        new_value="status:approved,amount:8500",
        ip_or_source="WhatsApp"
    )
    # El prev_hash de e2 debe ser exactamente la firma HMAC de e1
    assert e2["prev_hash"] == e1["signature_hmac"], "Encadenamiento criptográfico falló entre e1 y e2"

    e3 = log_audit_event(
        actor="system_replace",
        action="REPLACE_FALLEN",
        target_type="account",
        target_id="202",
        old_value="old:#50",
        new_value="new:#51",
        ip_or_source="bot"
    )
    assert e3["prev_hash"] == e2["signature_hmac"], "Encadenamiento criptográfico falló entre e2 y e3"

    # Verificar cadena íntegra
    is_valid, count, msg = verify_audit_chain()
    assert is_valid, f"La cadena legítima debe ser 100% válida: {msg}"
    assert count >= 3, f"Deben existir al menos 3 bloques verificados: {count}"

    # Consultar historial filtrado
    history_pay = get_audit_history(target_id="101")
    assert len(history_pay) >= 1, "Debe encontrar el evento de pago #101"
    assert history_pay[0]["action"] == "APPROVE_PAYMENT"

    # Prueba de manipulación de datos (Tampering Simulation)
    # Un atacante modifica el registro e2 directamente en el archivo SQLite
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE audit_log SET action = 'HACKED_ACTION' WHERE id = ?", (e2["id"],))
    finally:
        conn.close()

    # La verificación de cadena DEBE fallar inmediatamente
    is_valid_after_hack, corrupted_idx, hack_msg = verify_audit_chain()
    assert not is_valid_after_hack, "La verificación DEBIÓ fallar tras la manipulación manual de SQLite"
    assert "MANIPULACIÓN DETECTADA" in hack_msg or "CORRUPCIÓN" in hack_msg

    # Restaurar para no dejar la prueba corrupta
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE audit_log SET action = 'APPROVE_PAYMENT' WHERE id = ?", (e2["id"],))
    finally:
        conn.close()

    is_valid_restored, _, _ = verify_audit_chain()
    assert is_valid_restored, "La cadena debe volver a ser válida tras restaurar el valor auténtico"

    print("    ✅ Encadenamiento criptográfico HMAC, integridad no repudiable y detección de manipulación validados.")


def test_ephemeral_secrets():
    """4. Valida los enlaces efímeros One-Time Secrets (Anti-SIM Swap)."""
    print("  [4/5] Probando Enlaces Efímeros de Credenciales (Anti-SIM Swap / One-Time Secrets)...")

    sensitive_payload = {
        "title": "Credenciales Netflix Juan",
        "items": [
            {
                "platform": "Netflix 4K",
                "email": "juan_netflix@streamvault.app",
                "password": "PasswordEfimera99!",
                "profile": "Perfil 1",
                "pin": "1234",
                "expiry": "2026-10-20"
            }
        ]
    }

    # 1. Creación de secreto efímero (válido por 10 minutos)
    token, full_url = create_ephemeral_secret(
        data=sensitive_payload,
        title="Credenciales Netflix Juan",
        ttl_seconds=600,
        max_views=1
    )
    assert token.startswith("sec_"), f"El token debe iniciar con 'sec_': {token}"
    assert f"/v/{token}" in full_url, f"La URL debe contener el path del token: {full_url}"

    # 2. Primer acceso: debe revelar exitosamente el payload y quemarlo en el servidor
    revealed_data, status1 = reveal_and_burn_secret(token)
    assert status1 == "revealed", f"El primer acceso debe devolver 'revealed': {status1}"
    assert revealed_data is not None
    assert revealed_data["items"][0]["password"] == "PasswordEfimera99!"

    # 3. Segundo acceso inmediato: debe reportar que ya fue quemado/autodestruido
    second_data, status2 = reveal_and_burn_secret(token)
    assert status2 == "already_burned", f"El segundo acceso debe fallar con 'already_burned': {status2}"
    assert second_data is None, "No debe retornar datos una vez consumido"

    # 4. Token inexistente
    non_existent, status_none = reveal_and_burn_secret("sec_invalido_total_999")
    assert status_none == "not_found"
    assert non_existent is None

    # 5. Token expirado por TTL (creamos uno con TTL negativo para simular expiración)
    exp_token, _ = create_ephemeral_secret(
        data={"test": "vencido"},
        title="Expirado Test",
        ttl_seconds=-10,
        max_views=1
    )
    exp_data, status_exp = reveal_and_burn_secret(exp_token)
    assert status_exp == "expired", f"El secreto con fecha pasada debe dar 'expired': {status_exp}"
    assert exp_data is None

    print("    ✅ Creación de One-Time Secrets, autodestrucción inmediata y expiración TTL validadas.")


def test_rbac_roles_and_permissions():
    """5. Valida el control de acceso basado en roles (RBAC) y comando /auditoria."""
    print("  [5/5] Probando Roles Granulares RBAC y Comando /auditoria...")

    # Remitente desconocido -> UNAUTHORIZED
    role_unknown = get_actor_role("5491100000000")
    assert role_unknown == ROLE_UNAUTHORIZED, f"Debe ser UNAUTHORIZED: {role_unknown}"

    # Mensaje from_me propio del bot -> SUPER_ADMIN
    role_bot = get_actor_role("5491100000000", is_from_me=True)
    assert role_bot == ROLE_SUPER_ADMIN, "from_me=True debe ser SUPER_ADMIN"

    # Permisos por rol
    # FINANZAS puede aprobar pagos pero no puede gestionar caídas
    assert has_permission(ROLE_FINANZAS, "pagoapro"), "FINANZAS debe poder ejecutar pagoapro"
    assert has_permission(ROLE_FINANZAS, "pagodene"), "FINANZAS debe poder ejecutar pagodene"
    assert has_permission(ROLE_FINANZAS, "auditoria"), "FINANZAS debe poder consultar auditoria"
    assert not has_permission(ROLE_FINANZAS, "caida"), "FINANZAS NO debe poder ejecutar caida"
    assert not has_permission(ROLE_FINANZAS, "reemplazo"), "FINANZAS NO debe poder ejecutar reemplazo"

    # SOPORTE puede gestionar caídas pero no puede aprobar pagos
    assert has_permission(ROLE_SOPORTE, "caida"), "SOPORTE debe poder ejecutar caida"
    assert has_permission(ROLE_SOPORTE, "reemplazo"), "SOPORTE debe poder ejecutar reemplazo"
    assert has_permission(ROLE_SOPORTE, "auditoria"), "SOPORTE debe poder consultar auditoria"
    assert not has_permission(ROLE_SOPORTE, "pagoapro"), "SOPORTE NO debe poder ejecutar pagoapro"
    assert not has_permission(ROLE_SOPORTE, "balance"), "SOPORTE NO debe poder ejecutar balance"

    # SUPER_ADMIN puede todo
    assert has_permission(ROLE_SUPER_ADMIN, "pagoapro")
    assert has_permission(ROLE_SUPER_ADMIN, "caida")
    assert has_permission(ROLE_SUPER_ADMIN, "auditoria")
    assert has_permission(ROLE_SUPER_ADMIN, "cualquier_cosa")

    # Normalización de comandos compuestos (sufijos numéricos, _all, argumentos)
    assert normalize_command_action("/pagoapro 10") == "pagoapro"
    assert normalize_command_action("pagoapro_12") == "pagoapro"
    assert normalize_command_action("pagoapro_45_all") == "pagoapro"
    assert normalize_command_action("/revertir_pago 15") == "revertir_pago"
    assert normalize_command_action("revertir_pago_15") == "revertir_pago"
    assert normalize_command_action("esperar_10") == "esperar"
    assert normalize_command_action("posponer_5") == "posponer"
    assert normalize_command_action("/caida netflix@gmail.com") == "caida"
    assert normalize_command_action("deshacer_cambio_3") == "deshacer_cambio"

    # Autorizaciones granulares con comandos compuestos
    assert has_permission(ROLE_SOPORTE, "esperar_10"), "SOPORTE debe poder ejecutar esperar_10"
    assert has_permission(ROLE_SOPORTE, "posponer_5"), "SOPORTE debe poder ejecutar posponer_5"
    assert has_permission(ROLE_SOPORTE, "caida netflix@gmail.com"), "SOPORTE debe poder ejecutar caida con args"
    assert has_permission(ROLE_FINANZAS, "revertir_pago_12"), "FINANZAS debe poder ejecutar revertir_pago_12"
    assert has_permission(ROLE_FINANZAS, "pagoapro_45_all"), "FINANZAS debe poder ejecutar pagoapro_45_all"
    assert not has_permission(ROLE_SOPORTE, "pagoapro_45_all"), "SOPORTE NO debe poder ejecutar pagoapro_45_all"
    assert not has_permission(ROLE_FINANZAS, "caida_10"), "FINANZAS NO debe poder ejecutar caida_10"

    # Formateo de reporte de auditoría
    records = get_audit_history(limit=5)
    report = database.format_audit_report(records)
    assert "BITÁCORA INMUTABLE DE AUDITORÍA" in report
    assert "Estado Criptográfico" in report

    print("    ✅ Matriz de roles RBAC (SUPER_ADMIN, FINANZAS, SOPORTE), normalización de comandos y reporte /auditoria validados.")


def run_tests():
    """Ejecutor principal de la Suite 9 para el Harness."""
    print("Iniciando Suite 9: Auditoría Inmutable, Enlaces Efímeros y Cifrado en Reposo (Fase 3)...")
    test_column_level_encryption()
    test_accounts_repo_transparent_encryption()
    test_immutable_audit_log_blockchain()
    test_ephemeral_secrets()
    test_rbac_roles_and_permissions()
    print("✅ Suite 9 completada exitosamente sin incidencias.")


if __name__ == "__main__":
    run_tests()
