from typing import Optional, List
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.security import verify_session_cookie
from application.community.polls_service import create_and_dispatch_poll
from application.community.scheduled_broadcast_service import (
    run_monday_rules_broadcast,
    run_friday_weekend_promo_broadcast
)
from application.community.word_filter_service import inspect_community_message

router = APIRouter(tags=["Community"])


class PollRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=200, description="Pregunta de la encuesta")
    options: List[str] = Field(..., min_items=2, max_items=12, description="Opciones de respuesta")
    send_whatsapp: bool = Field(True, description="Enviar encuesta a grupo de WhatsApp")
    whatsapp_target: Optional[str] = Field(None, description="JID del grupo de WhatsApp")
    send_telegram: bool = Field(True, description="Enviar encuesta a Telegram")
    telegram_target: Optional[str] = Field(None, description="Canal o chat de Telegram")
    selectable_count: int = Field(1, ge=1, le=5, description="Cantidad máxima de opciones elegibles")


@router.post("/api/community/polls/send")
async def api_send_community_poll(request: Request, payload: PollRequest):
    """Crea y despacha una encuesta interactiva nativa a WhatsApp y Telegram."""
    session_token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    user = verify_session_cookie(session_token) or (verify_session_cookie(bearer_token) if bearer_token else None)

    if not user:
        raise HTTPException(status_code=401, detail="Se requiere autenticación de administrador.")

    res = await create_and_dispatch_poll(
        question=payload.question,
        options=payload.options,
        send_whatsapp=payload.send_whatsapp,
        whatsapp_target=payload.whatsapp_target,
        send_telegram=payload.send_telegram,
        telegram_target=payload.telegram_target,
        selectable_count=payload.selectable_count,
        actor=str(user)
    )
    return JSONResponse(status_code=200 if res.get("success") else 400, content=res)


@router.post("/api/community/broadcasts/trigger")
async def api_trigger_scheduled_broadcast(
    request: Request,
    broadcast_type: str = Form("monday_rules"),
    target_group: Optional[str] = Form(None)
):
    """Dispara a demanda un comunicado programado de la comunidad (lunes normas o viernes liquidación)."""
    session_token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    user = verify_session_cookie(session_token) or (verify_session_cookie(bearer_token) if bearer_token else None)

    if not user:
        raise HTTPException(status_code=401, detail="Se requiere autenticación de administrador.")

    targets = [target_group.strip()] if target_group and target_group.strip() else None

    if broadcast_type.lower() == "friday_promo":
        res = await run_friday_weekend_promo_broadcast(target_groups=targets, actor=str(user))
    else:
        res = await run_monday_rules_broadcast(target_groups=targets, actor=str(user))

    return JSONResponse(status_code=200 if res.get("success") else 400, content=res)


@router.post("/api/community/moderation/check")
async def api_check_moderation(
    request: Request,
    text: str = Form(...),
    sender_phone: str = Form(""),
    group_jid: str = Form("")
):
    """Inspecciona un texto para detectar patrones de estafa, enlaces no autorizados o lenguaje tóxico."""
    session_token = request.cookies.get("session_token")
    user = verify_session_cookie(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")

    report = inspect_community_message(text=text, sender_phone=sender_phone, group_jid=group_jid)
    return JSONResponse(status_code=200, content=report)
