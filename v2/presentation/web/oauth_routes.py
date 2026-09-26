import base64
import html
import secrets
import urllib.parse
from typing import Optional, Dict, Set

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import database
from core.config import settings
from core.principal import generate_csrf_token, verify_csrf_token
from core.rate_limiter import auth_rate_limiter
from core.security import verify_session_cookie
from core.templates import render_template, SafeHTML

router = APIRouter()

ALLOWED_OAUTH_SCOPES: Set[str] = {
    "",
    "mcp",
    "mcp:admin",
    "mcp:client",
    "finance:read",
    "secrets:create",
}

TOKEN_NO_STORE_HEADERS: Dict[str, str] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}


def _validate_scopes(raw_scope: str) -> bool:
    parts = [s.strip() for s in (raw_scope or "").replace(",", " ").split() if s.strip()]
    return all(p in ALLOWED_OAUTH_SCOPES for p in parts)


def _render_oauth_error_html(title: str, detail: str, status_code: int = 400) -> HTMLResponse:
    safe_title = html.escape(str(title), quote=True)
    safe_detail = html.escape(str(detail), quote=True)
    body = (
        "<body style='background:#0b0f19;color:#f87171;font-family:sans-serif;padding:40px;text-align:center;'>"
        f"<h2>❌ {safe_title}</h2>"
        f"<p style='color:#cbd5e1;'>{safe_detail}</p>"
        "</body>"
    )
    return HTMLResponse(body, status_code=status_code)


def render_oauth_authorize_page(
    client_id: str,
    redirect_uri: str,
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "",
    is_logged_in: bool = False,
    username: str = "",
    error: str = "",
) -> str:
    user_block = ""
    login_fields = ""

    if error:
        safe_err = html.escape(str(error), quote=True)
        user_block += f'<div class="alert-error">⚠️ {safe_err}</div>'

    if is_logged_in and username:
        safe_user = html.escape(str(username), quote=True)
        csrf_tok = generate_csrf_token(username)
        user_block += f'<div style="text-align:center;"><span class="logged-user">👤 Conectado con 2FA como: <b>{safe_user}</b></span></div>'
        login_fields = f'<input type="hidden" name="csrf_token" value="{html.escape(csrf_tok, quote=True)}">'
    else:
        login_fields = (
            '<div class="alert-error">🔐 Se requiere iniciar sesión con segundo factor (2FA) en '
            '<a href="/login" style="color:#60a5fa;">/login</a> antes de autorizar este cliente OAuth.</div>'
        )

    context = {
        "CLIENT_ID": client_id,
        "REDIRECT_URI": redirect_uri,
        "STATE": state or "",
        "CODE_CHALLENGE": code_challenge or "",
        "CODE_CHALLENGE_METHOD": code_challenge_method or "S256",
        "SCOPE": scope or "mcp",
        "USER_BLOCK": SafeHTML(user_block),
        "LOGIN_FIELDS": SafeHTML(login_fields),
    }
    return render_template("oauth_authorize.html", context)


@router.get("/.well-known/oauth-authorization-server")
@router.get("/mcp/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
@router.get("/mcp/.well-known/openid-configuration")
async def oauth_discovery(request: Request):
    # Usar origen público configurado para evitar envenenamiento por Host header (V05)
    configured_origin = (
        getattr(settings, "PUBLIC_BASE_URL", "") or
        getattr(settings, "APP_BASE_URL", "")
    ).strip().rstrip("/")
    if configured_origin:
        server_origin = configured_origin
    else:
        proto = "https" if request.url.scheme == "https" else "http"
        server_origin = f"{proto}://localhost:{settings.PORT}"

    return {
        "issuer": server_origin,
        "authorization_endpoint": f"{server_origin}/oauth/authorize",
        "token_endpoint": f"{server_origin}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": ["mcp", "mcp:admin", "mcp:client", "finance:read", "secrets:create"],
    }


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize_get(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "code",
    state: str = "",
    scope: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    req_id: str = "",
):
    # Si se retoma una solicitud pendiente tras completar login + 2FA (V04)
    if req_id.strip():
        pending = database.get_pending_oauth_request(req_id.strip())
        if not pending:
            return _render_oauth_error_html("Solicitud OAuth Expirada", "La solicitud de autorización expiró o ya fue procesada.", 400)
        client_id = pending["client_id"]
        redirect_uri = pending["redirect_uri"]
        state = pending.get("state", "")
        code_challenge = pending.get("code_challenge", "")
        code_challenge_method = pending.get("code_challenge_method", "S256")
        scope = pending.get("scope", "mcp")
        response_type = "code"

    oauth_cfg = database.get_oauth_settings()
    if not oauth_cfg.get("enabled", 1):
        return _render_oauth_error_html("OAuth Deshabilitado", "El servidor de autorización OAuth está desactivado.", 403)

    clean_client_id = client_id.strip()
    if not clean_client_id or not secrets.compare_digest(clean_client_id, str(oauth_cfg["client_id"]).strip()):
        return _render_oauth_error_html(
            "Error OAuth: Client ID Inválido",
            f"El ID de cliente recibido ('{clean_client_id}') no coincide con el registrado.",
            400,
        )

    clean_redirect_uri = redirect_uri.strip()
    if not clean_redirect_uri or not database.is_registered_redirect_uri(clean_redirect_uri, oauth_cfg):
        return _render_oauth_error_html(
            "Error OAuth: Redirect URI No Registrado",
            "La dirección de redirección solicitada no pertenece a la lista autorizada.",
            400,
        )

    if response_type.strip() != "code":
        return _render_oauth_error_html(
            "Error OAuth: response_type no soportado",
            "Solo se admite response_type='code'.",
            400,
        )

    if not _validate_scopes(scope):
        return _render_oauth_error_html(
            "Error OAuth: Scope Inválido",
            "Uno o más permisos solicitados no están permitidos.",
            400,
        )

    clean_method = (code_challenge_method or "S256").strip().upper()
    if code_challenge.strip() and clean_method != "S256":
        return _render_oauth_error_html(
            "Error OAuth: Método PKCE No Permitido",
            "Se requiere estrictamente code_challenge_method='S256'.",
            400,
        )

    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        # Guardar solicitud pendiente y redirigir al flujo unificado /login + /2fa (V04)
        pending_id = database.create_pending_oauth_request(
            client_id=clean_client_id,
            redirect_uri=clean_redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=clean_method,
            scope=scope or "mcp",
        )
        resp = RedirectResponse(
            url="/login?msg=Inicia+sesi%C3%B3n+con+2FA+para+continuar+la+autorizaci%C3%B3n+OAuth",
            status_code=302,
        )
        resp.set_cookie(
            key="oauth_pending_req",
            value=pending_id,
            httponly=True,
            samesite="lax",
            max_age=600,
        )
        return resp

    if req_id.strip():
        database.mark_pending_oauth_request_used(req_id.strip())

    return HTMLResponse(
        render_oauth_authorize_page(
            client_id=clean_client_id,
            redirect_uri=clean_redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=clean_method,
            scope=scope,
            is_logged_in=True,
            username=user,
        )
    )


@router.post("/oauth/authorize")
async def oauth_authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(default=""),
    code_challenge: str = Form(default=""),
    code_challenge_method: str = Form(default="S256"),
    scope: str = Form(default=""),
    action: str = Form(default="allow"),
    csrf_token: str = Form(default=""),
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, _ = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Intente más tarde.")

    oauth_cfg = database.get_oauth_settings()
    if not oauth_cfg.get("enabled", 1):
        raise HTTPException(status_code=403, detail="OAuth deshabilitado")

    clean_client_id = client_id.strip()
    clean_redirect_uri = redirect_uri.strip()

    if not secrets.compare_digest(clean_client_id, str(oauth_cfg["client_id"]).strip()):
        raise HTTPException(status_code=400, detail="Client ID inválido")

    # Validar redirect_uri ANTES de cualquier redirección, incluso en denegación (V05)
    if not database.is_registered_redirect_uri(clean_redirect_uri, oauth_cfg):
        raise HTTPException(status_code=400, detail="Redirect URI no registrado")

    if not _validate_scopes(scope):
        raise HTTPException(status_code=400, detail="Scope no permitido")

    clean_method = (code_challenge_method or "S256").strip().upper()
    if code_challenge.strip() and clean_method != "S256":
        raise HTTPException(status_code=400, detail="Método PKCE inválido: se exige S256")

    sep = "&" if "?" in clean_redirect_uri else "?"
    if action != "allow":
        deny_url = f"{clean_redirect_uri}{sep}error=access_denied"
        if state:
            deny_url += f"&state={urllib.parse.quote(state)}"
        return RedirectResponse(deny_url, status_code=302)

    # Exigir sesión autenticada con 2FA; nunca validar usuario/contraseña directo sin 2FA (V04)
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        auth_rate_limiter.record_failed_attempt(client_ip)
        return HTMLResponse(
            render_oauth_authorize_page(
                client_id=clean_client_id,
                redirect_uri=clean_redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=clean_method,
                scope=scope,
                is_logged_in=False,
                error="Se requiere una sesión activa verificada con segundo factor (2FA). Inicia sesión en /login.",
            ),
            status_code=401,
        )

    supplied_csrf = (csrf_token or request.headers.get("X-CSRF-Token") or "").strip()
    if supplied_csrf and not verify_csrf_token(user, supplied_csrf):
        raise HTTPException(status_code=403, detail="Token CSRF inválido")

    code = database.create_oauth_auth_code(
        client_id=clean_client_id,
        redirect_uri=clean_redirect_uri,
        code_challenge=code_challenge.strip(),
        code_challenge_method=clean_method,
        user_id=user,
    )

    redirect_target = f"{clean_redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if state:
        redirect_target += f"&state={urllib.parse.quote(state)}"

    return RedirectResponse(redirect_target, status_code=302)


@router.post("/oauth/token")
async def oauth_token_endpoint(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    is_locked, _ = auth_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        return JSONResponse(
            status_code=429,
            content={"error": "slow_down", "error_description": "Demasiados intentos de autenticación."},
            headers=TOKEN_NO_STORE_HEADERS,
        )

    content_type = request.headers.get("content-type", "")
    params = {}
    if "application/json" in content_type:
        try:
            params = await request.json()
        except Exception:
            params = {}
    else:
        form = await request.form()
        params = dict(form)

    auth_header = request.headers.get("authorization", "")
    client_id = str(params.get("client_id", ""))
    client_secret = str(params.get("client_secret", ""))

    if auth_header.lower().startswith("basic "):
        try:
            b64_creds = auth_header[6:].strip()
            decoded = base64.b64decode(b64_creds).decode("utf-8")
            if ":" in decoded:
                b_id, b_sec = decoded.split(":", 1)
                client_id = client_id or b_id
                client_secret = client_secret or b_sec
        except Exception:
            pass

    grant_type = str(params.get("grant_type", "authorization_code"))

    if not database.validate_oauth_client(client_id, client_secret):
        auth_rate_limiter.record_failed_attempt(client_ip)
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_client", "error_description": "Client ID o Client Secret no válidos."},
            headers=TOKEN_NO_STORE_HEADERS,
        )

    if grant_type == "authorization_code":
        code = str(params.get("code", ""))
        redirect_uri = str(params.get("redirect_uri", ""))
        code_verifier = str(params.get("code_verifier", ""))

        auth_data = database.verify_and_consume_auth_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        if not auth_data:
            auth_rate_limiter.record_failed_attempt(client_ip)
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "El código de autorización es inválido, ya fue usado, expiró o falló la validación PKCE/redirect_uri."},
                headers=TOKEN_NO_STORE_HEADERS,
            )

        tokens = database.create_oauth_tokens(
            client_id=client_id,
            user_id=auth_data.get("user_id", "admin"),
        )
        return JSONResponse(tokens, headers=TOKEN_NO_STORE_HEADERS)

    elif grant_type == "refresh_token":
        refresh_token = str(params.get("refresh_token", ""))
        tokens = database.refresh_oauth_token(refresh_token=refresh_token, client_id=client_id)
        if not tokens:
            auth_rate_limiter.record_failed_attempt(client_ip)
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "Refresh token inválido o expirado."},
                headers=TOKEN_NO_STORE_HEADERS,
            )
        return JSONResponse(tokens, headers=TOKEN_NO_STORE_HEADERS)

    else:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_grant_type", "error_description": "Tipo de concesión no soportado."},
            headers=TOKEN_NO_STORE_HEADERS,
        )


@router.get("/api/oauth/settings")
async def api_oauth_settings_get(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    return JSONResponse(database.get_oauth_settings(), headers=TOKEN_NO_STORE_HEADERS)


@router.post("/api/oauth/settings")
async def api_oauth_settings_post(
    request: Request,
    client_id: str = Form("gemini-spark-joif"),
    client_secret: str = Form(...),
    redirect_uris: str = Form("https://gemini.google.com"),
    enabled: Optional[str] = Form(None),
):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_oauth_settings(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        redirect_uris=redirect_uris.strip(),
        enabled=1 if enabled in ("1", "on", "true") else 0,
    )
    return RedirectResponse(url="/?msg=oauth_saved#tab-oauth", status_code=302)


@router.post("/api/oauth/regenerate")
async def api_oauth_regenerate_post(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.regenerate_oauth_secret()
    return RedirectResponse(url="/?msg=oauth_secret_regenerated#tab-oauth", status_code=302)
