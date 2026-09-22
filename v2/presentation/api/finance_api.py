from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from core.config import settings
from core.security import verify_session_cookie
from db.repositories.finance_repo import get_profitability_by_platform
from services.finance_service import get_financial_balance

router = APIRouter(tags=["Finance"])


def _authenticate_admin_or_service(request: Request) -> bool:
    """Valida sesión activa de navegador o token de servicio para n8n/microservicios."""
    session_token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    x_api_key = request.headers.get("X-API-KEY", "").strip()

    token_candidate = bearer_token or x_api_key
    if token_candidate:
        if (
            token_candidate == getattr(settings, "SESSION_SECRET_KEY", "") or
            token_candidate == getattr(settings, "ADMIN_PASSWORD", "") or
            (getattr(settings, "WEBHOOK_SECRET", "") and token_candidate == settings.WEBHOOK_SECRET)
        ):
            return True

    user = verify_session_cookie(session_token) or (verify_session_cookie(bearer_token) if bearer_token else None)
    return bool(user)


@router.get("/api/finance/profitability-by-platform")
async def api_get_profitability_by_platform(request: Request, platform: Optional[str] = None):
    """Retorna el informe consolidado de rentabilidad neta real por plataforma en Pesos Argentinos (ARS)."""
    if not _authenticate_admin_or_service(request):
        raise HTTPException(status_code=401, detail="Se requiere autenticación de administrador.")

    data = get_profitability_by_platform(target_platform=platform)
    return JSONResponse(status_code=200, content={"status": "success", "platforms": data})


@router.get("/api/finance/balance")
async def api_get_finance_balance(request: Request, period: str = "mes_actual"):
    """Retorna el balance financiero global de ingresos, costos y proyección."""
    if not _authenticate_admin_or_service(request):
        raise HTTPException(status_code=401, detail="Se requiere autenticación de administrador.")


    balance = get_financial_balance(period=period)
    return JSONResponse(status_code=200, content={"status": "success", "balance": balance})
