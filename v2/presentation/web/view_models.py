import re
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import database

def render_msg_banner(msg_raw: str, wa_param: str = "", err_param: str = "") -> str:
    if err_param:
        return f"""
        <div style="background:#450a0a; border:1px solid #ef4444; color:#fca5a5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>⚠️ <strong>Error:</strong> {err_param}</span>
            <a href="/" style="color:#fca5a5; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """
    if msg_raw == "combo_sold" and wa_param:
        return f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:gap:10px;">
            <span>🎉 <strong>¡Combo vendido y asignado con éxito!</strong> Los perfiles quedaron asignados y las cuentas sincronizadas.</span>
            <a href="{wa_param}" target="_blank" class="btn" style="background:#25d366; color:#fff; text-decoration:none; font-weight:bold; padding:8px 16px; border-radius:6px;">📲 Enviar Accesos por WhatsApp (1 Clic)</a>
        </div>
        """
    if msg_raw:
        messages = {
            "price_saved": "✅ Precio de catálogo configurado correctamente.",
            "price_deleted": "🗑️ Precio de catálogo eliminado.",
            "combo_created": "🎉 ¡Combo multipantalla creado con éxito! Ya puedes ofrecerlo en el catálogo.",
            "combo_deleted": "🗑️ Combo eliminado del catálogo.",
            "supplier_saved": "👔 Proveedor mayorista guardado con éxito.",
            "supplier_deleted": "🗑️ Proveedor mayorista eliminado.",
            "master_renewed": "✅ Cuenta madre renovada con éxito y perfiles sincronizados.",
            "logs_cleared": "🧹 Buffer de logs en memoria limpiado.",
            "wa_settings_saved": "✅ Configuración de Evolution API WhatsApp guardada con éxito.",
            "wa_test_sent": "✅ Mensaje de prueba enviado exitosamente por WhatsApp.",
            "wa_logged_out": "🚪 Sesión de WhatsApp cerrada correctamente.",
            "wa_webhook_configured": "🔗 Webhook configurado exitosamente en Evolution API.",
            "wa_chatwoot_configured": "🎉 ¡Chatwoot vinculado exitosamente con Evolution API! Ya puedes gestionar tus clientes desde la app móvil.",
            "chatwoot_settings_saved": "✅ Configuración de Chatwoot guardada con éxito.",
            "chatwoot_webhook_configured": "🔗 ¡Webhook de Chatwoot configurado exitosamente!",
            "fallen_authorized": "✅ ¡Reporte de cuenta caída autorizado! Se asignó stock libre y se enviaron los datos al cliente.",
            "fallen_wait": "⏳ Cliente puesto en espera prioritaria.",
            "fallen_dismissed": "🗑️ Reporte de cuenta caída descartado.",
            "fallen_out_of_stock": "⚠️ ¡Sin stock libre para reemplazar!",
            "fallen_already_resolved": "ℹ️ Este reporte ya fue resuelto previamente.",
            "password_rotated": "🔐 ¡Contraseña actualizada con éxito! Transmitida a los clientes.",
            "partial_payment_saved": "💵 ¡Pago parcial / seña registrado con éxito!",
            "payment_reversed": "🔄 Cobro revertido exitosamente y vencimiento previo restaurado.",
            "report_rolled_back": "🔄 Reemplazo de cuenta deshecho exitosamente.",
            "marked_for_baja": "🛑 Cuenta marcada para baja / rotación de clave."
        }
        text = messages.get(msg_raw, msg_raw)
        return f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>{text}</span>
            <a href="/" style="color:#a7f3d0; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """
    return ""

def render_active_accounts_rows(active_accounts: List[Dict[str, Any]]) -> str:
    rows = ""
    for a in active_accounts:
        days = a.get("days_remaining")
        st = a.get("status") or "ocupada"
        debt = float(a.get("debt_balance") or 0.0)

        if st == "por_cambiar_clave":
            badge = "badge-danger"
            badge_txt = "🛑 Rotar Clave"
        elif days is None:
            badge = "badge-warn"; badge_txt = "Fecha inválida"
        elif days < 0:
            badge = "badge-danger"; badge_txt = f"Vencida (-{abs(days)}d)"
        elif days <= 2:
            badge = "badge-warn"; badge_txt = f"¡Vence en {days}d!"
        else:
            badge = "badge-ok"
            badge_txt = f"En {days}d"

        debt_badge = f"<br><span class='badge' style='background:#7c2d12;color:#fdba74;font-size:0.7rem;'>Debe {database.format_ars(debt)}</span>" if debt > 0 else ""

        wa_clean = re.sub(r'[^0-9]', '', a.get("whatsapp", ""))
        wa_link = f'<a href="https://wa.me/{wa_clean}" target="_blank" style="color: #22c55e;">{a.get("whatsapp")}</a>' if wa_clean else '-'
        tg_clean = a.get("telegram", "").lstrip("@")
        tg_link = f'<a href="https://t.me/{tg_clean}" target="_blank" style="color: #38bdf8;">@{tg_clean}</a>' if tg_clean else '-'
        c_type_val = (a.get("client_type") or "").lower()
        if "vip" in c_type_val:
            client_tag = f"👑 {a.get('client_name')} <small style='color:#f59e0b;font-weight:700;'>(VIP)</small>"
        elif "revend" in c_type_val:
            client_tag = f"💼 {a.get('client_name')}"
        else:
            client_tag = f"👤 {a.get('client_name')}"

        client_id_val = a.get("client_id")
        client_click = f'onclick="openClient360Modal({client_id_val})" style="cursor:pointer;color:#38bdf8;text-decoration:underline;" title="Ver Ficha 360° del Cliente"' if client_id_val else ''
        btn_360 = f'<button type="button" onclick="openClient360Modal({client_id_val})" class="btn-action" style="background:#1e293b;border:1px solid #38bdf8;color:#38bdf8;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Ver Ficha 360°">👤 360°</button>' if client_id_val else ''

        perf = f"<br><small style='color:#94a3b8;'>Perf: {a['profile_name']}</small>" if a.get("profile_name") else ""
        pin = f"<small style='color:#94a3b8;'>PIN: {a['profile_pin']}</small>" if a.get("profile_pin") else ""

        is_http_custom = (a.get("platform") == "HTTP Custom")
        if is_http_custom:
            hwid_val = a.get('password') or ''
            hwid_display = (hwid_val[:8] + '...' + hwid_val[-6:]) if len(hwid_val) > 16 else hwid_val
            cred_html = f"<span style='color:#a78bfa;font-size:0.75rem;font-weight:600;'>👤 User:</span> <code>{a['email']}</code><br><span style='color:#38bdf8;font-size:0.75rem;font-weight:600;'>🔑 HWID:</span> <code title='{hwid_val}'>{hwid_display}</code>"
            plat_badge = '<span class="badge" style="background:#312e81;color:#c7d2fe;">⚡ HTTP Custom</span>'
        else:
            cred_html = f"<code>{a['email']}</code><br><code>{a['password']}</code> {pin}"
            plat_badge = f'<span class="badge" style="background:#1e3a8a;color:#93c5fd;">{a["platform"]}</span>'

        wa_cobro = database.generate_whatsapp_message(a, message_type="cobro")
        wa_link_cobro = wa_cobro.get("wa_link", "#")
        wa_entrega = database.generate_whatsapp_message(a, message_type="entrega")
        wa_link_entrega = wa_entrega.get("wa_link", "#")

        is_recent_purchase = (days is not None and days > 15)
        if is_recent_purchase:
            btn_pago = f"""
            <form action="/api/collect-payment/{a['id']}?extend=0" method="POST" style="display:inline;" onsubmit="return confirm('¿Confirmar que {a['client_name']} pagó su compra inicial? (Mantiene el vencimiento actual en {a['expiry_date']})');">
                <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Confirmar Pago de Compra">💵 Pagó Compra</button>
            </form>
            """
        else:
            btn_pago = f"""
            <form action="/api/collect-payment/{a['id']}?extend=1" method="POST" style="display:inline;" onsubmit="return confirm('¿Registrar cobro y renovar 30 días para {a['client_name']}?');">
                <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Registrar Cobro y Renovar +30 días">🔄 Renovar (+30d)</button>
            </form>
            """

        safe_cname = re.sub(r"['\"\\\r\n]", " ", str(a.get('client_name') or 'Cliente')).strip()
        safe_email = re.sub(r"['\"\\\r\n]", " ", str(a.get('email') or '')).strip()
        safe_plat = re.sub(r"['\"\\\r\n]", " ", str(a.get('platform') or '')).strip()
        acc_id = a.get('id', '')

        btn_rotate = f'<button type="button" onclick="openRotatePasswordModal(\'{safe_email}\', \'{safe_plat}\', {acc_id})" class="btn-action" style="background:#be185d;color:white;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Rotar Contraseña y Notificar Co-Usuarios">🔐 Rotar Clave</button>'
        btn_partial = f'<button type="button" onclick="openPartialPaymentModal({acc_id}, \'{safe_cname}\', \'{safe_plat}\', {debt})" class="btn-action" style="background:#78350f;color:#fde68a;padding:4px 6px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Registrar Pago Parcial">💵 Parcial</button>'
        btn_baja = f'<form action="/api/accounts/mark-baja/{acc_id}" method="POST" style="display:inline;" onsubmit="return confirm(\'¿Marcar cuenta #{acc_id} para baja / cambio de clave? Se detendrán los avisos automáticos diarios.\');"><button type="submit" class="btn-action" style="color:#f43f5e;padding:4px 6px;border-radius:5px;font-size:0.75rem;" title="Marcar para Baja / Detener alertas">🛑</button></form>' if st != 'por_cambiar_clave' else ''

        rows += f"""
        <tr>
            <td><strong {client_click}>{client_tag}</strong><br><small style="color:#64748b;">{a.get('client_code') or ''}</small></td>
            <td>{wa_link}<br>{tg_link}</td>
            <td>{plat_badge}{perf}</td>
            <td>{cred_html}</td>
            <td><code>{a['expiry_date']}</code></td>
            <td><span class="badge {badge}">{badge_txt}</span>{debt_badge}</td>
            <td><strong>{a.get('price') or '-'}</strong></td>
            <td style="white-space: nowrap;">
                {btn_360}
                <a href="{wa_link_cobro}" target="_blank" class="btn-action" style="background:#15803d;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con mensaje de cobro listo">💬 Cobro</a>
                <a href="{wa_link_entrega}" target="_blank" class="btn-action" style="background:#0284c7;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con credenciales listas">📩 Datos</a>
                {btn_pago}
                {btn_partial}
                {btn_rotate if st == 'por_cambiar_clave' else ''}
                {btn_baja}
                <form action="/api/mark-fallen/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar {a['email']} como caída?');">
                    <button type="submit" class="btn-action btn-warn" style="padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Reportar Caída">🚨</button>
                </form>
                <form action="/api/delete-account/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar cuenta?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas activas asignadas actualmente.</td></tr>"

def render_stock_rows(free_stock: List[Dict[str, Any]]) -> str:
    rows = ""
    for s in free_stock:
        perf = f" (Perf: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        rows += f"""
        <tr>
            <td><strong>{s['platform']}</strong>{perf}</td>
            <td><code>{s['email']}</code></td>
            <td><code>{s['password']}</code>{pin}</td>
            <td>{s.get('cost') or '-'}</td>
            <td><span class="badge badge-ok">Disponible</span></td>
            <td>
                <form action="/api/delete-account/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar del stock?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;">🗑️</button>
                </form>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='6' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas libres en stock. Agrega cuentas o pídeselo a Gemini.</td></tr>"

def render_stock_health_html(stock_health: Dict[str, Any]) -> str:
    html = ""
    for p in stock_health.get("platforms", []):
        st = p["status"]
        if st == "agotado":
            st_color = "#ef4444"; st_bg = "#450a0a"; st_border = "#991b1b"; st_badge = "🔴 Agotado"
        elif st == "bajo":
            st_color = "#f59e0b"; st_bg = "#451a03"; st_border = "#92400e"; st_badge = "🟡 Stock Bajo"
        else:
            st_color = "#10b981"; st_bg = "#064e3b"; st_border = "#047857"; st_badge = "🟢 Óptimo"

        html += f"""
        <div style="background:{st_bg};border:1px solid {st_border};border-radius:10px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <strong style="color:#f1f5f9;font-size:0.95rem;">{p['platform']}</strong>
                <span class="badge" style="background:{st_border};color:{st_color};">{st_badge}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;">
                <span style="font-size:1.3rem;font-weight:800;color:{st_color};">{p['free_count']} <small style="font-size:0.75rem;color:#94a3b8;font-weight:normal;">libres</small></span>
                <small style="color:#94a3b8;">Activas: {p['occupied_count']}</small>
            </div>
            <form action="/api/set-stock-threshold" method="POST" style="display:flex;align-items:center;gap:6px;font-size:0.75rem;border-top:1px solid {st_border};padding-top:8px;">
                <input type="hidden" name="platform" value="{p['platform']}">
                <span style="color:#94a3b8;">Mín:</span>
                <input type="number" name="min_stock" value="{p['min_threshold']}" min="0" max="99" style="width:45px;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:4px;padding:2px 4px;text-align:center;">
                <button type="submit" class="btn-action" style="padding:2px 6px;font-size:0.7rem;background:#1e293b;color:#38bdf8;border-color:#334155;">Guardar</button>
            </form>
        </div>
        """
    return html or "<div style='color:#64748b;padding:15px;grid-column:1/-1;'>No hay plataformas registradas en el inventario aún.</div>"

def render_fallen_rows(fallen_accounts: List[Dict[str, Any]]) -> str:
    rows = ""
    for f in fallen_accounts:
        client_name = f.get("client_name") or "Sin cliente asignado"
        rows += f"""
        <tr>
            <td><strong>{f['platform']}</strong></td>
            <td><code>{f['email']}</code></td>
            <td><strong>{client_name}</strong></td>
            <td><small style="color:#fca5a5;">{f.get('notes') or 'Reportada'}</small></td>
            <td style="white-space:nowrap;">
                <form action="/api/reactivate-fallen/{f['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Reactivar esta cuenta y volver a ponerla en servicio (no estaba caída)?');">
                    <button type="submit" class="btn-action" style="background:#059669;color:white;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-right:4px;" title="Reactivar cuenta (no estaba caída)">✅ Reactivar / En Servicio</button>
                </form>
                <form action="/api/auto-replace/{f['id']}" method="POST" style="display:inline;">
                    <button type="submit" class="btn-action btn-replace" title="Buscar reemplazo en stock de la misma plataforma">🔄 Reemplazar</button>
                </form>
                <form action="/api/delete-account/{f['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar definitivamente este registro caído?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;padding:4px 7px;border-radius:5px;font-size:0.75rem;margin-left:4px;" title="Eliminar cuenta">🗑️</button>
                </form>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='5' style='text-align:center;color:#10b981;padding:20px;'>🎉 ¡No hay cuentas caídas! Todo el sistema está funcionando.</td></tr>"

def render_transactions_rows(transactions: List[Dict[str, Any]]) -> str:
    rows = ""
    for t in transactions:
        c_name = t.get("client_name") or "Venta General"
        plat = t.get("platform") or "Streaming"
        t_ctype = (t.get("client_type") or "").lower()
        if "vip" in t_ctype:
            c_type = "👑 VIP"
        elif "revend" in t_ctype:
            c_type = "💼 Revendedor"
        else:
            c_type = "👤 Final"
        is_rev = t.get("status") == "reversed"
        amt_html = f"<s style='color:#ef4444;'>{database.format_ars(t['amount'])}</s>" if is_rev else f"<strong style='color:#10b981;'>+{database.format_ars(t['amount'])}</strong>"
        profit_html = "<small style='color:#64748b;'>Anulado</small>" if is_rev else f"<strong style='color:#38bdf8;'>+{database.format_ars(t['profit'])}</strong>"
        rev_btn = f"""
        <form action="/api/payments/reverse/{t['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Revertir y anular este cobro #{t['id']}? Se restaurará el vencimiento previo.');">
            <button type="submit" class="btn-action" style="color:#f59e0b;font-size:0.7rem;padding:2px 5px;border-radius:4px;" title="Revertir y anular cobro">🔄</button>
        </form>
        """ if not is_rev else "<span class='badge' style='background:#450a0a;color:#fca5a5;font-size:0.65rem;'>Revertido</span>"

        rows += f"""
        <tr>
            <td><small style="color:#94a3b8;">{t['created_at'][:16]}</small></td>
            <td><strong>{c_name}</strong> ({c_type})</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span></td>
            <td>{amt_html}</td>
            <td><span style="color:#f59e0b;">-{database.format_ars(t['cost'])}</span></td>
            <td>{profit_html}</td>
            <td><small>{t.get('payment_method') or 'Transf.'}</small></td>
            <td>{rev_btn}</td>
        </tr>
        """
    return rows or "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay transacciones registradas este mes aún.</td></tr>"

def render_screens_overview_html(screens_overview: List[Dict[str, Any]]) -> str:
    screens_html = ""
    for s in screens_overview:
        fill_pct = s['occupancy_rate']
        chips_html = ""
        for p in s['profiles']:
            st = p['status']
            pin_label = f" (PIN: {p['profile_pin']})" if p.get('profile_pin') else ""
            if st == 'ocupada':
                c_name = p.get('client_name') or 'Cliente'
                wa_cobro = database.generate_whatsapp_message(p, "cobro").get("wa_link", "#")
                wa_datos = database.generate_whatsapp_message(p, "entrega").get("wa_link", "#")
                chips_html += f"""
                <div class="chip chip-occupied">
                    <div>
                        <strong>{p['profile_name']}</strong>{pin_label}: <span>{c_name}</span>
                        <small style='color:#94a3b8;margin-left:6px;'>(Vence: {p.get('expiry_date')})</small>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <a href="{wa_cobro}" target="_blank" class="btn-action" style="background:#15803d;color:white;text-decoration:none;padding:2px 6px;font-size:0.7rem;" title="Cobrar WhatsApp">💬</a>
                        <a href="{wa_datos}" target="_blank" class="btn-action" style="background:#0284c7;color:white;text-decoration:none;padding:2px 6px;font-size:0.7rem;" title="Datos WhatsApp">📩</a>
                    </div>
                </div>
                """
            elif st == 'libre':
                chips_html += f"""
                <div class="chip chip-free">
                    <span><strong>{p['profile_name']}</strong>{pin_label}: <em>Disponible para venta</em></span>
                    <span class="badge badge-ok" style="font-size:0.7rem;">Libre</span>
                </div>
                """
            elif st == 'caida':
                chips_html += f"""
                <div class="chip chip-fallen">
                    <span><strong>{p['profile_name']}</strong>{pin_label}: 🚨 Caída ({p.get('client_name') or 'Sin cliente'})</span>
                    <span class="badge badge-danger" style="font-size:0.7rem;">Caída</span>
                </div>
                """

        screens_html += f"""
        <div class="screen-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <h4 style="margin:0; font-size:1.05rem; color:#f8fafc;">{s['platform']}</h4>
                <form action="/api/report-master-fallen" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar toda la cuenta madre como caída? Se afectarán todas las pantallas.');">
                    <input type="hidden" name="email" value="{s['email']}">
                    <button type="submit" class="btn-action btn-warn" style="font-size:0.75rem; padding:3px 8px;">🚨 Reportar Caída Madre</button>
                </form>
            </div>
            <p style="margin:0 0 10px 0; font-size:0.85rem; color:#94a3b8;">
                Correo: <code>{s['email']}</code> | Clave: <code>{s['password']}</code>
            </p>
            <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;">
                <span>Ocupación: {s['occupied_count']}/{s['total_profiles']} ({fill_pct}%)</span>
                <span>{s['free_count']} libres</span>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width:{fill_pct}%;"></div>
            </div>
            <div class="profile-chips">
                {chips_html}
            </div>
        </div>
        """
    return screens_html or "<div style='grid-column:1/-1;text-align:center;color:#64748b;padding:30px;'>No hay cuentas registradas con pantallas múltiples aún.</div>"

def render_catalog_rows(catalog_items: List[Dict[str, Any]]) -> str:
    rows = ""
    for c in catalog_items:
        if c.get("service_type") == "hwid":
            stype_badge = "⚡ HWID / VPN"
        elif c.get("service_type") == "pantalla":
            stype_badge = "📱 Pantalla"
        else:
            stype_badge = "👑 Completa"
        vip_info = f"<br><small style='color:#f59e0b;'>👑 VIP: {c.get('price_reseller_vip_formatted')}</small>" if c.get('price_reseller_vip') and c['price_reseller_vip'] > 0 else ""
        rows += f"""
        <tr>
            <td><strong>{c['platform']}</strong></td>
            <td><span class="badge" style="background:#1e293b;color:#94a3b8;">{stype_badge}</span></td>
            <td style="color:#f59e0b;">{c['cost_price_formatted']}</td>
            <td><strong style="color:#10b981;">{c['price_final_formatted']}</strong></td>
            <td><strong style="color:#38bdf8;">{c['price_reseller_formatted']}</strong>{vip_info}</td>
            <td>
                <small style="color:#10b981;">+{database.format_ars(c['profit_final'])} (Final)</small><br>
                <small style="color:#38bdf8;">+{database.format_ars(c['profit_reseller'])} (Rev.)</small>
            </td>
            <td>
                <form action="/api/catalog/delete/{c['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este precio del catálogo?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay precios configurados en el catálogo aún.</td></tr>"

def render_combos_html(combos_list: List[Dict[str, Any]]) -> str:
    html = ""
    for cb in combos_list:
        pills = "".join([f'<span class="badge" style="background:#0b0f19;border:1px solid #334155;color:#38bdf8;margin:2px;">{it["platform"]}</span>' for it in cb["items"]])
        html += f"""
        <div style="background:#0b0f19;border:1px solid #1e293b;border-radius:12px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                    <h4 style="margin:0;color:#f8fafc;font-size:1.05rem;">{cb['name']}</h4>
                    <form action="/api/combos/delete/{cb['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este combo?');">
                        <button type="submit" class="btn-action" style="background:transparent;border:none;color:#ef4444;cursor:pointer;font-size:1.1rem;padding:0;" title="Eliminar Combo">✕</button>
                    </form>
                </div>
                <p style="margin:0 0 10px 0;font-size:0.8rem;color:#94a3b8;">{cb.get('description') or 'Pack promocional'}</p>
                <div style="margin-bottom:12px;display:flex;flex-wrap:wrap;gap:4px;">{pills}</div>
            </div>
            <div style="border-top:1px solid #1e293b;padding-top:12px;">
                <div style="display:flex;justify-content:space-between;margin-bottom:10px;font-size:0.85rem;">
                    <span style="color:#94a3b8;">Final: <strong style="color:#10b981;">{cb['price_final_formatted']}</strong></span>
                    <span style="color:#94a3b8;">Rev: <strong style="color:#38bdf8;">{cb['price_reseller_formatted']}</strong></span>
                </div>
                <button type="button" onclick="openSellComboModal('{cb['id']}', '{cb['name']}')" class="btn" style="width:100%;background:#0284c7;padding:8px;font-size:0.85rem;font-weight:bold;">⚡ Vender Combo (1 Toque)</button>
            </div>
        </div>
        """
    return html or "<div style='color:#64748b;padding:20px;grid-column:1/-1;'>No hay combos activos configurados aún.</div>"

def render_suppliers_rows(suppliers_list: List[Dict[str, Any]]) -> str:
    rows = ""
    for s in suppliers_list:
        clean_contact = re.sub(r'[^0-9]', '', s.get("contact") or "")
        contact_html = "-"
        if s.get("contact"):
            if "@" in s["contact"]:
                tg_user = s["contact"].replace("@", "").strip()
                contact_html = f'<a href="https://t.me/{tg_user}" target="_blank" style="color:#38bdf8;">@{tg_user}</a>'
            elif clean_contact:
                contact_html = f'<a href="https://wa.me/{clean_contact}" target="_blank" style="color:#22c55e;">{s["contact"]}</a>'
            else:
                contact_html = s["contact"]

        safe_name = s['name'].replace("'", "\\'")
        safe_contact = (s.get('contact') or '').replace("'", "\\'")
        safe_payment = (s.get('payment_info') or '').replace("'", "\\'")
        safe_notes = (s.get('notes') or '').replace("'", "\\'")

        rows += f"""
        <tr>
            <td><strong>{s['name']}</strong></td>
            <td>{contact_html}</td>
            <td><code>{s.get('payment_info') or '-'}</code></td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{s['master_accounts_count']} cuentas ({s['profiles_count']} perfiles)</span></td>
            <td><strong style="color:#f59e0b;">{s['total_spent_formatted']}</strong></td>
            <td><small style="color:#94a3b8;">{s.get('notes') or '-'}</small></td>
            <td>
                <div style="display:flex;gap:6px;">
                    <button type="button" class="btn-action" style="color:#38bdf8;" onclick="openSupplierModal('{s['id']}', '{safe_name}', '{safe_contact}', '{safe_payment}', '{safe_notes}')" title="Editar">✏️</button>
                    <form action="/api/suppliers/delete/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar al proveedor {safe_name}?');">
                        <button type="submit" class="btn-action" style="color:#ef4444;" title="Eliminar">🗑️</button>
                    </form>
                </div>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay proveedores mayoristas registrados aún.</td></tr>"

def render_master_accounts_rows(master_accounts_list: List[Dict[str, Any]]) -> str:
    rows = ""
    for m in master_accounts_list:
        sup_days = m.get("days_remaining_supplier")
        if sup_days is None:
            s_badge = '<span class="badge badge-warn">Sin Fecha</span>'
        elif sup_days < 0:
            s_badge = f'<span class="badge badge-danger">Vencida (-{abs(sup_days)}d)</span>'
        elif sup_days <= 3:
            s_badge = f'<span class="badge badge-warn">¡Vence en {sup_days}d!</span>'
        else:
            s_badge = f'<span class="badge badge-ok">En {sup_days}d</span>'

        if m.get("risk_mismatch"):
            risk_cell = f'<span class="badge badge-danger">🚨 Desfase de Corte</span><br><small style="color:#f87171;font-size:0.75rem;">{m.get("mismatch_warning")}</small>'
        else:
            risk_cell = '<span class="badge badge-ok" style="font-size:0.75rem;">✓ Sincronizado</span>'

        safe_email = m['email'].replace("'", "\\'")
        safe_plat = m['platform'].replace("'", "\\'")
        cur_expiry = m.get('supplier_expiry_date') or ''
        cur_cost = m.get('supplier_cost') or 0.0

        rows += f"""
        <tr style="{'background:rgba(239,68,68,0.06);' if m.get('risk_mismatch') else ''}">
            <td><strong>{m['platform']}</strong></td>
            <td><code>{m['email']}</code></td>
            <td><span class="badge" style="background:#1e293b;color:#cbd5e1;">{m['supplier_name']}</span></td>
            <td><code>{cur_expiry or 'No fijada'}</code> {s_badge}</td>
            <td>{m.get('profiles_occupied', 0)} / {m.get('profiles_total', 0)} ({m.get('occupancy_rate', 0)}%)</td>
            <td style="color:#f59e0b;font-weight:600;">{m['supplier_cost_formatted']}</td>
            <td>{risk_cell}</td>
            <td>
                <button type="button" onclick="openRenewMasterModal('{safe_email}', '{safe_plat}', '{cur_expiry}', {cur_cost})" class="btn" style="background:#0284c7;padding:5px 10px;font-size:0.8rem;">🔄 Renovar</button>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas madre registradas.</td></tr>"

def render_logs_html(recent_logs: List[Dict[str, Any]]) -> str:
    html = ""
    for l in recent_logs:
        lvl = l.get("level", "INFO").upper()
        lvl_class = "error" if lvl in ("ERROR", "CRITICAL") else ("warn" if lvl == "WARNING" else "info")
        tb_str = l.get("traceback") or ""
        safe_tb = tb_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        tb_html = f'<div class="log-tb">{safe_tb}</div>' if safe_tb else ""
        safe_msg = l.get("message", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += f"""
        <div class="log-row log-{lvl_class}">
            <span class="log-time">{l['timestamp']}</span>
            <span class="log-badge log-badge-{lvl_class}">{lvl}</span>
            <span class="log-mod">[{l['module']}]</span>
            <span class="log-txt">{safe_msg}</span>
            {tb_html}
        </div>
        """
    return html or "<div style='color:#64748b;padding:20px;text-align:center;'>No hay logs registrados en memoria.</div>"

def render_pending_payments_rows(pending_payments: List[Dict[str, Any]]) -> str:
    rows = ""
    for p in pending_payments:
        pid = p["id"]
        c_name = (p.get("client_name") or "Cliente").replace("\r", "").replace("\n", " ").strip()
        c_phone = p.get("sender_phone") or p.get("client_whatsapp") or ""
        wa_link = f'<a href="https://wa.me/{c_phone}" target="_blank" style="color: #22c55e; font-weight: 600;">+{c_phone}</a>' if c_phone else '-'
        plat = (p.get("platform") or "Suscripción").replace("\r", "").replace("\n", " ").strip()
        acc_email = p.get("account_email") or "-"
        amt_val = float(p.get("amount") or 0.0)
        amt_str = p.get("amount_formatted") or (database.format_ars(amt_val))
        bank_info = p.get("bank") or "Transferencia"
        op_info = f"<br><small style='color:#94a3b8;'>Op: #{p.get('operation_id')}</small>" if p.get("operation_id") else ""
        date_str = p.get("created_at", "")[:16]

        has_receipt = bool(p.get("receipt_base64"))
        mime_type = (p.get("receipt_mimetype") or "").lower()
        clean_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', p.get("receipt_filename") or "comprobante")
        is_pdf = "pdf" in mime_type or clean_filename.lower().endswith(".pdf")

        if has_receipt:
            is_pdf_js = "true" if is_pdf else "false"
            if is_pdf:
                receipt_html = f'<button type="button" onclick="viewReceipt({pid}, {is_pdf_js}, \'{clean_filename}\')" class="btn-action" style="background:#1e293b;border:1px solid #f43f5e;color:#f43f5e;padding:3px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;">📄 Ver PDF</button>'
            else:
                receipt_html = f'<button type="button" onclick="viewReceipt({pid}, {is_pdf_js}, \'{clean_filename}\')" class="btn-action" style="background:#1e293b;border:1px solid #38bdf8;color:#38bdf8;padding:3px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;">🖼️ Ver Imagen</button>'
        elif p.get("raw_text"):
            safe_raw = p.get("raw_text")[:50].replace('"', '&quot;').replace("'", "&#39;").replace("\n", " ")
            receipt_html = f'<span title="{safe_raw}" style="color:#94a3b8;font-size:0.75rem;cursor:help;">📝 Texto</span>'
        else:
            receipt_html = '<span style="color:#64748b;font-size:0.75rem;">Sin archivo</span>'

        js_client_name = re.sub(r"['\"\\\r\n]", " ", str(c_name)).strip()
        js_plat = re.sub(r"['\"\\\r\n]", " ", str(plat)).strip()
        acc_id_arg = p.get("account_id") or 0

        rows += f"""
        <tr>
            <td style="width:36px;text-align:center;">
                <input type="checkbox" class="pending-checkbox cursor-pointer" value="{pid}" onchange="updatePendingSelection()" style="width:16px;height:16px;accent-color:#10b981;">
            </td>
            <td><strong style="color:#38bdf8;font-size:0.95rem;">#P{pid}</strong></td>
            <td><strong>{c_name}</strong><br><small>{wa_link}</small></td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span><br><code style="font-size:0.75rem;">{acc_email}</code></td>
            <td><strong style="color:#10b981;font-size:0.95rem;">{amt_str}</strong><br><small style="color:#cbd5e1;">{bank_info}</small>{op_info}</td>
            <td><small style="color:#94a3b8;">{date_str}</small></td>
            <td>{receipt_html}</td>
            <td style="white-space:nowrap;">
                <button type="button" onclick="openApprovePaymentModal({pid}, '{js_client_name}', '{js_plat}', '{amt_str}')" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;" title="Aprobar Pago">✅ Aprobar</button>
                <button type="button" onclick="openPartialPaymentModal('{acc_id_arg}', '{js_client_name}', '{js_plat}', 0, {pid}, {amt_val})" class="btn-action" style="background:#78350f;color:#fde68a;border:none;padding:4px 6px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-left:4px;cursor:pointer;" title="Registrar Pago Parcial">💵 Parcial</button>
                <button type="button" onclick="openRejectPaymentModal({pid}, '{js_client_name}')" class="btn-action" style="background:#dc2626;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-left:4px;cursor:pointer;" title="Denegar Pago">❌ Denegar</button>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='8' style='text-align:center;color:#10b981;padding:24px;'>🎉 ¡No hay pagos pendientes de aprobación! Todos los cobros están al día.</td></tr>"

def render_fallen_reports_rows(fallen_reports: List[Dict[str, Any]]) -> str:
    rows = ""
    for r in fallen_reports:
        rid = r["id"]
        c_name = r.get("client_name") or "Cliente"
        c_phone = r.get("sender_phone") or r.get("client_whatsapp") or ""
        wa_link = f'<a href="https://wa.me/{c_phone}" target="_blank" style="color: #22c55e; font-weight: 600;">+{c_phone}</a>' if c_phone else '-'
        plat = r.get("platform") or "Streaming"
        acc_email = r.get("account_email") or "-"
        prof = f" ({r.get('profile_name')})" if r.get('profile_name') else ""
        raw_msg = r.get("raw_message") or ""
        safe_raw = raw_msg[:80].replace('"', '&quot;').replace("'", "&#39;")
        st = r.get("status") or "pending"
        date_str = (r.get("created_at") or "")[:16]

        if st == "pending":
            st_badge = '<span class="badge badge-danger" style="font-size:0.75rem;">🚨 Pendiente</span>'
        elif st == "waiting":
            st_badge = '<span class="badge" style="background:#b45309;color:#fef3c7;font-size:0.75rem;">⏳ En Espera</span>'
        elif st == "resolved":
            st_badge = '<span class="badge badge-ok" style="font-size:0.75rem;">✓ Resuelto</span>'
        else:
            st_badge = f'<span class="badge" style="background:#334155;color:#94a3b8;font-size:0.75rem;">{st}</span>'

        safe_client_name = c_name.replace("'", "\\'")
        actions_html = ""
        if st in ("pending", "waiting"):
            actions_html += f"""
            <form action="/api/fallen-reports/authorize/{rid}" method="POST" style="display:inline;" onsubmit="return confirm('¿Autorizar y asignar nueva cuenta libre a {safe_client_name}?');">
                <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Autorizar Reemplazo">🔄 Cambiar</button>
            </form>
            """
            if st == "pending":
                actions_html += f"""
                <form action="/api/fallen-reports/wait/{rid}" method="POST" style="display:inline;" onsubmit="return confirm('¿Poner en espera a {safe_client_name}? Se le avisará que aguarde.');">
                    <button type="submit" class="btn-action" style="background:#d97706;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-left:4px;" title="Poner en Espera">⏳ Esperar</button>
                </form>
                """
            actions_html += f"""
            <form action="/api/fallen-reports/dismiss/{rid}" method="POST" style="display:inline;" onsubmit="return confirm('¿Descartar este reporte?');">
                <button type="submit" class="btn-action" style="background:#475569;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-left:4px;" title="Descartar">✕</button>
            </form>
            """
        else:
            notes_str = (r.get("admin_notes") or "Completado").replace('"', '&quot;')
            actions_html = f'<small style="color:#94a3b8;" title="{notes_str}">Finalizado</small>'
            if r.get("reassigned_account_id"):
                actions_html += f"""
                <form action="/api/fallen-reports/rollback/{rid}" method="POST" style="display:inline;margin-left:6px;" onsubmit="return confirm('¿Deshacer reemplazo del reporte #C{rid}? Se devolverá la cuenta asignada a stock y se reactivará la anterior.');">
                    <button type="submit" class="btn-action" style="color:#f59e0b;font-size:0.7rem;padding:2px 6px;" title="Deshacer reemplazo y restaurar inventario">🔄 Deshacer</button>
                </form>
                """

        rows += f"""
        <tr>
            <td><strong style="color:#f43f5e;font-size:0.95rem;">#C{rid}</strong></td>
            <td><strong>{c_name}</strong><br><small>{wa_link}</small></td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span><br><code style="font-size:0.75rem;">{acc_email}{prof}</code></td>
            <td><small style="color:#cbd5e1;" title="{safe_raw}">{safe_raw or 'Reporte de caída'}</small></td>
            <td>{st_badge}</td>
            <td><small style="color:#94a3b8;">{date_str}</small></td>
            <td style="white-space:nowrap;">{actions_html}</td>
        </tr>
        """
    return rows or "<tr><td colspan='7' style='text-align:center;color:#10b981;padding:24px;'>🎉 ¡No hay incidentes ni reportes de cuentas caídas pendientes!</td></tr>"

def render_client_select_options(clients_list: List[Dict[str, Any]]) -> str:
    opts = ""
    for c in clients_list:
        ctype = (c.get("client_type") or "").lower()
        if "vip" in ctype:
            badge_icon = "👑 VIP"
        elif "revend" in ctype:
            badge_icon = "💼 Revendedor"
        else:
            badge_icon = "👤 Final"
        opts += f'<option value="{c["id"]}">{c["name"]} ({c.get("client_code") or ""}) - {badge_icon}</option>'
    return opts

def render_http_custom_rows(custom_accounts: List[Dict[str, Any]]) -> str:
    """Renderiza las filas HTML dedicadas para la tabla de servidores HTTP Custom."""
    rows = ""
    today = date.today()
    for a in custom_accounts:
        aid = a["id"]
        user = a.get("email") or "Usuario"
        hwid = a.get("password") or ""
        c_name = a.get("client_name") or "Sin asignar"
        c_phone = a.get("client_phone") or ""
        wa_link = f'<a href="https://wa.me/{c_phone}" target="_blank" style="color:#22c55e;font-weight:600;">+{c_phone}</a>' if c_phone else '-'
        
        c_type = (a.get("client_type") or "consumidor_final").lower()
        if "vip" in c_type:
            t_badge = '<span class="badge" style="background:#4a1d96;color:#e9d5ff;font-size:0.75rem;">👑 VIP ($3.500)</span>'
        elif "revend" in c_type:
            t_badge = '<span class="badge" style="background:#1e3a8a;color:#93c5fd;font-size:0.75rem;">💼 Revendedor ($4.500)</span>'
        else:
            t_badge = '<span class="badge" style="background:#334155;color:#cbd5e1;font-size:0.75rem;">👤 Final ($8.000)</span>'

        exp_str = a.get("expiry_date") or "-"
        days_badge = '<span class="badge badge-neutral">-</span>'
        if exp_str and exp_str != "-":
            try:
                d_exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
                days_left = (d_exp - today).days
                if days_left > 5:
                    days_badge = f'<span class="badge badge-ok" style="font-size:0.75rem;">Quedan {days_left}d</span>'
                elif 0 <= days_left <= 5:
                    days_badge = f'<span class="badge badge-warn" style="font-size:0.75rem;">⚠️ {days_left}d restantes</span>'
                else:
                    days_badge = f'<span class="badge badge-danger" style="font-size:0.75rem;">🚨 Vencido hace {abs(days_left)}d</span>'
            except Exception:
                pass

        short_hwid = f"{hwid[:10]}...{hwid[-8:]}" if len(hwid) > 20 else (hwid or "Sin HWID")
        safe_hwid = hwid.replace("'", "\\'").replace('"', '&quot;')
        safe_user = user.replace("'", "\\'").replace('"', '&quot;')

        copy_hwid_btn = f"""
        <button type="button" onclick="navigator.clipboard.writeText('{safe_hwid}'); alert('HWID copiado al portapapeles: {safe_hwid}');" 
                class="btn-action" style="padding:2px 6px;font-size:0.7rem;background:#1e293b;color:#38bdf8;border:1px solid #334155;cursor:pointer;" title="Copiar HWID completo">
            📋 Copiar
        </button>
        """ if hwid else ""

        rows += f"""
        <tr>
            <td><strong style="color:#38bdf8;font-size:0.95rem;"><code>{user}</code></strong></td>
            <td><code style="font-size:0.75rem;color:#cbd5e1;" title="{safe_hwid}">{short_hwid}</code> {copy_hwid_btn}</td>
            <td><strong>{c_name}</strong><br><small>{wa_link}</small></td>
            <td>{t_badge}</td>
            <td><strong>{exp_str}</strong><br>{days_badge}</td>
            <td style="white-space:nowrap;">
                <form action="/api/http-custom/renew/{aid}" method="POST" style="display:inline;" onsubmit="return confirm('¿Renovar servidor HTTP Custom de {safe_user} por 30 días?');">
                    <input type="hidden" name="days" value="30">
                    <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;" title="Extender 30 días">🔄 Renovar 30d</button>
                </form>
            </td>
        </tr>
        """
    return rows or "<tr><td colspan='6' style='text-align:center;color:#94a3b8;padding:24px;'>No hay servidores HTTP Custom registrados aún. Registra ventas por WhatsApp o envía el formato estándar.</td></tr>"

