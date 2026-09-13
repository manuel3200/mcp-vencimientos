import re
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

import database
import system_logger
from core.security import verify_session_cookie
from core.templates import render_template

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active_accounts = database.get_active_accounts()
    free_stock = database.get_free_stock()
    fallen_accounts = database.get_fallen_accounts()
    finance = database.get_financial_balance()
    transactions = database.get_recent_transactions(limit=15)
    catalog_items = database.get_price_catalog()
    combos_list = database.get_combos()
    all_clients_list = database.list_all_clients()
    client_select_options = "".join([f'<option value="{c["id"]}">{c["name"]} ({c.get("client_code") or ""})</option>' for c in all_clients_list])
    payment_settings = database.get_payment_settings()
    whatsapp_templates = database.get_whatsapp_templates()
    formatted_payment_preview = database.get_formatted_payment_methods()
    suppliers_list = database.get_suppliers()
    master_accounts_list = database.get_master_accounts_overview()
    system_health = system_logger.get_system_health_report()
    recent_logs = system_logger.get_recent_logs(limit=120)
    wa_settings = database.get_whatsapp_api_settings()
    cw_settings = database.get_chatwoot_settings()
    oauth_cfg = database.get_oauth_settings()
    oauth_client_id = oauth_cfg.get("client_id", "gemini-spark-joif")
    oauth_client_secret = oauth_cfg.get("client_secret", "")
    oauth_redirect_uris = oauth_cfg.get("redirect_uris", "https://gemini.google.com")
    oauth_enabled = oauth_cfg.get("enabled", 1)

    msg_raw = request.query_params.get("msg", "")
    wa_param = request.query_params.get("wa", "")
    err_param = request.query_params.get("err", "")
    msg_banner = ""
    if err_param:
        msg_banner = f"""
        <div style="background:#450a0a; border:1px solid #ef4444; color:#fca5a5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>⚠️ <strong>Error:</strong> {err_param}</span>
            <a href="/" style="color:#fca5a5; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """
    elif msg_raw == "combo_sold" and wa_param:
        msg_banner = f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <span>🎉 <strong>¡Combo vendido y asignado con éxito!</strong> Los perfiles quedaron asignados y las cuentas sincronizadas.</span>
            <a href="{wa_param}" target="_blank" class="btn" style="background:#25d366; color:#fff; text-decoration:none; font-weight:bold; padding:8px 16px; border-radius:6px;">📲 Enviar Accesos por WhatsApp (1 Clic)</a>
        </div>
        """
    elif msg_raw:
        msg_text = "¡Cambios guardados con éxito!"
        if msg_raw == "price_saved":
            msg_text = "✅ Precio de catálogo configurado correctamente."
        elif msg_raw == "price_deleted":
            msg_text = "🗑️ Precio de catálogo eliminado."
        elif msg_raw == "combo_created":
            msg_text = "🎉 ¡Combo multipantalla creado con éxito! Ya puedes ofrecerlo en el catálogo."
        elif msg_raw == "combo_deleted":
            msg_text = "🗑️ Combo eliminado del catálogo."
        elif msg_raw == "supplier_saved":
            msg_text = "👔 Proveedor mayorista guardado con éxito."
        elif msg_raw == "supplier_deleted":
            msg_text = "🗑️ Proveedor mayorista eliminado."
        elif msg_raw == "master_renewed":
            msg_text = "✅ Cuenta madre renovada con éxito y perfiles sincronizados."
        elif msg_raw == "logs_cleared":
            msg_text = "🧹 Buffer de logs en memoria limpiado."
        elif msg_raw == "wa_settings_saved":
            msg_text = "✅ Configuración de Evolution API WhatsApp guardada con éxito."
        elif msg_raw == "wa_test_sent":
            msg_text = "✅ Mensaje de prueba enviado exitosamente por WhatsApp."
        elif msg_raw == "wa_logged_out":
            msg_text = "🚪 Sesión de WhatsApp cerrada correctamente."
        elif msg_raw == "wa_webhook_configured":
            msg_text = "🔗 Webhook configurado exitosamente en Evolution API."
        elif msg_raw == "wa_chatwoot_configured":
            msg_text = "🎉 ¡Chatwoot vinculado exitosamente con Evolution API! Ya puedes gestionar tus clientes desde la app móvil."
        elif msg_raw == "chatwoot_synced":
            imp = request.query_params.get("imported", "0")
            upd = request.query_params.get("updated", "0")
            msg_text = f"🔄 ¡Sincronización con Chatwoot completada! {imp} nuevos clientes dados de alta en el CRM, {upd} actualizados."
        elif msg_raw == "chatwoot_connected":
            user_val = request.query_params.get("user", "Usuario")
            msg_text = f"🎉 ¡Conexión con Chatwoot verificada exitosamente! Autenticado como {user_val}."
        elif msg_raw == "chatwoot_settings_saved":
            msg_text = "✅ Configuración de Chatwoot guardada con éxito."
        elif msg_raw == "chatwoot_webhook_configured":
            msg_text = "🔗 ¡Webhook de Chatwoot configurado exitosamente! Los atajos /nc_n_casaextra, /stock y /cbu ya están activos."
        elif msg_raw == "chatwoot_canned_synced":
            created = request.query_params.get("created", "0")
            existing = request.query_params.get("existing", "0")
            msg_text = f"⚡ ¡Atajos de Chatwoot sincronizados! ({created} creados, {existing} ya existentes). Ya puedes escribir /nc en cualquier chat."
        elif msg_raw == "chatwoot_names_synced":
            upd = request.query_params.get("updated", "0")
            chk = request.query_params.get("checked", "0")
            msg_text = f"✨ ¡Nombres de WhatsApp sincronizados a Chatwoot! Se revisaron {chk} contactos y se actualizaron {upd} nombres con la agenda real."
        else:
            msg_text = msg_raw
        msg_banner = f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>{msg_text}</span>
            <a href="/" style="color:#a7f3d0; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """

    # 1. Filas de Cuentas Activas con botón de Cobrar y Ficha 360
    active_rows = ""
    for a in active_accounts:
        days = a.get("days_remaining")
        badge = "badge-ok"
        badge_txt = f"En {days}d"
        if days is None:
            badge = "badge-warn"; badge_txt = "Fecha inválida"
        elif days < 0:
            badge = "badge-danger"; badge_txt = f"Vencida (-{abs(days)}d)"
        elif days <= 2:
            badge = "badge-warn"; badge_txt = f"¡Vence en {days}d!"

        wa_clean = re.sub(r'[^0-9]', '', a.get("whatsapp", ""))
        wa_link = f'<a href="https://wa.me/{wa_clean}" target="_blank" style="color: #22c55e;">{a.get("whatsapp")}</a>' if wa_clean else '-'
        tg_clean = a.get("telegram", "").lstrip("@")
        tg_link = f'<a href="https://t.me/{tg_clean}" target="_blank" style="color: #38bdf8;">@{tg_clean}</a>' if tg_clean else '-'
        client_tag = f"👔 {a.get('client_name')}" if "revend" in (a.get("client_type") or "").lower() else f"👤 {a.get('client_name')}"
        client_id_val = a.get("client_id")
        client_click = f'onclick="openClient360Modal({client_id_val})" style="cursor:pointer;color:#38bdf8;text-decoration:underline;" title="Ver Ficha 360° del Cliente"' if client_id_val else ''
        btn_360 = f'<button type="button" onclick="openClient360Modal({client_id_val})" class="btn-action" style="background:#1e293b;border:1px solid #38bdf8;color:#38bdf8;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Ver Ficha 360°">👤 360°</button>' if client_id_val else ''

        perf = f"<br><small style='color:#94a3b8;'>Perf: {a['profile_name']}</small>" if a.get("profile_name") else ""
        pin = f"<small style='color:#94a3b8;'>PIN: {a['profile_pin']}</small>" if a.get("profile_pin") else ""

        wa_cobro = database.generate_whatsapp_message(a, message_type="cobro")
        wa_link_cobro = wa_cobro.get("wa_link", "#")
        wa_entrega = database.generate_whatsapp_message(a, message_type="entrega")
        wa_link_entrega = wa_entrega.get("wa_link", "#")

        # Botón inteligente: Pago inicial de compra vs Renovación mensual (+30d)
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

        active_rows += f"""
        <tr>
            <td><strong {client_click}>{client_tag}</strong><br><small style="color:#64748b;">{a.get('client_code') or ''}</small></td>
            <td>{wa_link}<br>{tg_link}</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{a['platform']}</span>{perf}</td>
            <td><code>{a['email']}</code><br><code>{a['password']}</code> {pin}</td>
            <td><code>{a['expiry_date']}</code></td>
            <td><span class="badge {badge}">{badge_txt}</span></td>
            <td><strong>{a.get('price') or '-'}</strong></td>
            <td style="white-space: nowrap;">
                {btn_360}
                <a href="{wa_link_cobro}" target="_blank" class="btn-action" style="background:#15803d;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con mensaje de cobro listo">💬 Cobro</a>
                <a href="{wa_link_entrega}" target="_blank" class="btn-action" style="background:#0284c7;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con credenciales listas">📩 Datos</a>
                {btn_pago}
                <form action="/api/mark-fallen/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar {a['email']} como caída?');">
                    <button type="submit" class="btn-action btn-warn" style="padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Reportar Caída">🚨</button>
                </form>
                <form action="/api/delete-account/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar cuenta?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not active_rows:
        active_rows = "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas activas asignadas actualmente.</td></tr>"

    # 2. Filas de Stock Libre
    stock_rows = ""
    for s in free_stock:
        perf = f" (Perf: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        stock_rows += f"""
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
    if not stock_rows:
        stock_rows = "<tr><td colspan='6' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas libres en stock. Agrega cuentas o pídeselo a Gemini.</td></tr>"

    # Salud del Inventario y Semáforo de Stock
    stock_health = database.get_stock_health_summary()
    stock_health_html = ""
    for p in stock_health.get("platforms", []):
        st = p["status"]
        if st == "agotado":
            st_color = "#ef4444"
            st_bg = "#450a0a"
            st_border = "#991b1b"
            st_badge = "🔴 Agotado"
        elif st == "bajo":
            st_color = "#f59e0b"
            st_bg = "#451a03"
            st_border = "#92400e"
            st_badge = "🟡 Stock Bajo"
        else:
            st_color = "#10b981"
            st_bg = "#064e3b"
            st_border = "#047857"
            st_badge = "🟢 Óptimo"

        stock_health_html += f"""
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
    if not stock_health_html:
        stock_health_html = "<div style='color:#64748b;padding:15px;grid-column:1/-1;'>No hay plataformas registradas en el inventario aún.</div>"

    # 3. Filas de Cuentas Caídas
    fallen_rows = ""
    for f in fallen_accounts:
        client_name = f.get("client_name") or "Sin cliente asignado"
        fallen_rows += f"""
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
    if not fallen_rows:
        fallen_rows = "<tr><td colspan='5' style='text-align:center;color:#10b981;padding:20px;'>🎉 ¡No hay cuentas caídas! Todo el sistema está funcionando.</td></tr>"

    # 4. Filas de Transacciones Financieras
    tx_rows = ""
    for t in transactions:
        c_name = t.get("client_name") or "Venta General"
        plat = t.get("platform") or "Streaming"
        c_type = "👔 Revendedor" if "revend" in (t.get("client_type") or "").lower() else "👤 Final"
        tx_rows += f"""
        <tr>
            <td><small style="color:#94a3b8;">{t['created_at'][:16]}</small></td>
            <td><strong>{c_name}</strong> ({c_type})</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span></td>
            <td><strong style="color:#10b981;">+{database.format_ars(t['amount'])}</strong></td>
            <td><span style="color:#f59e0b;">-{database.format_ars(t['cost'])}</span></td>
            <td><strong style="color:#38bdf8;">+{database.format_ars(t['profit'])}</strong></td>
            <td><small>{t.get('payment_method') or 'Transf.'}</small></td>
        </tr>
        """
    if not tx_rows:
        tx_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay transacciones registradas este mes aún.</td></tr>"

    # 5. Cuentas Madre y Pantallas Compartidas
    screens_overview = database.get_shared_screens_overview()
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
    if not screens_html:
        screens_html = "<div style='grid-column:1/-1;text-align:center;color:#64748b;padding:30px;'>No hay cuentas registradas con pantallas múltiples aún. Puedes pedirle a Gemini: <em>'Crea una cuenta de Netflix con 4 pantallas'</em>.</div>"

    # 6. Catálogo de Precios ARS
    catalog_rows = ""
    for c in catalog_items:
        stype_badge = "📱 Pantalla" if c["service_type"] == "pantalla" else "👑 Completa"
        catalog_rows += f"""
        <tr>
            <td><strong>{c['platform']}</strong></td>
            <td><span class="badge" style="background:#1e293b;color:#94a3b8;">{stype_badge}</span></td>
            <td style="color:#f59e0b;">{c['cost_price_formatted']}</td>
            <td><strong style="color:#10b981;">{c['price_final_formatted']}</strong></td>
            <td><strong style="color:#38bdf8;">{c['price_reseller_formatted']}</strong></td>
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
    if not catalog_rows:
        catalog_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay precios configurados en el catálogo aún.</td></tr>"

    # 7. Combos y Packs
    combos_html = ""
    for cb in combos_list:
        pills = "".join([f'<span class="badge" style="background:#0b0f19;border:1px solid #334155;color:#38bdf8;margin:2px;">{it["platform"]}</span>' for it in cb["items"]])
        combos_html += f"""
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
    if not combos_html:
        combos_html = "<div style='color:#64748b;padding:20px;grid-column:1/-1;'>No hay combos activos configurados aún.</div>"

    # 8. Filas de Proveedores Mayoristas
    suppliers_table_rows = ""
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

        suppliers_table_rows += f"""
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
    if not suppliers_table_rows:
        suppliers_table_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay proveedores mayoristas registrados aún.</td></tr>"

    # 9. Filas de Cuentas Madre ante Proveedores
    master_accounts_table_rows = ""
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

        if m["risk_mismatch"]:
            risk_cell = f'<span class="badge badge-danger">🚨 Desfase de Corte</span><br><small style="color:#f87171;font-size:0.75rem;">{m["mismatch_warning"]}</small>'
        else:
            risk_cell = '<span class="badge badge-ok" style="font-size:0.75rem;">✓ Sincronizado</span>'

        safe_email = m['email'].replace("'", "\\'")
        safe_plat = m['platform'].replace("'", "\\'")
        cur_expiry = m.get('supplier_expiry_date') or ''
        cur_cost = m.get('supplier_cost') or 0.0

        master_accounts_table_rows += f"""
        <tr style="{'background:rgba(239,68,68,0.06);' if m['risk_mismatch'] else ''}">
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
    if not master_accounts_table_rows:
        master_accounts_table_rows = "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas madre registradas.</td></tr>"

    # 10. Filas del Terminal de Logs
    initial_logs_html = ""
    for l in recent_logs:
        lvl = l.get("level", "INFO").upper()
        lvl_class = "error" if lvl in ("ERROR", "CRITICAL") else ("warn" if lvl == "WARNING" else "info")
        tb_str = l.get("traceback") or ""
        safe_tb = tb_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        tb_html = f'<div class="log-tb">{safe_tb}</div>' if safe_tb else ""
        safe_msg = l.get("message", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        initial_logs_html += f"""
        <div class="log-row log-{lvl_class}">
            <span class="log-time">{l['timestamp']}</span>
            <span class="log-badge log-badge-{lvl_class}">{lvl}</span>
            <span class="log-mod">[{l['module']}]</span>
            <span class="log-txt">{safe_msg}</span>
            {tb_html}
        </div>
        """
    if not initial_logs_html:
        initial_logs_html = "<div style='color:#64748b;padding:20px;text-align:center;'>No hay logs registrados en memoria.</div>"

    # 11. Filas de Pagos y Comprobantes Esperando Aprobación
    pending_payments = database.list_pending_payments(status="pending")
    pending_payments_rows = ""
    for p in pending_payments:
        pid = p["id"]
        c_name = p.get("client_name") or "Cliente"
        c_phone = p.get("sender_phone") or p.get("client_whatsapp") or ""
        wa_link = f'<a href="https://wa.me/{c_phone}" target="_blank" style="color: #22c55e; font-weight: 600;">+{c_phone}</a>' if c_phone else '-'
        plat = p.get("platform") or "Suscripción"
        acc_email = p.get("account_email") or "-"
        amt_str = p.get("amount_formatted") or (database.format_ars(p.get("amount") or 0.0))
        bank_info = p.get("bank") or "Transferencia"
        op_info = f"<br><small style='color:#94a3b8;'>Op: #{p.get('operation_id')}</small>" if p.get("operation_id") else ""
        date_str = p.get("created_at", "")[:16]

        b64 = p.get("receipt_base64") or ""
        mime_type = p.get("receipt_mimetype") or ""
        clean_filename = (p.get("receipt_filename") or "comprobante").replace("'", "\\'")
        if b64:
            if "pdf" in mime_type.lower():
                receipt_html = f'<button type="button" onclick="viewReceiptDoc(\'data:{mime_type};base64,{b64}\', \'{clean_filename}\')" class="btn-action" style="background:#1e293b;border:1px solid #f43f5e;color:#f43f5e;padding:3px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;">📄 Ver PDF</button>'
            else:
                receipt_html = f'<button type="button" onclick="viewReceiptDoc(\'data:{mime_type};base64,{b64}\', \'{clean_filename}\')" class="btn-action" style="background:#1e293b;border:1px solid #38bdf8;color:#38bdf8;padding:3px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;">🖼️ Ver Imagen</button>'
        elif p.get("raw_text"):
            safe_raw = p.get("raw_text")[:50].replace('"', '&quot;').replace("'", "&#39;")
            receipt_html = f'<span title="{safe_raw}" style="color:#94a3b8;font-size:0.75rem;cursor:help;">📝 Texto</span>'
        else:
            receipt_html = '<span style="color:#64748b;font-size:0.75rem;">Sin archivo</span>'

        safe_client_name = c_name.replace("'", "\\'")
        pending_payments_rows += f"""
        <tr>
            <td><strong style="color:#38bdf8;font-size:0.95rem;">#P{pid}</strong></td>
            <td><strong>{c_name}</strong><br><small>{wa_link}</small></td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span><br><code style="font-size:0.75rem;">{acc_email}</code></td>
            <td><strong style="color:#10b981;font-size:0.95rem;">{amt_str}</strong><br><small style="color:#cbd5e1;">{bank_info}</small>{op_info}</td>
            <td><small style="color:#94a3b8;">{date_str}</small></td>
            <td>{receipt_html}</td>
            <td style="white-space:nowrap;">
                <form action="/api/pending-payments/approve/{pid}" method="POST" style="display:inline;" onsubmit="return confirm('¿Aprobar pago #P{pid} de {safe_client_name}? Se renovará la suscripción y se registrará en finanzas.');">
                    <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Aprobar Pago">✅ Aprobar</button>
                </form>
                <button type="button" onclick="openRejectPaymentModal({pid}, '{safe_client_name}')" class="btn-action" style="background:#dc2626;color:white;border:none;padding:4px 8px;border-radius:5px;font-size:0.75rem;font-weight:600;margin-left:4px;" title="Denegar Pago">❌ Denegar</button>
            </td>
        </tr>
        """

    if not pending_payments_rows:
        pending_payments_rows = "<tr><td colspan='7' style='text-align:center;color:#10b981;padding:24px;'>🎉 ¡No hay pagos pendientes de aprobación! Todos los cobros están al día.</td></tr>"

    context = {
        "USER": user,
        "MSG_BANNER": msg_banner,
        "PENDING_PAYMENTS_COUNT": len(pending_payments),
        "PENDING_PAYMENTS_ROWS": pending_payments_rows,
        "ADMIN_WHATSAPP": wa_settings.get('admin_whatsapp', ''),
        "FINANCE_INCOME": database.format_ars(finance['collected_income']),
        "FINANCE_TX_COUNT": finance['transactions_count'],
        "FINANCE_COSTS": database.format_ars(finance['collected_costs']),
        "FINANCE_PROFIT": database.format_ars(finance['collected_profit']),
        "FINANCE_PENDING_7D": database.format_ars(finance['pending_receivables_7d']),
        "FINANCE_PENDING_COUNT": finance['pending_accounts_count'],
        "OAUTH_HEADER_BADGE": '<span style="color:#10b981;">(OAuth 2.0 Protegido 🔒)</span>' if oauth_enabled else '<span style="color:#eab308;">(Público)</span>',
        "ACTIVE_ACCOUNTS_COUNT": len(active_accounts),
        "SCREENS_OVERVIEW_COUNT": len(screens_overview),
        "SUPPLIERS_COUNT": len(suppliers_list),
        "MASTER_ACCOUNTS_COUNT": len(master_accounts_list),
        "CATALOG_COUNT": len(catalog_items),
        "COMBOS_COUNT": len(combos_list),
        "TRANSACTIONS_COUNT": len(transactions),
        "FREE_STOCK_COUNT": len(free_stock),
        "FALLEN_ACCOUNTS_COUNT": len(fallen_accounts),
        "CLIENT_SELECT_OPTIONS": client_select_options,
        "ACTIVE_ROWS": active_rows,
        "SCREENS_HTML": screens_html,
        "COMBOS_HTML": combos_html,
        "CATALOG_ROWS": catalog_rows,
        "TX_ROWS": tx_rows,
        "STOCK_HEALTH_HTML": stock_health_html,
        "STOCK_ROWS": stock_rows,
        "FALLEN_ROWS": fallen_rows,
        "WA_API_URL": wa_settings.get('api_url', 'http://evolution-api:8080'),
        "WA_API_KEY": wa_settings.get('api_key', 'mcp-evolution-key-2026'),
        "WA_INSTANCE_NAME": wa_settings.get('instance_name', 'streaming-bot'),
        "WA_AUTO_EXPIRY_CHECKED": 'checked' if wa_settings.get('auto_send_expiry') == 1 else '',
        "WA_AUTO_SALES_CHECKED": 'checked' if wa_settings.get('auto_send_sales') == 1 else '',
        "WA_AUTO_REPLY_CHECKED": 'checked' if wa_settings.get('auto_reply_enabled', 1) == 1 else '',
        "WA_UPDATED_AT": wa_settings.get('updated_at', 'Predeterminado'),
        "CW_URL": cw_settings.get('url', 'https://chat.joif.net'),
        "CW_TOKEN": cw_settings.get('token', ''),
        "CW_STATUS_COLOR": '#34d399' if cw_settings.get('token') else '#fbbf24',
        "CW_STATUS_LABEL": '🟢 Configurado y Vinculado' if cw_settings.get('token') else '⚠️ Falta Token de Acceso',
        "PAYMENT_ALIAS_MP": payment_settings.get('alias_mp', ''),
        "PAYMENT_CVU_CBU": payment_settings.get('cvu_cbu', ''),
        "PAYMENT_ACCOUNT_HOLDER": payment_settings.get('account_holder', ''),
        "PAYMENT_BANK_NAME": payment_settings.get('bank_name', 'Mercado Pago / Transferencia Bancaria'),
        "PAYMENT_USDT_ADDRESS": payment_settings.get('usdt_address', ''),
        "PAYMENT_EXTRA_INSTRUCTIONS": payment_settings.get('extra_instructions', ''),
        "PAYMENT_UPDATED_AT": payment_settings.get('updated_at', 'Predeterminado'),
        "MASTER_ACCOUNTS_TABLE_ROWS": master_accounts_table_rows,
        "SUPPLIERS_TABLE_ROWS": suppliers_table_rows,
        "HEALTH_BADGE_CLASS": 'badge-ok' if system_health['status'] == 'OK' else ('badge-warn' if system_health['status'] == 'WARNING' else 'badge-danger'),
        "HEALTH_STATUS": system_health['status'],
        "HEALTH_UPTIME": system_health['uptime'],
        "HEALTH_DB_TOTAL": system_health['database']['total_accounts'],
        "HEALTH_DB_SIZE": system_health['database']['size'],
        "HEALTH_DB_STATUS": system_health['database']['status'],
        "HEALTH_TG_STATUS": system_health['telegram']['status'],
        "HEALTH_ERRORS_COLOR": '#ef4444' if system_health['logs_summary']['errors_count'] > 0 else '#10b981',
        "HEALTH_ERRORS_COUNT": system_health['logs_summary']['errors_count'],
        "HEALTH_WARNINGS_COUNT": system_health['logs_summary']['warnings_count'],
        "HEALTH_TOTAL_BUFFERED": system_health['logs_summary']['total_buffered'],
        "INITIAL_LOGS_HTML": initial_logs_html,
        "OAUTH_BADGE_CLASS": 'badge-ok' if oauth_enabled else 'badge-warn',
        "OAUTH_STATUS_LABEL": '🔒 Protección OAuth Activa' if oauth_enabled else '⚠️ Protección Desactivada',
        "OAUTH_CLIENT_ID": oauth_client_id,
        "OAUTH_CLIENT_SECRET": oauth_client_secret,
        "OAUTH_REDIRECT_URIS": oauth_redirect_uris,
        "OAUTH_CHECKED": 'checked' if oauth_enabled else ''
    }

    return HTMLResponse(render_template("dashboard.html", context))

@router.get("/api/client/360/{client_id}")
async def api_get_client_360(client_id: str, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    profile = database.get_client_360_profile(client_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return profile
