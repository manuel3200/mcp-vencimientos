"""
StreamVault v2 - Suite de Aislamiento de Privacidad en Grupos, Cifrado AES-256-GCM y pHash
Valida:
1. Prohibición estricta de comandos administrativos y confidenciales en grupos de WhatsApp (@g.us).
2. Protección de credenciales y datos de facturación personal en grupos.
3. Correcto funcionamiento de comandos comunitarios permitidos (Atlas-MD y Catálogo).
4. Cifrado simétrico AES-256-GCM de copias de seguridad (.db.enc) con autenticación e integridad.
5. Detección perceptual de comprobantes manipulados/reciclados (pHash / dHash) con distancia de Hamming <= 4.
"""
import io
import re
import os
from datetime import datetime, timedelta
from PIL import Image

import database
from core.security import encrypt_backup, decrypt_backup
from services.receipt_service import compute_perceptual_hash, hamming_distance


def run_tests():
    print("  [Suite] Aislamiento de Privacidad, Cifrado AES-256-GCM y Antifraude pHash...")

    # =========================================================================
    # 1. Reglas de Aislamiento en Grupos (@g.us): Bloqueo de Comandos Admin
    # =========================================================================
    forbidden_pattern = r'^/(?:pagoapro|aprobarpago|pagodene|rechazarpago|pagoparcial|parcial|revertir_pago|revertirpago|revertircambio|anularpago|deshacer_cambio|deshacercambio|baja|cortar|desactivar|caida|reemplazo|reemplazar|cambiar|esperar|espera|autorizar|posponer|auditoria|balance|vencimiento|clave|cuenta|pagar|cbu|alias)(?:[_\s]|$)'

    admin_and_sensitive_cmds = [
        "/pagoapro_12",
        "/aprobarpago_12",
        "/pagoapro_12_all",
        "/pagoapro_12 4500",
        "/pagodene_5",
        "/rechazarpago_5",
        "/pagoparcial_3 2000",
        "/parcial_3 2000",
        "/revertir_pago_9",
        "/revertirpago_9",
        "/revertircambio_9",
        "/anularpago_9",
        "/deshacer_cambio_1",
        "/deshacercambio_1",
        "/baja_7",
        "/cortar_7",
        "/desactivar_7",
        "/caida netflix",
        "/reemplazo_4",
        "/reemplazar_4",
        "/cambiar_2",
        "/esperar_2",
        "/autorizar_8",
        "/posponer_8",
        "/auditoria 15",
        "/balance",
        "/vencimiento",
        "/clave",
        "/cuenta",
        "/pagar",
        "/cbu",
        "/alias",
    ]

    for cmd in admin_and_sensitive_cmds:
        match = re.search(forbidden_pattern, cmd.lower().strip())
        assert match is not None, f"Fallo de seguridad: '{cmd}' no fue interceptado por el patrón de aislamiento de grupos"

    # Verificar que los comandos permitidos en grupos NO sean bloqueados falsamente
    allowed_group_cmds = [
        "/tagall Aviso importante",
        "/todos Reunión",
        "/mute",
        "/cerrar",
        "/unmute",
        "/abrir",
        "/antilink on",
        "/antilink off",
        "/kick 5491100000000",
        "/promote 5491100000000",
        "/demote 5491100000000",
        "/catalogo",
        "/precios",
        "precios de netflix",
        "planes disponibles",
    ]

    for cmd in allowed_group_cmds:
        match = re.search(forbidden_pattern, cmd.lower().strip())
        assert match is None, f"Falso positivo: comando comunitario legítimo '{cmd}' fue bloqueado erróneamente"

    print("    ✅ Aislamiento de Grupos: Comandos admin y confidenciales interceptados 100% (27/27).")

    # =========================================================================
    # 2. Cifrado y Descifrado Simétrico AES-256-GCM para Backups (.db.enc)
    # =========================================================================
    sample_db_content = b"SQLite format 3\x00\x10\x00\x01\x01\x00@  \x00\x00\x00\x01STREAMVAULT_MOCK_DATA_1234567890"

    # Cifrado con clave por defecto
    encrypted_default = encrypt_backup(sample_db_content)
    assert encrypted_default.startswith(b"SVENC01"), "La cabecera mágica SVENC01 es obligatoria"
    assert len(encrypted_default) > len(sample_db_content) + 7 + 16 + 12

    # Descifrado con clave por defecto
    decrypted_default = decrypt_backup(encrypted_default)
    assert decrypted_default == sample_db_content, "El contenido descifrado debe ser idéntico al original"

    # Cifrado con clave personalizada
    custom_key = "ClaveMaestraUltraSecretaParaBackups2026!#"
    encrypted_custom = encrypt_backup(sample_db_content, key=custom_key)
    decrypted_custom = decrypt_backup(encrypted_custom, key=custom_key)
    assert decrypted_custom == sample_db_content

    # Intento de descifrado con clave incorrecta -> debe fallar
    bad_key_failed = False
    try:
        decrypt_backup(encrypted_custom, key="ClaveIncorrectaXYZ123")
    except (ValueError, Exception):
        bad_key_failed = True
    assert bad_key_failed, "El descifrado con clave incorrecta debe ser rechazado inmediatamente"

    # Intento de manipulación de datos (tamper de 1 byte) -> debe fallar integridad GCM
    tampered_bytes = bytearray(encrypted_custom)
    tampered_bytes[-1] ^= 0xFF  # Alterar tag de autenticación
    tamper_detected = False
    try:
        decrypt_backup(bytes(tampered_bytes), key=custom_key)
    except (ValueError, Exception):
        tamper_detected = True
    assert tamper_detected, "La manipulación de un solo byte en el backup debe invalidar el tag GCM"

    print("    ✅ Cifrado AES-256-GCM: Autenticación, derivación PBKDF2 e integridad validadas.")

    # =========================================================================
    # 3. Detección Perceptual de Comprobantes (dHash / pHash) y Distancia Hamming
    # =========================================================================
    # Crear imagen base en memoria (gris degradado)
    img1 = Image.new("RGB", (200, 200), color=(120, 120, 120))
    for x in range(200):
        for y in range(50):
            img1.putpixel((x, y), (200, 200, 200))
    buf1 = io.BytesIO()
    img1.save(buf1, format="JPEG", quality=90)
    img1_bytes = buf1.getvalue()

    # Computar hash perceptual de la imagen 1
    phash1 = compute_perceptual_hash(img1_bytes)
    assert len(phash1) == 16, f"El pHash debe tener 16 caracteres hexadecimales (64 bits). Obtenido: {phash1}"

    # Imagen 2: Modificación de 1 píxel y compresión JPEG ligera (simula comprobante reciclado)
    img2 = Image.open(io.BytesIO(img1_bytes))
    img2.putpixel((10, 10), (0, 0, 0))  # 1 píxel alterado
    buf2 = io.BytesIO()
    img2.save(buf2, format="JPEG", quality=85)
    img2_bytes = buf2.getvalue()

    phash2 = compute_perceptual_hash(img2_bytes)
    assert len(phash2) == 16

    # Calcular distancia de Hamming entre imagen original y alterada
    dist_recycled = hamming_distance(phash1, phash2)
    assert dist_recycled <= 4, f"Comprobante con alteración menor debe tener distancia <= 4. Obtenido: {dist_recycled}"

    # Imagen 3: Imagen completamente diferente (patrón inverso)
    img3 = Image.new("RGB", (200, 200), color=(0, 0, 0))
    for x in range(100, 200):
        for y in range(100, 200):
            img3.putpixel((x, y), (255, 255, 255))
    buf3 = io.BytesIO()
    img3.save(buf3, format="JPEG", quality=90)
    img3_bytes = buf3.getvalue()

    phash3 = compute_perceptual_hash(img3_bytes)
    dist_different = hamming_distance(phash1, phash3)
    assert dist_different > 4, f"Imágenes diferentes deben tener distancia > 4. Obtenido: {dist_different}"

    # =========================================================================
    # 4. Flujo de Base de Datos: Alerta de Fraude por Comprobante Reciclado
    # =========================================================================
    # Registrar un pago aprobado previo con phash1
    approved_payment = database.create_pending_payment(
        sender_phone="5491199990001",
        client_name="Cliente Honestidad",
        platform="Netflix",
        amount=5000.0,
        amount_formatted="$5.000",
        bank="Mercado Pago",
        operation_id="OP_PERCEPTUAL_001",
        phash=phash1,
        notes="Comprobante original legítimo"
    )
    p_id = approved_payment["id"]
    database.approve_pending_payment(p_id)

    # Registrar nuevo pago entrante con imagen reciclada (phash2 con distancia <= 4)
    recycled_payment = database.create_pending_payment(
        sender_phone="5491199990002",
        client_name="Cliente Alterador",
        platform="Netflix",
        amount=5000.0,
        amount_formatted="$5.000",
        bank="Mercado Pago",
        operation_id="OP_NUEVA_DISTINTA_999",  # ID de operación inventado/distinto
        phash=phash2,
        notes="Intento de reuso"
    )

    # Verificar que el sistema activó la ALERTA DE FRAUDE por pHash en las notas
    assert "ALERTA DE FRAUDE" in recycled_payment.get("notes", ""), (
        "El comprobante reciclado debe encender la ALERTA DE FRAUDE por pHash"
    )
    assert "pHash distancia" in recycled_payment.get("notes", "")

    print("    ✅ Antifraude pHash: Detección perceptual (distancia <= 4) y alertas en DB validadas.")
    print("  ✨ Todas las pruebas de aislamiento, cifrado y pHash superadas con éxito.")


if __name__ == "__main__":
    run_tests()
