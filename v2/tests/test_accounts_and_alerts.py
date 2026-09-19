from datetime import date, timedelta
import database

def run_tests():
    print("  [Suite] Vencimientos, Estados y Control de Cuentas...")

    # 1. Cálculo de días restantes
    today = date.today()
    exp_5d = (today + timedelta(days=5)).isoformat()
    exp_past = (today - timedelta(days=2)).isoformat()

    acc1 = database.add_free_account(
        platform="Disney+ Premium",
        email="test_disney@stream.com",
        password="pass123",
        cost="1500"
    )
    assert acc1["status"] == "libre"

    # 2. Venta de la cuenta libre
    sold = database.assign_or_sell_account(
        client_name="Cliente Vencimiento",
        platform="Disney+ Premium",
        email="test_disney@stream.com",
        password="pass123",
        expiry_date=exp_5d,
        whatsapp="5491144445555",
        price="4000"
    )
    assert sold["status"] == "ocupada"
    assert sold["payment_status"] == "pagado"

    # 3. Reporte de caída
    fallen = database.mark_account_fallen(str(sold["id"]), reason="Caída de prueba")
    assert fallen is not None
    assert fallen["status"] == "caida"

    # 4. Renovación (+30d) y salida de estado de caída
    new_exp = (today + timedelta(days=35)).isoformat()
    renewed = database.register_customer_payment(
        email_or_id=str(sold["id"]),
        amount=4000.0,
        new_expiry_date=new_exp,
        payment_method="Panel Web"
    )
    assert renewed["success"] is True
    assert renewed["new_expiry"] == new_exp

    acc_check = database.get_account_by_id(sold["id"])
    assert acc_check["status"] == "ocupada"
    assert acc_check["expiry_date"] == new_exp

    print("    ✅ Cuentas y Vencimientos: 4/4 casos de prueba superados exitosamente.")

if __name__ == "__main__":
    run_tests()
