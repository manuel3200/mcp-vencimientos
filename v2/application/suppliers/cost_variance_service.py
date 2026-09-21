import logging
from typing import Optional, Dict, Any

from core.audit import log_audit_event
from core.utils import format_ars
from infrastructure.external.telegram.bot_app import send_telegram_message

logger = logging.getLogger("application.suppliers.cost_variance")

DEFAULT_TARGET_MARGIN = 0.35  # 35% de margen bruto objetivo


async def evaluate_cost_variance(
    platform: str,
    service_type: str = "pantalla",
    old_cost: float = 0.0,
    new_cost: float = 0.0,
    threshold_pct: float = 5.0,
    min_ars_diff: float = 200.0,
    current_sale_price: Optional[float] = None,
    notify_telegram: bool = True,
    actor: str = "system"
) -> Dict[str, Any]:
    """Evalúa la variación de costos de proveedores, detecta aumentos/bajas significativas
    y emite alertas de salvaguarda de margen a Telegram sugiriendo el reajuste del catálogo.
    """
    plat_name = str(platform or "").strip()
    stype = str(service_type or "pantalla").strip().lower()
    old_c = float(old_cost or 0.0)
    new_c = float(new_cost or 0.0)

    if not plat_name:
        return {"evaluated": False, "error": "Nombre de plataforma requerido"}

    diff_ars = new_c - old_c
    diff_pct = round(((new_c - old_c) / old_c) * 100, 2) if old_c > 0 else 0.0

    # Determinar si la fluctuación amerita alerta (supera umbral porcentual o monto mínimo en ARS)
    is_significant = bool(old_c > 0 and (abs(diff_pct) >= threshold_pct or abs(diff_ars) >= min_ars_diff))

    # Proyección de precio sugerido al público para preservar el margen objetivo (35%)
    if new_c > 0:
        raw_sugg = new_c / (1.0 - DEFAULT_TARGET_MARGIN)
        recommended_retail = round(raw_sugg / 100.0) * 100.0  # Redondeo amigable al múltiplo de 100
    else:
        recommended_retail = current_sale_price or 0.0

    alert_sent = False

    if is_significant:
        direction_icon = "📈" if diff_ars > 0 else "📉"
        direction_word = "AUMENTO" if diff_ars > 0 else "REDUCCIÓN"
        sign_str = "+" if diff_ars > 0 else ""

        alert_text = (
            f"🚨 <b>ALERTA DE VARIACIÓN DE COSTOS DE PROVEEDOR</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📺 <b>Servicio:</b> <code>{plat_name}</code> ({stype})\n"
            f"🏷️ <b>Costo Anterior:</b> {format_ars(old_c)}\n"
            f"{direction_icon} <b>Nuevo Costo Proveedor:</b> {format_ars(new_c)} (<b>{direction_word}: {sign_str}{format_ars(diff_ars)} / {sign_str}{diff_pct}%</b>)\n\n"
            f"⚠️ <i>Se ha detectado una fluctuación mayor al umbral de seguridad ({threshold_pct}% / {format_ars(min_ars_diff)}).</i>\n\n"
            f"💡 <b>PRECIO DE VENTA SUGERIDO AL PÚBLICO:</b>\n"
            f"• Nuevo valor recomendado: <b>{format_ars(recommended_retail)}</b> (Margen objetivo: 35%)\n"
            f"📌 <i>Revisa el Catálogo de Precios para garantizar la rentabilidad operativa de StreamVault.</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        if notify_telegram:
            try:
                alert_sent = await send_telegram_message(alert_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"No se pudo enviar alerta de costo a Telegram: {e}")

        # Registrar en auditoría inmutable HMAC
        try:
            log_audit_event(
                actor=actor,
                action="SUPPLIER_COST_VARIANCE",
                target_type="supplier_cost",
                target_id=plat_name,
                old_value=f"cost:{old_c}",
                new_value=f"cost:{new_c},diff:{diff_ars},diff_pct:{diff_pct},suggested_price:{recommended_retail}",
                ip_or_source="cost_variance_service"
            )
        except Exception as e:
            logger.warning(f"Error en log de auditoría de variación de costo: {e}")

    return {
        "evaluated": True,
        "is_significant": is_significant,
        "platform": plat_name,
        "service_type": stype,
        "old_cost": old_c,
        "new_cost": new_c,
        "diff_ars": diff_ars,
        "diff_pct": diff_pct,
        "recommended_retail": recommended_retail,
        "recommended_retail_formatted": format_ars(recommended_retail),
        "telegram_alert_sent": alert_sent
    }
