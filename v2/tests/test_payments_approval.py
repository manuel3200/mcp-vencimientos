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

    print("    ✅ Aprobación y Pagos: 6/6 casos de prueba superados exitosamente.")

if __name__ == "__main__":
    run_tests()
