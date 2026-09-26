import os
import urllib.parse
from datetime import date, datetime
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
    from core.rate_limiter import read_bounded_upload_file

    content = ""
    if file and file.filename:
        file_bytes = await read_bounded_upload_file(file, max_bytes=2 * 1024 * 1024)
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        if len(csv_text.encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="El texto CSV excede el tamaño máximo de 2 MB.")
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
    from core.rate_limiter import read_bounded_upload_file

    content = ""
    if file and file.filename:
        file_bytes = await read_bounded_upload_file(file, max_bytes=2 * 1024 * 1024)
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        if len(csv_text.encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="El texto CSV excede el tamaño máximo de 2 MB.")
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

from core.config import settings
from application.http_custom.renew_custom import execute_renew_http_custom
from services.backup_service import create_encrypted_sqlite_backup_bytes_async

@router.get("/api/system/backup-download")
async def api_download_database_backup(request: Request):
    """Genera un snapshot consistente de SQLite con sqlite3.backup(), lo cifra con BACKUP_ENCRYPTION_KEY y lo entrega (O06)."""
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    db_file = settings.DB_PATH
    if not os.path.exists(db_file):
        raise HTTPException(status_code=404, detail="Archivo de base de datos no encontrado")
    actor = user if isinstance(user, str) else user.get("username", "admin")
    encrypted_bytes = await create_encrypted_sqlite_backup_bytes_async(db_file, actor=f"web:{actor}")
    today_str = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(
        content=encrypted_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=services_backup_{today_str}.db.enc",
            "Cache-Control": "no-store",
        }
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
    """Retorna el ancla criptográfica actual (root hash, total de bloques y último ID) para consumo por n8n o auditores autenticados."""
    import secrets
    from core.principal import authenticate_request
    from core.audit import get_latest_audit_entry, GENESIS_HASH
    from db.connection import get_connection

    principal = authenticate_request(request)
    if principal is None:
        api_key = (request.headers.get("X-API-Key") or request.headers.get("apikey") or "").strip()
        expected_n8n = (getattr(settings, "N8N_WEBHOOK_SECRET", "") or os.getenv("N8N_WEBHOOK_SECRET", "")).strip()
        if not (api_key and expected_n8n and secrets.compare_digest(api_key, expected_n8n)):
            raise HTTPException(status_code=401, detail="No autorizado")

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
# O05: Cola Persistente de Despacho Saliente (202 Accepted + Consulta de Estado)
# ==========================================

def _authorize_outbound_queue(request: Request) -> str:
    import secrets
    from core.principal import authenticate_request

    principal = authenticate_request(request)
    if principal is not None:
        return principal.subject

    api_key = (request.headers.get("X-API-Key") or request.headers.get("apikey") or "").strip()
    expected_n8n = (getattr(settings, "N8N_WEBHOOK_SECRET", "") or os.getenv("N8N_WEBHOOK_SECRET", "")).strip()
    if api_key and expected_n8n and secrets.compare_digest(api_key, expected_n8n):
        return "n8n-buffer"
    raise HTTPException(status_code=401, detail="No autorizado para operar la cola de envíos.")


@router.post("/api/v1/outbound-queue/enqueue")
async def api_enqueue_outbound_job(request: Request):
    """Encola un mensaje saliente con clave de idempotencia y responde 202 Accepted tras persistir (O05)."""
    import json
    from core.rate_limiter import read_bounded_body
    from services.outbound_queue_service import enqueue_outbound_job

    producer = _authorize_outbound_queue(request)
    raw_body = await read_bounded_body(request, max_bytes=32 * 1024)
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Cuerpo JSON inválido.")

    try:
        job = enqueue_outbound_job(
            producer=str(data.get("producer") or producer),
            idempotency_key=str(data.get("idempotency_key") or ""),
            recipient=str(data.get("recipient") or ""),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {"message": str(data.get("message") or "")},
            instance=data.get("instance"),
        )
        return JSONResponse(
            {
                "status": "accepted",
                "job_id": job.get("id"),
                "duplicate": bool(job.get("duplicate")),
                "job_status": job.get("status", "pending"),
            },
            status_code=202,
        )
    except PermissionError as p_err:
        raise HTTPException(status_code=403, detail=str(p_err))
    except ValueError as v_err:
        raise HTTPException(status_code=400, detail=str(v_err))


@router.get("/api/v1/outbound-queue/jobs/{job_id}")
async def api_get_outbound_job_status(job_id: int, request: Request):
    """Consulta el estado de una tarea en la cola de despacho (O05)."""
    from services.outbound_queue_service import get_outbound_job

    _authorize_outbound_queue(request)
    job = get_outbound_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return {"status": "ok", "job": job}


@router.get("/api/v1/outbound-queue/dead-letter")
async def api_list_outbound_dead_letter(request: Request, limit: int = 50):
    """Lista las tareas en dead_letter o ambiguous_review para revisión operativa (O05)."""
    from services.outbound_queue_service import list_failed_outbound_jobs

    _authorize_outbound_queue(request)
    return {"status": "ok", "jobs": list_failed_outbound_jobs(limit=limit)}