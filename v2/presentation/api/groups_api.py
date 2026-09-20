import logging
from typing import Optional, Any, Dict
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

import database
from core.security import verify_session_cookie
from infrastructure.external.evolution_whatsapp.client import whatsapp_client

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
            jid = g.get("id") or g.get("jid")
            if not jid or "@g.us" not in jid:
                continue
            name = g.get("subject") or g.get("name") or jid
            saved = database.upsert_group_config(
                group_jid=jid,
                group_name=name
            )
            synced.append(saved)

        logger.info(f"Sincronizados {len(synced)} grupos desde Evolution API")
        return JSONResponse({
            "status": "ok",
            "count": len(synced),
            "groups": database.list_groups_config()
        })
    except Exception as e:
        logger.error(f"Error sincronizando grupos: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


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
