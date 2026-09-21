import os
from typing import Optional, List
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from core.security import verify_session_cookie
from application.channels.broadcast_service import broadcast_announcement

router = APIRouter(tags=["Channels"])


class BroadcastRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=150, description="Título del comunicado o promoción")
    message: str = Field(..., min_length=5, description="Cuerpo del mensaje")
    category: str = Field("promo", description="Categoría: promo, stock, mantenimiento, comunicado")
    platforms: Optional[List[str]] = Field(default=None, description="Plataformas asociadas")
    send_whatsapp: bool = Field(True, description="Enviar a Canal o Grupo de WhatsApp")
    whatsapp_target: Optional[str] = Field(None, description="JID destino (@newsletter o @g.us)")
    send_telegram: bool = Field(True, description="Enviar a Canal de Telegram")
    telegram_target: Optional[str] = Field(None, description="Canal destino (@canal o ID)")
    custom_cta: Optional[str] = Field(None, description="Llamado a la acción personalizado")


@router.post("/api/channels/broadcast")
async def api_broadcast_announcement(
    request: Request,
    payload: Optional[BroadcastRequest] = None,
    title: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
    category: str = Form("promo"),
    send_whatsapp: Optional[str] = Form(None),
    whatsapp_target: Optional[str] = Form(None),
    send_telegram: Optional[str] = Form(None),
    telegram_target: Optional[str] = Form(None)
):
    """Difunde un anuncio o promoción flash en Canales de WhatsApp y Telegram simultáneamente con 1 clic."""
    # 1. Autenticación de administrador (Cookie de sesión o Bearer Token)
    session_token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    user = verify_session_cookie(session_token) or (verify_session_cookie(bearer_token) if bearer_token else None)

    if not user:
        raise HTTPException(status_code=401, detail="Se requiere autenticación de administrador para difusión en canales.")

    # 2. Parsear parámetros (JSON o Form)
    if payload:
        b_title = payload.title
        b_message = payload.message
        b_category = payload.category
        b_platforms = payload.platforms
        b_send_wa = payload.send_whatsapp
        b_wa_target = payload.whatsapp_target
        b_send_tg = payload.send_telegram
        b_tg_target = payload.telegram_target
        b_cta = payload.custom_cta
    else:
        if not title or not message:
            raise HTTPException(status_code=400, detail="Título y mensaje son requeridos")
        b_title = title
        b_message = message
        b_category = category
        b_platforms = None
        b_send_wa = send_whatsapp is not None and send_whatsapp not in ("0", "false", "off")
        b_wa_target = whatsapp_target
        b_send_tg = send_telegram is not None and send_telegram not in ("0", "false", "off")
        b_tg_target = telegram_target
        b_cta = None

    # 3. Despachar difusión
    res = await broadcast_announcement(
        title=b_title,
        message=b_message,
        category=b_category,
        platforms=b_platforms,
        send_whatsapp=b_send_wa,
        whatsapp_target=b_wa_target,
        send_telegram=b_send_tg,
        telegram_target=b_tg_target,
        actor=str(user),
        custom_cta=b_cta
    )

    if "text/html" in request.headers.get("accept", "") and not payload:
        status_msg = "broadcast_sent" if res.get("success") else "broadcast_failed"
        return RedirectResponse(url=f"/?msg={status_msg}#channels", status_code=303)

    return JSONResponse(status_code=200 if res.get("success") else 400, content=res)


@router.get("/api/channels/targets")
async def api_get_channel_targets(request: Request):
    """Devuelve los canales predeterminados configurados para difusión."""
    session_token = request.cookies.get("session_token")
    user = verify_session_cookie(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")

    return {
        "whatsapp_broadcast_target": os.getenv("WHATSAPP_BROADCAST_TARGET", ""),
        "telegram_channel_id": os.getenv("TELEGRAM_CHANNEL_ID", ""),
        "categories": ["promo", "stock", "mantenimiento", "comunicado"]
    }
