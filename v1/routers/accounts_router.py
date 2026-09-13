import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
from core.security import verify_session_cookie
from telegram_bot import send_telegram_message, format_and_send_stock_alert
from scheduler import check_and_send_alerts, check_and_send_stock_alerts

router = APIRouter()

@router.post("/api/collect-payment/{account_id}")
async def collect_payment_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    extend_param = request.query_params.get("extend")
    extend_bool = (extend_param == "1") if extend_param in ("0", "1") else None
    res = database.register_customer_payment(
        email_or_id=str(account_id),
        payment_method="Panel Web",
        extend_expiry=extend_bool
    )
    if res.get("success"):
        is_initial = res.get("is_initial", False)
        title = "💵 <b>Pago de Compra Registrado</b>" if is_initial else "🔄 <b>Cobro y Renovación (+30d) Registrados</b>"
        expiry_info = f"• Vencimiento: {res['new_expiry']} (al día)" if is_initial else f"• Próximo vencimiento: {res['new_expiry']} (+30 días)"
        wa_data = database.generate_whatsapp_message(str(account_id), message_type="entrega")
        wa_url = wa_data.get("wa_link", "")
        wa_link_html = f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR COMPROBANTE Y DATOS POR WHATSAPP (1 Clic)</b></a>" if wa_url else ""
        await send_telegram_message(
            f"{title}\n\n"
            f"• Cliente: {res['client_name']}\n"
            f"• Servicio: {res['platform']}\n"
            f"• Monto cobrado: {database.format_ars(res['amount'])}\n"
            f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
            f"{expiry_info}"
            f"{wa_link_html}"
        )
    return RedirectResponse(url="/#accounts", status_code=303)


@router.post("/api/mark-fallen/{account_id}")
async def mark_fallen_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.mark_account_fallen(str(account_id), reason="Marcada desde el Panel")
    return RedirectResponse(url="/#accounts", status_code=303)

@router.post("/api/reactivate-fallen/{account_id}")
async def reactivate_fallen_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.reactivate_fallen_account(str(account_id))
    return RedirectResponse(url="/#stock", status_code=303)


@router.post("/api/report-master-fallen")
async def report_master_fallen_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    form = await request.form()
    email = str(form.get("email", "")).strip()
    if email:
        database.mark_entire_master_account_fallen(email, reason="Caída de cuenta completa reportada desde el Panel")
    return RedirectResponse(url="/#screens", status_code=303)


@router.post("/api/auto-replace/{account_id}")
async def auto_replace_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.replace_fallen_account(str(account_id))
    if res and res.get("success"):
        new_a = res["new_account"]
        wa_data = database.generate_whatsapp_message(new_a, message_type="reemplazo")
        wa_url = wa_data.get("wa_link", "")
        wa_link_html = f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR NUEVA CUENTA POR WHATSAPP (1 Clic)</b></a>" if wa_url else ""
        msg = (
            f"✅ Reemplazo exitoso para <b>{new_a.get('client_name')}</b>:\n"
            f"• Plataforma: {new_a['platform']}\n"
            f"• Nueva cuenta: <code>{new_a['email']}</code>\n"
            f"• Clave: <code>{new_a['password']}</code>"
        )
        await send_telegram_message(f"🔄 <b>Reemplazo de Cuenta</b>\n\n{msg}{wa_link_html}")
    return RedirectResponse(url="/#stock", status_code=303)

@router.post("/api/delete-account/{account_id}")
async def delete_account_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_account(account_id)
    return RedirectResponse(url="/#accounts", status_code=303)

@router.post("/api/purge-accounts")
async def purge_accounts_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    form = await request.form()
    client_name = str(form.get("client_name", "Samuel Martinez")).strip() or "Samuel Martinez"
    database.purge_accounts_except_client(client_name)
    return RedirectResponse(url="/#stock", status_code=303)


@router.post("/api/test-telegram")
async def test_telegram_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    ok = await send_telegram_message("🔔 <b>Prueba de Telegram exitosa desde el Panel Web de Streaming CRM</b>")
    return JSONResponse({"ok": ok})

@router.post("/api/check-now")
async def check_now_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    sent = await check_and_send_alerts(days_window=7, force=True)
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@router.post("/api/check-stock-alert")
async def check_stock_alert_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    from telegram_bot import format_and_send_stock_alert
    await format_and_send_stock_alert()
    return RedirectResponse(url="/#stock", status_code=303)

@router.post("/api/set-stock-threshold")
async def set_stock_threshold_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    form = await request.form()
    platform = str(form.get("platform", "")).strip()
    try:
        min_stock = int(form.get("min_stock", 2))
    except ValueError:
        min_stock = 2
    if platform:
        database.set_platform_min_stock(platform, min_stock)
    return RedirectResponse(url="/#stock", status_code=303)

@router.post("/api/account/update-price")
async def update_account_price_endpoint(
    request: Request,
    account_id: int = Form(...),
    new_price: str = Form(...),
    mark_as_reseller: bool = Form(False)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.update_account_price(
        identifier=account_id,
        new_price=new_price,
        mark_as_reseller=mark_as_reseller
    )
    if not res:
        return RedirectResponse(url="/?err=No+se+pudo+actualizar+el+precio#accounts", status_code=303)
    return RedirectResponse(url="/?msg=price_saved#accounts", status_code=303)


@router.post("/api/sell-account")
async def sell_account_api(
    request: Request,
    platform: str = Form(...),
    expiry_date: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    client_id: Optional[str] = Form(None),
    new_client_name: Optional[str] = Form(None),
    client_whatsapp: Optional[str] = Form(""),
    price: Optional[str] = Form(""),
    profile_name: Optional[str] = Form(""),
    profile_pin: Optional[str] = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)

    client_name = ""
    client_phone = client_whatsapp.strip() if client_whatsapp else ""
    client_type = "consumidor_final"

    if client_id and client_id.strip():
        try:
            cid = int(client_id.strip())
            c_info = database.get_client_360(cid)
            if c_info and c_info.get("client"):
                client_name = c_info["client"].get("name", "")
                if not client_phone:
                    client_phone = c_info["client"].get("whatsapp", "")
                client_type = c_info["client"].get("client_type", "consumidor_final")
        except Exception:
            pass

    if not client_name:
        client_name = (new_client_name or "").strip() or "Cliente"

    acc = database.assign_or_sell_account(
        client_name=client_name,
        platform=platform,
        email=email,
        password=password,
        expiry_date=expiry_date,
        whatsapp=client_phone,
        client_type=client_type,
        profile_name=profile_name or "",
        profile_pin=profile_pin or "",
        price=price or ""
    )
    return RedirectResponse(url="/?msg=sale_saved#accounts", status_code=303)


@router.post("/api/free-stock")
async def free_stock_api(
    request: Request,
    platform: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    cost: Optional[str] = Form(""),
    profile_name: Optional[str] = Form(""),
    profile_pin: Optional[str] = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)

    database.add_free_account(
        platform=platform,
        email=email,
        password=password,
        profile_name=profile_name or "",
        profile_pin=profile_pin or "",
        cost=cost or ""
    )
    return RedirectResponse(url="/?msg=stock_saved#stock", status_code=303)


# ==========================================
# Endpoints de Exportación e Importación (Excel / CSV)