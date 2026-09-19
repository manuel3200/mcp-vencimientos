from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
from core.security import verify_session_cookie

router = APIRouter()

@router.post("/api/suppliers/save")
async def api_save_supplier(
    request: Request,
    name: str = Form(...),
    contact: str = Form(""),
    payment_info: str = Form(""),
    notes: str = Form(""),
    supplier_id: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    sid = int(supplier_id) if supplier_id and supplier_id.strip() else None
    database.save_supplier(
        supplier_id=sid,
        name=name.strip(),
        contact=contact.strip(),
        payment_info=payment_info.strip(),
        notes=notes.strip()
    )
    return RedirectResponse(url="/?msg=supplier_saved#suppliers", status_code=303)

@router.post("/api/suppliers/delete/{supplier_id}")
async def api_delete_supplier(supplier_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_supplier(supplier_id)
    return RedirectResponse(url="/?msg=supplier_deleted#suppliers", status_code=303)

@router.post("/api/master-accounts/renew")
async def api_renew_master(
    request: Request,
    email: str = Form(...),
    platform: str = Form(...),
    new_supplier_expiry: str = Form(...),
    cost: float = Form(0.0),
    payment_method: str = Form("Transferencia"),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.renew_master_account(
        email=email.strip(),
        platform=platform.strip(),
        new_supplier_expiry=new_supplier_expiry.strip(),
        cost=cost,
        payment_method=payment_method.strip(),
        notes=notes.strip()
    )
    return RedirectResponse(url="/?msg=master_renewed#suppliers", status_code=303)

# API Logs y Diagnóstico del Sistema