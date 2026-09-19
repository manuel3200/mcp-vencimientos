import urllib.parse
from typing import Optional, Dict, Any, Union

from db.schema import DEFAULT_WHATSAPP_TEMPLATES
from db.repositories.settings_repo import get_payment_settings, get_whatsapp_template
from core.utils import format_ars, clean_whatsapp_phone

def get_formatted_payment_methods(settings: Optional[Dict[str, Any]] = None) -> str:
    """Devuelve el bloque de métodos de pago con los datos bancarios formateados para WhatsApp."""
    s = settings or get_payment_settings()
    alias = s.get("alias_mp", "").strip()
    cbu = s.get("cvu_cbu", "").strip()
    holder = s.get("account_holder", "").strip()
    bank = s.get("bank_name", "").strip() or "Mercado Pago / Transferencia"
    usdt = s.get("usdt_address", "").strip()
    extra = s.get("extra_instructions", "").strip()

    if alias or cbu or holder:
        lines = [f"• *{bank}*"]
        if alias:
            lines.append(f"  👉 *Alias:* `{alias}`")
        if cbu:
            lines.append(f"  👉 *CVU/CBU:* `{cbu}`")
        if holder:
            lines.append(f"  👤 *Titular:* {holder}")
        if usdt:
            lines.append(f"• *Cripto / USDT:* `{usdt}`")
        if extra:
            lines.append(f"• {extra}")
        return "\n".join(lines)

    return (
        "• Transferencia Bancaria / CVU / CBU\n"
        "• Mercado Pago\n"
        "• Binance USDT / Cripto"
    )

def render_dynamic_template(template_str: str, context: Dict[str, Any]) -> str:
    """Reemplaza etiquetas dinámicas {tag} en la plantilla de WhatsApp sin fallar si faltan variables."""
    out = template_str
    for key, val in context.items():
        placeholder = f"{{{key}}}"
        out = out.replace(placeholder, str(val) if val is not None else "")
    
    out_lines = []
    for line in out.split("\n"):
        if line.strip() in ("👤 *Perfil:*", "👤 *Perfil Asignado:*", "🔒 *PIN:*", "🔒 *PIN de Perfil:*", "💰 *Valor:*", "💰 *Monto a Renovar:*"):
            continue
        out_lines.append(line)
    
    return "\n".join(out_lines).strip()

def generate_whatsapp_message(
    account_or_id: Union[int, str, Dict[str, Any]],
    message_type: str = "entrega",
    payment_methods: str = ""
) -> Dict[str, Any]:
    """Genera plantillas profesionales y enlaces directos con 1 clic para WhatsApp (wa.me)."""
    from db.repositories.accounts_repo import get_account_detail

    if isinstance(account_or_id, dict):
        acc = account_or_id
    else:
        acc = get_account_detail(account_or_id)
        if not acc:
            return {"success": False, "error": f"No se encontró la cuenta o servicio '{account_or_id}'"}

    client_name = acc.get("client_name") or "Estimado/a"
    platform = acc.get("platform") or "Streaming"
    email = acc.get("email") or ""
    password = acc.get("password") or ""
    profile = acc.get("profile_name") or ""
    pin = acc.get("profile_pin") or ""
    expiry = acc.get("expiry_date") or ""
    price = acc.get("price") or ""
    raw_phone = acc.get("whatsapp") or ""
    clean_phone = clean_whatsapp_phone(raw_phone)
    days_rem = acc.get("days_remaining")

    m_type = message_type.strip().lower()

    if m_type in ("cobro", "recordatorio", "vencimiento"):
        tpl_key = "cobro"
    elif m_type in ("reemplazo", "soporte", "caida"):
        tpl_key = "reemplazo"
    else:
        tpl_key = "entrega"

    tpl = get_whatsapp_template(tpl_key)
    tpl_content = tpl.get("content") or DEFAULT_WHATSAPP_TEMPLATES.get(tpl_key, {}).get("content", "")

    # Días restantes formato
    days_str = ""
    if days_rem is not None:
        if days_rem == 0:
            days_str = " (¡Vence HOY!)"
        elif days_rem == 1:
            days_str = " (vence mañana)"
        elif days_rem > 0:
            days_str = f" (vence en {days_rem} días)"
        else:
            days_str = f" (vencida hace {abs(days_rem)} días)"

    pm_text = payment_methods.strip() if payment_methods else get_formatted_payment_methods()
    p_settings = get_payment_settings()
    price_str = price or format_ars(acc.get("price")) or "Consultar valor"

    context = {
        "cliente": client_name,
        "plataforma": platform,
        "email": email,
        "password": password,
        "perfil": profile,
        "pin": pin,
        "vencimiento": expiry,
        "dias_restantes": days_str,
        "monto": price_str,
        "metodos_pago": pm_text,
        "alias_mp": p_settings.get("alias_mp", ""),
        "cbu": p_settings.get("cvu_cbu", ""),
        "titular": p_settings.get("account_holder", ""),
        "banco": p_settings.get("bank_name", ""),
        "usdt": p_settings.get("usdt_address", "")
    }

    msg = render_dynamic_template(tpl_content, context)

    encoded_text = urllib.parse.quote(msg)
    if clean_phone:
        wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"

    return {
        "success": True,
        "client_name": client_name,
        "platform": platform,
        "email": email,
        "whatsapp": raw_phone,
        "clean_phone": clean_phone,
        "message_type": m_type,
        "message_text": msg,
        "wa_link": wa_url
    }

def generate_consolidated_billing_whatsapp(
    client_id_or_dict: Union[str, int, Dict[str, Any]],
    payment_methods: str = ""
) -> Dict[str, Any]:
    """Genera un mensaje agrupado de cobro y enlace de 1 clic (wa.me) para clientes con 1 o más servicios."""
    from db.repositories.clients_repo import get_client_360_profile

    if isinstance(client_id_or_dict, dict) and "client" in client_id_or_dict:
        profile = client_id_or_dict
    else:
        profile = get_client_360_profile(client_id_or_dict)

    if not profile or not profile.get("client"):
        return {"success": False, "error": "No se encontró el cliente especificado."}

    client = profile["client"]
    client_name = client.get("name") or "Estimado/a"
    raw_phone = client.get("whatsapp") or ""
    clean_phone = client.get("clean_whatsapp") or clean_whatsapp_phone(raw_phone)
    active_accounts = profile.get("active_accounts", [])

    if not active_accounts:
        return {
            "success": False,
            "client_name": client_name,
            "whatsapp": raw_phone,
            "clean_phone": clean_phone,
            "error": "El cliente no tiene suscripciones activas registradas actualmente."
        }

    total_amount = sum(a.get("price_num", 0.0) for a in active_accounts)

    services_lines = []
    for a in active_accounts:
        plat = a.get("platform", "Servicio")
        perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
        pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
        vence = a.get("expiry_date") or "-"
        d_lbl = a.get("days_label", "")
        d_str = f" ({d_lbl})" if d_lbl else ""
        price_str = a.get("price_formatted") or format_ars(a.get("price"))
        services_lines.append(f"• *{plat}*{perf}: `{a.get('email')}`\n  Vence: {vence}{d_str} | Valor: {price_str}")

    services_text = "\n".join(services_lines)

    pm_text = payment_methods.strip() if payment_methods else get_formatted_payment_methods()
    p_settings = get_payment_settings()

    tpl = get_whatsapp_template("cobro_consolidado")
    tpl_content = tpl.get("content") or DEFAULT_WHATSAPP_TEMPLATES.get("cobro_consolidado", {}).get("content", "")

    context = {
        "cliente": client_name,
        "servicios_lista": services_text,
        "monto": format_ars(total_amount),
        "metodos_pago": pm_text,
        "cuentas_cantidad": len(active_accounts),
        "alias_mp": p_settings.get("alias_mp", ""),
        "cbu": p_settings.get("cvu_cbu", ""),
        "titular": p_settings.get("account_holder", ""),
        "banco": p_settings.get("bank_name", ""),
        "usdt": p_settings.get("usdt_address", "")
    }

    msg = render_dynamic_template(tpl_content, context)

    encoded_text = urllib.parse.quote(msg)
    if clean_phone:
        wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"

    return {
        "success": True,
        "client_name": client_name,
        "whatsapp": raw_phone,
        "clean_phone": clean_phone,
        "accounts_count": len(active_accounts),
        "total_amount": total_amount,
        "total_amount_formatted": format_ars(total_amount),
        "message_text": msg,
        "wa_link": wa_url
    }
