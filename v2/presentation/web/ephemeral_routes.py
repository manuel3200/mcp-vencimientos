"""
ephemeral_routes.py - Rutas Web para Visualización de Credenciales Efímeras (Anti-SIM Swap)
Expone el endpoint público /v/{token} con revelado explícito por POST (V15)
y creación autenticada estrictamente con scopes (V03).
"""

import html
import json
import logging
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.ephemeral_secrets import (
    create_ephemeral_secret,
    peek_ephemeral_secret,
    reveal_and_burn_secret,
    burn_secret_immediately,
)
from core.principal import (
    authenticate_request,
    require_scope,
    verify_csrf_token,
)
from core.templates import render_template, SafeHTML

logger = logging.getLogger("presentation.web.ephemeral")

router = APIRouter()

SECRET_HEADERS: Dict[str, str] = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


class CreateEphemeralSecretRequest(BaseModel):
    title: str = "Credenciales Seguras"
    items: List[Dict[str, Any]]
    ttl_seconds: int = 600
    max_views: int = 1


def _validate_secret_limits(payload: CreateEphemeralSecretRequest) -> None:
    """Valida límites de elementos, bytes, TTL y lecturas para evitar abuso de almacenamiento (V03)."""
    if not payload.items or len(payload.items) > 20:
        raise HTTPException(status_code=400, detail="La lista de items debe contener entre 1 y 20 elementos.")
    if payload.ttl_seconds < 30 or payload.ttl_seconds > 86400:
        raise HTTPException(status_code=400, detail="El TTL debe ubicarse entre 30 y 86400 segundos.")
    if payload.max_views < 1 or payload.max_views > 5:
        raise HTTPException(status_code=400, detail="max_views debe ubicarse entre 1 y 5.")
    serialized_len = len(json.dumps({"title": payload.title, "items": payload.items}, ensure_ascii=False).encode("utf-8"))
    if serialized_len > 16384:
        raise HTTPException(status_code=413, detail="El contenido del secreto excede el límite permitido (16 KB).")


def _build_neutral_prompt_html(token: str, title: str) -> str:
    """Construye la pantalla neutra previa al revelado para evitar consumo por bots de previsualización (V15)."""
    safe_title = html.escape(title or "Credenciales Seguras", quote=True)
    safe_token = html.escape(token, quote=True)
    return f"""
        <div class="icon-header">🛡️</div>
        <h2>{safe_title}</h2>
        <p class="subtitle">StreamVault • Enlace Protegido de Un Solo Uso</p>

        <div class="alert-warning">
            <span style="font-size: 1.2rem;">ℹ️</span>
            <div>
                <strong>Confirmación requerida:</strong> Al presionar el botón inferior se mostrarán tus credenciales y el enlace se autodestruirá inmediatamente en el servidor.
            </div>
        </div>

        <form method="POST" action="/v/{safe_token}" style="margin-top: 20px;">
            <button type="submit" class="btn-destroy" style="background: #2563eb;">🔓 Revelar Credenciales Ahora</button>
        </form>
        <div class="footer">Protección contra previsualizadores automáticos • AES-256-GCM</div>
    """


def _build_revealed_html(payload: Dict[str, Any]) -> str:
    """Construye el fragmento HTML para credenciales reveladas exitosamente."""
    title = html.escape(payload.get("title") or "Credencial de Acceso", quote=True)
    items = payload.get("items") or []
    if not items and any(k in payload for k in ("email", "password", "platform")):
        items = [payload]

    cards_html = []
    for idx, it in enumerate(items, 1):
        plat = html.escape(str(it.get("platform") or "Servicio"), quote=True)
        expiry = html.escape(str(it.get("expiry") or it.get("expiry_date") or ""), quote=True)
        email_val = html.escape(str(it.get("email") or ""), quote=True)
        pwd_val = html.escape(str(it.get("password") or ""), quote=True)
        profile_val = html.escape(str(it.get("profile") or it.get("profile_name") or ""), quote=True)
        pin_val = html.escape(str(it.get("pin") or it.get("profile_pin") or ""), quote=True)

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
async def view_ephemeral_secret_get(token: str, request: Request):
    """Presenta una página neutra sin descifrar ni consumir el secreto (V15)."""
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept

    title, status = peek_ephemeral_secret(token)

    if wants_json:
        if status == "available":
            return JSONResponse(
                {"status": "available", "title": title, "message": "Enviar POST para revelar y quemar el secreto."},
                headers=SECRET_HEADERS,
            )
        return JSONResponse(
            {"status": status, "message": "El secreto expiró o ya fue consumido."},
            status_code=404,
            headers=SECRET_HEADERS,
        )

    if status == "available":
        content_html = _build_neutral_prompt_html(token, title or "Credenciales Seguras")
        http_status = 200
    else:
        content_html = _build_burned_html()
        http_status = 404

    page_html = render_template(
        "ephemeral_secret.html",
        {"CONTENT_HTML": SafeHTML(content_html)},
        use_cache=True,
    )
    return HTMLResponse(page_html, status_code=http_status, headers=SECRET_HEADERS)


@router.post("/v/{token}", response_class=HTMLResponse)
async def view_ephemeral_secret_post(token: str, request: Request):
    """Revela explícitamente mediante POST y autodestruye en el servidor el secreto efímero (V15)."""
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept

    payload, status = reveal_and_burn_secret(token)

    if wants_json:
        if status == "revealed":
            return JSONResponse({"status": "revealed", "data": payload}, headers=SECRET_HEADERS)
        return JSONResponse(
            {"status": status, "message": "El secreto expiró o ya fue consumido."},
            status_code=404,
            headers=SECRET_HEADERS,
        )

    if status == "revealed" and payload:
        content_html = _build_revealed_html(payload)
        http_status = 200
    else:
        content_html = _build_burned_html()
        http_status = 404

    page_html = render_template(
        "ephemeral_secret.html",
        {"CONTENT_HTML": SafeHTML(content_html)},
        use_cache=True,
    )
    return HTMLResponse(page_html, status_code=http_status, headers=SECRET_HEADERS)


@router.post("/api/v1/ephemeral-secrets")
async def create_ephemeral_secret_api(payload: CreateEphemeralSecretRequest, request: Request):
    """Crea programáticamente un secreto efímero validando identidad real y permiso secrets:create (V03)."""
    principal = authenticate_request(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Autenticación válida requerida para generar enlaces efímeros.",
        )

    try:
        require_scope(principal, "secrets:create")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if principal.kind == "human":
        csrf_hdr = (request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token") or "").strip()
        if csrf_hdr and not verify_csrf_token(principal.subject, csrf_hdr):
            raise HTTPException(status_code=403, detail="Token CSRF inválido.")

    _validate_secret_limits(payload)

    data_to_store = {
        "title": payload.title,
        "items": payload.items,
    }
    token, url = create_ephemeral_secret(
        data=data_to_store,
        title=payload.title,
        ttl_seconds=payload.ttl_seconds,
        max_views=payload.max_views,
        actor=principal.subject,
    )
    return JSONResponse(
        {
            "status": "success",
            "token": token,
            "url": url,
            "ttl_seconds": payload.ttl_seconds,
        },
        headers=SECRET_HEADERS,
    )
