from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

import database
import system_logger
from core.security import verify_session_cookie
from core.templates import render_template
from presentation.web.view_models import (
    render_msg_banner,
    render_active_accounts_rows,
    render_stock_rows,
    render_stock_health_html,
    render_fallen_rows,
    render_transactions_rows,
    render_screens_overview_html,
    render_catalog_rows,
    render_combos_html,
    render_suppliers_rows,
    render_master_accounts_rows,
    render_logs_html,
    render_pending_payments_rows,
    render_fallen_reports_rows,
    render_client_select_options,
    render_http_custom_rows,
)

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # 1. Obtener datos de repositorios
    active_accounts = database.get_active_accounts()
    http_custom_accounts = database.get_http_custom_accounts()
    free_stock = database.get_free_stock()
    fallen_accounts = database.get_fallen_accounts()
    finance = database.get_financial_balance()
    transactions = database.get_recent_transactions(limit=15)
    catalog_items = database.get_price_catalog()
    combos_list = database.get_combos()
    all_clients_list = database.list_all_clients()
    payment_settings = database.get_payment_settings()
    suppliers_list = database.get_suppliers()
    master_accounts_list = database.get_master_accounts_overview()
    system_health = system_logger.get_system_health_report()
    recent_logs = system_logger.get_recent_logs(limit=120)
    wa_settings = database.get_whatsapp_api_settings()
    cw_settings = database.get_chatwoot_settings()
    oauth_cfg = database.get_oauth_settings()
    screens_overview = database.get_shared_screens_overview()
    pending_payments = database.list_pending_payments(status="pending")
    fallen_reports = database.list_fallen_reports(limit=50)
    fallen_reports_count = database.count_pending_fallen_reports()

    # 2. Renderizar view-models desacoplados
    msg_raw = request.query_params.get("msg", "")
    wa_param = request.query_params.get("wa", "")
    err_param = request.query_params.get("err", "")
    msg_banner = render_msg_banner(msg_raw, wa_param, err_param)

    oauth_enabled = oauth_cfg.get("enabled", 1)

    context = {
        "USER": user,
        "MSG_BANNER": msg_banner,
        "PENDING_PAYMENTS_COUNT": len(pending_payments),
        "PENDING_PAYMENTS_ROWS": render_pending_payments_rows(pending_payments),
        "FALLEN_REPORTS_COUNT": fallen_reports_count,
        "FALLEN_REPORTS_ROWS": render_fallen_reports_rows(fallen_reports),
        "ADMIN_WHATSAPP": wa_settings.get('admin_whatsapp', ''),
        "FINANCE_INCOME": database.format_ars(finance['collected_income']),
        "FINANCE_TX_COUNT": finance['transactions_count'],
        "FINANCE_COSTS": database.format_ars(finance['collected_costs']),
        "FINANCE_PROFIT": database.format_ars(finance['collected_profit']),
        "FINANCE_PENDING_7D": database.format_ars(finance['pending_receivables_7d']),
        "FINANCE_PENDING_COUNT": finance['pending_accounts_count'],
        "OAUTH_HEADER_BADGE": '<span style="color:#10b981;">(OAuth 2.0 Protegido 🔒)</span>' if oauth_enabled else '<span style="color:#eab308;">(Público)</span>',
        "ACTIVE_ACCOUNTS_COUNT": len(active_accounts),
        "HTTP_CUSTOM_COUNT": len(http_custom_accounts),
        "HTTP_CUSTOM_ROWS": render_http_custom_rows(http_custom_accounts),
        "SCREENS_OVERVIEW_COUNT": len(screens_overview),
        "SUPPLIERS_COUNT": len(suppliers_list),
        "MASTER_ACCOUNTS_COUNT": len(master_accounts_list),
        "CATALOG_COUNT": len(catalog_items),
        "COMBOS_COUNT": len(combos_list),
        "TRANSACTIONS_COUNT": len(transactions),
        "FREE_STOCK_COUNT": len(free_stock),
        "FALLEN_ACCOUNTS_COUNT": len(fallen_accounts),
        "CLIENT_SELECT_OPTIONS": render_client_select_options(all_clients_list),
        "ACTIVE_ROWS": render_active_accounts_rows(active_accounts),
        "SCREENS_HTML": render_screens_overview_html(screens_overview),
        "COMBOS_HTML": render_combos_html(combos_list),
        "CATALOG_ROWS": render_catalog_rows(catalog_items),
        "TX_ROWS": render_transactions_rows(transactions),
        "STOCK_HEALTH_HTML": render_stock_health_html(database.get_stock_health_summary()),
        "STOCK_ROWS": render_stock_rows(free_stock),
        "FALLEN_ROWS": render_fallen_rows(fallen_accounts),
        "WA_API_URL": wa_settings.get('api_url', 'http://evolution-api:8080'),
        "WA_API_KEY": wa_settings.get('api_key', 'mcp-evolution-key-2026'),
        "WA_INSTANCE_NAME": wa_settings.get('instance_name', 'streaming-bot'),
        "WA_GEMINI_KEY": wa_settings.get('gemini_api_key', ''),
        "WA_AUTO_EXPIRY_CHECKED": 'checked' if wa_settings.get('auto_send_expiry') == 1 else '',
        "WA_AUTO_SALES_CHECKED": 'checked' if wa_settings.get('auto_send_sales') == 1 else '',
        "WA_AUTO_REPLY_CHECKED": 'checked' if wa_settings.get('auto_reply_enabled', 1) == 1 else '',
        "WA_EXPIRY_CUTOFF_HOUR": wa_settings.get('expiry_cutoff_hour', 17),
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
        "MASTER_ACCOUNTS_TABLE_ROWS": render_master_accounts_rows(master_accounts_list),
        "SUPPLIERS_TABLE_ROWS": render_suppliers_rows(suppliers_list),
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
        "INITIAL_LOGS_HTML": render_logs_html(recent_logs),
        "OAUTH_BADGE_CLASS": 'badge-ok' if oauth_enabled else 'badge-warn',
        "OAUTH_STATUS_LABEL": '🔒 Protección OAuth Activa' if oauth_enabled else '⚠️ Protección Desactivada',
        "OAUTH_CLIENT_ID": oauth_cfg.get("client_id", "gemini-spark-joif"),
        "OAUTH_CLIENT_SECRET": oauth_cfg.get("client_secret", ""),
        "OAUTH_REDIRECT_URIS": oauth_cfg.get("redirect_uris", "https://gemini.google.com"),
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
