import base64
import secrets
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import database
from core.security import verify_session_cookie
from core.templates import render_template

router = APIRouter()

def render_oauth_authorize_page(
    client_id: str,
    redirect_uri: str,
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "plain",
    scope: str = "",
    is_logged_in: bool = False,
    username: str = "",
    error: str = ""
) -> str:
    user_block = ""
    login_fields = ""
    
    if error:
        user_block += f'<div class="alert-error">⚠️ {error}</div>'
    
    if is_logged_in:
        user_block += f'<div style="text-align:center;"><span class="logged-user">👤 Conectado como: <b>{username}</b></span></div>'
    else:
        login_fields = """
        <div class="form-group">
            <label>Usuario Administrador:</label>
            <input type="text" name="username" required placeholder="admin" autocomplete="username">
        </div>
        <div class="form-group">
            <label>Contraseña:</label>
            <input type="password" name="password" required placeholder="••••••••" autocomplete="current-password">
        </div>
        """
    
    context = {
        "CLIENT_ID": client_id,
        "REDIRECT_URI": redirect_uri,
        "STATE": state or "",
        "CODE_CHALLENGE": code_challenge or "",
        "CODE_CHALLENGE_METHOD": code_challenge_method or "plain",
        "SCOPE": scope or "mcp",
        "USER_BLOCK": user_block,
        "LOGIN_FIELDS": login_fields
    }
    return render_template("oauth_authorize.html", context)

@router.get("/.well-known/oauth-authorization-server")
@router.get("/mcp/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
@router.get("/mcp/.well-known/openid-configuration")
async def oauth_discovery(request: Request):
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", request.url.netloc)
    server_origin = f"{proto}://{host}"
    
    return {
        "issuer": server_origin,
        "authorization_endpoint": f"{server_origin}/oauth/authorize",
        "token_endpoint": f"{server_origin}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": ["mcp"]
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
    code_challenge_method: str = "plain"
):
    oauth_cfg = database.get_oauth_settings()
    clean_client_id = client_id.strip()
    if not clean_client_id or not secrets.compare_digest(clean_client_id, oauth_cfg["client_id"]):
        return HTMLResponse(
            f"<body style='background:#0b0f19;color:#f87171;font-family:sans-serif;padding:40px;text-align:center;'>"
            f"<h2>❌ Error OAuth: Client ID Inválido</h2>"
            f"<p>El ID de cliente recibido ('{clean_client_id}') no coincide con el configurado en tu panel.</p>"
            f"</body>",
            status_code=400
        )
    
    if not redirect_uri.strip():
        return HTMLResponse(
            "<body style='background:#0b0f19;color:#f87171;font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h2>❌ Error OAuth: Falta redirect_uri</h2>"
            "</body>",
            status_code=400
        )

    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    return HTMLResponse(render_oauth_authorize_page(
        client_id=clean_client_id,
        redirect_uri=redirect_uri.strip(),
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        is_logged_in=bool(user),
        username=user or ""
    ))

@router.post("/oauth/authorize")
async def oauth_authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(default=""),
    code_challenge: str = Form(default=""),
    code_challenge_method: str = Form(default="plain"),
    scope: str = Form(default=""),
    action: str = Form(default="allow"),
    username: str = Form(default=""),
    password: str = Form(default="")
):
    oauth_cfg = database.get_oauth_settings()
    clean_client_id = client_id.strip()
    clean_redirect_uri = redirect_uri.strip()
    
    if not secrets.compare_digest(clean_client_id, oauth_cfg["client_id"]):
        raise HTTPException(status_code=400, detail="Client ID inválido")
    
    sep = "&" if "?" in clean_redirect_uri else "?"
    if action != "allow":
        deny_url = f"{clean_redirect_uri}{sep}error=access_denied"
        if state:
            deny_url += f"&state={urllib.parse.quote(state)}"
        return RedirectResponse(deny_url, status_code=302)

    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        clean_u = username.strip().lower()
        if not database.verify_admin_credentials(clean_u, password.strip()):
            return HTMLResponse(
                render_oauth_authorize_page(
                    client_id=clean_client_id,
                    redirect_uri=clean_redirect_uri,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                    scope=scope,
                    is_logged_in=False,
                    error="Credenciales de administrador incorrectas."
                ),
                status_code=401
            )
        user = clean_u

    code = database.create_oauth_auth_code(
        client_id=clean_client_id,
        redirect_uri=clean_redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        user_id=user
    )

    redirect_target = f"{clean_redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if state:
        redirect_target += f"&state={urllib.parse.quote(state)}"
    
    return RedirectResponse(redirect_target, status_code=302)

@router.post("/oauth/token")
async def oauth_token_endpoint(request: Request):
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

    # Autenticación HTTP Basic Auth
    auth_header = request.headers.get("authorization", "")
    client_id = params.get("client_id", "")
    client_secret = params.get("client_secret", "")
    
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

    grant_type = params.get("grant_type", "authorization_code")
    
    if not database.validate_oauth_client(client_id, client_secret):
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_client", "error_description": "Client ID o Client Secret no válidos."}
        )

    if grant_type == "authorization_code":
        code = params.get("code", "")
        redirect_uri = params.get("redirect_uri", "")
        code_verifier = params.get("code_verifier", "")
        
        auth_data = database.verify_and_consume_auth_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier
        )
        if not auth_data:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "El código de autorización es inválido, ya fue usado o expiró."}
            )
        
        tokens = database.create_oauth_tokens(client_id=client_id, user_id=auth_data.get("user_id", "admin"))
        return JSONResponse(tokens)

    elif grant_type == "refresh_token":
        refresh_token = params.get("refresh_token", "")
        tokens = database.refresh_oauth_token(refresh_token=refresh_token, client_id=client_id)
        if not tokens:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "Refresh token inválido o expirado."}
            )
        return JSONResponse(tokens)

    else:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_grant_type", "error_description": f"Tipo de concesión '{grant_type}' no soportado."}
        )

@router.get("/api/oauth/settings")
async def api_oauth_settings_get(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    return JSONResponse(database.get_oauth_settings())

@router.post("/api/oauth/settings")
async def api_oauth_settings_post(
    request: Request,
    client_id: str = Form("gemini-spark-joif"),
    client_secret: str = Form(...),
    redirect_uris: str = Form("https://gemini.google.com"),
    enabled: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_oauth_settings(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        redirect_uris=redirect_uris.strip(),
        enabled=1 if enabled in ("1", "on", "true") else 0
    )
    return RedirectResponse(url="/?msg=oauth_saved#tab-oauth", status_code=302)

@router.post("/api/oauth/regenerate")
async def api_oauth_regenerate_post(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.regenerate_oauth_secret()
    return RedirectResponse(url="/?msg=oauth_secret_regenerated#tab-oauth", status_code=302)
