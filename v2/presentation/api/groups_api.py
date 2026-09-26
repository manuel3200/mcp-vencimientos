import logging
import re
from typing import Optional, Any, Dict
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

import database
from core.security import verify_session_cookie
import whatsapp_client

logger = logging.getLogger("routers.groups")
router = APIRouter()

def _check_auth(request: Request) -> Any:
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    return user

async def _extract_payload(request: Request) -> Dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    form = await request.form()
    return dict(form)


@router.get("/api/groups")
async def api_get_groups(request: Request):
    """Retorna la lista de grupos configurados, modo del bot y lista de baneos silenciosos."""
    _check_auth(request)
    return JSONResponse({
        "status": "ok",
        "bot_mode": database.get_bot_mode(),
        "groups": database.list_groups_config(),
        "silent_bans": database.list_silent_bans()
    })


@router.post("/api/groups/sync")
async def api_sync_groups(request: Request):
    """Sincroniza los grupos de WhatsApp desde Evolution API y los registra en la base de datos."""
    _check_auth(request)
    try:
        raw_groups = await whatsapp_client.fetch_all_groups(get_participants=False)
        synced = []
        for g in raw_groups:
            jid = g.get("id") or g.get("jid") or g.get("remoteJid") or g.get("groupJid")
            if not jid:
                continue
            jid_str = str(jid).strip()
            if "@g.us" not in jid_str:
                if jid_str.isdigit() and len(jid_str) >= 10:
                    jid_str = f"{jid_str}@g.us"
                else:
                    continue
            name = g.get("subject") or g.get("name") or g.get("pushName") or g.get("notify") or jid_str
            saved = database.upsert_group_config(
                group_jid=jid_str,
                group_name=str(name).strip()
            )
            synced.append(saved)

        logger.info(f"Sincronizados {len(synced)} grupos desde Evolution API")
        return JSONResponse({
            "status": "ok",
            "count": len(synced),
            "groups": database.list_groups_config()
        })
    except Exception as e:
        import secrets
        incident_id = f"INC-{secrets.token_hex(4).upper()}"
        logger.error(f"[{incident_id}] Error sincronizando grupos: {e}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": f"Error interno sincronizando grupos ({incident_id})", "incident_id": incident_id},
            status_code=500,
        )


@router.post("/api/groups/add-manual")
async def api_add_group_manual(request: Request):
    """Agrega manualmente un grupo a la lista blanca usando un enlace de invitación o JID."""
    _check_auth(request)
    payload = await _extract_payload(request)
    target = str(payload.get("target", "")).strip()
    custom_name = str(payload.get("group_name", "")).strip()
    bot_enabled_raw = payload.get("bot_enabled", True)
    bot_enabled = 1 if (bot_enabled_raw is True or bot_enabled_raw in (1, "1", "true", "True", "on")) else 0
    antilink_raw = payload.get("antilink_enabled", False)
    antilink_enabled = 1 if (antilink_raw is True or antilink_raw in (1, "1", "true", "True", "on")) else 0

    if not target:
        raise HTTPException(status_code=400, detail="Debes ingresar un enlace de invitación o JID del grupo.")

    resolved_jid = ""
    resolved_name = custom_name

    # 1. Si es un enlace de invitación o código (ej: chat.whatsapp.com/...)
    invite_code_match = re.search(r'(?:chat\.whatsapp\.com\/)([a-zA-Z0-9_-]+)', target)
    if invite_code_match or (not "@" in target and len(target) in (20, 21, 22, 23, 24, 25)):
        code = invite_code_match.group(1) if invite_code_match else target
        logger.info(f"Intentando resolver grupo por código de invitación: {code}")

        # Consultar metadatos del código de invitación en Evolution API
        info = await whatsapp_client.find_group_info_from_invite_code(code)
        if info:
            resolved_jid = info.get("id") or info.get("jid") or info.get("groupJid") or ""
            if not resolved_name:
                resolved_name = info.get("subject") or info.get("name") or ""

        # Si aún no se resolvió el JID, intentar unirse al grupo para obtener el JID
        if not resolved_jid:
            join_res = await whatsapp_client.accept_group_invite_code(code)
            if join_res and isinstance(join_res, dict):
                resolved_jid = join_res.get("id") or join_res.get("jid") or join_res.get("groupJid") or ""
                if not resolved_name:
                    resolved_name = join_res.get("subject") or join_res.get("name") or ""

    # 2. Si es un JID directo o numérico
    if not resolved_jid:
        clean_target = target.strip()
        if "@g.us" in clean_target:
            resolved_jid = clean_target
        elif clean_target.isdigit() and len(clean_target) >= 10:
            resolved_jid = f"{clean_target}@g.us"

    if not resolved_jid or "@g.us" not in resolved_jid:
        raise HTTPException(
            status_code=400,
            detail="No se pudo resolver el JID del grupo desde el enlace. Ingresa el JID directamente (ej: 120363...@g.us) o envía un mensaje en el grupo para detección automática."
        )

    # 3. Si no tenemos nombre, consultar a Evolution API por find_group_info
    if not resolved_name:
        try:
            grp_meta = await whatsapp_client.find_group_info(resolved_jid)
            if grp_meta and isinstance(grp_meta, dict):
                resolved_name = grp_meta.get("subject") or grp_meta.get("name") or ""
        except Exception as e:
            logger.warning(f"No se pudo consultar nombre para {resolved_jid}: {e}")

    if not resolved_name:
        resolved_name = f"Grupo {resolved_jid.split('@')[0]}"

    saved = database.upsert_group_config(
        group_jid=resolved_jid,
        group_name=resolved_name,
        bot_enabled=bot_enabled,
        antilink_enabled=antilink_enabled
    )

    logger.info(f"Grupo agregado manualmente: {resolved_jid} ({resolved_name})")
    return JSONResponse({
        "status": "ok",
        "message": f"Grupo '{resolved_name}' agregado correctamente.",
        "group": saved,
        "groups": database.list_groups_config()
    })


@router.post("/api/groups/delete")
async def api_delete_group(request: Request):
    """Elimina un grupo de la lista de configuración."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    if not group_jid:
        raise HTTPException(status_code=400, detail="group_jid requerido")

    deleted = database.delete_group_config(group_jid)
    return JSONResponse({
        "status": "ok",
        "group_jid": group_jid,
        "deleted": deleted,
        "groups": database.list_groups_config()
    })



@router.post("/api/groups/toggle-bot")
async def api_toggle_bot(request: Request):
    """Habilita o deshabilita la atención del bot en un grupo específico."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    enabled_raw = payload.get("enabled")
    if not group_jid:
        raise HTTPException(status_code=400, detail="group_jid requerido")

    enabled = bool(enabled_raw is True or enabled_raw in (1, "1", "true", "True"))
    database.set_group_bot_enabled(group_jid, enabled)
    return JSONResponse({
        "status": "ok",
        "group_jid": group_jid,
        "bot_enabled": enabled
    })


@router.post("/api/groups/toggle-antilink")
async def api_toggle_antilink(request: Request):
    """Activa o desactiva la protección antilink en un grupo."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    enabled_raw = payload.get("enabled")
    action = str(payload.get("action", "delete")).strip()
    if not group_jid:
        raise HTTPException(status_code=400, detail="group_jid requerido")

    enabled = bool(enabled_raw is True or enabled_raw in (1, "1", "true", "True"))
    database.set_group_antilink(group_jid, enabled, action=action)
    return JSONResponse({
        "status": "ok",
        "group_jid": group_jid,
        "antilink_enabled": enabled,
        "action": action
    })


@router.post("/api/groups/toggle-welcome")
async def api_toggle_welcome(request: Request):
    """Activa o desactiva el mensaje de bienvenida para un grupo."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    enabled_raw = payload.get("enabled")
    if not group_jid:
        raise HTTPException(status_code=400, detail="group_jid requerido")

    enabled_val = 1 if (enabled_raw is True or enabled_raw in (1, "1", "true", "True")) else 0
    updated = database.upsert_group_config(group_jid=group_jid, welcome_enabled=enabled_val)
    return JSONResponse({
        "status": "ok",
        "group_jid": group_jid,
        "welcome_enabled": bool(enabled_val),
        "group": updated
    })


@router.post("/api/groups/save-templates")
async def api_save_group_templates(request: Request):
    """Guarda los mensajes de bienvenida y despedida personalizados para un grupo."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    welcome_msg = str(payload.get("welcome_message", "")).strip()
    goodbye_msg = str(payload.get("goodbye_message", "")).strip()
    if not group_jid:
        raise HTTPException(status_code=400, detail="group_jid requerido")

    updated = database.upsert_group_config(
        group_jid=group_jid,
        welcome_message=welcome_msg,
        goodbye_message=goodbye_msg
    )
    return JSONResponse({
        "status": "ok",
        "group_jid": group_jid,
        "group": updated
    })


@router.post("/api/groups/action")
async def api_group_action(request: Request):
    """Ejecuta una acción administrativa sobre el grupo (cerrar/abrir, link, tagall, etc.)."""
    _check_auth(request)
    payload = await _extract_payload(request)
    group_jid = str(payload.get("group_jid", "")).strip()
    action = str(payload.get("action", "")).strip().lower()
    if not group_jid or not action:
        raise HTTPException(status_code=400, detail="group_jid y action son requeridos")

    if action in ("mute", "cerrar"):
        res = await whatsapp_client.update_group_setting(group_jid, "announcement")
        return JSONResponse({"status": "ok", "action": "mute", "details": res})

    elif action in ("unmute", "abrir"):
        res = await whatsapp_client.update_group_setting(group_jid, "not_announcement")
        return JSONResponse({"status": "ok", "action": "unmute", "details": res})

    elif action in ("link", "enlace"):
        link_str = await whatsapp_client.get_group_invite_code(group_jid)
        return JSONResponse({"status": "ok", "action": "link", "link": link_str})

    elif action in ("tagall", "mencionar"):
        msg = str(payload.get("message", "Aviso importante")).strip()
        res = await whatsapp_client.send_group_tagall(group_jid, message=msg, sender_name="Admin")
        return JSONResponse({"status": "ok", "action": "tagall", "details": res})

    elif action in ("kick", "remove"):
        participant = str(payload.get("participant", "")).strip()
        if not participant:
            raise HTTPException(status_code=400, detail="participant requerido")
        res = await whatsapp_client.update_group_participant(group_jid, "remove", [participant])
        return JSONResponse({"status": "ok", "action": "kick", "details": res})

    elif action in ("promote", "demote"):
        participant = str(payload.get("participant", "")).strip()
        if not participant:
            raise HTTPException(status_code=400, detail="participant requerido")
        res = await whatsapp_client.update_group_participant(group_jid, action, [participant])
        return JSONResponse({"status": "ok", "action": action, "details": res})

    else:
        raise HTTPException(status_code=400, detail=f"Acción desconocida: {action}")


@router.post("/api/moderation/bot-mode")
async def api_set_bot_mode(request: Request):
    """Establece el modo global de operación del bot (public / private / self)."""
    _check_auth(request)
    payload = await _extract_payload(request)
    mode = str(payload.get("mode", "public")).strip().lower()
    new_mode = database.set_bot_mode(mode)
    return JSONResponse({"status": "ok", "bot_mode": new_mode})


@router.post("/api/moderation/silent-ban")
async def api_add_silent_ban(request: Request):
    """Agrega un número o grupo al baneo silencioso."""
    _check_auth(request)
    payload = await _extract_payload(request)
    target_id = str(payload.get("target_id", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id requerido")

    success = database.add_silent_ban(target_id=target_id, reason=reason)
    return JSONResponse({
        "status": "ok" if success else "error",
        "target_id": target_id,
        "bans": database.list_silent_bans()
    })


@router.post("/api/moderation/unban")
async def api_remove_silent_ban(request: Request):
    """Remueve un número o grupo del baneo silencioso."""
    _check_auth(request)
    payload = await _extract_payload(request)
    target_id = str(payload.get("target_id", "")).strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id requerido")

    removed = database.remove_silent_ban(target_id)
    return JSONResponse({
        "status": "ok",
        "target_id": target_id,
        "removed": removed,
        "bans": database.list_silent_bans()
    })
