"""
ephemeral_routes.py - Rutas Web para Visualización de Credenciales Efímeras (Anti-SIM Swap)
Expone el endpoint público /v/{token} para revelación y autodestrucción en un solo uso.
"""

import html
import logging
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.ephemeral_secrets import (
    create_ephemeral_secret,
    reveal_and_burn_secret,
    burn_secret_immediately
)
from core.templates import render_template
from core.security import verify_session_cookie

logger = logging.getLogger("presentation.web.ephemeral")

router = APIRouter()


class CreateEphemeralSecretRequest(BaseModel):
    title: str = "Credenciales Seguras"
    items: List[Dict[str, Any]]
    ttl_seconds: int = 600
    max_views: int = 1


def _build_revealed_html(payload: Dict[str, Any]) -> str:
    """Construye el fragmento HTML para credenciales reveladas exitosamente."""
    title = html.escape(payload.get("title") or "Credencial de Acceso")
    items = payload.get("items") or []
    if not items and any(k in payload for k in ("email", "password", "platform")):
        items = [payload]

    cards_html = []
    for idx, it in enumerate(items, 1):
        plat = html.escape(str(it.get("platform") or "Servicio"))
        expiry = html.escape(str(it.get("expiry") or it.get("expiry_date") or ""))
        email_val = html.escape(str(it.get("email") or ""))
        pwd_val = html.escape(str(it.get("password") or ""))
        profile_val = html.escape(str(it.get("profile") or it.get("profile_name") or ""))
        pin_val = html.escape(str(it.get("pin") or it.get("profile_pin") or ""))

        expiry_badge = f'<span class="expiry-label">📅 Vence: {expiry}</span>' if expiry else ''

        profile_row = ""
        if profile_val or pin_val:
            prof_field = ""
            pin_field = ""
            if profile_val:
                prof_field = f"""
                    <div style="flex: 1;">
                        <div class="field-label">Perfil Asignado</div>
                        <div class="field-value-group">
                            <input type="text" readonly class="field-input" value="{profile_val}" id="prof_{idx}">
                            <button type="button" class="btn-action" onclick="copyField('prof_{idx}', this)">📋</button>
                        </div>
                    </div>
                """
            if pin_val:
                pin_field = f"""
                    <div style="width: 130px;">
                        <div class="field-label">PIN</div>
                        <div class="field-value-group">
                            <input type="password" readonly class="field-input" value="{pin_val}" id="pin_{idx}">
                            <button type="button" class="btn-action" onclick="toggleVisibility('pin_{idx}', this)">👁️</button>
                            <button type="button" class="btn-action" onclick="copyField('pin_{idx}', this)">📋</button>
                        </div>
                    </div>
                """
            profile_row = f'<div class="field-row" style="display: flex; gap: 10px;">{prof_field}{pin_field}</div>'

        card = f"""
            <div class="credential-card">
                <div class="credential-header">
                    <span class="platform-badge">{plat}</span>
                    {expiry_badge}
                </div>
                <div class="field-row">
                    <div class="field-label">Usuario / Correo</div>
                    <div class="field-value-group">
                        <input type="text" readonly class="field-input" value="{email_val}" id="email_{idx}">
                        <button type="button" class="btn-action" onclick="copyField('email_{idx}', this)">📋 Copiar</button>
                    </div>
                </div>
                <div class="field-row">
                    <div class="field-label">Contraseña</div>
                    <div class="field-value-group">
                        <input type="password" readonly class="field-input" value="{pwd_val}" id="pwd_{idx}">
                        <button type="button" class="btn-action" onclick="toggleVisibility('pwd_{idx}', this)">👁️</button>
                        <button type="button" class="btn-action" onclick="copyField('pwd_{idx}', this)">📋 Copiar</button>
                    </div>
                </div>
                {profile_row}
            </div>
        """
        cards_html.append(card)

    cards_joined = "\n".join(cards_html)
    return f"""
        <div class="icon-header">🔐</div>
        <h2>{title}</h2>
        <p class="subtitle">StreamVault • Enlace Efímero Anti-SIM Swap</p>

        <div class="alert-warning">
            <span style="font-size: 1.2rem;">⚠️</span>
            <div>
                <strong>Un solo uso:</strong> Esta información ya fue eliminada permanentemente del servidor. Si sales o recargas esta pantalla, no podrás volver a consultarla.
            </div>
        </div>

        {cards_joined}

        <button type="button" class="btn-destroy" onclick="closeScreen()">🗑️ Borrar y Cerrar Pantalla</button>
        <div class="footer">Protección criptográfica AES-256-GCM • Zero-Knowledge Session</div>
    """


def _build_burned_html() -> str:
    """Construye el fragmento HTML para un secreto expirado o destruido."""
    return """
        <div class="icon-header">🔥</div>
        <h2>Enlace Expirado o Ya Destruido</h2>
        <p class="subtitle">StreamVault • Protección de Credenciales</p>

        <div class="alert-burned">
            🔒 <strong>El secreto ya no está disponible.</strong><br>
            Este enlace fue de un solo uso y ya ha sido consumido, o superó su tiempo límite de 10 minutos para proteger tu cuenta de robos o clonaciones (Anti-SIM Swap).
        </div>

        <p style="text-align: center; font-size: 0.88rem; color: #94a3b8; line-height: 1.5;">
            Si necesitas acceder nuevamente a tus datos de acceso, solicítalo a través de nuestro bot de WhatsApp o chat oficial de soporte.
        </p>

        <div class="footer">StreamVault v2 • Arquitectura de Alta Seguridad</div>
    """


@router.get("/v/{token}", response_class=HTMLResponse)
async def view_ephemeral_secret(token: str, request: Request):
    """Muestra y autodestruye en el servidor el secreto efímero de un solo uso."""
    # Verificar si el cliente solicita JSON
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept

    payload, status = reveal_and_burn_secret(token)

    if wants_json:
        if status == "revealed":
            return JSONResponse({"status": "revealed", "data": payload})
        else:
            return JSONResponse({"status": status, "message": "El secreto expiró o ya fue consumido."}, status_code=404)

    if status == "revealed" and payload:
        content_html = _build_revealed_html(payload)
    else:
        content_html = _build_burned_html()

    page_html = render_template(
        "ephemeral_secret.html",
        {"CONTENT_HTML": content_html},
        use_cache=True
    )
    return HTMLResponse(page_html)


@router.post("/api/v1/ephemeral-secrets")
async def create_ephemeral_secret_api(payload: CreateEphemeralSecretRequest, request: Request):
    """Crea programáticamente un secreto efímero (protegido por sesión)."""
    session_user = verify_session_cookie(request.cookies.get("session_token"))
    if not session_user:
        # Permitir llamadas internas con token de autorización si aplica
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            raise HTTPException(status_code=401, detail="Autenticación requerida para generar enlaces efímeros.")

    data_to_store = {
        "title": payload.title,
        "items": payload.items
    }
    token, url = create_ephemeral_secret(
        data=data_to_store,
        title=payload.title,
        ttl_seconds=payload.ttl_seconds,
        max_views=payload.max_views
    )
    return JSONResponse({
        "status": "success",
        "token": token,
        "url": url,
        "ttl_seconds": payload.ttl_seconds
    })
