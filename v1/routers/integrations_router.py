import os
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
import services.receipt_service as receipt_service
import base64

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
@router.post("/api/settings/whatsapp-api")
async def api_whatsapp_settings(
    request: Request,
    api_url: str = Form("http://evolution-api:8080"),
    api_key: str = Form("mcp-evolution-key-2026"),
    instance_name: str = Form("streaming-bot"),
    admin_whatsapp: Optional[str] = Form(""),
    gemini_api_key: Optional[str] = Form(""),
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
        auto_reply_enabled=1 if auto_reply_enabled in ("1", "on", "true") else 0,
        admin_whatsapp=admin_whatsapp.strip() if admin_whatsapp else "",
        gemini_api_key=gemini_api_key.strip() if gemini_api_key else ""
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

@router.api_route("/api/chatwoot/sync-names", methods=["GET", "POST"])
async def api_chatwoot_sync_names(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    try:
        res = await whatsapp_client.sync_whatsapp_names_to_chatwoot()
        if res.get("success"):
            upd = res.get("total_updated", 0)
            chk = res.get("total_contacts_checked", 0)
            return RedirectResponse(
                url=f"/?msg=chatwoot_names_synced&updated={upd}&checked={chk}#integrations",
                status_code=302
            )
        else:
            err = urllib.parse.quote(res.get("error", "Error sincronizando nombres de WhatsApp a Chatwoot"))
            return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)
    except Exception as e:
        logger.error(f"Error en api_chatwoot_sync_names: {e}", exc_info=True)
        err = urllib.parse.quote(f"Error interno: {str(e)}")
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

    # 1. Extraer contenido de texto o caption primero para verificar si es un comando administrativo
    msg_obj = data.get("message", {}) or body.get("message", {}) or {}
    text = (
        msg_obj.get("conversation") or
        msg_obj.get("extendedTextMessage", {}).get("text") or
        msg_obj.get("imageMessage", {}).get("caption") or
        msg_obj.get("documentMessage", {}).get("caption") or
        ""
    ).strip()
    is_media = bool(msg_obj.get("imageMessage") or msg_obj.get("documentMessage"))
    text_lower = text.lower().strip()

    # Ignorar mensajes de grupos o transmisiones/estados
    if not remote_jid or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
        return JSONResponse({"status": "ignored"})

    # Extraer número de teléfono limpio
    phone_raw = remote_jid.split("@")[0].split(":")[0]
    sender_phone = re.sub(r'[^0-9]', '', phone_raw)
    if not sender_phone or len(sender_phone) < 8:
        return JSONResponse({"status": "ignored", "reason": "invalid_phone"})

    # Verificar si es un comando administrativo o comando de caída
    is_admin_cmd = bool(re.search(r'^/(?:pagoapro|aprobarpago|pagodene|rechazarpago|caida|reemplazo|reemplazar)', text_lower))

    # Si es from_me (mensaje saliente propio) y NO es un comando administrativo, ignorar para evitar bucles
    if from_me and not is_admin_cmd:
        return JSONResponse({"status": "ignored", "reason": "outgoing_non_command"})

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

    # 5.1 COMANDOS DE ADMINISTRADOR POR WHATSAPP (/pagoapro_<ID>, /pagodene_<ID>, /caida, /reemplazo)
    admin_approval_match = re.search(r'^/(?:pagoapro|aprobarpago)[_\s]+(\d+)(?:\s+(\d+(?:[.,]\d+)?))?', text_lower)
    admin_reject_match = re.search(r'^/(?:pagodene|rechazarpago)[_\s]+(\d+)', text_lower)
    admin_fallen_match = re.search(r'^/(?:caida|reemplazo|reemplazar)(?:[_\s]+(.+))?', text_lower)

    if admin_approval_match or admin_reject_match or (admin_fallen_match and (from_me or (settings.get("admin_whatsapp") and sender_phone.endswith(database.clean_whatsapp_phone(settings.get("admin_whatsapp"))[-8:])))):
        admin_configured = (settings.get("admin_whatsapp") or os.getenv("ADMIN_WHATSAPP", "")).strip()
        clean_admin = database.clean_whatsapp_phone(admin_configured) if admin_configured else ""

        is_auth = False
        if from_me:
            # Mensaje emitido desde la propia sesión de WhatsApp vinculada
            is_auth = True
        elif clean_admin:
            if sender_phone == clean_admin or sender_phone.endswith(clean_admin[-8:]) or clean_admin.endswith(sender_phone[-8:]):
                is_auth = True
        else:
            is_auth = True

        if not is_auth:
            logger.warning(f"Intento de comando administrativo no autorizado desde {sender_phone}")
            return JSONResponse({"status": "ignored", "reason": "unauthorized_admin_command"})

        if admin_approval_match:
            pid = int(admin_approval_match.group(1))
            custom_amt_str = admin_approval_match.group(2)
            custom_amt = float(custom_amt_str.replace(",", ".")) if custom_amt_str else None

            res = database.approve_pending_payment(
                pid,
                admin_user=f"WhatsApp Admin (+{sender_phone})",
                custom_amount=custom_amt
            )
            if res.get("success"):
                p = res.get("payment", {})
                amt_fmt = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)
                admin_ack = (
                    f"✅ *PAGO #P{pid} APROBADO EXITOSAMENTE*\n\n"
                    f"• Cliente: *{p.get('client_name')}*\n"
                    f"• Servicio: *{p.get('platform') or 'Streaming'}*\n"
                    f"• Monto Acreditado: *{amt_fmt}*\n"
                    f"• Estado: Renovado/activado y asentado en Finanzas & MCP."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                c_phone = p.get("sender_phone") or p.get("client_whatsapp")
                if c_phone and c_phone != sender_phone:
                    c_clean = database.clean_whatsapp_phone(c_phone)
                    if c_clean:
                        c_msg = (
                            f"🎉 ¡Hola {p.get('client_name', 'Cliente')}! Confirmamos la recepción y acreditación de tu pago"
                            + (f" de *{amt_fmt}*" if amt_fmt else "") + f" para tu servicio *{p.get('platform') or 'activo'}*.\n\n"
                            f"Tu suscripción quedó confirmada y al día. ¡Muchas gracias por tu pago y preferencia! 🙌✨"
                        )
                        await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)

                await send_telegram_message(
                    f"✅ <b>PAGO #P{pid} APROBADO DESDE WHATSAPP ADMIN</b>\n\n"
                    f"• Cliente: <b>{p.get('client_name')}</b>\n"
                    f"• Servicio: <b>{p.get('platform')}</b>\n"
                    f"• Monto: <b>{amt_fmt}</b>\n"
                    f"• Comando ejecutado por el administrador desde WhatsApp."
                )
                return JSONResponse({"status": "ok", "action": "payment_approved", "payment_id": pid})
            else:
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ Error al procesar pago #P{pid}: {res.get('error')}"
                )
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_reject_match:
            pid = int(admin_reject_match.group(1))
            res = database.reject_pending_payment(pid, reason="Denegado por el administrador vía WhatsApp", admin_user=f"WhatsApp Admin (+{sender_phone})")
            if res.get("success"):
                p = res.get("payment", {})
                admin_ack = f"❌ *PAGO #P{pid} DENEGADO / RECHAZADO*\n• Cliente: {p.get('client_name')}\n• Se marcó como rechazado en el sistema."
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                c_phone = p.get("sender_phone") or p.get("client_whatsapp")
                if c_phone and c_phone != sender_phone:
                    c_clean = database.clean_whatsapp_phone(c_phone)
                    if c_clean:
                        c_msg = (
                            f"Hola {p.get('client_name', 'Cliente')}. Te informamos que no pudimos validar el comprobante de pago enviado (#P{pid}).\n\n"
                            f"Por favor revisa que el importe y los datos de destino correspondan a nuestros datos oficiales, o comunícate con nosotros para verificarlo."
                        )
                        await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)

                await send_telegram_message(
                    f"❌ <b>COMPROBANTE #P{pid} DENEGADO DESDE WHATSAPP ADMIN</b>\n\n"
                    f"• Cliente: <b>{p.get('client_name')}</b>\n"
                    f"• Estado: Rechazado"
                )
                return JSONResponse({"status": "ok", "action": "payment_rejected", "payment_id": pid})
            else:
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ Error al denegar el pago #P{pid}: {res.get('error')}"
                )
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_fallen_match:
            target_q = (admin_fallen_match.group(1) or "").strip()
            # Si el admin no especificó argumento
            if not target_q and (sender_phone == clean_admin or from_me):
                fallen_list = database.get_fallen_accounts()
                lines = ["🚨 *GESTIÓN INSTANTÁNEA DE CUENTAS CAÍDAS (/caida)*\n"]
                if fallen_list:
                    lines.append(f"Hay *{len(fallen_list)}* cuenta(s) caídas pendientes de reemplazo:")
                    for f_item in fallen_list[:5]:
                        lines.append(f"• ID #{f_item['id']} ({f_item['platform']}): `{f_item['email']}` - Cliente: *{f_item.get('client_name') or 'Sin cliente'}*")
                    lines.append("\nPara reemplazar una cuenta por una libre al instante, escribe:")
                    lines.append("👉 `/caida <ID>` (ej: `/caida 15`)")
                    lines.append("👉 `/caida <correo>`")
                else:
                    lines.append("No hay cuentas caídas pendientes en este momento.")
                    lines.append("\nPuedes forzar el reemplazo de cualquier cuenta o cliente escribiendo:")
                    lines.append("👉 `/caida <ID>`")
                    lines.append("👉 `/caida <correo>`")
                    lines.append("👉 `/caida <nombre_cliente>`")
                    lines.append("👉 `/caida <teléfono>`")
                lines.append("\n💡 El sistema buscará stock libre de la plataforma, reasignará la cuenta y enviará los nuevos datos al cliente.")
                await whatsapp_client.send_text_message(sender_phone, "\n".join(lines))
                return JSONResponse({"status": "ok", "action": "fallen_help_sent"})

            target_search = target_q if target_q else sender_phone
            res = database.report_and_auto_replace_account(target_search, reason=f"Comando WhatsApp Admin (+{sender_phone})")
            if res.get("replaced"):
                new_a = res["new_account"]
                old_a = res["old_account"]
                c_phone = res.get("clean_phone")

                # Enviar automáticamente nuevas credenciales al cliente
                if c_phone:
                    await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)

                admin_ack = (
                    f"✅ *CUENTA REEMPLAZADA EN 1 CLIC EXITOSAMENTE*\n\n"
                    f"• Cliente: *{res.get('client_name')}*\n"
                    f"• Plataforma: *{res.get('platform')}*\n"
                    f"• Cuenta anterior (caída): `{old_a.get('email')}`\n"
                    f"• Nueva cuenta: `{new_a.get('email')}`\n"
                    f"• Clave: `{new_a.get('password')}`" + (f"\n• Perfil: {new_a.get('profile_name')}" if new_a.get('profile_name') else "") + (f" [PIN: {new_a.get('profile_pin')}]" if new_a.get('profile_pin') else "") + "\n"
                    f"• Vencimiento conservado: `{new_a.get('expiry_date')}`\n\n"
                    f"📲 Los nuevos datos de acceso ya fueron enviados por WhatsApp al cliente."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                await send_telegram_message(
                    f"🔄 <b>REEMPLAZO EN 1 CLIC EJECUTADO (WHATSAPP ADMIN)</b>\n\n"
                    f"• Cliente: <b>{res.get('client_name')}</b>\n"
                    f"• Plataforma: <b>{res.get('platform')}</b>\n"
                    f"• Nueva Cuenta: <code>{new_a.get('email')}</code>\n"
                    f"• Clave: <code>{new_a.get('password')}</code>\n"
                    f"• Ejecutado por administrador vía WhatsApp."
                )
                return JSONResponse({"status": "ok", "action": "account_replaced", "details": res})
            elif res.get("out_of_stock"):
                old_a = res["old_account"]
                admin_ack = (
                    f"⚠️ *ATENCIÓN: SIN STOCK LIBRE PARA REEMPLAZO*\n\n"
                    f"• Cliente: *{res.get('client_name')}*\n"
                    f"• Plataforma: *{res.get('platform')}*\n"
                    f"• Cuenta: `{old_a.get('email')}`\n\n"
                    f"La cuenta fue marcada como *CAÍDA*. No se encontró stock libre en el inventario para asignarle un reemplazo automático.\n"
                    f"Por favor ingresa al panel web para agregar cuentas libres de esta plataforma."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)
                await send_telegram_message(
                    f"🚨 <b>ALERTA: CAÍDA SIN STOCK LIBRE</b>\n\n"
                    f"• Cliente: <b>{res.get('client_name')}</b>\n"
                    f"• Plataforma: <b>{res.get('platform')}</b>\n"
                    f"• Cuenta: <code>{old_a.get('email')}</code>\n"
                    f"• Se requiere recarga urgente de stock libre."
                )
                return JSONResponse({"status": "ok", "action": "out_of_stock", "details": res})
            else:
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ No se encontró ninguna cuenta activa o caída para '{target_search}'."
                )
                return JSONResponse({"status": "error", "error": res.get("error")})

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

    # REGLA A: Detección rigurosa e inteligente de comprobantes de pago
    doc_msg = msg_obj.get("documentMessage") or {}
    img_msg = msg_obj.get("imageMessage") or {}
    is_doc = bool(doc_msg)
    is_img = bool(img_msg)
    is_media = is_doc or is_img

    file_name = (doc_msg.get("fileName") or "").strip()
    file_name_lower = file_name.lower()

    # 1. Filtro estricto de extensiones: sólo imágenes y PDFs pueden ser comprobantes bancarios
    non_receipt_extensions = (
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".zip", ".rar", ".7z", ".mp3", ".mp4", ".wav", ".avi", ".mkv"
    )
    if is_doc and file_name_lower.endswith(non_receipt_extensions):
        logger.info(f"Archivo de {client_name} ({sender_phone}) ignorado como comprobante: extensión '{file_name}' no admitida para pagos.")
        return JSONResponse({"status": "ignored", "reason": "unsupported_receipt_extension"})

    # 2. Filtro estricto por nombres de archivo académicos, libros, manuales, etc.
    academic_file_keywords = [
        "manual", "guia", "guía", "resumen", "apunte", "clase", "libro", "capitulo",
        "capítulo", "unidad", "tp", "trabajo", "examen", "parcial", "psico", "bender",
        "medicina", "derecho", "lectura", "programa", "teoria", "teoría", "modulo", "módulo"
    ]
    if any(k in file_name_lower for k in academic_file_keywords):
        logger.info(f"Archivo de {client_name} ({sender_phone}) ignorado como comprobante: archivo académico/casual ('{file_name}').")
        return JSONResponse({"status": "ignored", "reason": "academic_or_casual_file"})

    # 3. Filtro de mensajes con contexto casual/estudio en el texto
    casual_academic_text = [
        "profe", "profesor", "profesora", "facultad", "universidad", "materia", "carrera",
        "parcial", "tarea", "la clase", "las clases", "habia pasado la", "había pasado la",
        "mira lo que", "mira esto", "miren esto", "para estudiar", "para el examen"
    ]
    if any(k in text_lower for k in casual_academic_text):
        logger.info(f"Mensaje de {client_name} ({sender_phone}) omitido como comprobante: contexto de estudio/casual ('{text}').")
        return JSONResponse({"status": "ignored", "reason": "casual_or_academic_text"})

    receipt_keywords = [
        "comprobante", "pague", "pagué", "transferi", "transferí", "adjunto el comprobante",
        "constancia", "abone", "aboné", "ya te pague", "ya te transferi", "ahi te pase",
        "ahí te pasé", "te mande el pago", "aca te dejo el pago", "acá te dejo el pago"
    ]
    has_receipt_intent = any(k in text_lower for k in receipt_keywords)

    is_confirmed_receipt = False
    detected_info = None

    # 4. Análisis profundo de media si está presente
    if is_media and key.get("id"):
        try:
            media_data = await whatsapp_client.get_media_base64(key)
            if media_data and media_data.get("base64"):
                b64_str = media_data["base64"]
                mime = (media_data.get("mimetype") or "").lower()

                # A. Si es PDF
                if is_doc or "pdf" in mime or file_name_lower.endswith(".pdf"):
                    try:
                        pdf_bytes = base64.b64decode(b64_str.split(",")[-1])
                        pdf_text, num_pages, pdf_img_bytes = receipt_service.extract_text_from_pdf(pdf_bytes)
                        if num_pages <= 3:
                            if pdf_text:
                                detected_info = receipt_service.parse_transfer_receipt_text(pdf_text)
                                if detected_info and detected_info.get("is_receipt"):
                                    is_confirmed_receipt = True

                            # Si no se detectó texto pero hay imagen incrustada (comprobantes bancarios exportados como imagen única)
                            if not is_confirmed_receipt and pdf_img_bytes:
                                try:
                                    pdf_img_b64 = base64.b64encode(pdf_img_bytes).decode("utf-8")
                                    gemini_res = await receipt_service.analyze_image_with_gemini(pdf_img_b64, mime_type="image/png")
                                    if gemini_res and gemini_res.get("is_receipt"):
                                        detected_info = gemini_res
                                        is_confirmed_receipt = True
                                except Exception as g_err:
                                    logger.debug(f"Error analizando imagen incrustada de PDF con Gemini: {g_err}")

                            # REGLA FAILSAFE PARA PDFs DE 1 A 3 PÁGINAS:
                            # Si no fue filtrado por palabras académicas/manuales y tiene <= 3 páginas, no descartar
                            if not is_confirmed_receipt:
                                is_confirmed_receipt = True
                                if not detected_info:
                                    detected_info = {
                                        "is_receipt": True,
                                        "bank": "PDF Bancario",
                                        "amount": None,
                                        "operation_id": None,
                                        "summary": "Comprobante en PDF (Revisar documento adjunto)"
                                    }
                        else:
                            logger.info(f"PDF de {client_name} rechazado como comprobante ({num_pages} páginas > 3).")
                    except Exception as e:
                        logger.debug(f"Error analizando PDF: {e}")

                # B. Si es Imagen
                elif is_img or "image" in mime:
                    img_bytes = None
                    try:
                        img_bytes = base64.b64decode(b64_str.split(",")[-1])
                    except Exception:
                        pass

                    # 1. Probar Google Gemini Vision
                    try:
                        detected_info = await receipt_service.analyze_image_with_gemini(
                            b64_str,
                            mime_type=mime or "image/jpeg"
                        )
                        if detected_info and detected_info.get("is_receipt"):
                            is_confirmed_receipt = True
                        elif detected_info and detected_info.get("is_receipt") is False:
                            logger.info(f"Gemini descartó imagen de {client_name}: no es comprobante de pago.")
                            return JSONResponse({"status": "ignored", "reason": "not_a_receipt_image"})
                    except Exception as e:
                        logger.debug(f"Error analizando imagen con Gemini: {e}")

                    # 2. Probar OCR Local con Tesseract si Gemini no confirmó
                    if not is_confirmed_receipt and img_bytes:
                        try:
                            ocr_text = receipt_service.extract_text_from_image(img_bytes)
                            if ocr_text:
                                ocr_info = receipt_service.parse_transfer_receipt_text(ocr_text)
                                if ocr_info and ocr_info.get("is_receipt"):
                                    detected_info = ocr_info
                                    is_confirmed_receipt = True
                        except Exception as ocr_err:
                            logger.debug(f"Error en OCR local de imagen: {ocr_err}")

                    # 3. Si el cliente escribió palabras explícitas de pago en el caption
                    if not is_confirmed_receipt and has_receipt_intent:
                        is_confirmed_receipt = True
                        if not detected_info:
                            detected_info = receipt_service.parse_transfer_receipt_text(text)

                    # 4. REGLA FAILSAFE PARA IMÁGENES:
                    # Todo cliente que envía una captura sin texto de estudio debe registrarse como comprobante
                    # para que NUNCA se pierda un pago y quede visible en 'Esperando Pago' para revisión visual.
                    if not is_confirmed_receipt:
                        is_confirmed_receipt = True
                        if not detected_info:
                            detected_info = {
                                "is_receipt": True,
                                "bank": "Captura / Comprobante",
                                "amount": None,
                                "operation_id": None,
                                "summary": "Comprobante en imagen (Revisar captura)"
                            }
        except Exception as e:
            logger.debug(f"No se pudo descargar media de Evolution: {e}")

    # 5. Si es solo texto sin media pero tiene intención explícita y datos financieros
    elif has_receipt_intent:
        detected_info = receipt_service.parse_transfer_receipt_text(text)
        if detected_info and (detected_info.get("is_receipt") or detected_info.get("amount")):
            is_confirmed_receipt = True

    # 6. SOLO ACCIONAR EL FLUJO DE COMPROBANTE SI FUE VERIFICADO
    if is_confirmed_receipt:
        logger.info(f"¡Comprobante VERIFICADO de {client_name} ({sender_phone})! Datos: {detected_info}")

        # Obtener información de la cuenta activa del cliente
        active_accs = client_profile.get("active_accounts", []) if client_profile else []
        target_acc = active_accs[0] if active_accs else None

        file_type_label = "📄 PDF" if (is_doc or file_name_lower.endswith(".pdf")) else ("🖼️ Imagen" if is_img else "📝 Mensaje")

        client_id_val = client_profile.get("client", {}).get("id") if client_profile else None
        account_id_val = target_acc["id"] if target_acc else None
        platform_val = target_acc["platform"] if target_acc else (detected_info.get("platform") if detected_info else "")
        amount_val = float(detected_info.get("amount") or 0.0) if detected_info else 0.0

        # Si no se detectó el monto numérico del ticket, inferir de la tarifa del servicio activo del cliente
        if (not amount_val or amount_val == 0.0) and target_acc and target_acc.get("price"):
            try:
                raw_digits = re.sub(r'[^\d]', '', str(target_acc.get("price")))
                if raw_digits:
                    amount_val = float(raw_digits)
            except Exception:
                pass

        amount_fmt_val = (detected_info.get("amount_formatted") or (format_ars(amount_val) if amount_val > 0 else "")) if detected_info else (format_ars(amount_val) if amount_val > 0 else "")
        bank_val = (detected_info.get("bank") or "") if detected_info else ""
        op_val = (detected_info.get("operation_id") or "") if detected_info else ""
        date_val = (detected_info.get("date") or "") if detected_info else ""

        b64_val = b64_str if ('b64_str' in locals() and b64_str) else ""
        mime_val = mime if ('mime' in locals() and mime) else ("application/pdf" if is_doc else ("image/jpeg" if is_img else ""))
        filename_val = file_name or ("comprobante.pdf" if is_doc else ("comprobante.jpg" if is_img else ""))

        # 1. Crear registro centralizado en estado 'pending' con ID único (#P<ID>)
        pending_item = database.create_pending_payment(
            sender_phone=sender_phone,
            client_name=client_name,
            client_id=client_id_val,
            account_id=account_id_val,
            platform=platform_val or "Streaming",
            amount=amount_val,
            amount_formatted=amount_fmt_val,
            bank=bank_val,
            operation_id=op_val,
            date_detected=date_val,
            receipt_filename=filename_val,
            receipt_mimetype=mime_val,
            receipt_base64=b64_val,
            raw_text=text,
            notes="Detectado vía WhatsApp Webhook"
        )
        payment_id = pending_item.get("id")

        # Resumen del análisis
        analysis_line = ""
        if detected_info and (detected_info.get("amount_formatted") or detected_info.get("bank")):
            detected_parts = []
            if detected_info.get("bank"):
                detected_parts.append(f"<b>{detected_info['bank']}</b>")
            if detected_info.get("amount_formatted"):
                detected_parts.append(f"Monto: <b>{detected_info['amount_formatted']}</b>")
            if detected_info.get("operation_id"):
                detected_parts.append(f"Op: <code>#{detected_info['operation_id']}</code>")
            analysis_line = "• Detección inteligente: " + " | ".join(detected_parts) + "\n"

        caption_txt = f"<i>\"{text}\"</i>" if text else (f"{file_type_label}: <code>{file_name}</code>" if file_name else "(Archivo adjunto)")
        client_tag = "👔 Revendedor" if "revend" in (client_profile.get("client", {}).get("client_type") or "").lower() else "👤 Consumidor Final"

        # Armar mensaje interactivo con botones para Telegram
        service_lines = ""
        if target_acc:
            price_str = target_acc.get("price") or "-"
            exp_date = target_acc.get("expiry_date") or "-"
            days_left = target_acc.get("days_remaining", 0)
            is_new_purchase = days_left is not None and days_left > 15
            service_action_label = "Compra Nueva" if is_new_purchase else "Renovación"

            service_lines = (
                f"• Servicio: <b>{target_acc['platform']}</b> (<code>{target_acc['email']}</code>)\n"
                f"• Tarifa Acordada: <b>{price_str}</b> | Vence: <code>{exp_date}</code> ({service_action_label})\n"
            )

        client_btn = [{"text": "👤 Ficha 360°", "callback_data": f"client_{client_id_val}"}] if client_id_val else []
        kb = {
            "inline_keyboard": [
                [
                    {"text": f"✅ Aprobar Pago (#P{payment_id})", "callback_data": f"payapp_{payment_id}"},
                    {"text": f"❌ Denegar (#P{payment_id})", "callback_data": f"payrej_{payment_id}"}
                ],
                client_btn + [{"text": "💬 Abrir WhatsApp", "url": f"https://wa.me/{sender_phone}"}]
            ]
        }

        tg_msg = (
            f"🧾 <b>¡NUEVO COMPROBANTE RECIBIDO! (#P{payment_id})</b>\n\n"
            f"• Cliente: <b>{client_name}</b> ({client_tag})\n"
            f"• WhatsApp: <code>{sender_phone}</code>\n"
            f"{service_lines}"
            f"• Adjunto: {caption_txt}\n"
            f"{analysis_line}"
            f"• Estado: ⏳ <b>Esperando Pago / Aprobación</b>\n\n"
            f"👉 <i>Toca los botones abajo para aprobar o denegar de inmediato:</i>"
        )
        await send_telegram_message(tg_msg, reply_markup=kb)

        # 2. Reenvío directo al WhatsApp Privado del Administrador con comandos
        admin_configured = (settings.get("admin_whatsapp") or os.getenv("ADMIN_WHATSAPP", "")).strip()
        clean_admin = database.clean_whatsapp_phone(admin_configured) if admin_configured else ""
        if clean_admin and clean_admin != sender_phone:
            admin_notice = (
                f"🧾 *NUEVO COMPROBANTE RECIBIDO (#P{payment_id})*\n"
                f"• *Cliente:* {client_name} (+{sender_phone})\n"
                f"• *Servicio:* {platform_val or 'Suscripción'}" + (f" ({target_acc['email']})" if target_acc else "") + "\n"
                f"• *Monto Detectado:* {amount_fmt_val or 'No detectado'}" + (f" | *Banco:* {bank_val}" if bank_val else "") + "\n"
                + (f"• *Op:* #{op_val}\n" if op_val else "") +
                f"\n👉 *Para APROBAR y renovar/activar:*\n"
                f"/pagoapro_{payment_id}\n\n"
                f"👉 *Para DENEGAR / RECHAZAR:*\n"
                f"/pagodene_{payment_id}"
            )
            try:
                if b64_val and mime_val:
                    await whatsapp_client.send_media_message(
                        phone=clean_admin,
                        base64_data=b64_val,
                        mime_type=mime_val,
                        file_name=filename_val or "comprobante",
                        caption=admin_notice
                    )
                else:
                    await whatsapp_client.send_text_message(
                        phone=clean_admin,
                        message=admin_notice
                    )
            except Exception as e:
                logger.error(f"Fallo al avisar comprobante a WhatsApp admin ({clean_admin}): {e}")

        # 3. Respuesta automática al cliente
        reply = (
            f"¡Hola {client_name}! 🙌 Recibimos tu comprobante correctamente (#P{payment_id}).\n\n"
            f"Nuestro equipo lo verificará en el sistema a la brevedad y extenderá tu servicio. ¡Muchas gracias por tu pago! ✨"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        return JSONResponse({"status": "ok", "action": "receipt_acknowledged", "payment_id": payment_id, "detected": detected_info})


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

    # REGLA D: Consultas de Catálogo, Precios y Disponibilidad
    catalog_intents = [
        "/catalogo", "/precios", "/precio", "/planes", "/combos", "/servicios",
        "catalogo", "catálogo", "precios", "precio", "lista de precios",
        "planes", "servicios", "combos", "que tenes", "qué tenés",
        "que tenes disponible", "qué tenés disponible", "que tenés disponible",
        "que servicios tenes", "qué servicios tenés", "que cuentas tenes",
        "qué cuentas tenés", "cuanto sale", "cuánto sale", "cuanto cuesta",
        "cuánto cuesta", "cuanto esta", "cuánto está", "info de precios",
        "quiero contratar", "para comprar", "que plataformas tenes", "qué plataformas tenés"
    ]
    if any(k in text_lower for k in catalog_intents):
        platform_keywords = [
            "netflix", "disney", "max", "hbo", "prime", "amazon", "spotify",
            "youtube", "paramount", "crunchyroll", "apple", "star", "iptv"
        ]
        target_platform = None
        for pk in platform_keywords:
            if pk in text_lower:
                target_platform = pk
                break

        client_type_val = (client_profile.get("client", {}).get("client_type") or "consumidor_final") if client_profile else "consumidor_final"
        reply = database.generate_catalog_message(
            client_type=client_type_val,
            platform_filter=target_platform,
            include_payment_methods=True
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        _AUTO_REPLY_COOLDOWNS[sender_phone] = now
        return JSONResponse({"status": "ok", "action": "catalog_sent", "platform_filter": target_platform})

    # REGLA E: Gestión y Reporte Instantáneo de Cuentas Caídas por el Cliente (/caida, /reemplazo, soporte)
    fallen_intents = [
        "/caida", "/reemplazo", "/soporte", "caida", "caída", "reemplazo",
        "se cayo", "se cayó", "se me cayo", "se me cayó",
        "cuenta caida", "cuenta caída", "mi cuenta se cayo", "mi cuenta se cayó",
        "no anda la cuenta", "no me anda la cuenta", "no funciona la cuenta",
        "no anda mi cuenta", "no funciona mi cuenta",
        "no anda netflix", "se cayo netflix", "se cayó netflix",
        "no anda disney", "se cayo disney", "se cayó disney",
        "no anda max", "se cayo max", "se cayó max",
        "no anda prime", "se cayo prime", "se cayó prime",
        "clave incorrecta", "contraseña incorrecta", "contrasena incorrecta",
        "no me deja entrar", "no puedo entrar", "cambiaron la clave",
        "cambiaron la contrasena", "cambiaron la contraseña",
        "pantalla ocupada", "limite de pantallas", "límite de pantallas",
        "actualizar hogar", "hogar netflix"
    ]
    if any(k in text_lower for k in fallen_intents):
        active_accs = client_profile.get("active_accounts", []) if client_profile else []
        if not active_accs:
            reply = (
                f"¡Hola {client_name}! En este momento no registramos una suscripción activa asociada a tu número en el sistema. "
                f"Si contrataste con otro nombre o correo, indícanoslo por favor para verificar tu servicio."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "no_active_for_replacement"})

        target_account = None
        if len(active_accs) == 1:
            target_account = active_accs[0]
        else:
            for acc in active_accs:
                plat_slug = acc["platform"].lower()
                if any(slug in text_lower for slug in plat_slug.split()):
                    target_account = acc
                    break

        if not target_account:
            lines = [
                f"🛠️ ¡Hola {client_name}! Vemos que tienes varios servicios activos con nosotros:\n"
            ]
            for a in active_accs:
                lines.append(f"• *{a['platform']}* (`{a['email']}`)")
            lines.append(f"\nPor favor indícanos cuál presenta inconvenientes respondiendo con el nombre del servicio o escribiendo: */caida <plataforma>*")
            reply = "\n".join(lines)
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "multiple_accounts_clarification"})

        res = database.report_and_auto_replace_account(
            str(target_account["id"]),
            reason=f"Reporte de cliente vía WhatsApp ({text[:50]})",
            platform_filter=target_account.get("platform")
        )

        if res.get("replaced"):
            await whatsapp_client.send_text_message(sender_phone, res["whatsapp_message"], delay_seconds=2.0)

            new_a = res["new_account"]
            await send_telegram_message(
                f"⚡ <b>AUTO-REEMPLAZO INSTANTÁNEO A CLIENTE</b>\n\n"
                f"• Cliente: <b>{client_name}</b> (<code>+{sender_phone}</code>)\n"
                f"• Servicio: <b>{res.get('platform')}</b>\n"
                f"• Motivo: Reporte de caída de cliente\n"
                f"• Nueva Cuenta: <code>{new_a.get('email')}</code>\n"
                f"• Clave: <code>{new_a.get('password')}</code>\n"
                f"• Entregada automáticamente por WhatsApp."
            )
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "client_auto_replacement_success", "details": res})

        elif res.get("out_of_stock"):
            await whatsapp_client.send_text_message(sender_phone, res["whatsapp_message"], delay_seconds=2.0)

            await send_telegram_message(
                f"🚨 <b>CLIENTE REPORTÓ CAÍDA - ¡SIN STOCK LIBRE!</b>\n\n"
                f"• Cliente: <b>{client_name}</b> (<code>+{sender_phone}</code>)\n"
                f"• Plataforma: <b>{res.get('platform')}</b>\n"
                f"• Cuenta: <code>{target_account.get('email')}</code>\n"
                f"• Mensaje del cliente: <i>\"{text}\"</i>\n"
                f"• Se notificó al cliente que se está gestionando la reposición con urgencia."
            )
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "client_auto_replacement_out_of_stock", "details": res})
        else:
            reply = (
                f"¡Hola {client_name}! Hemos registrado tu consulta. Nuestro equipo técnico revisará el estado de tu servicio a la brevedad."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "client_report_fallback"})

    return JSONResponse({"status": "ok", "action": "none"})


# ==========================================
# CHATWOOT CRM SLASH COMMANDS & WEBHOOKS
# ==========================================
@router.post("/api/settings/chatwoot")
async def api_settings_chatwoot(
    request: Request,
    url: str = Form("https://chat.joif.net"),
    token: str = Form(""),
    account_id: str = Form("1"),
    enabled: Optional[str] = Form(None),
    auto_sync: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)

    clean_url = url.strip() or "https://chat.joif.net"
    clean_token = token.strip()
    clean_acc = account_id.strip() or "1"

    database.save_chatwoot_settings(
        url=clean_url,
        token=clean_token,
        account_id=clean_acc,
        enabled=1 if enabled in ("1", "on", "true") else (1 if enabled is None else 0),
        auto_sync=1 if auto_sync in ("1", "on", "true") else (1 if auto_sync is None else 0)
    )

    if clean_token:
        test_res = await whatsapp_client.test_chatwoot_connection(url=clean_url, token=clean_token, account_id=clean_acc)
        if test_res.get("success"):
            user_info = test_res.get("user") or {}
            user_name = user_info.get("name") or user_info.get("email") or "Usuario"
            return RedirectResponse(url=f"/?msg=chatwoot_connected&user={urllib.parse.quote(user_name)}#integrations", status_code=302)
        else:
            err = urllib.parse.quote(test_res.get("error", "Error autenticando con Chatwoot"))
            return RedirectResponse(url=f"/?err={err}#integrations", status_code=302)

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


