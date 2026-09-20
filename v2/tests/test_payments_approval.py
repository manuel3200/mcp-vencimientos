import database

def run_tests():
    print("  [Suite] Aprobación de Pagos, Notificaciones y Operaciones Masivas...")

    # 1. Creación de Comprobante Pendiente
    p1 = database.create_pending_payment(
        sender_phone="5491133334444",
        client_name="Cliente Cobro 1",
        amount=5000.0,
        bank="Banco Galicia",
        operation_id="OP-1001"
    )
    assert p1["id"] > 0
    assert p1["status"] == "pending"

    # 2. Aprobación Individual
    res_app = database.approve_pending_payment(p1["id"], admin_user="test_admin")
    assert res_app["success"] is True, f"Fallo al aprobar pago: {res_app}"

    item_app = database.get_pending_payment(p1["id"])
    assert item_app["status"] == "approved"

    # 3. Idempotencia: No permitir re-aprobar un pago ya procesado
    res_app_again = database.approve_pending_payment(p1["id"], admin_user="test_admin")
    assert res_app_again["success"] is False, "No debe permitir procesar un pago dos veces"

    # 4. Rechazo Individual
    p2 = database.create_pending_payment(
        sender_phone="5491155556666",
        client_name="Cliente Cobro 2",
        amount=4000.0,
        bank="Mercado Pago"
    )
    res_rej = database.reject_pending_payment(p2["id"], reason="Comprobante ilegible")
    assert res_rej["success"] is True

    item_rej = database.get_pending_payment(p2["id"])
    assert item_rej["status"] == "rejected"

    # 5. Operación Masiva (Bulk Approval)
    p3 = database.create_pending_payment(sender_phone="5491177778888", client_name="Multi 1", amount=3000.0)
    p4 = database.create_pending_payment(sender_phone="5491188889999", client_name="Multi 2", amount=3500.0)

    for pid in [p3["id"], p4["id"]]:
        res_bulk = database.approve_pending_payment(pid, admin_user="bulk_admin")
        assert res_bulk["success"] is True

    assert database.get_pending_payment(p3["id"])["status"] == "approved"
    assert database.get_pending_payment(p4["id"])["status"] == "approved"

    # 6. Verificación de lógica de banderas de notificación (Form checkbox HTML)
    def evaluate_notify_flag(notify_client_raw, toggle_present_raw):
        # Simula la lógica implementada en accounts_router.py
        if toggle_present_raw:
            return bool(notify_client_raw and notify_client_raw not in ("0", "false", "off"))
        return True

    # Checkbox marcado -> True
    assert evaluate_notify_flag("1", "1") is True
    assert evaluate_notify_flag("on", "1") is True
    # Checkbox desmarcado (omitido en POST, pero formulario presente) -> False
    assert evaluate_notify_flag(None, "1") is False
    assert evaluate_notify_flag("", "1") is False

    # 5. Prueba de Purga de Comprobantes Base64 Antiguos
    pen_old = database.create_pending_payment(
        sender_phone="5491100001111",
        client_name="Cliente Antiguo",
        amount=5000.0,
        receipt_base64="data:image/jpeg;base64,dGVzdA=="
    )
    database.approve_pending_payment(pen_old["id"])
    
    # Simular que se resolvió hace 90 días
    from db.connection import get_connection
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE pending_payments SET resolved_at = datetime('now', '-90 days') WHERE id = ?", (pen_old["id"],))
    finally:
        conn.close()

    pruned = database.prune_old_approved_receipts_base64(days_threshold=60)
    assert pruned >= 1, f"Se esperaba al menos 1 comprobante purgado, obtenido: {pruned}"

    pen_checked = database.get_pending_payment(pen_old["id"])
    assert pen_checked["receipt_base64"] == "", "El Base64 debía haber sido purgado"
    assert pen_checked["amount"] == 5000.0, "Los metadatos contables deben preservarse"

    # 6. Idempotencia de creación de comprobantes: No duplicar en ventana de 10 min
    idem_p1 = database.create_pending_payment(
        sender_phone="5493704336652",
        client_name="Jackelinne Coria",
        amount=8000.0,
        bank="Naranja X",
        operation_id="PDX4OGNY4L6L0RPV20L6EY"
    )
    # Segundo reintento idéntico (simulando webhook retry de Evolution API)
    idem_p2 = database.create_pending_payment(
        sender_phone="5493704336652",
        client_name="Jackelinne Coria",
        amount=8000.0,
        bank="Naranja X",
        operation_id="PDX4OGNY4L6L0RPV20L6EY"
    )
    assert idem_p1["id"] == idem_p2["id"], f"Fallo de idempotencia: creó IDs distintos ({idem_p1['id']} vs {idem_p2['id']})"

    # 7. Parseo inteligente de comprobante Naranja X y mitigación de artefactos OCR
    import services.receipt_service as receipt_service

    receipt_raw_text = """
    NaranjaX
    Comprobante de transferencia
    Enviaste
    $ 8.000 00
    19/SEP/2026 - 19:03 h
    Cuenta origen
    Jackelinne Coria
    Naranja X
    CBU 4530000800015433014397
    CUIL 20-36959836-9
    Cuenta destino
    Juan Manuel Ortiz
    Mercado Pago
    CVU 0000003100098090274687
    CUIL 20-42185991-5
    Información de la operación
    COELSA ID
    PDX4OGNY4L6L0RPV20L6EY
    Código de transacción
    e0b5fadc-14c8-40fb-8202-7fd4e5b131ab
    """
    parsed = receipt_service.parse_transfer_receipt_text(receipt_raw_text)
    assert parsed["is_receipt"] is True
    assert parsed["amount"] == 8000.0, f"Monto incorrecto extraído: {parsed['amount']}"
    assert parsed["bank"] == "Naranja X", f"Banco emisor incorrecto: {parsed['bank']}"
    assert parsed["operation_id"] == "PDX4OGNY4L6L0RPV20L6EY", f"Op ID incorrecto: {parsed['operation_id']}"
    assert parsed["operation_id"] != "COELSA", "No debe extraer la palabra 'COELSA' como ID"

    # 8. Corrección de artefacto OCR ('$' leído como '5' antes del monto)
    ocr_corrupt_text = "NaranjaX Comprobante Enviaste 58.000 00 COELSA ID PDX4OGNY4L6L0RPV20L6EY"
    parsed_corrupt = receipt_service.parse_transfer_receipt_text(ocr_corrupt_text)
    assert parsed_corrupt["amount"] == 8000.0, f"Falló corrección de artefacto OCR: {parsed_corrupt['amount']}"

    # 9. Exclusión estricta de CBU/CVU (22 dígitos) y CUIT (11 dígitos) de operation_id
    receipt_cvu_only = """
    Transferencia exitosa
    Monto: $ 8.000
    CVU destino: 0000003100098090274687
    CUIT: 20421859915
    """
    parsed_cvu = receipt_service.parse_transfer_receipt_text(receipt_cvu_only)
    assert parsed_cvu["operation_id"] != "0000003100098090274687", "CVU de 22 dígitos nunca debe ser tomado como operation_id"
    assert parsed_cvu["operation_id"] != "20421859915", "CUIT de 11 dígitos nunca debe ser tomado como operation_id"

    # 10. Limpieza y saneamiento automático de registros duplicados
    # Simular registros duplicados en pending_payments
    from db.connection import get_connection
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO pending_payments (sender_phone, client_name, amount, status)
                VALUES ('5493704998877', 'Sergio Test', 58000.0, 'pending'),
                       ('5493704998877', 'Sergio Test', 8000.0, 'pending')
            """)
    finally:
        conn.close()

    cleaned_count = database.cleanup_duplicate_pending_payments()
    assert cleaned_count >= 1, f"Debió sanear al menos 1 clon duplicado, saneó: {cleaned_count}"

    conn = get_connection()
    try:
        remaining_pending = conn.execute(
            "SELECT id, amount, bank FROM pending_payments WHERE sender_phone = '5493704998877' AND status = 'pending'"
        ).fetchall()
        assert len(remaining_pending) == 1, f"Debe quedar exactamente 1 pendiente por cliente, quedaron: {len(remaining_pending)}"
    finally:
        conn.close()

    # 11. Prueba de Herramientas MCP para Gestión de Comprobantes
    import asyncio
    from mcp_server.tools import finance_tools, system_tools

    p_mcp1 = database.create_pending_payment(
        sender_phone="5491144445555",
        client_name="Cliente MCP Aprobación",
        amount=6500.0,
        bank="Mercado Pago",
        operation_id="OP-MCP-101"
    )
    assert p_mcp1["id"] > 0

    # Test listar_comprobantes_pendientes
    list_output = finance_tools.listar_comprobantes_pendientes(estado="pending")
    assert "Cliente MCP Aprobación" in list_output
    assert "#P" in list_output
    assert "6.500" in list_output

    # Test aprobar_comprobante_pago
    app_output = asyncio.run(finance_tools.aprobar_comprobante_pago(
        pago_id=p_mcp1["id"],
        notificar_cliente=False
    ))
    assert "APROBADO EXITOSAMENTE" in app_output
    assert str(p_mcp1["id"]) in app_output

    p_mcp1_check = database.get_pending_payment(p_mcp1["id"])
    assert p_mcp1_check["status"] == "approved"

    # Test rechazar_comprobante_pago
    p_mcp2 = database.create_pending_payment(
        sender_phone="5491166667777",
        client_name="Cliente MCP Rechazo",
        amount=3000.0,
        bank="Brubank",
        operation_id="OP-MCP-102"
    )
    rej_output = asyncio.run(finance_tools.rechazar_comprobante_pago(
        pago_id=p_mcp2["id"],
        motivo="Ticket borroso",
        notificar_cliente=False
    ))
    assert "RECHAZADO" in rej_output
    assert "Ticket borroso" in rej_output

    p_mcp2_check = database.get_pending_payment(p_mcp2["id"])
    assert p_mcp2_check["status"] == "rejected"

    # 13. Test de Renovación Temprana (> 15 días restantes con historial previo)
    from datetime import date, timedelta
    cli_test = database.find_or_create_client(name="Cliente Renovador Temprano", whatsapp="5491122334455")
    exp_20d = (date.today() + timedelta(days=20)).isoformat()
    acc_test = database.assign_or_sell_account(
        client_name="Cliente Renovador Temprano",
        platform="Netflix",
        email="renovador_temprano@test.com",
        password="pass",
        expiry_date=exp_20d,
        whatsapp="5491122334455",
        price="5000"
    )
    
    # Simular que el cliente paga su renovación 20 días antes de vencer
    p_early = database.create_pending_payment(
        sender_phone="5491122334455",
        client_name="Cliente Renovador Temprano",
        client_id=cli_test["id"],
        account_id=acc_test["id"],
        amount=5000.0
    )
    res_early_app = database.approve_pending_payment(p_early["id"])
    assert res_early_app["success"] is True, f"Error al aprobar: {res_early_app}"
    
    # Verificar que se extendió la fecha en 30 días a partir del vencimiento actual (+50 días desde hoy)
    acc_after = database.get_account_by_id(acc_test["id"])
    expected_exp = (date.today() + timedelta(days=50)).isoformat()
    assert acc_after["expiry_date"] == expected_exp, f"Esperado {expected_exp}, obtenido {acc_after['expiry_date']}"

    # 14. Test Freno de Emergencia en Purga de Cuentas
    res_purge_fail = database.purge_accounts_except_client("cliente_completamente_inexistente_99999")
    assert res_purge_fail["success"] is False, "La purga debe abortar si el cliente no existe"
    assert res_purge_fail["deleted_count"] == 0
    assert "abortada por seguridad" in res_purge_fail["error"]

    # 15. Test Búsqueda Jerárquica de Teléfonos (10 dígitos vs 8 dígitos)
    cli_ba = database.find_or_create_client(name="Cliente BA", whatsapp="5491198765432")
    cli_cba = database.find_or_create_client(name="Cliente CBA", whatsapp="54935198765432")
    
    match_ba = database.get_client_by_phone("5491198765432")
    assert match_ba is not None and match_ba["client"]["id"] == cli_ba["id"], "Debe coincidir exactamente con Cliente BA"

    match_cba = database.get_client_by_phone("54935198765432")
    assert match_cba is not None and match_cba["client"]["id"] == cli_cba["id"], "Debe coincidir exactamente con Cliente CBA"

    # 16. Test Detección de Comprobante Reciclado / Fraude por Operation ID
    p_legit = database.create_pending_payment(
        sender_phone="5491100112233",
        client_name="Cliente Honesto",
        amount=6000.0,
        operation_id="OP-UNICA-999"
    )
    database.approve_pending_payment(p_legit["id"])

    # Intento de reenvío del mismo comprobante ya aprobado
    p_fraud = database.create_pending_payment(
        sender_phone="5491199887766",
        client_name="Cliente Tramposo",
        amount=6000.0,
        operation_id="OP-UNICA-999"
    )
    assert "ALERTA DE FRAUDE" in p_fraud["notes"], "Debe alertar intento de comprobante reciclado por op_id"

    # 17. Test Detección de Comprobante Reciclado por Similitud Perceptual (pHash dHash)
    test_phash_1 = "1a2b3c4d5e6f7a8b"
    test_phash_similar = "1a2b3c4d5e6f7a8f" # Distancia de Hamming = 2 (<= 4)
    test_phash_different = "ffffffffffffffff" # Distancia alta

    p_legit_phash = database.create_pending_payment(
        sender_phone="5491100112244",
        client_name="Cliente Original pHash",
        amount=7000.0,
        phash=test_phash_1
    )
    assert p_legit_phash.get("phash") == test_phash_1
    database.approve_pending_payment(p_legit_phash["id"])

    # Intento de reenvío con pHash idéntico o similar (distancia <= 4)
    p_fraud_phash = database.create_pending_payment(
        sender_phone="5491199887755",
        client_name="Cliente Reciclador pHash",
        amount=7000.0,
        phash=test_phash_similar
    )
    assert "ALERTA DE FRAUDE" in p_fraud_phash["notes"], "Debe alertar fraude por similitud perceptual pHash"
    assert "perceptual" in p_fraud_phash["notes"].lower()

    # Comprobante con imagen diferente (no debe alertar fraude)
    p_diff_phash = database.create_pending_payment(
        sender_phone="5491199887744",
        client_name="Cliente Diferente",
        amount=7500.0,
        phash=test_phash_different
    )
    assert "ALERTA DE FRAUDE" not in p_diff_phash["notes"], "No debe alertar si el pHash es diferente"

    # 18. Test Hamming Distance y Perceptual Hash
    import services.receipt_service as rs
    assert rs.hamming_distance("0000000000000000", "0000000000000000") == 0
    assert rs.hamming_distance("0000000000000000", "0000000000000001") == 1
    assert rs.hamming_distance("0000000000000000", "0000000000000003") == 2
    assert rs.hamming_distance("1a2b3c4d5e6f7a8b", "1a2b3c4d5e6f7a8f") == 1 # 0xb (1011) vs 0xf (1111) difiere en 1 bit
    assert rs.hamming_distance("short", "other") == 999
    assert rs.hamming_distance("", "") == 999

    # 19. Test Cifrado Simétrico AES-256-GCM para Backups
    from core.security import encrypt_backup, decrypt_backup
    test_db_bytes = b"SQLite format 3\x00 StreamVault DB Test Data 2026"
    custom_master_key = "SuperMasterBackupKey2026-StrictTest"

    # Cifrado y descifrado con clave por defecto (SESSION_SECRET_KEY / BACKUP_ENCRYPTION_KEY)
    enc = encrypt_backup(test_db_bytes)
    assert enc.startswith(b"SVENC01")
    dec = decrypt_backup(enc)
    assert dec == test_db_bytes

    # Cifrado y descifrado con clave personalizada
    enc_custom = encrypt_backup(test_db_bytes, key=custom_master_key)
    dec_custom = decrypt_backup(enc_custom, key=custom_master_key)
    assert dec_custom == test_db_bytes

    # Falla con clave incorrecta
    try:
        from cryptography.exceptions import InvalidTag
        failed = False
        try:
            decrypt_backup(enc_custom, key="ClaveTotalmenteEquivocada")
        except (InvalidTag, ValueError):
            failed = True
        assert failed is True, "Debe rechazar descifrado con clave errónea"
    except ImportError:
        pass

    # Falla ante corrupción / manipulación de bytes (integridad GCM)
    corrupted = bytearray(enc)
    corrupted[-5] ^= 0xFF # Alterar 1 bit en el ciphertext/tag
    tamper_failed = False
    try:
        decrypt_backup(bytes(corrupted))
    except Exception:
        tamper_failed = True
    assert tamper_failed is True, "Debe rechazar backup alterado/manipulado"

    # 20. Test Reversión de Pago Individual y Restauración de Vencimiento
    from db.connection import get_connection
    cli_rev = database.find_or_create_client(name="Cliente Reversion", whatsapp="5491144556677")
    acc_rev = database.create_account(
        platform="Netflix",
        email="rev_test@netflix.com",
        password="pass",
        expiry_date=date.today().isoformat(),
        whatsapp="5491144556677",
        price="6000"
    )
    database.collect_payment(
        account_id=acc_rev["id"],
        extend_days=30,
        amount=6000.0,
        payment_method="Transferencia"
    )
    conn_t = get_connection()
    try:
        p_row = conn_t.execute("SELECT id, amount, status FROM payments WHERE account_id = ? ORDER BY id DESC LIMIT 1", (acc_rev["id"],)).fetchone()
        assert p_row is not None
        p_id = p_row["id"]
    finally:
        conn_t.close()

    rev_res = database.reverse_customer_payment(p_id, reason="Error de prueba")
    assert rev_res["success"] is True, f"Error al revertir: {rev_res}"
    assert rev_res["reversed_amount"] == 6000.0
    assert rev_res["amount"] == 6000.0

    acc_rev_after = database.get_account_by_id(acc_rev["id"])
    assert acc_rev_after["payment_status"] == "pendiente", "Cuenta con pago único revertido debe quedar 'pendiente'"

    # 21. Test Reversión de Pago Duplicado (Conserva 'pagado' si existe otro pago activo)
    cli_dup = database.find_or_create_client(name="Cliente Doble", whatsapp="5491155667788")
    acc_dup = database.create_account(
        platform="HTTP Custom",
        email="dup_user",
        password="HWID",
        expiry_date=date.today().isoformat(),
        whatsapp="5491155667788",
        price="8000"
    )
    database.collect_payment(account_id=acc_dup["id"], extend_days=30, amount=8000.0, payment_method="WhatsApp Auto")
    exp_after_p1 = (date.today() + timedelta(days=30)).isoformat()
    database.collect_payment(account_id=acc_dup["id"], extend_days=30, amount=8000.0, payment_method="MP")
    
    conn_t = get_connection()
    try:
        p2_id = conn_t.execute("SELECT id FROM payments WHERE account_id = ? ORDER BY id DESC LIMIT 1", (acc_dup["id"],)).fetchone()["id"]
    finally:
        conn_t.close()

    rev_dup_res = database.reverse_customer_payment(p2_id, reason="Cobro duplicado")
    assert rev_dup_res["success"] is True
    acc_dup_after = database.get_account_by_id(acc_dup["id"])
    assert acc_dup_after["payment_status"] == "pagado", "Cuenta con otro pago activo debe conservar 'pagado'"
    assert acc_dup_after["expiry_date"] == exp_after_p1, f"Vencimiento debía volver a {exp_after_p1}, quedó {acc_dup_after['expiry_date']}"

    # 22. Test Fusión Inteligente en approve_pending_payment (Evitar cobro doble con WhatsApp Auto)
    cli_fuse = database.find_or_create_client(name="Cliente Fusión", whatsapp="5491166778899")
    acc_fuse = database.create_account(
        platform="HTTP Custom",
        email="riveroangel_test",
        password="HWID",
        expiry_date=date.today().isoformat(),
        whatsapp="5491166778899",
        price="8000"
    )
    conn_t = get_connection()
    try:
        with conn_t:
            conn_t.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, 8000.0, 0.0, 8000.0, 'WhatsApp Auto', 'Renovación HTTP Custom - Usuario: riveroangel_test')
            """, (acc_fuse["id"], cli_fuse["id"]))
            existing_wa_pay_id = conn_t.execute("SELECT last_insert_rowid()").fetchone()[0]
            exp_fuse_target = (date.today() + timedelta(days=30)).isoformat()
            conn_t.execute("UPDATE streaming_accounts SET expiry_date = ?, payment_status = 'pagado' WHERE id = ?", (exp_fuse_target, acc_fuse["id"]))
    finally:
        conn_t.close()

    p_pen_fuse = database.create_pending_payment(
        sender_phone="5491166778899",
        client_name="Cliente Fusión",
        client_id=cli_fuse["id"],
        account_id=acc_fuse["id"],
        amount=8000.0,
        bank="Mercado Pago",
        operation_id="OP-FUSE-1234"
    )
    fuse_app_res = database.approve_pending_payment(p_pen_fuse["id"], admin_user="admin")
    assert fuse_app_res["success"] is True
    fin_d = fuse_app_res.get("finance_details") or {}
    assert fin_d.get("merged") is True, "Debía fusionar con el pago existente de WhatsApp Auto"
    assert fin_d.get("existing_payment_id") == existing_wa_pay_id

    conn_t = get_connection()
    try:
        total_p_count = conn_t.execute("SELECT COUNT(*) FROM payments WHERE account_id = ?", (acc_fuse["id"],)).fetchone()[0]
        fused_row = conn_t.execute("SELECT payment_method, notes FROM payments WHERE id = ?", (existing_wa_pay_id,)).fetchone()
    finally:
        conn_t.close()

    assert total_p_count == 1, f"Se esperaba 1 solo pago en el libro contable, se encontraron {total_p_count}"
    assert fused_row["payment_method"] == "Mercado Pago", f"El método debió actualizarse a Mercado Pago, tiene: {fused_row['payment_method']}"
    assert "Comprobante" in fused_row["notes"]

    acc_fuse_check = database.get_account_by_id(acc_fuse["id"])
    assert acc_fuse_check["expiry_date"] == exp_fuse_target, f"Vencimiento esperado {exp_fuse_target}, obtenido {acc_fuse_check['expiry_date']}"

    print("    ✅ Aprobación y Pagos: Todos los casos de prueba y parches defensivos superados exitosamente.")

if __name__ == "__main__":
    run_tests()


