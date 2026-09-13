import io
import csv
import json
from datetime import datetime, date, timedelta
from typing import Dict, Any, List

from db.connection import get_connection
from db.repositories.accounts_repo import get_active_accounts, get_free_stock, assign_or_sell_account
from db.repositories.catalog_repo import get_combos
from core.utils import parse_money, _parse_date_flexible

def _detect_csv_delimiter(content: str) -> str:
    """Detecta inteligentemente si el archivo viene separado por coma, punto y coma o tabulación."""
    first_line = content.strip().split("\n")[0] if content else ""
    if ";" in first_line and first_line.count(";") >= first_line.count(","):
        return ";"
    elif "\t" in first_line:
        return "\t"
    return ","

def export_active_accounts_csv() -> str:
    """Exporta todas las cuentas y clientes activos a formato CSV con UTF-8 BOM para Excel."""
    accounts = get_active_accounts()
    output = io.StringIO()
    output.write("\ufeff")
    
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID", "Cliente", "Codigo_Cliente", "Tipo_Cliente", "WhatsApp", "Telegram",
        "Plataforma", "Perfil", "PIN", "Correo", "Contrasena", "Fecha_Inicio",
        "Fecha_Vence", "Dias_Restantes", "Precio_Cobrado", "Costo_Proveedor",
        "Ganancia_Estimada", "Estado_Pago", "Notas"
    ])

    for a in accounts:
        price_num = parse_money(a.get("price"))
        cost_num = parse_money(a.get("cost"))
        profit_num = price_num - cost_num
        writer.writerow([
            a.get("id", ""),
            a.get("client_name", ""),
            a.get("client_code", ""),
            a.get("client_type", "consumidor_final"),
            a.get("whatsapp", ""),
            a.get("telegram", ""),
            a.get("platform", ""),
            a.get("profile_name", ""),
            a.get("profile_pin", ""),
            a.get("email", ""),
            a.get("password", ""),
            a.get("start_date", ""),
            a.get("expiry_date", ""),
            a.get("days_remaining", ""),
            f"{price_num:.2f}",
            f"{cost_num:.2f}",
            f"{profit_num:.2f}",
            a.get("payment_status", "pagado"),
            a.get("notes", "")
        ])

    return output.getvalue()

def export_free_stock_csv() -> str:
    """Exporta el inventario de cuentas y perfiles libres a CSV con UTF-8 BOM."""
    stock = get_free_stock()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["ID", "Plataforma", "Perfil", "PIN", "Correo", "Contrasena", "Costo", "Notas"])

    for s in stock:
        writer.writerow([
            s.get("id", ""),
            s.get("platform", ""),
            s.get("profile_name", ""),
            s.get("profile_pin", ""),
            s.get("email", ""),
            s.get("password", ""),
            s.get("cost", ""),
            s.get("notes", "")
        ])

    return output.getvalue()

def export_transactions_csv() -> str:
    """Exporta el historial de pagos y cobros registrados a CSV con UTF-8 BOM."""
    conn = get_connection()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID_Transaccion", "Fecha_Hora", "Cliente", "Tipo_Cliente", "Plataforma",
        "Monto_Cobrado_ARS", "Costo_ARS", "Ganancia_Neta_ARS", "Metodo_Pago", "Notas"
    ])

    try:
        rows = conn.execute("""
            SELECT p.*, c.name as client_name, c.client_type, a.platform
            FROM payments p
            LEFT JOIN clients c ON p.client_id = c.id
            LEFT JOIN streaming_accounts a ON p.account_id = a.id
            ORDER BY p.id DESC
        """).fetchall()

        for r in rows:
            writer.writerow([
                r["id"],
                r["created_at"],
                r["client_name"] or "Venta General",
                r["client_type"] or "",
                r["platform"] or "",
                f"{r['amount']:.2f}",
                f"{r['cost']:.2f}",
                f"{r['profit']:.2f}",
                r["payment_method"] or "Transferencia",
                r["notes"] or ""
            ])
    finally:
        conn.close()

    return output.getvalue()

def export_full_backup_json() -> str:
    """Genera una copia de seguridad íntegra en formato JSON de todas las tablas."""
    conn = get_connection()
    try:
        clients = [dict(r) for r in conn.execute("SELECT * FROM clients").fetchall()]
        accounts = [dict(r) for r in conn.execute("SELECT * FROM streaming_accounts").fetchall()]
        payments = [dict(r) for r in conn.execute("SELECT * FROM payments").fetchall()]
        thresholds = [dict(r) for r in conn.execute("SELECT * FROM stock_thresholds").fetchall()]
        catalog = [dict(r) for r in conn.execute("SELECT * FROM price_catalog").fetchall()]
        combos = get_combos()
        
        backup_data = {
            "backup_version": "2.8.0",
            "currency": "ARS",
            "created_at": datetime.now().isoformat(),
            "stats": {
                "clients_count": len(clients),
                "accounts_count": len(accounts),
                "payments_count": len(payments),
                "catalog_count": len(catalog),
                "combos_count": len(combos)
            },
            "clients": clients,
            "streaming_accounts": accounts,
            "payments": payments,
            "stock_thresholds": thresholds,
            "price_catalog": catalog,
            "combos": combos
        }
        return json.dumps(backup_data, indent=2, ensure_ascii=False)
    finally:
        conn.close()

def get_csv_template_stock() -> str:
    """Plantilla CSV modelo para cargar inventario libre en Excel (montos en ARS)."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow(["Plataforma", "Correo", "Contrasena", "Perfil", "PIN", "Costo", "Notas"])
    writer.writerow(["Netflix 4K", "cuenta1@ejemplo.com", "ClaveSegura123", "Perfil 1", "1234", "3200", "Proveedor Central"])
    writer.writerow(["Disney+", "cuenta2@ejemplo.com", "ClaveSegura456", "", "", "2000", "Cuenta Completa"])
    writer.writerow(["Spotify Familiar", "cuenta3@ejemplo.com", "ClaveSegura789", "", "", "2500", "Plan Familiar"])
    return output.getvalue()

def get_csv_template_sales() -> str:
    """Plantilla CSV modelo para migrar o cargar ventas con clientes en Excel (montos en ARS)."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "Cliente", "WhatsApp", "Telegram", "Tipo_Cliente", "Plataforma",
        "Correo", "Contrasena", "Perfil", "PIN", "Vencimiento", "Precio", "Costo", "Notas"
    ])
    writer.writerow([
        "Juan Perez", "+5491112345678", "@juanp", "consumidor_final", "Netflix 4K",
        "net@ejemplo.com", "Clave123", "Perfil 1", "1234", "2026-10-15", "5500", "3200", "Cliente puntual"
    ])
    writer.writerow([
        "Matias Revendedor", "+5491187654321", "@matias_reseller", "revendedor", "Disney+",
        "dis@ejemplo.com", "Pass456", "", "", "2026-10-20", "3000", "2000", "Lote mensual"
    ])
    return output.getvalue()

def import_free_stock_csv(csv_content: str) -> Dict[str, Any]:
    """
    Importa masivamente cuentas al stock libre desde contenido CSV o TSV copiado de Excel.
    Reconoce encabezados flexibles en español e inglés.
    """
    content = csv_content.lstrip("\ufeff").strip()
    if not content:
        return {"success": False, "error": "El contenido CSV está vacío."}

    delim = _detect_csv_delimiter(content)
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    
    rows = list(reader)
    if not rows:
        return {"success": False, "error": "No se encontraron filas de datos."}

    headers = [h.strip().lower() for h in rows[0]]
    
    def find_col_idx(aliases: List[str]) -> int:
        for idx, h in enumerate(headers):
            for a in aliases:
                if a in h:
                    return idx
        return -1

    col_plat = find_col_idx(["plat", "serv", "service"])
    col_mail = find_col_idx(["corr", "email", "mail", "user", "cuenta"])
    col_pass = find_col_idx(["contra", "pass", "clave"])
    col_perf = find_col_idx(["perf", "pantalla", "screen"])
    col_pin = find_col_idx(["pin"])
    col_cost = find_col_idx(["cost", "prov"])
    col_notes = find_col_idx(["not", "obs", "coment"])

    if col_plat == -1 or col_mail == -1:
        return {
            "success": False,
            "error": "No se encontraron columnas obligatorias (Plataforma y Correo/Email). Verifica los encabezados."
        }

    imported = 0
    skipped = 0
    errors = []

    conn = get_connection()
    try:
        with conn:
            for row_num, r in enumerate(rows[1:], start=2):
                if not any(r):
                    continue
                try:
                    plat = r[col_plat].strip() if col_plat < len(r) else ""
                    mail = r[col_mail].strip() if col_mail < len(r) else ""
                    pwd = r[col_pass].strip() if col_pass != -1 and col_pass < len(r) else "123456"
                    perf = r[col_perf].strip() if col_perf != -1 and col_perf < len(r) else ""
                    pin = r[col_pin].strip() if col_pin != -1 and col_pin < len(r) else ""
                    cost = r[col_cost].strip() if col_cost != -1 and col_cost < len(r) else ""
                    notes = r[col_notes].strip() if col_notes != -1 and col_notes < len(r) else "Carga masiva CSV"

                    if not plat or not mail:
                        skipped += 1
                        continue

                    conn.execute("""
                        INSERT INTO streaming_accounts (
                            platform, email, password, profile_name, profile_pin,
                            status, payment_status, cost, notes
                        ) VALUES (?, ?, ?, ?, ?, 'libre', 'pagado', ?, ?)
                    """, (plat, mail, pwd, perf, pin, cost, notes))
                    imported += 1
                except Exception as ex:
                    errors.append(f"Línea {row_num}: {str(ex)}")

        return {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]
        }
    finally:
        conn.close()

def import_sales_csv(csv_content: str) -> Dict[str, Any]:
    """
    Importa masivamente clientes y suscripciones vendidas desde un archivo CSV o planilla.
    Crea automáticamente los clientes, asigna las cuentas con su fecha de vencimiento y registra los pagos.
    """
    content = csv_content.lstrip("\ufeff").strip()
    if not content:
        return {"success": False, "error": "El contenido CSV está vacío."}

    delim = _detect_csv_delimiter(content)
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    rows = list(reader)
    if not rows:
        return {"success": False, "error": "No se encontraron filas de datos."}

    headers = [h.strip().lower() for h in rows[0]]

    def find_col(aliases: List[str]) -> int:
        for idx, h in enumerate(headers):
            for a in aliases:
                if a in h:
                    return idx
        return -1

    c_client = find_col(["client", "nom", "user"])
    c_wa = find_col(["whats", "tel", "cel", "phone", "movil"])
    c_tg = find_col(["teleg", "tg"])
    c_type = find_col(["tipo", "type"])
    c_plat = find_col(["plat", "serv"])
    c_mail = find_col(["corr", "email", "mail", "cuenta"])
    c_pass = find_col(["contra", "pass", "clave"])
    c_perf = find_col(["perf", "pantalla"])
    c_pin = find_col(["pin"])
    c_venc = find_col(["venc", "expir", "fecha"])
    c_price = find_col(["prec", "price", "monto", "cobro"])
    c_cost = find_col(["cost", "prov"])
    c_notes = find_col(["not", "obs"])

    if c_client == -1 or c_plat == -1 or c_mail == -1:
        return {
            "success": False,
            "error": "Faltan columnas esenciales (Cliente, Plataforma, Correo). Verifica la estructura del archivo."
        }

    imported = 0
    skipped = 0
    errors = []

    for row_num, r in enumerate(rows[1:], start=2):
        if not any(r):
            continue
        try:
            client_name = r[c_client].strip() if c_client < len(r) else ""
            plat = r[c_plat].strip() if c_plat < len(r) else ""
            mail = r[c_mail].strip() if c_mail < len(r) else ""

            if not client_name or not plat or not mail:
                skipped += 1
                continue

            wa = r[c_wa].strip() if c_wa != -1 and c_wa < len(r) else ""
            tg = r[c_tg].strip() if c_tg != -1 and c_tg < len(r) else ""
            ctype = r[c_type].strip().lower() if c_type != -1 and c_type < len(r) else "consumidor_final"
            if "revend" in ctype:
                ctype = "revendedor"
            else:
                ctype = "consumidor_final"

            pwd = r[c_pass].strip() if c_pass != -1 and c_pass < len(r) else "123456"
            perf = r[c_perf].strip() if c_perf != -1 and c_perf < len(r) else ""
            pin = r[c_pin].strip() if c_pin != -1 and c_pin < len(r) else ""
            raw_venc = r[c_venc].strip() if c_venc != -1 and c_venc < len(r) else ""
            venc = _parse_date_flexible(raw_venc)
            price = r[c_price].strip() if c_price != -1 and c_price < len(r) else "$5.00"
            cost = r[c_cost].strip() if c_cost != -1 and c_cost < len(r) else "$2.50"
            notes = r[c_notes].strip() if c_notes != -1 and c_notes < len(r) else "Importación masiva"

            res = assign_or_sell_account(
                client_name=client_name,
                whatsapp=wa,
                telegram=tg,
                client_type=ctype,
                platform=plat,
                email=mail,
                password=pwd,
                profile_name=perf,
                profile_pin=pin,
                expiry_date=venc,
                price=price,
                cost=cost,
                notes=notes
            )
            if res:
                imported += 1
            else:
                skipped += 1
        except Exception as ex:
            errors.append(f"Línea {row_num}: {str(ex)}")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:10]
    }
