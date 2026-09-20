"""
referrals_api.py - Router API REST para Programa de Referidos y Fidelización (StreamVault v2)
Permite:
- Crear/asignar códigos únicos de referido a clientes.
- Acreditar comisiones y saldo bonificado a favor.
- Canjear saldo de la billetera de referidos.
- Consultar estadísticas y listas de referidos.
"""

import logging
from typing import Optional, Any, Dict
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

import database
from core.security import verify_session_cookie

logger = logging.getLogger("routers.referrals")
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


@router.get("/api/referrals/list")
async def api_list_referrals(request: Request):
    """Retorna la lista de códigos de referidos y estadísticas globales."""
    _check_auth(request)
    referrals = database.list_all_referral_codes()
    stats = database.get_referrals_overview_stats()
    return JSONResponse({
        "status": "ok",
        "referrals": referrals,
        "stats": stats
    })


@router.post("/api/referrals/create")
async def api_create_referral_code(request: Request):
    """Crea o asigna un código de referido a un cliente."""
    _check_auth(request)
    payload = await _extract_payload(request)
    
    raw_client_id = payload.get("client_id")
    if not raw_client_id:
        raise HTTPException(status_code=400, detail="Debe seleccionar un cliente válido.")

    try:
        client_id = int(raw_client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cliente inválido.")

    custom_code = (payload.get("custom_code") or "").strip()
    
    result = database.get_or_create_client_referral_code(client_id, custom_code=custom_code if custom_code else None)
    if not result:
        raise HTTPException(status_code=500, detail="No se pudo generar el código de referidos.")

    code = result.get("code", "")
    msg = f"Código de referido {code} asignado con éxito."

    accept_header = request.headers.get("accept", "")
    if "application/json" not in accept_header and "application/json" not in request.headers.get("content-type", ""):
        return RedirectResponse(url=f"/?msg={msg}#referrals", status_code=303)

    return JSONResponse({
        "status": "ok",
        "message": msg,
        "referral": result
    })


@router.post("/api/referrals/credit")
async def api_credit_referral(request: Request):
    """Acredita manualmente una bonificación de saldo a un recomendador."""
    _check_auth(request)
    payload = await _extract_payload(request)

    raw_referrer_id = payload.get("referrer_client_id") or payload.get("client_id")
    if not raw_referrer_id:
        raise HTTPException(status_code=400, detail="Debe indicar el cliente recomendador.")

    try:
        referrer_id = int(raw_referrer_id)
        referred_id = int(payload.get("referred_client_id") or 0)
        amount = float(payload.get("amount") or payload.get("reward_amount") or 0.0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Parámetros numéricos inválidos.")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="El monto a acreditar debe ser mayor a cero.")

    # Asegurar que el cliente tenga código antes de bonificarle
    database.get_or_create_client_referral_code(referrer_id)

    success = database.credit_referral_reward(referrer_id, referred_id, amount)
    if not success:
        raise HTTPException(status_code=400, detail="No se pudo acreditar la bonificación (posible auto-referido o error interno).")

    msg = f"Se bonificaron ${amount:,.2f} ARS al cliente recomendador.".replace(",", "X").replace(".", ",").replace("X", ".")

    accept_header = request.headers.get("accept", "")
    if "application/json" not in accept_header and "application/json" not in request.headers.get("content-type", ""):
        return RedirectResponse(url=f"/?msg={msg}#referrals", status_code=303)

    return JSONResponse({
        "status": "ok",
        "message": msg
    })


@router.post("/api/referrals/redeem")
async def api_redeem_referral(request: Request):
    """Canjea parte o la totalidad del saldo a favor de referidos de un cliente."""
    _check_auth(request)
    payload = await _extract_payload(request)

    raw_client_id = payload.get("client_id")
    if not raw_client_id:
        raise HTTPException(status_code=400, detail="Debe indicar el cliente para el canje.")

    try:
        client_id = int(raw_client_id)
        amount = float(payload.get("amount") or 0.0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Parámetros numéricos inválidos.")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="El monto a canjear debe ser mayor a cero.")

    ok, message, new_balance = database.redeem_referral_balance(client_id, amount)
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    accept_header = request.headers.get("accept", "")
    if "application/json" not in accept_header and "application/json" not in request.headers.get("content-type", ""):
        return RedirectResponse(url=f"/?msg={message}#referrals", status_code=303)

    return JSONResponse({
        "status": "ok",
        "message": message,
        "new_balance": new_balance
    })
