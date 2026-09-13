import urllib.parse
import json
from typing import Optional, List, Dict, Any, Union

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

import database
from core.security import verify_session_cookie

router = APIRouter()

@router.post("/api/catalog/save")
async def api_catalog_save(
    request: Request,
    platform: str = Form(...),
    service_type: str = Form("pantalla"),
    cost_price: float = Form(0.0),
    price_final: float = Form(0.0),
    price_reseller: float = Form(0.0),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.upsert_catalog_price(
        platform=platform,
        service_type=service_type,
        cost_price=cost_price,
        price_final=price_final,
        price_reseller=price_reseller,
        notes=notes
    )
    return RedirectResponse(url="/?msg=catalog_saved#tab-catalog", status_code=302)

@router.post("/api/catalog/delete/{price_id}")
async def api_catalog_delete(price_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_catalog_price(price_id)
    return RedirectResponse(url="/?msg=catalog_deleted#tab-catalog", status_code=302)

@router.post("/api/combos/save")
async def api_combos_save(
    request: Request,
    name: str = Form(...),
    platforms: str = Form(...),
    price_final: float = Form(...),
    price_reseller: float = Form(...),
    description: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    plat_list = [p.strip() for p in platforms.split(",") if p.strip()]
    database.create_or_update_combo(
        name=name,
        description=description,
        price_final=price_final,
        price_reseller=price_reseller,
        platforms=plat_list
    )
    return RedirectResponse(url="/?msg=combo_saved#tab-catalog", status_code=302)

@router.post("/api/combos/delete/{combo_id}")
async def api_combos_delete(combo_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_combo(combo_id)
    return RedirectResponse(url="/?msg=combo_deleted#tab-catalog", status_code=302)

@router.post("/api/combos/sell")
async def api_combos_sell(
    request: Request,
    combo_id: int = Form(...),
    client_name: str = Form(...),
    whatsapp: str = Form(""),
    telegram: str = Form(""),
    client_type: str = Form("consumidor_final"),
    payment_method: str = Form("Transferencia"),
    duration_days: int = Form(30),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.sell_combo(
        combo_name_or_id=combo_id,
        client_name=client_name,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=client_type,
        payment_method=payment_method,
        duration_days=duration_days,
        notes=notes
    )
    if res.get("success"):
        wa_url = res.get("wa_link", "")
        wa_text = res.get("whatsapp_message", "")
        wa_auto_sent = False
        if whatsapp:
            wa_settings = database.get_whatsapp_api_settings()
            if wa_settings.get("auto_send_sales") == 1:
                try:
                    wa_st = await whatsapp_client.check_connection_status()
                    if wa_st.get("connected"):
                        send_res = await whatsapp_client.send_text_message(whatsapp, wa_text, delay_seconds=2.0)
                        if send_res.get("success"):
                            wa_auto_sent = True
                            logger.info(f"Accesos de combo despachados automáticamente a {whatsapp}")
                except Exception as e:
                    logger.error(f"Error despachando combo automáticamente por WhatsApp: {e}")

        wa_info_telegram = "\n📲 <b>WhatsApp:</b> ✅ Entregado automáticamente al cliente" if wa_auto_sent else f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR ACCESOS POR WHATSAPP (1 Clic)</b></a>"
        await send_telegram_message(
            f"🎉 <b>¡Combo Vendido desde el Panel Web!</b>\n\n"
            f"• Pack: <b>{res['combo_name']}</b>\n"
            f"• Cliente: {res['client_name']} ({client_type})\n"
            f"• Total Cobrado: <b>{database.format_ars(res['amount'])}</b>\n"
            f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
            f"• Cuentas asignadas: {len(res['accounts'])}\n"
            f"• Vencimiento: <code>{res['expiry_date']}</code>"
            f"{wa_info_telegram}"
        )
        return RedirectResponse(url=f"/?msg=combo_sold&wa={urllib.parse.quote(wa_url)}#tab-active", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error al vender combo"))
        return RedirectResponse(url=f"/?err={err}#tab-catalog", status_code=302)
