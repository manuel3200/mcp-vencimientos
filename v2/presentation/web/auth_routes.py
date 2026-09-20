import os
import secrets
import pyotp
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

import database
from core.security import (
    create_session_cookie,
    verify_session_cookie,
    create_preauth_cookie,
    verify_preauth_cookie
)
from core.rate_limiter import auth_rate_limiter
from core.templates import render_template
from telegram_bot import send_telegram_message

router = APIRouter()

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None, msg: Optional[str] = None):
    session_user = verify_session_cookie(request.cookies.get("session_token"))
    if session_user:
        return RedirectResponse(url="/", status_code=302)

    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        error = f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min)."
        
    message_html = ""
    if error:
        message_html = f'<div class="error-msg">{error}</div>'
    elif msg:
        message_html = f'<div class="success-msg">{msg}</div>'
        
    return render_template("login.html", {"MESSAGE_HTML": message_html})

@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min).")
        return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)

    user = username.strip().lower()
    passw = password.strip()
    
    if not database.verify_admin_credentials(user, passw):
        auth_rate_limiter.record_failed_attempt(client_ip)
        is_locked_now, remaining_sec_now = auth_rate_limiter.is_locked_out(client_ip)
        if is_locked_now:
            minutos = max(1, (remaining_sec_now + 59) // 60)
            err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min).")
            return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)
        return RedirectResponse(url="/login?error=Usuario+o+contrase%C3%B1a+incorrectos", status_code=302)

    otp = f"{secrets.randbelow(900000) + 100000}"
    database.set_telegram_otp(user, otp, duration_seconds=300)

    msg = (
        "🔐 <b>Código de Verificación 2FA</b>\n\n"
        "Alguien está iniciando sesión en el Panel de Streaming.\n\n"
        f"Tu código de acceso es: <code>{otp}</code>\n\n"
        "⏱️ <i>Válido durante 5 minutos.</i>"
    )
    await send_telegram_message(msg)

    preauth = create_preauth_cookie(user)
    response = RedirectResponse(url="/2fa", status_code=302)
    response.set_cookie(
        key="preauth_token",
        value=preauth,
        httponly=True,
        samesite="lax",
        max_age=300
    )
    return response

@router.post("/recuperar")
async def recover_password():
    raw_user = os.getenv("ADMIN_USERNAME")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    
    temp_pass = f"{secrets.randbelow(900000) + 100000}"
    database.create_or_update_admin(admin_user, temp_pass)
    
    msg = (
        "🔑 <b>Recuperación de Contraseña</b>\n\n"
        "Has solicitado una clave temporal de acceso al Panel Web:\n\n"
        f"• Usuario: <code>{admin_user}</code>\n"
        f"• Contraseña temporal: <code>{temp_pass}</code>\n\n"
        "Ingresa con estos datos en tu panel de control."
    )
    ok = await send_telegram_message(msg)
    if ok:
        return RedirectResponse(url="/login?msg=Se+envi%C3%B3+tu+clave+temporal+a+Telegram", status_code=302)
    else:
        return RedirectResponse(url="/login?error=Fallo+al+enviar+mensaje+a+Telegram", status_code=302)

@router.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request, error: Optional[str] = None):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min).")
        return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)

    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login", status_code=302)
        
    error_html = f'<div class="error-msg">{error}</div>' if error else ""
    return render_template("twofa.html", {"ERROR_HTML": error_html})

@router.post("/2fa")
async def twofa_submit(request: Request, otp_code: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min).")
        return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)

    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login?error=Sesi%C3%B3n+expirada.+Intenta+nuevamente.", status_code=302)

    code = otp_code.strip()
    is_valid = False

    if database.verify_telegram_otp(preauth_user, code):
        is_valid = True
    else:
        admin_data = database.get_admin_user(preauth_user)
        if admin_data and admin_data.get("totp_secret"):
            totp = pyotp.TOTP(admin_data["totp_secret"])
            if totp.verify(code):
                is_valid = True

    if not is_valid:
        auth_rate_limiter.record_failed_attempt(client_ip)
        is_locked_now, remaining_sec_now = auth_rate_limiter.is_locked_out(client_ip)
        if is_locked_now:
            minutos = max(1, (remaining_sec_now + 59) // 60)
            err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado temporalmente ({minutos} min).")
            return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)
        return RedirectResponse(url="/2fa?error=C%C3%B3digo+inv%C3%A1lido+o+expirado", status_code=302)

    auth_rate_limiter.reset_attempts(client_ip)
    session = create_session_cookie(preauth_user)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_token",
        value=session,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7
    )
    response.delete_cookie("preauth_token")
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_token")
    response.delete_cookie("preauth_token")
    return response
