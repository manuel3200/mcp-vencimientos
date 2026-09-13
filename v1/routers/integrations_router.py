import re
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
import whatsapp_client
from core.security import verify_session_cookie
from core.utils import format_ars

logger = logging.getLogger("integrations")

router = APIRouter()

# ==========================================
@router.get("/api/whatsapp/status")
async def api_whatsapp_status(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    st = await whatsapp_client.check_connection_status()
    cfg = whatsapp_client.get_evolution_config()
    return {"status": st, "config": cfg}

@router.get("/api/whatsapp/qr")
async def api_whatsapp_qr(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    qr_data = await whatsapp_client.get_qr_code()
    return qr_data

@router.post("/api/whatsapp/settings")
async def api_whatsapp_settings(
    request: Request,
    api_url: str = Form("http://evolution-api:8080"),
    api_key: str = Form("mcp-evolution-key-2026"),
    instance_name: str = Form("streaming-bot"),
    auto_send_expiry: Optional[str] = Form(None),
    auto_send_sales: Optional[str] = Form(None),
    auto_reply_enabled: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_whatsapp_api_settings(
        api_url=api_url.strip(),
        api_key=api_key.strip(),
        instance_name=instance_name.strip(),
        auto_send_expiry=1 if auto_send_expiry in ("1", "on", "true") else 0,
        auto_send_sales=1 if auto_send_sales in ("1", "on", "true") else 0,
        auto_reply_enabled=1 if auto_reply_enabled in ("1", "on", "true") else 0
    )
    return RedirectResponse(url="/?msg=wa_settings_saved#tab-templates", status_code=302)

@router.post("/api/whatsapp/setup-webhook")
async def api_whatsapp_setup_webhook(request: Request, webhook_url: str = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    target_url = webhook_url.strip() or "https://mcp.juanconnect.online/api/webhook/whatsapp"
    res = await whatsapp_client.configure_webhook(target_url)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_webhook_configured#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error configurando webhook"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@router.post("/api/whatsapp/setup-chatwoot")
async def api_whatsapp_setup_chatwoot(
    request: Request,
    chatwoot_url: str = Form("http://chatwoot-rails:3000"),
    chatwoot_token: str = Form(...),
    account_id: str = Form("1"),
    sign_msg: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.configure_chatwoot(
        chatwoot_url=chatwoot_url.strip(),
        chatwoot_token=chatwoot_token.strip(),
        account_id=account_id.strip() or "1",
        sign_msg=True if sign_msg in ("1", "on", "true") else False
    )
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_chatwoot_configured#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error vinculando Chatwoot con Evolution API"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@router.post("/api/chatwoot/sync")
async def api_chatwoot_sync(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.sync_chatwoot_contacts_to_crm()
    if res.get("success"):
        imp = res.get("imported", 0)
        upd = res.get("updated", 0)
        return RedirectResponse(url=f"/?msg=chatwoot_synced&imported={imp}&updated={upd}#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error sincronizando contactos de Chatwoot"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@router.post("/api/whatsapp/test")
async def api_whatsapp_test(request: Request, test_phone: str = Form(...), test_message: str = Form(...)):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.send_text_message(test_phone, test_message, delay_seconds=1.0)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_test_sent#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Fallo al enviar mensaje de prueba"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@router.post("/api/whatsapp/logout")
async def api_whatsapp_logout(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    await whatsapp_client.logout_instance()
    return RedirectResponse(url="/?msg=wa_logged_out#tab-templates", status_code=302)


@router.post("/api/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Webhook receptor de eventos de Evolution API v2 (Baileys).
    Procesa mensajes entrantes de clientes, auto-responde consultas de vencimientos/claves/CBU y
    alerta a Telegram ante el envío de comprobantes de pago.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored", "reason": "invalid_json"})

    event = body.get("event") or body.get("type", "")
    data = body.get("data", {}) or {}

    key = data.get("key", {}) or body.get("key", {})
    from_me = key.get("fromMe", False)
    remote_jid = key.get("remoteJid", "")

    # 1. Ignorar mensajes salientes propios o grupos para evitar bucles
    if from_me or not remote_jid or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
        return JSONResponse({"status": "ignored"})

    # 2. Extraer número de teléfono limpio
    phone_raw = remote_jid.split("@")[0]
    sender_phone = re.sub(r'[^0-9]', '', phone_raw)
    if not sender_phone or len(sender_phone) < 8:
        return JSONResponse({"status": "ignored", "reason": "invalid_phone"})

    # 3. Extraer contenido de texto o caption de imagen/documento
    msg_obj = data.get("message", {}) or body.get("message", {}) or {}
    text = (
        msg_obj.get("conversation") or
        msg_obj.get("extendedTextMessage", {}).get("text") or
        msg_obj.get("imageMessage", {}).get("caption") or
        msg_obj.get("documentMessage", {}).get("caption") or
        ""
    ).strip()
    is_media = bool(msg_obj.get("imageMessage") or msg_obj.get("documentMessage"))

    # 4. Verificar si la auto-respuesta está habilitada
    settings = database.get_whatsapp_api_settings()
    if not settings.get("auto_reply_enabled"):
        return JSONResponse({"status": "disabled"})

    push_name = data.get("pushName") or body.get("pushName") or "Cliente"
    client_profile = database.get_client_by_phone(sender_phone)
    client_name = client_profile["client"]["name"] if client_profile else push_name

    text_lower = text.lower()

    # REGLA A: Detección de comprobantes de pago (Imágenes/Docs o palabras clave de pago)
    receipt_keywords = ["comprobante", "pague", "pagué", "transferi", "transferí", "adjunto", "constancia", "abone", "aboné"]
    is_receipt = is_media or any(k in text_lower for k in receipt_keywords)

    if is_receipt:
        logger.info(f"Comprobante recibido de {client_name} ({sender_phone})")
        caption_txt = f"<i>\"{text}\"</i>" if text else "(Archivo multimedia adjunto)"
        await send_telegram_message(
            f"🧾 <b>¡COMPROBANTE RECIBIDO POR WHATSAPP!</b>\n\n"
            f"• Cliente: <b>{client_name}</b>\n"
            f"• WhatsApp: <code>{sender_phone}</code>\n"
            f"• Mensaje: {caption_txt}\n\n"
            f"👉 Por favor verifica el ingreso en tu cuenta bancaria y confirma el cobro en el panel."
        )

        reply = (
            f"¡Hola {client_name}! 🙌 Recibimos tu comprobante correctamente.\n\n"
            f"Nuestro equipo lo verificará en el sistema a la brevedad y extenderá tu servicio. ¡Muchas gracias por tu pago! ✨"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        return JSONResponse({"status": "ok", "action": "receipt_acknowledged"})

    # REGLA B: Consultas de Vencimiento o Credenciales ("vence", "vencimiento", "clave", "pin", "acceso", "contraseña")
    expiry_keywords = ["vence", "vencimiento", "cuando vence", "cuándo vence", "clave", "contraseña", "contrasena", "pin", "acceso", "accesos", "cuenta"]
    if any(k in text_lower for k in expiry_keywords):
        if client_profile and client_profile.get("active_accounts"):
            accs = client_profile["active_accounts"]
            lines = [f"¡Hola {client_name}! 🍿 Aquí tienes el estado de tus servicios activos:\n"]
            for a in accs:
                perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
                pin = f" | PIN: {a['profile_pin']}" if a.get("profile_pin") else ""
                lines.append(
                    f"📺 *{a['platform']}*{perf}\n"
                    f"📧 Usuario: `{a['email']}`\n"
                    f"🔑 Clave: `{a['password']}`{pin}\n"
                    f"📅 Vence: *{a.get('expiry_date')}* ({a.get('days_label')})\n"
                )
            lines.append("¡Cualquier consulta o renovación estamos a tu disposición!")
            reply = "\n".join(lines)
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            return JSONResponse({"status": "ok", "action": "expiry_info_sent"})
        else:
            reply = (
                f"¡Hola {client_name}! En este momento no registramos suscripciones activas a tu nombre en el sistema. "
                f"Si deseas contratar Netflix, Disney+, Max u otra plataforma, avísanos y te enviamos los planes disponibles."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            return JSONResponse({"status": "ok", "action": "no_active_services"})

    # REGLA C: Consulta de Medios de Pago / CBU / Alias
    payment_keywords = ["alias", "cbu", "cvu", "como pago", "cómo pago", "datos de pago", "medios de pago", "transferir", "donde transfiero", "dónde transfiero", "pagar", "cuenta bancaria"]
    if any(k in text_lower for k in payment_keywords):
        pm = database.get_formatted_payment_methods()
        reply = (
            f"¡Hola {client_name}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
            f"{pm}\n\n"
            f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu renovación. ¡Muchas gracias! 🙌"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        return JSONResponse({"status": "ok", "action": "payment_info_sent"})

    return JSONResponse({"status": "ok", "action": "none"})
