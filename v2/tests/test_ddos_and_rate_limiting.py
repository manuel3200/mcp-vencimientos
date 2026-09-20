"""
StreamVault v2 - Suite de Pruebas de Protección Perimetral, Anti-DDoS y Blindaje OCR (Fase 2)
Valida:
1. Rate Limiting multi-nivel (Token Bucket por usuario, por grupo y cooldown progresivo).
2. Throttling de comandos pesados (/tagall a 10 min, /backup a 15 min).
3. Blindaje del motor OCR: Cuota de 5 comprobantes/hora por teléfono.
4. Pre-filtros de tamaño (> 8MB) y dimensiones (< 200x200) para comprobantes.
5. Caché de percepción negativa (pHash dHash) para descarte instantáneo de no-comprobantes.
6. Protección anti-fuerza bruta en login y 2FA (bloqueo de 30 min tras 5 intentos fallidos).
7. Transición de estados del Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
"""
import io
import time
from PIL import Image

from core.rate_limiter import (
    TokenBucket,
    RateLimiter,
    ReceiptQuotaManager,
    CircuitBreaker,
    AuthRateLimiter,
    rate_limiter,
    receipt_quota_manager,
    gemini_circuit_breaker,
    auth_rate_limiter
)
from services.receipt_service import (
    compute_perceptual_hash,
    hamming_distance
)


def run_tests():
    print("  [Suite] Protección Anti-DDoS, Rate Limiting y Blindaje OCR (Fase 2)...")

    # =========================================================================
    # 1. Pruebas de TokenBucket y Rate Limiting de Usuario
    # =========================================================================
    rl = RateLimiter()
    test_user = "5491188880001"

    # Consumir los primeros 10 tokens permitidos en 1 minuto
    for i in range(10):
        allowed, reason = rl.check_user_rate_limit(test_user)
        assert allowed is True, f"La petición {i+1} debe ser permitida dentro del límite de 10/min"

    # La 11va petición debe ser rechazada inmediatamente
    blocked, reason = rl.check_user_rate_limit(test_user)
    assert blocked is False, "La petición 11 debe ser bloqueada por exceder límite de usuario"
    assert "user_rate_limit_exceeded" in reason

    # Verificar que el cooldown progresivo se activó
    is_in_cooldown, wait_time = rl.get_user_cooldown(test_user)
    assert is_in_cooldown is True
    assert wait_time > 0

    print("    ✅ Rate Limit Usuario: 10 peticiones/min y cooldown progresivo validados.")

    # =========================================================================
    # 2. Pruebas de Rate Limiting en Grupos de WhatsApp
    # =========================================================================
    test_group = "12036399887766@g.us"

    # Consumir los 25 mensajes permitidos por minuto en el grupo
    for i in range(25):
        allowed, _ = rl.check_group_rate_limit(test_group)
        assert allowed is True, f"El mensaje grupal {i+1} debe ser permitido dentro del límite de 25/min"

    # El mensaje 26 debe ser bloqueado para proteger el servidor
    blocked_group, reason_group = rl.check_group_rate_limit(test_group)
    assert blocked_group is False
    assert "group_rate_limit_exceeded" in reason_group

    print("    ✅ Rate Limit Grupos: Límite de 25 mensajes/minuto por grupo validado.")

    # =========================================================================
    # 3. Throttling de Comandos de Alto Impacto (/tagall y /backup)
    # =========================================================================
    # /tagall (1 cada 10 min por grupo)
    tagall_ok, _ = rl.check_tagall_throttle(test_group)
    assert tagall_ok is True, "El primer /tagall debe ser permitido"

    tagall_blocked, tagall_reason = rl.check_tagall_throttle(test_group)
    assert tagall_blocked is False, "El segundo /tagall inmediato debe ser rechazado por throttling"
    assert "tagall_throttled" in tagall_reason

    # /backup (1 cada 15 min por admin)
    test_admin = "admin_super"
    backup_ok, _ = rl.check_backup_throttle(test_admin)
    assert backup_ok is True, "El primer /backup debe ser permitido"

    backup_blocked, backup_reason = rl.check_backup_throttle(test_admin)
    assert backup_blocked is False, "El segundo /backup inmediato debe ser rechazado por throttling"
    assert "backup_throttled" in backup_reason

    print("    ✅ Throttling de Comandos: /tagall (10 min) y /backup (15 min) validados.")

    # =========================================================================
    # 4. Blindaje OCR: Cuota Horaria de Comprobantes (Máx 5/hora)
    # =========================================================================
    rqm = ReceiptQuotaManager()
    payer_phone = "5491177665544"

    for i in range(5):
        allowed, _ = rqm.check_receipt_quota(payer_phone)
        assert allowed is True, f"El comprobante {i+1} debe ser permitido dentro de la cuota de 5/hora"

    quota_blocked, quota_reason = rqm.check_receipt_quota(payer_phone)
    assert quota_blocked is False, "El 6to comprobante en la misma hora debe ser rechazado por cuota"
    assert "receipt_hourly_quota_exceeded" in quota_reason

    print("    ✅ Cuota de Comprobantes: Límite estricto de 5 comprobantes/hora por teléfono validado.")

    # =========================================================================
    # 5. Pre-filtros de Imagen y Caché de Percepción Negativa (pHash)
    # =========================================================================
    # A. Pre-filtro de resolución insuficiente (< 200x200)
    small_img = Image.new("RGB", (150, 150), color=(255, 255, 255))
    small_buf = io.BytesIO()
    small_img.save(small_buf, format="JPEG")
    small_bytes = small_buf.getvalue()

    small_res = rqm.validate_image_constraints(small_bytes)
    assert small_res[0] is False
    assert "resolución insuficiente" in small_res[1]

    # B. Pre-filtro de tamaño excesivo (> 8 MB)
    fake_huge_bytes = b"0" * (8 * 1024 * 1024 + 1024)
    huge_res = rqm.validate_image_constraints(fake_huge_bytes)
    assert huge_res[0] is False
    assert "tamaño excesivo" in huge_res[1]

    # C. Imagen válida (>= 200x200 y < 8MB)
    valid_img = Image.new("RGB", (300, 300), color=(200, 200, 200))
    valid_buf = io.BytesIO()
    valid_img.save(valid_buf, format="JPEG")
    valid_bytes = valid_buf.getvalue()
    valid_res = rqm.validate_image_constraints(valid_bytes)
    assert valid_res[0] is True

    # D. Caché de percepción negativa (memorizar imágenes clasificadas como no-comprobante)
    non_receipt_phash = compute_perceptual_hash(valid_bytes)
    assert rqm.is_known_non_receipt(non_receipt_phash) is False

    rqm.record_non_receipt(non_receipt_phash)
    assert rqm.is_known_non_receipt(non_receipt_phash) is True

    # Imagen similar con 1-bit de diferencia en el pHash -> debe ser reconocida en caché
    similar_phash = non_receipt_phash[:-1] + ('f' if non_receipt_phash[-1] != 'f' else 'e')
    dist = hamming_distance(non_receipt_phash, similar_phash)
    if dist <= 4:
        assert rqm.is_known_non_receipt(similar_phash) is True

    print("    ✅ Pre-filtros OCR y Caché de Percepción: Tamaño, dimensiones y dHash en memoria validados.")

    # =========================================================================
    # 6. Protección Anti-Fuerza Bruta en 2FA y Login (IP Lockout)
    # =========================================================================
    auth_limiter = AuthRateLimiter(max_attempts=5, lockout_seconds=1800)
    attacker_ip = "192.168.1.105"

    # Registrar 4 intentos fallidos
    for i in range(4):
        auth_limiter.record_failed_attempt(attacker_ip)
        locked, _ = auth_limiter.is_locked_out(attacker_ip)
        assert locked is False, f"Tras {i+1} intentos aún no debe estar bloqueado"

    # Registrar el 5to intento fallido -> Bloqueo activado
    auth_limiter.record_failed_attempt(attacker_ip)
    locked, remaining_seconds = auth_limiter.is_locked_out(attacker_ip)
    assert locked is True, "El 5to intento fallido debe bloquear la IP inmediatamente"
    assert remaining_seconds > 1700

    # Tras reseteo por login exitoso
    auth_limiter.reset_attempts(attacker_ip)
    locked_after_reset, _ = auth_limiter.is_locked_out(attacker_ip)
    assert locked_after_reset is False, "El reseteo tras éxito debe desbloquear la IP"

    print("    ✅ Anti-Fuerza Bruta 2FA: Bloqueo de 30 min tras 5 intentos fallidos validado.")

    # =========================================================================
    # 7. Máquina de Estados del Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
    # =========================================================================
    cb = CircuitBreaker(fail_threshold=3, reset_timeout=0.2)
    assert cb.get_state() == "CLOSED"
    assert cb.can_execute() is True

    # 3 fallos consecutivos
    cb.record_failure()
    assert cb.get_state() == "CLOSED"
    cb.record_failure()
    assert cb.get_state() == "CLOSED"
    cb.record_failure()
    assert cb.get_state() == "OPEN"
    assert cb.can_execute() is False, "En estado OPEN no debe permitir ejecuciones hacia la API"

    # Esperar el reset_timeout para conmutar a HALF_OPEN
    time.sleep(0.25)
    assert cb.can_execute() is True, "Tras reset_timeout debe permitir prueba en HALF_OPEN"
    assert cb.get_state() == "HALF_OPEN"

    # Éxito en HALF_OPEN -> Vuelve a CLOSED
    cb.record_success()
    assert cb.get_state() == "CLOSED"
    assert cb.can_execute() is True

    print("    ✅ Circuit Breaker: Transiciones CLOSED -> OPEN -> HALF_OPEN -> CLOSED validadas.")
    print("  ✨ Todas las pruebas de Protección Anti-DDoS, Rate Limiting y Blindaje OCR superadas exitosamente.")


if __name__ == "__main__":
    run_tests()
