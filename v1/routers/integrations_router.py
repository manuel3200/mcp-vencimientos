import re
import time
import urllib.parse
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
import whatsapp_client
from telegram_bot import send_telegram_message
from core.security import verify_session_cookie
from core.utils import format_ars
from services.chatwoot_bot_service import process_chatwoot_command

logger = logging.getLogger("integrations")

router = APIRouter()

# Cooldown en memoria para evitar repeticiones o bucles de auto-respuesta hacia el mismo número (3 minutos)
_AUTO_REPLY_COOLDOWNS: Dict[str, float] = {}
COOLDOWN_SECONDS = 180.0

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
    return RedirectResponse(url="/?msg=wa_settings_saved#integrations", status_code=302)

@router.post("/api/whatsapp/setup-webhook")
async def api_whatsapp_setup_webhook(request: Request, webhook_url: str = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    target_url = webhook_url.strip() or "https://mcp.juanconnect.online/api/webhook/whatsapp"
    res = await whatsapp_client.configure_webhook(target_url)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_webhook_configured#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error configurando webhook"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)

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
        return RedirectResponse(url="/?msg=wa_chatwoot_configured#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error vinculando Chatwoot con Evolution API"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)

@router.post("/api/chatwoot/sync")
async def api_chatwoot_sync(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.sync_chatwoot_contacts_to_crm()
    if res.get("success"):
        imp = res.get("imported", 0)
        upd = res.get("updated", 0)
        return RedirectResponse(url=f"/?msg=chatwoot_synced&imported={imp}&updated={upd}#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error sincronizando contactos de Chatwoot"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)

@router.post("/api/whatsapp/test")
async def api_whatsapp_test(request: Request, test_phone: str = Form(...), test_message: str = Form(...)):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.send_text_message(test_phone, test_message, delay_seconds=1.0)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_test_sent#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Fallo al enviar mensaje de prueba"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)

@router.post("/api/whatsapp/logout")
async def api_whatsapp_logout(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    await whatsapp_client.logout_instance()
    return RedirectResponse(url="/?msg=wa_logged_out#integrations", status_code=302)


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

    # 4. Extraer contexto de reenvío (Forwarded)
    context_info = (
        msg_obj.get("extendedTextMessage", {}).get("contextInfo") or
        msg_obj.get("imageMessage", {}).get("contextInfo") or
        msg_obj.get("documentMessage", {}).get("contextInfo") or
        msg_obj.get("contextInfo") or
        data.get("contextInfo") or
        {}
    )
    is_forwarded = bool(context_info.get("isForwarded") or (context_info.get("forwardingScore", 0) > 0))

    # 5. Verificar si la auto-respuesta está habilitada
    settings = database.get_whatsapp_api_settings()
    if not settings.get("auto_reply_enabled"):
        return JSONResponse({"status": "disabled"})

    push_name = data.get("pushName") or body.get("pushName") or "Cliente"
    client_profile = database.get_client_by_phone(sender_phone)
    client_name = client_profile["client"]["name"] if client_profile else push_name

    text_lower = text.lower()

    # 6. FILTRO ANTI-BUCLE / ECO DE PLANTILLA DEL SISTEMA:
    # Si el mensaje recibido contiene nuestras propias plantillas de entrega o estado (ej. el cliente lo reenvió sin querer),
    # NUNCA debemos volver a responderle con sus datos.
    is_template_echo = any(marker in text_lower for marker in [
        "datos de acceso a tu suscripción",
        "datos de acceso a tu suscripcion",
        "estado de tus servicios activos",
        "reglas de uso importantes",
        "no cambiar correo ni contraseña",
        "no cambiar correo ni contrasena",
        "utilizar únicamente el perfil asignado",
        "utilizar unicamente el perfil asignado",
        "usuario/correo:",
        "usuario / correo:",
        "datos de cobro oficiales",
        "recibimos tu comprobante correctamente",
    ])
    if is_template_echo:
        logger.info(f"Mensaje ignorado de {client_name} ({sender_phone}): es un reenvío o eco de plantilla del sistema.")
        return JSONResponse({"status": "ignored", "reason": "template_echo"})

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

    # 7. FILTRO DE MENSAJES REENVIADOS PARA CONSULTAS DE DATOS:
    # Si un cliente reenvía un mensaje de texto (sin ser comprobante), no debe disparar auto-entrega de credenciales
    if is_forwarded:
        logger.info(f"Mensaje reenviado de {client_name} ({sender_phone}): omitiendo auto-respuesta de credenciales para evitar spam.")
        return JSONResponse({"status": "ignored", "reason": "forwarded_message"})

    # 8. COOLDOWN ANTI-SPAM (Mínimo 3 minutos entre auto-respuestas automáticas al mismo cliente)
    now = time.time()
    last_reply_time = _AUTO_REPLY_COOLDOWNS.get(sender_phone, 0.0)
    if now - last_reply_time < COOLDOWN_SECONDS:
        logger.info(f"Auto-respuesta para {client_name} ({sender_phone}) omitida por cooldown ({int(now - last_reply_time)}s < {int(COOLDOWN_SECONDS)}s).")
        return JSONResponse({"status": "ignored", "reason": "cooldown"})

    # REGLA B: Consultas de Vencimiento o Credenciales con intención clara (No palabras sueltas como 'cuenta')
    expiry_intents = [
        # Preguntas directas de vencimiento
        "cuando vence", "cuándo vence", "que dia vence", "qué día vence",
        "que fecha vence", "qué fecha vence", "fecha de vencimiento",
        "cuando se me vence", "cuándo se me vence", "cuantos dias me quedan",
        "cuántos días me quedan", "hasta cuando tengo", "hasta cuándo tengo",
        # Pedidos directos de credenciales
        "pasame la clave", "pásame la clave", "cual es la clave", "cuál es la clave",
        "cual es mi clave", "cuál es mi clave", "cual es mi contrasena", "cuál es mi contraseña",
        "pasame la contrasena", "pásame la contraseña", "me pasas la clave", "me pasas la contraseña",
        "no me acuerdo la clave", "no recuerdo la clave", "olvide la clave", "olvidé la clave",
        "olvide mi clave", "olvidé mi clave", "olvide la contrasena", "olvidé la contraseña",
        "datos de mi cuenta", "datos de la cuenta", "mis accesos", "mis credenciales",
        # Comandos cortos
        "/vencimiento", "/clave", "/cuenta", "mi cuenta", "mis cuentas", "mi clave"
    ]
    if any(k in text_lower for k in expiry_intents):
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
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "expiry_info_sent"})
        else:
            reply = (
                f"¡Hola {client_name}! En este momento no registramos suscripciones activas a tu nombre en el sistema. "
                f"Si deseas contratar Netflix, Disney+, Max u otra plataforma, avísanos y te enviamos los planes disponibles."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "no_active_services"})

    # REGLA C: Consulta de Medios de Pago / CBU / Alias
    payment_intents = [
        "alias", "cbu", "cvu", "como pago", "cómo pago", "donde pago", "dónde pago",
        "donde transfiero", "dónde transfiero", "datos de pago", "medios de pago",
        "datos para transferir", "datos bancarios", "a que cuenta transfiero",
        "a qué cuenta transfiero", "como te transfiero", "cómo te transfiero",
        "pasame el alias", "pásame el alias", "pasame el cbu", "pásame el cbu",
        "pasa el alias", "pasa el cbu"
    ]
    if any(k in text_lower for k in payment_intents):
        pm = database.get_formatted_payment_methods()
        reply = (
            f"¡Hola {client_name}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
            f"{pm}\n\n"
            f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu renovación. ¡Muchas gracias! 🙌"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        _AUTO_REPLY_COOLDOWNS[sender_phone] = now
        return JSONResponse({"status": "ok", "action": "payment_info_sent"})

    return JSONResponse({"status": "ok", "action": "none"})


# ==========================================
# CHATWOOT CRM SLASH COMMANDS & WEBHOOKS
# ==========================================
@router.post("/api/settings/chatwoot")
async def api_settings_chatwoot(
    request: Request,
    url: str = Form("http://chatwoot-rails:3000"),
    token: str = Form(""),
    account_id: str = Form("1"),
    enabled: Optional[str] = Form(None),
    auto_sync: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_chatwoot_settings(
        url=url.strip(),
        token=token.strip(),
        account_id=account_id.strip() or "1",
        enabled=1 if enabled in ("1", "on", "true") else (1 if enabled is None else 0),
        auto_sync=1 if auto_sync in ("1", "on", "true") else (1 if auto_sync is None else 0)
    )
    return RedirectResponse(url="/?msg=chatwoot_settings_saved#integrations", status_code=302)


@router.post("/api/chatwoot/setup-webhook")
async def api_chatwoot_setup_webhook(
    request: Request,
    webhook_url: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.setup_chatwoot_webhook(webhook_url=webhook_url or "")
    if res.get("success"):
        return RedirectResponse(url="/?msg=chatwoot_webhook_configured#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error configurando webhook en Chatwoot"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)


@router.post("/api/chatwoot/setup-canned-responses")
async def api_chatwoot_setup_canned_responses(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.setup_chatwoot_canned_responses()
    if res.get("success"):
        created = res.get("created", 0)
        existing = res.get("existing", 0)
        return RedirectResponse(url=f"/?msg=chatwoot_canned_synced&created={created}&existing={existing}#integrations", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error sincronizando atajos en Chatwoot"))
        return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)


@router.post("/api/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Webhook receptor de eventos de Chatwoot (message_created).
    Permite a los agentes ejecutar comandos en el chat como /nc_n_casaextra, /nc_n_full, /stock, /cbu, etc.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.warning(f"Webhook Chatwoot con payload inválido: {e}")
        return JSONResponse({"status": "ignored", "reason": "invalid_json"})

    try:
        result = await process_chatwoot_command(body)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error procesando comando de Chatwoot: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


