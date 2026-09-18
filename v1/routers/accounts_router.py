import urllib.parse
import logging
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
from core.security import verify_session_cookie
from telegram_bot import send_telegram_message, format_and_send_stock_alert
from scheduler import check_and_send_alerts, check_and_send_stock_alerts

logger = logging.getLogger("routers.accounts")
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


@router.post("/api/pending-payments/approve/{payment_id}")
async def approve_pending_payment_api(payment_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")
    custom_amt = None
    try:
        if request.headers.get("content-type", "").startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form_data = await request.form()
            custom_amt_str = form_data.get("amount")
        else:
            custom_amt_str = request.query_params.get("amount")
        if custom_amt_str:
            from core.utils import parse_money
            parsed_a = parse_money(custom_amt_str)
            if parsed_a > 0:
                custom_amt = parsed_a
    except Exception:
        pass

    res = database.approve_pending_payment(payment_id, admin_user=f"Web ({username})", custom_amount=custom_amt)
    if res.get("success"):
        p = res.get("payment", {})
        amt_fmt = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)
        phone = p.get("sender_phone") or p.get("client_whatsapp")
        if phone:
            try:
                import whatsapp_client
                clean_phone = database.clean_whatsapp_phone(phone)
                if clean_phone:
                    wa_reply = (
                        f"🎉 ¡Hola {p.get('client_name', 'Cliente')}! Confirmamos la recepción y acreditación de tu pago"
                        + (f" de *{amt_fmt}*" if amt_fmt else "") + f" para tu servicio *{p.get('platform') or 'activo'}*.\n\n"
                        f"Tu suscripción quedó confirmada y al día. ¡Muchas gracias por tu pago y preferencia! 🙌✨"
                    )
                    await whatsapp_client.send_text_message(clean_phone, wa_reply, delay_seconds=1.0)
            except Exception:
                pass

        await send_telegram_message(
            f"✅ <b>PAGO #P{payment_id} APROBADO DESDE PANEL WEB</b>\n\n"
            f"• Cliente: <b>{p.get('client_name')}</b>\n"
            f"• Servicio: <b>{p.get('platform')}</b> (<code>{p.get('account_email') or '-'}</code>)\n"
            f"• Monto: <b>{amt_fmt}</b>\n"
            f"• Aprobado por: <b>{username}</b>"
        )
    return RedirectResponse(url="/#pending-payments", status_code=303)


@router.post("/api/pending-payments/reject/{payment_id}")
async def reject_pending_payment_api(payment_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")
    form = await request.form()
    reason = str(form.get("reason", "")).strip() if form else ""
    res = database.reject_pending_payment(payment_id, reason=reason, admin_user=f"Web ({username})")
    if res.get("success"):
        p = res.get("payment", {})
        phone = p.get("sender_phone") or p.get("client_whatsapp")
        if phone:
            try:
                import whatsapp_client
                clean_phone = database.clean_whatsapp_phone(phone)
                if clean_phone:
                    wa_reply = (
                        f"Hola {p.get('client_name', 'Cliente')}. Te informamos que no pudimos validar el comprobante de pago enviado (#P{payment_id}).\n\n"
                        f"Motivo: {reason or 'El monto o los datos de la transferencia no coinciden con la suscripción'}.\n"
                        f"Por favor revisa la operación o comunícate con nosotros para verificarlo."
                    )
                    await whatsapp_client.send_text_message(clean_phone, wa_reply, delay_seconds=1.0)
            except Exception:
                pass

        await send_telegram_message(
            f"❌ <b>COMPROBANTE #P{payment_id} DENEGADO DESDE PANEL WEB</b>\n\n"
            f"• Cliente: <b>{p.get('client_name')}</b>\n"
            f"• Motivo: {reason or 'Sin especificar'}\n"
            f"• Denegado por: <b>{username}</b>"
        )
    return RedirectResponse(url="/#pending-payments", status_code=303)


@router.post("/api/fallen-reports/authorize/{report_id}")
async def authorize_fallen_report_api(report_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")
    res = database.authorize_fallen_report(report_id, admin_user=f"Web ({username})")
    if res.get("success"):
        if res.get("replaced"):
            c_phone = res.get("clean_phone")
            if c_phone:
                try:
                    import whatsapp_client
                    await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)
                except Exception:
                    pass

            new_a = res.get("new_account", {})
            await send_telegram_message(
                f"✅ <b>REPORTE #C{report_id} AUTORIZADO DESDE PANEL WEB</b>\n\n"
                f"• Cliente: <b>{res.get('client_name')}</b>\n"
                f"• Servicio: <b>{res.get('platform')}</b>\n"
                f"• Nueva Cuenta: <code>{new_a.get('email')}</code>\n"
                f"• Clave: <code>{new_a.get('password')}</code>\n"
                f"• Autorizado por: <b>{username}</b>"
            )
            return RedirectResponse(url="/?msg=fallen_authorized#fallen-reports", status_code=303)
        elif res.get("out_of_stock"):
            return RedirectResponse(url="/?msg=fallen_out_of_stock#fallen-reports", status_code=303)
        elif res.get("already_resolved"):
            return RedirectResponse(url="/?msg=fallen_already_resolved#fallen-reports", status_code=303)
    return RedirectResponse(url="/?err=Error+al+autorizar+reporte#fallen-reports", status_code=303)


@router.post("/api/fallen-reports/wait/{report_id}")
async def wait_fallen_report_api(report_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")
    res = database.put_fallen_report_on_wait(report_id, admin_user=f"Web ({username})")
    if res.get("success"):
        c_phone = res.get("clean_phone")
        if c_phone:
            try:
                import whatsapp_client
                await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)
            except Exception:
                pass

        await send_telegram_message(
            f"⏳ <b>CLIENTE PUESTO EN ESPERA (#C{report_id}) DESDE PANEL WEB</b>\n\n"
            f"• Cliente: <b>{res.get('client_name')}</b>\n"
            f"• Puesto en espera por: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=fallen_wait#fallen-reports", status_code=303)
    return RedirectResponse(url="/?err=Error+al+poner+en+espera#fallen-reports", status_code=303)


@router.post("/api/fallen-reports/dismiss/{report_id}")
async def dismiss_fallen_report_api(report_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")
    database.dismiss_fallen_report(report_id, reason="Descartado desde panel web", admin_user=f"Web ({username})")
    return RedirectResponse(url="/?msg=fallen_dismissed#fallen-reports", status_code=303)


@router.post("/api/fallen-reports/rollback/{report_id}")
async def rollback_fallen_report_api(report_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")

    res = database.rollback_fallen_report_replacement(report_id)
    if res.get("success"):
        await send_telegram_message(
            f"🔄 <b>REEMPLAZO #C{report_id} DESHECHO (PANEL WEB)</b>\n\n"
            f"• Cuenta anterior reactivada: #{res.get('old_account_id')}\n"
            f"• Cuenta asignada devuelta a stock: #{res.get('reassigned_account_id')}\n"
            f"• Operador: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=report_rolled_back#fallen-reports", status_code=303)
    else:
        return RedirectResponse(url=f"/?err={urllib.parse.quote(res.get('error', 'Error'))}#fallen-reports", status_code=303)


@router.post("/api/accounts/rotate-password")
async def rotate_password_api(
    request: Request,
    email_or_account_id: str = Form(...),
    new_password: str = Form(...),
    unpaid_account_id: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")

    unpaid_id = int(unpaid_account_id.strip()) if (unpaid_account_id and unpaid_account_id.strip().isdigit()) else None
    res = database.rotate_master_password_and_broadcast(
        email_or_account_id=email_or_account_id.strip(),
        new_password=new_password.strip(),
        unpaid_account_id=unpaid_id
    )

    if res.get("success"):
        active_co_users = res.get("active_co_users", [])
        notified_count = 0
        for co in active_co_users:
            if co.get("clean_phone") and co.get("whatsapp_message"):
                try:
                    import whatsapp_client
                    await whatsapp_client.send_text_message(co["clean_phone"], co["whatsapp_message"], delay_seconds=1.0)
                    notified_count += 1
                except Exception as e:
                    logger.error(f"Error notificando nueva contraseña a {co.get('client_name')}: {e}")

        await send_telegram_message(
            f"🔐 <b>ROTACIÓN DE CONTRASEÑA EJECUTADA (PANEL WEB)</b>\n\n"
            f"• Cuenta: <code>{res.get('email')}</code> ({res.get('platform')})\n"
            f"• Nueva clave: <code>{res.get('new_password')}</code>\n"
            f"• Perfiles actualizados: <b>{res.get('total_profiles')}</b>\n"
            + (f"• Perfil impago liberado: #{unpaid_id}\n" if unpaid_id else "")
            + f"• Co-usuarios notificados por WhatsApp: <b>{notified_count}</b>\n"
            f"• Operador: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=password_rotated#accounts", status_code=303)
    else:
        return RedirectResponse(url=f"/?err={urllib.parse.quote(res.get('error', 'Error'))}#accounts", status_code=303)


@router.post("/api/accounts/partial-payment")
async def partial_payment_api(
    request: Request,
    account_id: str = Form(...),
    amount: float = Form(...),
    payment_method: Optional[str] = Form("Transferencia"),
    notes: Optional[str] = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")

    res = database.register_partial_payment(
        email_or_id=account_id.strip(),
        amount=float(amount),
        payment_method=payment_method or "Transferencia",
        notes=f"{notes.strip()} (Registrado por {username})".strip()
    )
    if res.get("success"):
        c_phone = res.get("client_whatsapp")
        c_name = res.get("client_name") or "Cliente"
        paid_fmt = database.format_ars(amount)
        rem_fmt = database.format_ars(res.get("remaining_debt", 0.0))

        if c_phone:
            try:
                import whatsapp_client
                c_clean = database.clean_whatsapp_phone(c_phone)
                if c_clean:
                    c_msg = (
                        f"¡Hola {c_name}! 🙌 Registramos tu pago parcial de *{paid_fmt}* para tu suscripción de *{res.get('platform')}*.\n\n"
                        f"📌 Tu saldo pendiente restante es de: *{rem_fmt}*.\n"
                        f"¡Muchas gracias! Cuando completes el saldo total se extenderá tu ciclo completo. ✨"
                    )
                    await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)
            except Exception:
                pass

        await send_telegram_message(
            f"💵 <b>PAGO PARCIAL REGISTRADO (PANEL WEB)</b>\n\n"
            f"• Cliente: <b>{c_name}</b>\n"
            f"• Servicio: <b>{res.get('platform')}</b>\n"
            f"• Monto abonado: <b>{paid_fmt}</b>\n"
            f"• Saldo restante: <b>{rem_fmt}</b>\n"
            f"• Registrado por: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=partial_payment_saved#accounts", status_code=303)
    else:
        return RedirectResponse(url=f"/?err={urllib.parse.quote(res.get('error', 'Error'))}#accounts", status_code=303)


@router.post("/api/accounts/mark-baja/{account_id}")
async def mark_baja_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")

    res = database.mark_account_for_password_change(str(account_id))
    if res.get("success"):
        await send_telegram_message(
            f"🛑 <b>CUENTA #{account_id} MARCADA PARA BAJA (PANEL WEB)</b>\n\n"
            f"• Servicio: <b>{res.get('platform')}</b> (<code>{res.get('email')}</code>)\n"
            f"• Estado: <code>por_cambiar_clave</code> (alertas automáticas detenidas)\n"
            f"• Operador: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=marked_for_baja#accounts", status_code=303)
    else:
        return RedirectResponse(url=f"/?err={urllib.parse.quote(res.get('error', 'Error'))}#accounts", status_code=303)


@router.post("/api/payments/reverse/{payment_id}")
async def reverse_payment_api(payment_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    username = user.get("username", "admin")

    res = database.reverse_customer_payment(payment_id, reason=f"Revertido desde Panel Web ({username})")
    if res.get("success"):
        rev_amt = database.format_ars(res.get("reversed_amount", 0.0))
        await send_telegram_message(
            f"🔄 <b>COBRO #{payment_id} REVERTIDO (PANEL WEB)</b>\n\n"
            f"• Monto Anulado: <b>{rev_amt}</b>\n"
            f"• Cuenta #{res.get('account_id')} restaurada al vencimiento previo: <code>{res.get('restored_expiry') or 'original'}</code>\n"
            f"• Descontado del balance financiero.\n"
            f"• Operador: <b>{username}</b>"
        )
        return RedirectResponse(url="/?msg=payment_reversed#finance", status_code=303)
    else:
        return RedirectResponse(url=f"/?err={urllib.parse.quote(res.get('error', 'Error'))}#finance", status_code=303)


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