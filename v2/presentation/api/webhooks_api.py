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

# Deduplicación en memoria de IDs de mensajes de WhatsApp procesados (TTL 2 horas)
_PROCESSED_MESSAGE_IDS: Dict[str, float] = {}

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
    auto_reply_enabled: Optional[str] = Form(None),
    expiry_cutoff_hour: Optional[int] = Form(17)
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
        gemini_api_key=gemini_api_key.strip() if gemini_api_key else "",
        expiry_cutoff_hour=int(expiry_cutoff_hour or 17)
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

    event = (body.get("event") or body.get("type", "")).lower()

    # 0. EVENTOS DE GRUPOS: Bienvenida y Despedida (group-participants.update)
    if event in ("group-participants.update", "group_participants_update", "groups.update"):
        group_jid = (data.get("id") or data.get("groupJid") or body.get("id") or "").strip()
        action = (data.get("action") or body.get("action") or "").lower() # 'add', 'remove'
        participants = data.get("participants") or body.get("participants") or []

        if group_jid and participants:
            group_cfg = database.get_group_config(group_jid)
            if group_cfg:
                if action == "add" and group_cfg.get("welcome_enabled"):
                    msg_tpl = group_cfg.get("welcome_message") or (
                        "👋 *¡Bienvenido/a al grupo!* 🍿\n"
                        "Disfruta del contenido y respeta las normas de la comunidad. Si deseas contratar servicios de streaming, escribe */catalogo*."
                    )
                    await whatsapp_client.send_text_message(group_jid, msg_tpl)
                    return JSONResponse({"status": "ok", "action": "welcome_sent", "group": group_jid})

                elif action == "remove" and group_cfg.get("goodbye_enabled"):
                    msg_tpl = group_cfg.get("goodbye_message") or "👋 ¡Hasta luego! Gracias por haber formado parte de la comunidad."
                    await whatsapp_client.send_text_message(group_jid, msg_tpl)
                    return JSONResponse({"status": "ok", "action": "goodbye_sent", "group": group_jid})

        return JSONResponse({"status": "ok", "action": "group_event_processed"})

    # 0.1 FILTRO DE EVENTOS: Solo procesar eventos de mensajes entrantes (messages.upsert)
    if event and event not in ("messages.upsert", "messages_upsert"):
        return JSONResponse({"status": "ignored", "reason": f"unhandled_event_{event}"})

    data = body.get("data", {}) or {}
    key = data.get("key", {}) or body.get("key", {})
    from_me = key.get("fromMe", False)
    remote_jid = key.get("remoteJid", "")
    msg_id = (key.get("id") or "").strip()

    # 0.2 DEDUPLICACIÓN POR MESSAGE ID (wamid) PARA EVITAR PROCESAR CLONES
    if msg_id:
        now = time.time()
        if len(_PROCESSED_MESSAGE_IDS) > 2000:
            expired = [m for m, t_exp in _PROCESSED_MESSAGE_IDS.items() if now - t_exp > 7200]
            for m in expired:
                _PROCESSED_MESSAGE_IDS.pop(m, None)

        if msg_id in _PROCESSED_MESSAGE_IDS:
            logger.info(f"Mensaje #{msg_id} ya procesado previamente. Ignorando evento duplicado de webhook.")
            return JSONResponse({"status": "ignored", "reason": "already_processed_msg_id"})
        _PROCESSED_MESSAGE_IDS[msg_id] = now


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

    # Ignorar transmisiones o estados
    if not remote_jid or "status@broadcast" in remote_jid:
        return JSONResponse({"status": "ignored"})

    is_group = "@g.us" in remote_jid
    group_jid = remote_jid if is_group else ""

    # Extraer remitente individual
    if is_group:
        participant_raw = (key.get("participant") or data.get("participant") or "").strip()
        phone_raw = participant_raw.split("@")[0].split(":")[0] if participant_raw else remote_jid.split("@")[0].split(":")[0]
    else:
        phone_raw = remote_jid.split("@")[0].split(":")[0]

    sender_phone = re.sub(r'[^0-9]', '', phone_raw)
    if not sender_phone or len(sender_phone) < 8:
        return JSONResponse({"status": "ignored", "reason": "invalid_phone"})

    # 2. FILTRO DE BANEO SILENCIOSO (Silent Ban - Atlas-MD)
    if database.is_silent_banned(sender_phone):
        logger.info(f"Mensaje de {sender_phone} omitido silenciosamente (usuario bajo Silent Ban).")
        return JSONResponse({"status": "ignored", "reason": "silent_banned_user"})

    if is_group and database.is_silent_banned(group_jid):
        logger.info(f"Mensaje del grupo {group_jid} omitido silenciosamente (grupo bajo Silent Ban).")
        return JSONResponse({"status": "ignored", "reason": "silent_banned_group"})

    # 3. FILTRO DE MODO DE OPERACIÓN DEL BOT (Bot Mode - Atlas-MD: public / private / self)
    settings = database.get_whatsapp_api_settings()
    admin_configured = (settings.get("admin_whatsapp") or os.getenv("ADMIN_WHATSAPP", "")).strip()
    clean_admin = database.clean_whatsapp_phone(admin_configured) if admin_configured else ""
    is_owner = from_me or (clean_admin and (sender_phone == clean_admin or sender_phone.endswith(clean_admin[-8:]) or clean_admin.endswith(sender_phone[-8:])))

    bot_mode = database.get_bot_mode() # 'public', 'private', 'self'
    if bot_mode == "self" and not is_owner:
        return JSONResponse({"status": "ignored", "reason": "bot_mode_self"})

    if bot_mode == "private" and is_group and not is_owner:
        return JSONResponse({"status": "ignored", "reason": "bot_mode_private_group_ignored"})

    # 4. CONTROL DE GRUPOS Y WHITELIST (¿Está habilitado el bot en este grupo?)
    group_cfg = None
    if is_group:
        group_cfg = database.get_group_config(group_jid)
        if not group_cfg:
            group_cfg = database.upsert_group_config(group_jid=group_jid, group_name="Grupo de WhatsApp", bot_enabled=1)

        is_bot_enabled_in_group = bool(group_cfg.get("bot_enabled", 1))
        if not is_bot_enabled_in_group and not is_owner:
            return JSONResponse({"status": "ignored", "reason": "group_bot_disabled"})

        # 4.1 ANTILINK: Detección y borrado de enlaces no autorizados en grupo
        if group_cfg.get("antilink_enabled") and not is_owner:
            has_forbidden_link = bool(re.search(r'(?:chat\.whatsapp\.com\/[a-zA-Z0-9]+|https?:\/\/[^\s]+|wa\.me\/[^\s]+)', text_lower))
            if has_forbidden_link:
                participant_jid = key.get("participant") or data.get("participant") or f"{sender_phone}@s.whatsapp.net"
                await whatsapp_client.delete_message_for_everyone(group_jid, msg_id, participant=participant_jid)
                action_type = group_cfg.get("antilink_action", "delete")
                if action_type == "kick":
                    await whatsapp_client.update_group_participant(group_jid, "remove", [sender_phone])
                    await whatsapp_client.send_text_message(group_jid, f"🛡️ *ANTILINK:* El usuario @{sender_phone} fue expulsado por enviar enlaces.")
                else:
                    await whatsapp_client.send_text_message(group_jid, f"⚠️ *ANTILINK:* @{sender_phone}, los enlaces no están permitidos en este grupo.")
                return JSONResponse({"status": "antilink_action_taken", "action": action_type})

        # 4.2 COMANDOS DE GRUPO (Atlas-MD Baileys)
        # /tagall, /todos, @everyone
        if re.search(r'^/(?:tagall|todos|mencionartodos)\b|^@everyone\b', text_lower):
            if is_owner:
                tag_msg = re.sub(r'^/(?:tagall|todos|mencionartodos)\s*|^@everyone\s*', '', text, flags=re.IGNORECASE).strip()
                res_tag = await whatsapp_client.send_group_tagall(group_jid, message=tag_msg, sender_name=data.get("pushName", "Admin"))
                return JSONResponse({"status": "ok", "action": "group_tagall", "details": res_tag})
            else:
                await whatsapp_client.send_text_message(group_jid, "⚠️ Solo los administradores pueden utilizar /tagall.")
                return JSONResponse({"status": "ignored", "reason": "unauthorized_tagall"})

        # /mute o /cerrar
        if re.search(r'^/(?:mute|cerrar|cerrargrupo)\b', text_lower):
            if is_owner:
                await whatsapp_client.update_group_setting(group_jid, "announcement")
                await whatsapp_client.send_text_message(group_jid, "🔒 *GRUPO CERRADO:* Solo administradores pueden enviar mensajes.")
                return JSONResponse({"status": "ok", "action": "group_muted"})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # /unmute o /abrir
        if re.search(r'^/(?:unmute|abrir|abrirgrupo)\b', text_lower):
            if is_owner:
                await whatsapp_client.update_group_setting(group_jid, "not_announcement")
                await whatsapp_client.send_text_message(group_jid, "📢 *GRUPO ABIERTO:* Todos los integrantes pueden enviar mensajes.")
                return JSONResponse({"status": "ok", "action": "group_unmuted"})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # /antilink on / off
        if re.search(r'^/antilink\b', text_lower):
            if is_owner:
                turn_on = "on" in text_lower or "activar" in text_lower or "1" in text_lower
                database.set_group_antilink(group_jid, enabled=turn_on)
                st_str = "ACTIVADA 🛡️ (Enlaces prohibidos serán borrados)" if turn_on else "DESACTIVADA ⚪"
                await whatsapp_client.send_text_message(group_jid, f"🛡️ *MODERACIÓN:* Protección Antilink {st_str}.")
                return JSONResponse({"status": "ok", "action": "antilink_toggled", "enabled": turn_on})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # /link o /enlace
        if re.search(r'^/(?:link|enlace|invitacion)\b', text_lower):
            link_val = await whatsapp_client.get_group_invite_code(group_jid)
            if link_val:
                await whatsapp_client.send_text_message(group_jid, f"🔗 *Enlace de invitación del grupo:*\n{link_val}")
            else:
                await whatsapp_client.send_text_message(group_jid, "⚠️ No se pudo obtener el enlace (verifica que el bot sea admin del grupo).")
            return JSONResponse({"status": "ok", "action": "group_link_sent"})

        # /infogrupo o /groupinfo
        if re.search(r'^/(?:infogrupo|groupinfo|grupo)\b', text_lower):
            info = await whatsapp_client.find_group_info(group_jid)
            if info:
                g_name = info.get("subject", "Grupo")
                g_parts = info.get("participants", [])
                admins_count = sum(1 for p in g_parts if p.get("admin"))
                await whatsapp_client.send_text_message(
                    group_jid,
                    f"ℹ️ *INFORMACIÓN DEL GRUPO:*\n\n"
                    f"• *Nombre:* {g_name}\n"
                    f"• *Integrantes:* {len(g_parts)}\n"
                    f"• *Administradores:* {admins_count}\n"
                    f"• *Antilink:* {'Activado 🛡️' if group_cfg.get('antilink_enabled') else 'Desactivado ⚪'}\n"
                    f"• *ID:* `{group_jid}`"
                )
            else:
                await whatsapp_client.send_text_message(group_jid, f"ℹ️ Grupo: `{group_jid}`")
            return JSONResponse({"status": "ok", "action": "group_info_sent"})

        # /kick o /expulsar
        kick_match = re.search(r'^/(?:kick|expulsar)\s+([0-9\+\s\-]+)', text_lower)
        if kick_match:
            if is_owner:
                target_digits = re.sub(r'[^0-9]', '', kick_match.group(1))
                if target_digits:
                    await whatsapp_client.update_group_participant(group_jid, "remove", [target_digits])
                    await whatsapp_client.send_text_message(group_jid, f"👢 Usuario @{target_digits} expulsado del grupo.")
                    return JSONResponse({"status": "ok", "action": "group_participant_removed"})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # /promote o /promover
        promote_match = re.search(r'^/(?:promote|promover|admin)\s+([0-9\+\s\-]+)', text_lower)
        if promote_match:
            if is_owner:
                target_digits = re.sub(r'[^0-9]', '', promote_match.group(1))
                if target_digits:
                    await whatsapp_client.update_group_participant(group_jid, "promote", [target_digits])
                    await whatsapp_client.send_text_message(group_jid, f"⭐ Usuario @{target_digits} promovido a Administrador.")
                    return JSONResponse({"status": "ok", "action": "group_participant_promoted"})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # /demote o /degradar
        demote_match = re.search(r'^/(?:demote|degradar|quitaradmin)\s+([0-9\+\s\-]+)', text_lower)
        if demote_match:
            if is_owner:
                target_digits = re.sub(r'[^0-9]', '', demote_match.group(1))
                if target_digits:
                    await whatsapp_client.update_group_participant(group_jid, "demote", [target_digits])
                    await whatsapp_client.send_text_message(group_jid, f"🔻 Usuario @{target_digits} degradado a miembro común.")
                    return JSONResponse({"status": "ok", "action": "group_participant_demoted"})
            return JSONResponse({"status": "ignored", "reason": "unauthorized_group_command"})

        # En grupos, omitir auto-atención de cobros personales o credenciales para proteger la privacidad
        # Solo permitir catálogo público si lo solicitan expresamente
        if not any(cmd in text_lower for cmd in ("/catalogo", "/precios", "precios", "planes")):
            return JSONResponse({"status": "ignored", "reason": "group_message_non_catalog"})

    # Verificar si es un comando administrativo o comando de caída/autorización
    is_admin_cmd = bool(re.search(r'^/(?:pagoapro|aprobarpago|pagodene|rechazarpago|pagoparcial|parcial|revertir_pago|revertirpago|anularpago|deshacer_cambio|deshacercambio|baja|cortar|caida|reemplazo|reemplazar|cambiar|esperar|espera|autorizar|posponer)', text_lower))

    # Si es from_me (mensaje saliente propio):
    if from_me:
        # Detectar si el administrador le está enviando alta o renovación de servidor HTTP Custom (HWID)
        custom_res = await database.process_http_custom_outgoing_message(sender_phone, text, source="WhatsApp")
        if custom_res.get("status") == "success":
            logger.info(f"HTTP Custom {custom_res.get('action')} procesado automáticamente vía WhatsApp para {sender_phone}")
            return JSONResponse({"status": "processed_http_custom", "data": custom_res})

        if not is_admin_cmd:
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

    # 5.1 COMANDOS DE ADMINISTRADOR POR WHATSAPP (/pagoapro_<ID>, /pagoapro_<ID>_all, /pagodene_<ID>, /pagoparcial_<ID>, /revertir_pago_<ID>, /deshacer_cambio_<ID>, /baja_<ID>, /cambiar_<ID>, /esperar_<ID>, /caida)
    admin_approval_all_match = re.search(r'^/(?:pagoapro|aprobarpago)[_\s]+(\d+)_all', text_lower)
    admin_approval_match = re.search(r'^/(?:pagoapro|aprobarpago)[_\s]+(\d+)(?:\s+(\d+(?:[.,]\d+)?))?', text_lower)
    admin_reject_match = re.search(r'^/(?:pagodene|rechazarpago)[_\s]+(\d+)', text_lower)
    admin_partial_pay_match = re.search(r'^/(?:pagoparcial|parcial)[_\s]+(\d+)(?:\s+(\d+(?:[.,]\d+)?))?', text_lower)
    admin_reverse_pay_match = re.search(r'^/(?:revertir_pago|revertirpago|anularpago)[_\s]+(\d+)', text_lower)
    admin_undo_replace_match = re.search(r'^/(?:deshacer_cambio|deshacercambio|revertircambio)[_\s]+(\d+)', text_lower)
    admin_baja_match = re.search(r'^/(?:baja|cortar|desactivar)[_\s]+(\d+)', text_lower)
    admin_change_match = re.search(r'^/(?:cambiar|reemplazar|autorizar)[_\s]+(\d+)', text_lower)
    admin_wait_match = re.search(r'^/(?:esperar|espera|posponer)[_\s]+(\d+)', text_lower)
    admin_fallen_match = re.search(r'^/(?:caida|reemplazo)(?:[_\s]+(.+))?', text_lower)

    if (admin_approval_all_match or admin_approval_match or admin_reject_match or admin_partial_pay_match or
        admin_reverse_pay_match or admin_undo_replace_match or admin_baja_match or
        admin_change_match or admin_wait_match or
        (admin_fallen_match and (from_me or (settings.get("admin_whatsapp") and sender_phone.endswith(database.clean_whatsapp_phone(settings.get("admin_whatsapp"))[-8:]))))):
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

        if admin_approval_all_match:
            pid = int(admin_approval_all_match.group(1))
            res = database.approve_pending_payment(
                pid,
                admin_user=f"WhatsApp Admin (+{sender_phone})",
                renew_all=True
            )
            if res.get("success"):
                p = res.get("payment", {})
                renewed = res.get("renewed_accounts", [])
                lines_renewed = "\n".join([f"• *{r['platform']}*: `{r['email']}` (Vence: {r.get('new_expiry_date')})" for r in renewed]) or "• Servicio activo renovado"
                amt_fmt = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)
                admin_ack = (
                    f"✅ *PAGO #P{pid} APROBADO (MULTI-SERVICIO)*\n\n"
                    f"• Cliente: *{p.get('client_name')}*\n"
                    f"• Cuentas Renovadas ({len(renewed)}):\n{lines_renewed}\n\n"
                    f"• Monto Total Acreditado: *{amt_fmt}*\n"
                    f"• Estado: Todas renovadas y asentadas en Finanzas & MCP."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                c_phone = p.get("sender_phone") or p.get("client_whatsapp")
                if c_phone and c_phone != sender_phone:
                    c_clean = database.clean_whatsapp_phone(c_phone)
                    if c_clean:
                        c_msg = (
                            f"🎉 ¡Hola {p.get('client_name', 'Cliente')}! Confirmamos la recepción de tu pago"
                            + (f" de *{amt_fmt}*" if amt_fmt else "")
                            + f" y la renovación exitosa de todos tus servicios activos:\n\n{lines_renewed}\n\n"
                            f"¡Tus suscripciones quedaron al día! Muchas gracias por tu pago y preferencia. 🙌✨"
                        )
                        await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)

                await send_telegram_message(
                    f"✅ <b>PAGO #P{pid} MULTI-SERVICIO APROBADO (WHATSAPP ADMIN)</b>\n\n"
                    f"• Cliente: <b>{p.get('client_name')}</b>\n"
                    f"• Servicios Renovados: <b>{len(renewed)}</b>\n"
                    f"• Monto: <b>{amt_fmt}</b>\n"
                    f"• Renovación masiva ejecutada por admin."
                )
                return JSONResponse({"status": "ok", "action": "payment_approved_all", "payment_id": pid})
            else:
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ Error al procesar multi-servicio #P{pid}: {res.get('error')}"
                )
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_approval_match:
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

        elif admin_partial_pay_match:
            target_id = int(admin_partial_pay_match.group(1))
            amt_str = admin_partial_pay_match.group(2)
            if not amt_str:
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ Debes indicar el monto del pago parcial.\nEjemplo: `/pagoparcial_{target_id} 3500`"
                )
                return JSONResponse({"status": "error", "error": "missing_amount"})
            amt_val = float(amt_str.replace(",", "."))

            # Buscar si target_id es un comprobante pendiente
            pending_item = database.get_pending_payment(target_id)
            acc_target = None
            if pending_item:
                acc_target = pending_item.get("account_id")
                if not acc_target and pending_item.get("client_id"):
                    c_pro = database.get_client_360_profile(pending_item["client_id"])
                    accs = c_pro.get("active_accounts", []) if c_pro else []
                    if accs:
                        acc_target = accs[0]["id"]
            if not acc_target:
                acc_target = str(target_id)

            res = database.register_partial_payment(
                email_or_id=str(acc_target),
                amount=amt_val,
                payment_method="Transferencia",
                notes=f"Pago parcial vía WhatsApp Admin (+{sender_phone}) [Ref #{target_id}]"
            )
            if res.get("success"):
                if pending_item:
                    database.reject_pending_payment(target_id, reason=f"Acreditado como pago parcial de {database.format_ars(amt_val)} (Saldo rest: {database.format_ars(res.get('remaining_debt', 0))})", admin_user=f"WhatsApp Admin (+{sender_phone})")

                c_name = res.get("client_name") or "Cliente"
                rem_fmt = database.format_ars(res.get("remaining_debt", 0.0))
                paid_fmt = database.format_ars(amt_val)
                admin_ack = (
                    f"✅ *PAGO PARCIAL / SEÑA REGISTRADO*\n\n"
                    f"• Cliente: *{c_name}*\n"
                    f"• Servicio: *{res.get('platform')}*\n"
                    f"• Monto Acreditado: *{paid_fmt}*\n"
                    f"• Saldo Restante Pendiente: *{rem_fmt}*\n"
                    f"• Estado de cuenta: *{res.get('payment_status', 'parcial').upper()}*"
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                c_phone = res.get("client_whatsapp") or (pending_item.get("sender_phone") if pending_item else None)
                if c_phone and c_phone != sender_phone:
                    c_clean = database.clean_whatsapp_phone(c_phone)
                    if c_clean:
                        c_msg = (
                            f"¡Hola {c_name}! 🙌 Registramos tu pago parcial de *{paid_fmt}* para tu suscripción de *{res.get('platform')}*.\n\n"
                            f"📌 Tu saldo pendiente restante es de: *{rem_fmt}*.\n"
                            f"¡Muchas gracias! Cuando completes el saldo total se extenderá tu ciclo completo. ✨"
                        )
                        await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)

                await send_telegram_message(
                    f"💵 <b>PAGO PARCIAL REGISTRADO (WHATSAPP ADMIN)</b>\n\n"
                    f"• Cliente: <b>{c_name}</b>\n"
                    f"• Servicio: <b>{res.get('platform')}</b>\n"
                    f"• Monto recibido: <b>{paid_fmt}</b>\n"
                    f"• Saldo pendiente: <b>{rem_fmt}</b>\n"
                    f"• Asentado en finanzas."
                )
                return JSONResponse({"status": "ok", "action": "partial_payment_registered", "details": res})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al registrar pago parcial: {res.get('error')}")
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_reverse_pay_match:
            pay_id = int(admin_reverse_pay_match.group(1))
            res = database.reverse_customer_payment(pay_id, reason=f"Revertido por Admin WhatsApp (+{sender_phone})")
            if res.get("success"):
                rev_amt = database.format_ars(res.get("reversed_amount", 0.0))
                admin_ack = (
                    f"🔄 *COBRO #{pay_id} REVERTIDO EXITOSAMENTE*\n\n"
                    f"• Monto Anulado: *{rev_amt}*\n"
                    f"• Cuenta #{res.get('account_id')} restaurada al vencimiento previo: `{res.get('restored_expiry') or 'original'}`\n"
                    f"• El importe fue descontado de los reportes y métricas de ganancias."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)
                await send_telegram_message(
                    f"🔄 <b>COBRO #{pay_id} REVERTIDO (WHATSAPP ADMIN)</b>\n\n"
                    f"• Monto anulado: <b>{rev_amt}</b>\n"
                    f"• Vencimiento restaurado a: <code>{res.get('restored_expiry') or '-'}</code>"
                )
                return JSONResponse({"status": "ok", "action": "payment_reversed", "payment_id": pay_id})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al revertir cobro #{pay_id}: {res.get('error')}")
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_undo_replace_match:
            rid = int(admin_undo_replace_match.group(1))
            res = database.rollback_fallen_report_replacement(rid)
            if res.get("success"):
                admin_ack = (
                    f"🔄 *REEMPLAZO #C{rid} DESHECHO / REVERTIDO*\n\n"
                    f"• Cuenta anterior reactivada: #{res.get('old_account_id')}\n"
                    f"• Cuenta nueva devuelta a stock libre: #{res.get('reassigned_account_id')}\n"
                    f"• Estado del reporte restaurado a 'waiting'."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)
                await send_telegram_message(
                    f"🔄 <b>REEMPLAZO #C{rid} DESHECHO (WHATSAPP ADMIN)</b>\n\n"
                    f"• Reporte #C{rid} devuelto a estado de espera.\n"
                    f"• Inventario restaurado a su estado original."
                )
                return JSONResponse({"status": "ok", "action": "replacement_undone", "report_id": rid})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al deshacer reemplazo #C{rid}: {res.get('error')}")
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_baja_match:
            acc_target = admin_baja_match.group(1)
            res = database.mark_account_for_password_change(acc_target)
            if res.get("success"):
                admin_ack = (
                    f"🛑 *CUENTA MARCADA PARA BAJA / ROTACIÓN DE CLAVE*\n\n"
                    f"• Cuenta #{res.get('account_id')}: `{res.get('email')}` ({res.get('platform')})\n"
                    f"• Cliente: *{res.get('client_name') or 'Sin asignar'}*\n"
                    f"• Estado: `por_cambiar_clave`\n"
                    f"• Se cancelaron todos los avisos automáticos diarios al cliente.\n\n"
                    f"💡 Puedes cambiar la contraseña desde el panel web en la pestaña 'Cuentas' y notificar a los demás usuarios si es compartida."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)
                await send_telegram_message(
                    f"🛑 <b>CUENTA #{res.get('account_id')} MARCADA PARA BAJA</b>\n\n"
                    f"• Servicio: <b>{res.get('platform')}</b> (<code>{res.get('email')}</code>)\n"
                    f"• Estado: <code>por_cambiar_clave</code>"
                )
                return JSONResponse({"status": "ok", "action": "account_marked_for_baja", "account_id": res.get("account_id")})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al dar de baja cuenta: {res.get('error')}")
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

        elif admin_change_match:
            rid = int(admin_change_match.group(1))
            res = database.authorize_fallen_report(rid, admin_user=f"WhatsApp Admin (+{sender_phone})")
            if res.get("replaced"):
                new_a = res["new_account"]
                old_a = res["old_account"]
                c_phone = res.get("clean_phone")

                # Enviar mensaje oficial con nuevas credenciales al cliente por WhatsApp
                if c_phone:
                    await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)

                admin_ack = (
                    f"✅ *REEMPLAZO AUTORIZADO Y ENTREGADO (#C{rid})*\n\n"
                    f"• Cliente: *{res.get('client_name')}*\n"
                    f"• Servicio: *{res.get('platform')}*\n"
                    f"• Cuenta anterior: `{old_a.get('email')}`\n"
                    f"• Nueva cuenta: `{new_a.get('email')}`\n"
                    f"• Clave: `{new_a.get('password')}`" + (f"\n• Perfil: {new_a.get('profile_name')}" if new_a.get('profile_name') else "") + (f" [PIN: {new_a.get('profile_pin')}]" if new_a.get('profile_pin') else "") + "\n"
                    f"• Vencimiento mantenido: `{new_a.get('expiry_date')}`\n\n"
                    f"📲 Las nuevas credenciales fueron enviadas automáticamente al WhatsApp del cliente."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                await send_telegram_message(
                    f"✅ <b>REEMPLAZO #C{rid} AUTORIZADO POR ADMIN WHATSAPP</b>\n\n"
                    f"• Cliente: <b>{res.get('client_name')}</b>\n"
                    f"• Servicio: <b>{res.get('platform')}</b>\n"
                    f"• Nueva Cuenta: <code>{new_a.get('email')}</code>\n"
                    f"• Clave: <code>{new_a.get('password')}</code>\n"
                    f"• Entregado al WhatsApp del cliente."
                )
                return JSONResponse({"status": "ok", "action": "report_authorized", "report_id": rid, "details": res})
            elif res.get("out_of_stock"):
                await whatsapp_client.send_text_message(
                    sender_phone,
                    f"⚠️ *SIN STOCK LIBRE PARA REEMPLAZAR (#C{rid})*\n"
                    f"No hay cuentas libres en inventario para esa plataforma. Carga stock en el panel web para proceder."
                )
                return JSONResponse({"status": "ok", "action": "out_of_stock", "report_id": rid})
            elif res.get("already_resolved"):
                await whatsapp_client.send_text_message(sender_phone, f"ℹ️ El reporte #C{rid} ya fue resuelto con anterioridad.")
                return JSONResponse({"status": "ok", "action": "already_resolved", "report_id": rid})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al autorizar reporte #C{rid}: {res.get('error')}")
                return JSONResponse({"status": "error", "error": res.get("error")})

        elif admin_wait_match:
            rid = int(admin_wait_match.group(1))
            res = database.put_fallen_report_on_wait(rid, admin_user=f"WhatsApp Admin (+{sender_phone})")
            if res.get("success"):
                c_phone = res.get("clean_phone")
                if c_phone:
                    await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)

                admin_ack = (
                    f"⏳ *CLIENTE PUESTO EN ESPERA (#C{rid})*\n\n"
                    f"• Cliente: *{res.get('client_name')}*\n"
                    f"• Estado: En cola de atención prioritaria.\n"
                    f"• Se notificó al cliente para que aguarde mientras gestionas la cuenta.\n\n"
                    f"💡 Cuando tengas la cuenta lista, escribe `/cambiar_{rid}` para asignársela automáticamente."
                )
                await whatsapp_client.send_text_message(sender_phone, admin_ack)

                await send_telegram_message(
                    f"⏳ <b>CLIENTE PUESTO EN ESPERA (#C{rid})</b>\n\n"
                    f"• Cliente: <b>{res.get('client_name')}</b>\n"
                    f"• Acción tomada por el administrador desde WhatsApp."
                )
                return JSONResponse({"status": "ok", "action": "report_put_on_wait", "report_id": rid})
            else:
                await whatsapp_client.send_text_message(sender_phone, f"⚠️ Error al poner en espera reporte #C{rid}: {res.get('error')}")
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
            b64_str = ""
            mime = ""
            if media_data:
                b64_str = media_data.get("base64") or (media_data.get("data", {}) if isinstance(media_data.get("data"), dict) else {}).get("base64") or ""
                mime = (media_data.get("mimetype") or (media_data.get("data", {}) if isinstance(media_data.get("data"), dict) else {}).get("mimetype") or "").lower()
            if not b64_str and msg_obj.get("base64"):
                b64_str = msg_obj["base64"]

            if b64_str:
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

                            # Si el cliente escribió palabras explícitas de pago en el caption y contiene datos bancarios
                            if not is_confirmed_receipt and has_receipt_intent:
                                text_info = receipt_service.parse_transfer_receipt_text(text)
                                if text_info and (text_info.get("is_receipt") or (text_info.get("amount") and text_info.get("bank"))):
                                    detected_info = text_info
                                    is_confirmed_receipt = True
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

                    # 1. Probar Google Gemini Vision con criterio estricto de clasificación
                    try:
                        detected_info = await receipt_service.analyze_image_with_gemini(
                            b64_str,
                            mime_type=mime or "image/jpeg"
                        )
                        if detected_info and detected_info.get("is_receipt"):
                            is_confirmed_receipt = True
                            logger.info(f"Gemini confirmó comprobante de pago de {client_name} ({sender_phone}): {detected_info.get('summary')}")
                        elif detected_info and detected_info.get("is_receipt") is False:
                            logger.info(f"Gemini descartó imagen de {client_name} ({sender_phone}): NO es comprobante de pago.")
                            is_confirmed_receipt = False
                    except Exception as e:
                        logger.debug(f"Error analizando imagen con Gemini: {e}")

                    # 2. Respaldo OCR Local con Tesseract si la IA no confirmó el comprobante (ej: si hay anuncios publicitarios al pie de página)
                    if not is_confirmed_receipt and img_bytes:
                        try:
                            ocr_text = receipt_service.extract_text_from_image(img_bytes)
                            if ocr_text:
                                ocr_info = receipt_service.parse_transfer_receipt_text(ocr_text)
                                if ocr_info and ocr_info.get("is_receipt"):
                                    detected_info = ocr_info
                                    is_confirmed_receipt = True
                                    logger.info(f"OCR local confirmó comprobante legítimo de {client_name} ({sender_phone}): {ocr_info.get('summary')}")
                        except Exception as ocr_err:
                            logger.debug(f"Error en OCR local de imagen: {ocr_err}")

                    # 3. Si el cliente escribió palabras explícitas de pago en el caption y hay datos financieros
                    if not is_confirmed_receipt and has_receipt_intent:
                        text_info = receipt_service.parse_transfer_receipt_text(text)
                        if text_info and (text_info.get("is_receipt") or (text_info.get("amount") and text_info.get("bank"))):
                            detected_info = text_info
                            is_confirmed_receipt = True
                            logger.info(f"Comprobante confirmado por intención explícita y datos financieros de {client_name}")

                    # 4. Salvaguarda: Si no fue verificado por IA, OCR o intención explícita con datos financieros,
                    # NUNCA registrar como pago ni enviar acuse falso. Las fotos de productos, juguetes, mascotas o casuales
                    # se ignoran como pago y continúan hacia la atención normal.
        except Exception as e:
            logger.debug(f"No se pudo descargar media de Evolution: {e}")

    # 5. Si es solo texto sin media pero tiene intención explícita y datos financieros
    elif has_receipt_intent:
        text_info = receipt_service.parse_transfer_receipt_text(text)
        if text_info and (text_info.get("is_receipt") or (text_info.get("amount") and text_info.get("bank"))):
            detected_info = text_info
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

        # Validación cruzada contra tarifa esperada para mitigar artefactos OCR '$' -> '5' (ej: 58.000 -> 8.000)
        if target_acc and target_acc.get("price"):
            try:
                raw_digits = re.sub(r'[^\d]', '', str(target_acc.get("price")))
                if raw_digits:
                    expected_price = float(raw_digits)
                    if amount_val == 50000.0 + expected_price:
                        logger.warning(f"Artefacto OCR '$'->'5' detectado ({amount_val} vs esperado {expected_price}). Corrigiendo a {expected_price}")
                        amount_val = expected_price
            except Exception:
                pass

        amount_fmt_val = (format_ars(amount_val) if amount_val > 0 else "") or (detected_info.get("amount_formatted") if detected_info else "")
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
        is_multi = len(active_accs) > 1
        service_lines = ""
        multi_summary = ""
        if is_multi:
            m_list = [f"• <b>{a['platform']}</b>: <code>{a['email']}</code> ({a.get('price') or '-'} | Vence: {a.get('expiry_date') or '-'})" for a in active_accs]
            multi_summary = f"📑 <b>Cliente con {len(active_accs)} servicios activos:</b>\n" + "\n".join(m_list) + "\n\n"
        elif target_acc:
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
        action_buttons = [
            {"text": f"✅ Aprobar (#{payment_id})", "callback_data": f"payapp_{payment_id}"},
            {"text": f"❌ Denegar (#{payment_id})", "callback_data": f"payrej_{payment_id}"}
        ]
        if is_multi:
            action_buttons.insert(1, {"text": f"🔥 Renovar Todas ({len(active_accs)})", "callback_data": f"payapp_all_{payment_id}"})

        kb = {
            "inline_keyboard": [
                action_buttons,
                client_btn + [{"text": "💬 Abrir WhatsApp", "url": f"https://wa.me/{sender_phone}"}]
            ]
        }

        tg_msg = (
            f"🧾 <b>¡NUEVO COMPROBANTE RECIBIDO! (#P{payment_id})</b>\n\n"
            f"• Cliente: <b>{client_name}</b> ({client_tag})\n"
            f"• WhatsApp: <code>{sender_phone}</code>\n"
            f"{service_lines}"
            f"{multi_summary}"
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
            multi_wa = ""
            if is_multi:
                m_lines = [f"  - {a['platform']}: {a['email']} (Vence: {a.get('expiry_date')})" for a in active_accs]
                multi_wa = f"\n📑 *El cliente tiene {len(active_accs)} servicios activos:*\n" + "\n".join(m_lines) + f"\n\n👉 *Para renovar TODOS sus servicios:*\n/pagoapro_{payment_id}_all\n"

            admin_notice = (
                f"🧾 *NUEVO COMPROBANTE RECIBIDO (#P{payment_id})*\n"
                f"• *Cliente:* {client_name} (+{sender_phone})\n"
                f"• *Servicio Principal:* {platform_val or 'Suscripción'}" + (f" ({target_acc['email']})" if target_acc else "") + "\n"
                f"• *Monto Detectado:* {amount_fmt_val or 'No detectado'}" + (f" | *Banco:* {bank_val}" if bank_val else "") + "\n"
                + (f"• *Op:* #{op_val}\n" if op_val else "")
                + multi_wa +
                f"\n👉 *Para APROBAR y renovar servicio:*\n"
                f"/pagoapro_{payment_id}\n\n"
                f"👉 *Para registrar PAGO PARCIAL / SEÑA:*\n"
                f"/pagoparcial_{payment_id} <monto>\n\n"
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
        "/vencimiento", "/vencimientos", "/clave", "/cuenta", "/servicios", "/miservicio", "/miservicios", "/estado", "mi cuenta", "mis cuentas", "mi clave", "mis servicios", "mi servicio"
    ]
    if any(k in text_lower for k in expiry_intents):
        if client_profile and client_profile.get("active_accounts"):
            accs = client_profile["active_accounts"]
            lines = [f"¡Hola {client_name}! 🍿 Aquí tienes el estado de tus servicios activos:\n"]
            for a in accs:
                plat = a.get("platform") or "Servicio"
                perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
                pin = f" | PIN: {a['profile_pin']}" if a.get("profile_pin") else ""
                days_txt = f" ({a.get('days_label')})" if a.get("days_label") else ""

                if plat.lower() == "http custom":
                    hwid_raw = a.get("password") or ""
                    hwid_disp = f"{hwid_raw[:10]}...{hwid_raw[-6:]}" if len(hwid_raw) > 16 else hwid_raw
                    lines.append(
                        f"🌐 *HTTP Custom (VPN / Servidor)*\n"
                        f"👤 Usuario: `{a.get('email')}`\n"
                        f"🔑 HWID: `{hwid_disp}`\n"
                        f"📅 Vence: *{a.get('expiry_date')}*{days_txt}\n"
                    )
                else:
                    lines.append(
                        f"📺 *{plat}*{perf}\n"
                        f"📧 Usuario: `{a.get('email')}`\n"
                        f"🔑 Clave: `{a.get('password')}`{pin}\n"
                        f"📅 Vence: *{a.get('expiry_date')}*{days_txt}\n"
                    )
            lines.append("¡Cualquier consulta o renovación estamos a tu disposición! 🙌✨")
            reply = "\n".join(lines)
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "expiry_info_sent"})
        else:
            reply = (
                f"¡Hola {client_name}! En este momento no registramos suscripciones activas a tu nombre en el sistema. "
                f"Si deseas contratar Netflix, Disney+, Max o servidores HTTP Custom, avísanos y te enviamos las tarifas disponibles."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "no_active_services"})

    # REGLA C: Consulta de Medios de Pago / CBU / Alias / Auto-Atención Financiera Personalizada
    payment_intents = [
        "alias", "cbu", "cvu", "como pago", "cómo pago", "donde pago", "dónde pago",
        "donde transfiero", "dónde transfiero", "datos de pago", "medios de pago",
        "datos para transferir", "datos bancarios", "a que cuenta transfiero",
        "a qué cuenta transfiero", "como te transfiero", "cómo te transfiero",
        "pasame el alias", "pásame el alias", "pasame el cbu", "pásame el cbu",
        "pasa el alias", "pasa el cbu",
        "/pagar", "/datos", "/pago", "/pagos", "/cbu", "/alias", "pagar",
        "cuanto debo", "cuánto debo", "cuanto tengo que pagar", "cuánto tengo que pagar",
        "cuanto es", "cuánto es", "cuanto te debo", "cuánto te debo",
        "precio a transferir", "quiero pagar", "para pagar"
    ]
    if any(k in text_lower for k in payment_intents):
        pm = database.get_formatted_payment_methods()
        active_accs = client_profile.get("active_accounts", []) if client_profile else []

        if active_accs:
            if len(active_accs) == 1:
                acc = active_accs[0]
                plat = acc.get("platform") or "Suscripción"
                perf = f" (Perfil: {acc['profile_name']})" if acc.get("profile_name") else ""
                price_str = acc.get("price_formatted") or database.format_ars(acc.get("price")) or "Consultar"
                exp_date = acc.get("expiry_date") or ""
                days_lbl = f" ({acc.get('days_label')})" if acc.get("days_label") else ""

                reply = (
                    f"¡Hola {client_name}! 🍿 Aquí tienes la información para abonar tu suscripción:\n\n"
                    f"📺 *Servicio:* {plat}{perf}\n"
                    f"💰 *Importe a transferir:* *{price_str}*\n"
                    f"📅 *Vencimiento:* {exp_date}{days_lbl}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 *DATOS DE PAGO OFICIALES:*\n"
                    f"{pm}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📲 Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu renovación de inmediato. ¡Muchas gracias! 🙌✨"
                )
            else:
                total_sum = 0.0
                svc_lines = []
                for a in active_accs:
                    p_num = a.get("price_num", 0.0)
                    if not p_num and a.get("price"):
                        try:
                            p_num = float(re.sub(r'[^\d.]', '', str(a.get("price"))) or 0.0)
                        except Exception:
                            p_num = 0.0
                    total_sum += p_num
                    p_fmt = a.get("price_formatted") or database.format_ars(a.get("price"))
                    v_str = f" | Vence: {a.get('expiry_date')}" if a.get('expiry_date') else ""
                    perf = f" ({a['profile_name']})" if a.get("profile_name") else ""
                    svc_lines.append(f"• *{a.get('platform', 'Servicio')}*{perf}: `{a.get('email')}` - *{p_fmt}*{v_str}")

                services_block = "\n".join(svc_lines)
                total_fmt = database.format_ars(total_sum) if total_sum > 0 else "Consultar"

                reply = (
                    f"¡Hola {client_name}! 🍿 Registramos *{len(active_accs)}* servicios activos a tu nombre:\n\n"
                    f"{services_block}\n\n"
                    f"💰 *TOTAL A TRANSFERIR:* *{total_fmt}*\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 *DATOS DE PAGO OFICIALES:*\n"
                    f"{pm}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📲 Una vez realizada la transferencia, envíanos el comprobante por este chat para acreditar la renovación de tus cuentas. ¡Muchas gracias! 🙌✨"
                )
        else:
            reply = (
                f"¡Hola {client_name}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💳 *DATOS DE PAGO OFICIALES:*\n"
                f"{pm}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💡 Si deseas consultar nuestros precios o contratar un servicio (Netflix, Disney+, Max, etc.), escribe */catalogo* o */precios*.\n"
                f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu pedido. ¡Muchas gracias! 🙌✨"
            )

        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        _AUTO_REPLY_COOLDOWNS[sender_phone] = now
        return JSONResponse({"status": "ok", "action": "payment_info_sent", "active_accounts_count": len(active_accs)})

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

        # 0. Anti-Duplicación: Verificar si el cliente ya tiene un reporte de caída activo ('pending' o 'waiting')
        existing_report = database.find_active_fallen_report_by_phone(sender_phone)
        if existing_report:
            rep_id = existing_report["id"]
            st = existing_report.get("status", "pending")
            database.record_fallen_report_followup(rep_id, text)

            client_reply = (
                f"🛠️ *¡Hola {client_name}!* Tu reporte previo (*#C{rep_id}*) por tu servicio de *{existing_report.get('platform')}* "
                + ("ya se encuentra en cola de atención técnica prioritaria" if st == "waiting" else "está siendo atendido por soporte")
                + f".\n\n📌 Hemos adjuntado tu nuevo mensaje a la solicitud abierta y te avisaremos por aquí apenas la nueva cuenta quede activa. ¡Muchas gracias por tu paciencia! 🙌✨"
            )
            await whatsapp_client.send_text_message(sender_phone, client_reply, delay_seconds=1.5)

            admin_configured = (settings.get("admin_whatsapp") or os.getenv("ADMIN_WHATSAPP", "")).strip()
            clean_admin = database.clean_whatsapp_phone(admin_configured) if admin_configured else ""
            if clean_admin and clean_admin != sender_phone:
                admin_notice = (
                    f"💬 *MENSAJE DE SEGUIMIENTO EN REPORTE ACTIVO (#C{rep_id})*\n"
                    f"• *Cliente:* {client_name} (+{sender_phone})\n"
                    f"• *Servicio:* {existing_report.get('platform')}\n"
                    f"• *Estado actual:* {st.upper()}\n"
                    f"• *Mensaje nuevo:* \"{text}\"\n\n"
                    f"👉 *Para autorizar cambio:* /cambiar_{rep_id}\n"
                    f"👉 *Para poner/mantener en espera:* /esperar_{rep_id}"
                )
                await whatsapp_client.send_text_message(clean_admin, admin_notice)

            _AUTO_REPLY_COOLDOWNS[sender_phone] = now
            return JSONResponse({"status": "ok", "action": "followup_recorded", "report_id": rep_id})

        # 1. Crear el reporte de cuenta caída en el sistema (#C<ID>)
        report = database.create_fallen_report(
            sender_phone=sender_phone,
            client_name=client_name,
            client_id=client_profile.get("client", {}).get("id") if client_profile else None,
            account_id=target_account["id"],
            platform=target_account["platform"],
            account_email=target_account.get("email", ""),
            profile_name=target_account.get("profile_name", ""),
            issue_type="caida",
            raw_message=text
        )
        report_id = report["id"]

        # 2. Consultar stock libre disponible para la plataforma
        free_stock = database.get_free_stock(platform=target_account["platform"])
        free_count = len(free_stock)
        stock_status_lbl = f"{free_count} cuenta(s) libre(s)" if free_count > 0 else "⚠️ SIN STOCK LIBRE"

        # 3. Respuesta inmediata de acuse de recibo y tranquilidad al CLIENTE
        client_reply = (
            f"🛠️ *¡Hola {client_name}!* 🙌 Hemos recibido tu reporte sobre el inconveniente con tu servicio de *{target_account['platform']}* (Reporte #C{report_id}).\n\n"
            f"Nuestro equipo técnico ya está revisando tu caso para brindarte una solución a la brevedad por este medio. ¡Muchas gracias por tu paciencia! ✨"
        )
        await whatsapp_client.send_text_message(sender_phone, client_reply, delay_seconds=1.5)

        # 4. Notificación y pedido de AUTORIZACIÓN al WhatsApp Privado del Administrador
        admin_configured = (settings.get("admin_whatsapp") or os.getenv("ADMIN_WHATSAPP", "")).strip()
        clean_admin = database.clean_whatsapp_phone(admin_configured) if admin_configured else ""
        if clean_admin and clean_admin != sender_phone:
            profile_extra = f" (Perfil: {target_account.get('profile_name')})" if target_account.get('profile_name') else ""
            admin_notice = (
                f"🚨 *REPORTE DE CUENTA CAÍDA (#C{report_id})*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Cliente:* {client_name} (+{sender_phone})\n"
                f"📺 *Servicio:* *{target_account['platform']}*\n"
                f"📧 *Cuenta afectada:* `{target_account.get('email')}`{profile_extra}\n"
                f"💬 *Mensaje del cliente:* \"{text}\"\n"
                f"📦 *Stock libre {target_account['platform']}:* {stock_status_lbl}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 *Para AUTORIZAR y cambiarle la cuenta al instante:*\n"
                f"/cambiar_{report_id}\n\n"
                f"👉 *Para avisarle que ESPERE mientras le consigues la cuenta:*\n"
                f"/esperar_{report_id}"
            )
            await whatsapp_client.send_text_message(clean_admin, admin_notice)

        # 5. Notificación interactiva con botones a Telegram
        kb = {
            "inline_keyboard": [
                [
                    {"text": f"🔄 Autorizar y Cambiar (#C{report_id})", "callback_data": f"fallapp_{report_id}"},
                    {"text": f"⏳ Poner en Espera (#C{report_id})", "callback_data": f"fallwait_{report_id}"}
                ],
                [
                    {"text": f"👤 Ficha {client_name}", "callback_data": f"client_{target_account.get('client_id')}"} if target_account.get("client_id") else {"text": "📦 Stock", "callback_data": "menu_stock"},
                    {"text": "💬 Abrir WhatsApp", "url": f"https://wa.me/{sender_phone}"}
                ]
            ]
        }
        tg_msg = (
            f"🚨 <b>REPORTE DE CUENTA CAÍDA (#C{report_id})</b>\n\n"
            f"• Cliente: <b>{client_name}</b> (<code>+{sender_phone}</code>)\n"
            f"• Servicio: <b>{target_account['platform']}</b>\n"
            f"• Cuenta afectada: <code>{target_account.get('email')}</code>\n"
            f"• Mensaje: <i>\"{text}\"</i>\n"
            f"• Stock libre disponible: <b>{stock_status_lbl}</b>\n\n"
            f"👉 <i>Selecciona una acción para responder al cliente:</i>"
        )
        await send_telegram_message(tg_msg, reply_markup=kb)

        _AUTO_REPLY_COOLDOWNS[sender_phone] = now
        return JSONResponse({"status": "ok", "action": "fallen_report_created", "report_id": report_id})

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


