import os
import html
import secrets
import pyotp
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

import database
from core.config import settings
from core.security import (
    create_session_cookie,
    verify_session_cookie,
    create_preauth_cookie,
    verify_preauth_cookie,
)
from core.rate_limiter import auth_rate_limiter
from core.templates import render_template, SafeHTML
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
        safe_error = html.escape(str(error), quote=True)
        message_html = f'<div class="error-msg">{safe_error}</div>'
    elif msg:
        safe_msg = html.escape(str(msg), quote=True)
        message_html = f'<div class="success-msg">{safe_msg}</div>'

    return render_template("login.html", {"MESSAGE_HTML": SafeHTML(message_html)})


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
        max_age=300,
    )
    return response


@router.post("/recuperar")
async def recover_password(request: Request):
    """Solicita un desafío de recuperación de un solo uso sin alterar la contraseña actual (V02)."""
    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        err_msg = urllib.parse.quote(f"Demasiados intentos. Bloqueado temporalmente ({minutos} min).")
        return RedirectResponse(url=f"/login?error={err_msg}", status_code=302)

    auth_rate_limiter.record_failed_attempt(client_ip)

    raw_user = os.getenv("ADMIN_USERNAME") or os.getenv("ADMIN_USER") or settings.ADMIN_USERNAME
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"

    reset_token = database.create_password_reset_token(
        username=admin_user,
        ttl_seconds=900,
        requester_ip=client_ip,
    )

    msg = (
        "🔑 <b>Desafío de Recuperación de Contraseña</b>\n\n"
        "Se solicitó restablecer el acceso al Panel Web (la clave actual NO fue modificada aún):\n\n"
        f"• Usuario: <code>{html.escape(admin_user)}</code>\n"
        f"• Token de un solo uso (15 min): <code>{reset_token}</code>\n\n"
        "Ingresa en <code>/recuperar/confirmar</code> para definir tu nueva contraseña."
    )
    ok = await send_telegram_message(msg)
    if ok:
        return RedirectResponse(
            url="/recuperar/confirmar?msg=Se+envi%C3%B3+un+token+de+recuperaci%C3%B3n+a+Telegram",
            status_code=302,
        )
    else:
        database.invalidate_password_reset_token(reset_token)
        return RedirectResponse(
            url="/login?error=No+se+pudo+enviar+el+token+a+Telegram.+Tu+clave+actual+sigue+intacta",
            status_code=302,
        )


@router.get("/recuperar/confirmar", response_class=HTMLResponse)
async def recover_confirm_page(request: Request, error: Optional[str] = None, msg: Optional[str] = None):
    """Formulario seguro para canjear el token de recuperación y definir nueva contraseña (V02)."""
    banner = ""
    if error:
        banner = f'<div style="background:#7f1d1d;color:#fecaca;padding:12px;border-radius:8px;margin-bottom:16px;">{html.escape(str(error), quote=True)}</div>'
    elif msg:
        banner = f'<div style="background:#14532d;color:#bbf7d0;padding:12px;border-radius:8px;margin-bottom:16px;">{html.escape(str(msg), quote=True)}</div>'

    page = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Confirmar Recuperación - StreamVault</title></head>
<body style="background:#0f172a;color:#f8fafc;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;">
  <div style="background:#1e293b;padding:32px;border-radius:12px;width:100%;max-width:400px;box-shadow:0 10px 25px rgba(0,0,0,0.4);">
    <h2 style="margin-top:0;">🔑 Restablecer Contraseña</h2>
    {banner}
    <form method="POST" action="/recuperar/confirmar">
      <div style="margin-bottom:14px;">
        <label style="display:block;margin-bottom:6px;font-size:0.9rem;">Token de Recuperación (Telegram):</label>
        <input type="text" name="token" required autocomplete="off" style="width:100%;padding:10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff;box-sizing:border-box;">
      </div>
      <div style="margin-bottom:18px;">
        <label style="display:block;margin-bottom:6px;font-size:0.9rem;">Nueva Contraseña (mín. 8 caracteres):</label>
        <input type="password" name="new_password" required minlength="8" autocomplete="new-password" style="width:100%;padding:10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff;box-sizing:border-box;">
      </div>
      <button type="submit" style="width:100%;padding:12px;background:#3b82f6;color:#fff;border:none;border-radius:6px;font-weight:bold;cursor:pointer;">Confirmar Cambio</button>
    </form>
  </div>
</body></html>"""
    return HTMLResponse(page)


@router.post("/recuperar/confirmar")
async def recover_confirm_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, remaining_seconds = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        minutos = max(1, (remaining_seconds + 59) // 60)
        err_msg = urllib.parse.quote(f"Demasiados intentos fallidos. Bloqueado ({minutos} min).")
        return RedirectResponse(url=f"/recuperar/confirmar?error={err_msg}", status_code=302)

    if database.consume_password_reset_token_and_update_password(token, new_password):
        auth_rate_limiter.reset_attempts(client_ip)
        response = RedirectResponse(
            url="/login?msg=Contrase%C3%B1a+actualizada+correctamente.+Inicia+sesi%C3%B3n",
            status_code=302,
        )
        response.delete_cookie("session_token")
        response.delete_cookie("preauth_token")
        return response

    auth_rate_limiter.record_failed_attempt(client_ip)
    return RedirectResponse(
        url="/recuperar/confirmar?error=Token+inv%C3%A1lido,+vencido+o+contrase%C3%B1a+insegura",
        status_code=302,
    )


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

    safe_error = html.escape(str(error), quote=True) if error else ""
    error_html = f'<div class="error-msg">{safe_error}</div>' if safe_error else ""
    return render_template("twofa.html", {"ERROR_HTML": SafeHTML(error_html)})


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

    # Si existe una solicitud OAuth pendiente vinculada a este navegador, retomar consentimiento (V04)
    pending_oauth_req = (request.cookies.get("oauth_pending_req") or "").strip()
    redirect_url = "/"
    if pending_oauth_req and pending_oauth_req.isalnum():
        redirect_url = f"/oauth/authorize?req_id={urllib.parse.quote(pending_oauth_req)}"

    is_secure = (
        request.url.scheme == "https" or
        request.headers.get("x-forwarded-proto", "").lower() == "https" or
        getattr(settings, "APP_ENV", "development") == "production"
    )
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key="session_token",
        value=session,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        path="/",
        max_age=86400 * 7,
    )
    response.delete_cookie("preauth_token", path="/")
    if pending_oauth_req:
        response.delete_cookie("oauth_pending_req", path="/")
    return response


@router.get("/logout")
@router.post("/logout")
async def logout(request: Request):
    from core.security import revoke_session_cookie
    revoke_session_cookie(request.cookies.get("session_token"))
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_token", path="/")
    response.delete_cookie("preauth_token", path="/")
    return response

