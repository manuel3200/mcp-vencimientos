from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from core.principal import Principal, authenticate_request, require_scope
from db.repositories.finance_repo import get_profitability_by_platform
from services.finance_service import get_financial_balance

router = APIRouter(tags=["Finance"])


def authorize_finance(request: Request) -> Principal:
    """Autoriza lectura financiera exigiendo sesión administrativa o token de servicio con scope 'finance:read' (V07).
    Rechaza contraseñas de administrador, claves de sesión y secretos de webhook.
    """
    principal = authenticate_request(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Se requiere sesión válida o token de servicio dedicado con permiso finance:read.",
        )
    try:
        require_scope(principal, "finance:read")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return principal


@router.get("/api/finance/profitability-by-platform")
async def api_get_profitability_by_platform(request: Request, platform: Optional[str] = None):
    """Retorna el informe consolidado de rentabilidad neta real por plataforma en Pesos Argentinos (ARS)."""
    authorize_finance(request)
    data = get_profitability_by_platform(target_platform=platform)
    return JSONResponse(status_code=200, content={"status": "success", "platforms": data})


@router.get("/api/finance/balance")
async def api_get_finance_balance(request: Request, period: str = "mes_actual"):
    """Retorna el balance financiero global de ingresos, costos y proyección."""
    authorize_finance(request)
    balance = get_financial_balance(period=period)
    return JSONResponse(status_code=200, content={"status": "success", "balance": balance})
