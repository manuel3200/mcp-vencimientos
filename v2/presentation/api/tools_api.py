import os
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, Response

import database
import system_logger
from core.security import verify_session_cookie
from telegram_bot import send_full_backup_to_telegram

router = APIRouter()

@router.get("/api/templates/json")
async def api_get_templates_json(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    return {
        "templates": database.get_whatsapp_templates(),
        "payment_settings": database.get_payment_settings(),
        "formatted_payment_methods": database.get_formatted_payment_methods()
    }

@router.post("/api/settings/payment")
async def api_save_payment_settings(
    request: Request,
    alias_mp: str = Form(""),
    cvu_cbu: str = Form(""),
    account_holder: str = Form(""),
    bank_name: str = Form(""),
    usdt_address: str = Form(""),
    extra_instructions: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_payment_settings(
        alias_mp=alias_mp,
        cvu_cbu=cvu_cbu,
        account_holder=account_holder,
        bank_name=bank_name,
        usdt_address=usdt_address,
        extra_instructions=extra_instructions
    )
    return RedirectResponse(url="/?msg=payment_settings_saved#tab-templates", status_code=302)

@router.post("/api/templates/save")
async def api_save_template(
    request: Request,
    template_key: str = Form(...),
    content: str = Form(...),
    title: str = Form(""),
    description: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_whatsapp_template(
        template_key=template_key,
        content=content,
        title=title,
        description=description
    )
    return RedirectResponse(url="/?msg=template_saved#tab-templates", status_code=302)

@router.post("/api/templates/reset/{template_key}")
async def api_reset_template(template_key: str, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.reset_whatsapp_template(template_key)
    return RedirectResponse(url="/?msg=template_reset#tab-templates", status_code=302)


# ==========================================
@router.get("/api/export/active.csv")
async def export_active_csv(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_active_accounts_csv()
    filename = f"crm_cuentas_activas_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/api/export/stock.csv")
async def export_stock_csv(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_free_stock_csv()
    filename = f"crm_stock_libre_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/api/export/transactions.csv")
async def export_transactions_csv_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_transactions_csv()
    filename = f"crm_balance_transacciones_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/api/export/template-stock.csv")
async def export_template_stock(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.get_csv_template_stock()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_stock_modelo.csv"}
    )

@router.get("/api/export/template-sales.csv")
async def export_template_sales(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.get_csv_template_sales()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_ventas_modelo.csv"}
    )

@router.post("/api/import/stock")
async def import_stock_api(request: Request, file: Optional[UploadFile] = None, csv_text: Optional[str] = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    content = ""
    if file and file.filename:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        content = csv_text.strip()
    
    if not content:
        msg = urllib.parse.quote("⚠️ No se proporcionó ningún archivo ni texto CSV.")
        return RedirectResponse(url=f"/?msg={msg}", status_code=303)
    
    res = database.import_free_stock_csv(content)
    if res.get("success"):
        msg = urllib.parse.quote(f"✅ Se importaron con éxito {res['imported']} cuenta(s) al stock libre ({res['skipped']} omitidas).")
    else:
        msg = urllib.parse.quote(f"❌ Error al importar: {res.get('error')}")
    return RedirectResponse(url=f"/?msg={msg}#stock", status_code=303)

@router.post("/api/import/sales")
async def import_sales_api(request: Request, file: Optional[UploadFile] = None, csv_text: Optional[str] = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    content = ""
    if file and file.filename:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        content = csv_text.strip()
    
    if not content:
        msg = urllib.parse.quote("⚠️ No se proporcionó ningún archivo ni texto CSV.")
        return RedirectResponse(url=f"/?msg={msg}#tools", status_code=303)
    
    res = database.import_sales_csv(content)
    if res.get("success"):
        msg = urllib.parse.quote(f"✅ Se migraron con éxito {res['imported']} venta(s) y cliente(s) ({res['skipped']} omitidas).")
    else:
        msg = urllib.parse.quote(f"❌ Error al importar: {res.get('error')}")
    return RedirectResponse(url=f"/?msg={msg}#accounts", status_code=303)


@router.post("/api/import/csv")
async def import_csv_dispatcher(
    request: Request,
    import_type: str = Form("stock"),
    file: Optional[UploadFile] = None
):
    if import_type == "sales":
        return await import_sales_api(request, file=file)
    else:
        return await import_stock_api(request, file=file)


@router.post("/api/trigger-backup")
async def trigger_backup_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    from telegram_bot import send_full_backup_to_telegram
    await send_full_backup_to_telegram()
    msg = urllib.parse.quote("📲 Copia de seguridad enviada exitosamente a tu Telegram.")
    return RedirectResponse(url=f"/?msg={msg}#tools", status_code=303)

# API Proveedores y Cuentas Madre (Paso 4)

@router.get("/api/logs/json")
async def api_get_logs_json(request: Request, level: str = "ALL", query: str = ""):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    logs = system_logger.get_recent_logs(level=level, query=query, limit=120)
    health = system_logger.get_system_health_report()
    return {"logs": logs, "health": health}

@router.get("/api/logs/download")
async def api_download_logs(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = system_logger.get_raw_log_file(max_lines=3000)
    today_str = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(
        content=content.encode("utf-8", errors="replace"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=system_logs_{today_str}.txt"}
    )

@router.post("/api/logs/clear")
async def api_clear_logs(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    system_logger.clear_memory_logs()
    return RedirectResponse(url="/?msg=logs_cleared#logs", status_code=303)

from fastapi.responses import FileResponse
from core.config import settings
from application.http_custom.renew_custom import execute_renew_http_custom

@router.get("/api/system/backup-download")
async def api_download_database_backup(request: Request):
    """Descarga el archivo físico SQLite services.db directamente en el navegador."""
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    db_file = settings.DB_PATH
    if not os.path.exists(db_file):
        raise HTTPException(status_code=404, detail="Archivo de base de datos no encontrado")
    today_str = datetime.now().strftime("%Y%m%d_%H%M")
    return FileResponse(
        path=db_file,
        filename=f"services_backup_{today_str}.db",
        media_type="application/x-sqlite3"
    )

@router.post("/api/system/prune-receipts")
async def api_prune_receipts(request: Request, days: int = Form(60)):
    """Ejecuta la purga manual de comprobantes Base64 antiguos resueltos."""
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    pruned = database.prune_old_approved_receipts_base64(days_threshold=days)
    msg = urllib.parse.quote(f"Se liberaron {pruned} imágenes Base64 antiguas de la base de datos.")
    return RedirectResponse(url=f"/?msg={msg}#tools", status_code=303)

@router.post("/api/http-custom/renew/{account_id}")
async def api_renew_http_custom(account_id: int, request: Request, days: int = Form(30)):
    """Renueva un servidor HTTP Custom por N días (default 30) desde el panel."""
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user if isinstance(user, str) else user.get("username", "admin")
    res = execute_renew_http_custom(account_id, extend_days=days, admin_user=username)
    if res.get("success"):
        msg = urllib.parse.quote(f"Servidor HTTP Custom ({res.get('username')}) renovado hasta {res.get('new_expiry_date')}")
        return RedirectResponse(url=f"/?msg={msg}#http-custom", status_code=303)
    else:
        err = urllib.parse.quote(res.get("error", "Error al renovar servidor"))
        return RedirectResponse(url=f"/?err={err}#http-custom", status_code=303)


@router.get("/api/audit/anchor")
async def api_get_audit_anchor(request: Request):
    """Retorna el ancla criptográfica actual (root hash, total de bloques y último ID) para consumo por n8n o auditores."""
    from core.audit import get_latest_audit_entry, GENESIS_HASH
    from db.connection import get_connection
    from datetime import datetime
    
    latest = get_latest_audit_entry()
    latest_sig = latest.get("signature_hmac", GENESIS_HASH) if latest else GENESIS_HASH
    latest_id = latest.get("id", 0) if latest else 0
    
    conn = get_connection()
    try:
        count_row = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()
        total_count = count_row["c"] if count_row else 0
    finally:
        conn.close()
        
    return {
        "success": True,
        "total_blocks": total_count,
        "latest_id": latest_id,
        "root_hash": latest_sig,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }


# ==========================================
# 12. Endpoints Evolution API WhatsApp & Webhooks