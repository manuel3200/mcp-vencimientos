"""
database.py - Fachada de compatibilidad regresiva 100%.
Re-exporta todas las funciones, repositorios y servicios del paquete modular `db/` y `services/`.
Cualquier módulo existente que ejecute `import database` o `from database import ...` continúa funcionando sin cambios.
"""

from db.connection import DB_DIR, DB_PATH, get_connection
from db.schema import DEFAULT_WHATSAPP_TEMPLATES, init_db

from core.security import (
    hash_password,
    verify_password,
    encrypt_backup,
    decrypt_backup,
    encrypt_secret,
    decrypt_secret,
    is_encrypted_secret
)
from core.audit import (
    log_audit_event,
    verify_audit_chain,
    get_audit_history,
    format_audit_report
)
from core.ephemeral_secrets import (
    create_ephemeral_secret,
    reveal_and_burn_secret,
    burn_secret_immediately
)
from core.rbac import (
    ROLE_SUPER_ADMIN,
    ROLE_FINANZAS,
    ROLE_SOPORTE,
    ROLE_UNAUTHORIZED,
    get_actor_role,
    has_permission,
    check_admin_permission
)
from core.utils import parse_money, format_ars, clean_whatsapp_phone, _parse_date_flexible

from db.repositories.admin_repo import (
    create_or_update_admin,
    get_admin_user,
    verify_admin_credentials,
    set_telegram_otp,
    verify_telegram_otp
)

from db.repositories.clients_repo import (
    find_or_create_client,
    search_client,
    list_all_clients,
    register_or_update_client,
    get_client_360_profile,
    get_client_by_phone,
    update_client_type
)

from db.repositories.accounts_repo import (
    add_free_account,
    assign_or_sell_account,
    mark_account_fallen,
    replace_fallen_account,
    get_free_stock,
    get_fallen_accounts,
    get_active_accounts,
    get_expiring_streaming_accounts,
    renew_account,
    delete_account,
    mark_streaming_alert_sent,
    get_account_detail,
    create_master_account_with_profiles,
    assign_next_free_profile,
    get_shared_screens_overview,
    mark_entire_master_account_fallen,
    set_platform_min_stock,
    get_stock_thresholds,
    get_stock_health_summary,
    update_account_price,
    purge_accounts_except_client,
    reactivate_fallen_account,
    report_and_auto_replace_account,
    get_due_today_unpaid_accounts,
    mark_overdue_accounts_for_password_change,
    get_accounts_pending_password_change,
    mark_account_for_password_change,
    rotate_master_password_and_broadcast,
    get_http_custom_accounts,
    migrate_encrypt_plaintext_accounts
)


from db.repositories.finance_repo import (
    register_customer_payment,
    register_partial_payment,
    reverse_customer_payment,
    collect_payment,
    get_recent_transactions
)

from services.finance_service import (
    get_financial_balance
)

from db.repositories.settings_repo import (
    get_payment_settings,
    save_payment_settings,
    get_whatsapp_templates,
    get_whatsapp_template,
    save_whatsapp_template,
    reset_whatsapp_template,
    get_whatsapp_api_settings,
    save_whatsapp_api_settings,
    get_chatwoot_settings,
    save_chatwoot_settings,
    get_oauth_settings,
    save_oauth_settings,
    regenerate_oauth_secret,
    validate_oauth_client,
    create_oauth_auth_code,
    verify_and_consume_auth_code,
    create_oauth_tokens,
    refresh_oauth_token,
    verify_oauth_access_token
)

from services.template_service import (
    get_formatted_payment_methods,
    render_dynamic_template,
    generate_whatsapp_message,
    generate_consolidated_billing_whatsapp
)

from services.csv_service import (
    _detect_csv_delimiter,
    export_active_accounts_csv,
    export_free_stock_csv,
    export_transactions_csv,
    export_full_backup_json,
    get_csv_template_stock,
    get_csv_template_sales,
    import_free_stock_csv,
    import_sales_csv
)

from db.repositories.catalog_repo import (
    get_price_catalog,
    upsert_catalog_price,
    delete_catalog_price,
    get_suggested_price,
    get_combos,
    create_or_update_combo,
    delete_combo,
    sell_combo,
    generate_catalog_message
)

from db.repositories.suppliers_repo import (
    get_suppliers,
    get_supplier,
    save_supplier,
    delete_supplier,
    get_master_accounts_overview,
    renew_master_account,
    get_expiring_master_accounts
)

from db.repositories.payments_approval_repo import (
    create_pending_payment,
    get_pending_payment,
    list_pending_payments,
    count_pending_payments,
    approve_pending_payment,
    reject_pending_payment,
    prune_old_approved_receipts_base64,
    cleanup_duplicate_pending_payments
)

from db.repositories.fallen_reports_repo import (
    create_fallen_report,
    get_fallen_report,
    list_fallen_reports,
    count_pending_fallen_reports,
    authorize_fallen_report,
    put_fallen_report_on_wait,
    dismiss_fallen_report,
    find_active_fallen_report_by_phone,
    record_fallen_report_followup,
    get_stale_waiting_reports,
    rollback_fallen_report_replacement
)

# Alias para compatibilidad con versiones previas
register_streaming_sale = assign_or_sell_account
get_client_360 = get_client_360_profile
get_account_by_id = get_account_detail

def update_pending_payment_status(payment_id: int, status: str, admin_notes: str = ""):
    if status == "approved":
        return approve_pending_payment(payment_id)
    elif status == "rejected":
        return reject_pending_payment(payment_id, reason=admin_notes)
    from db.connection import get_connection
    conn = get_connection()
    with conn:
        conn.execute("UPDATE pending_payments SET status = ?, notes = notes || ? WHERE id = ?", (status, f" [{admin_notes}]", payment_id))
    return {"success": True, "id": payment_id, "status": status}


from domain.rules.http_custom_rules import parse_http_custom_message
from services.http_custom_service import process_http_custom_outgoing_message

from db.repositories.groups_moderation_repo import (
    get_bot_mode,
    set_bot_mode,
    list_groups_config,
    get_group_config,
    upsert_group_config,
    set_group_bot_enabled,
    set_group_antilink,
    is_group_bot_enabled,
    is_group_antilink_enabled,
    list_silent_bans,
    add_silent_ban,
    remove_silent_ban,
    is_silent_banned,
    delete_group_config
)

# Crecimiento Comercial, Referidos, Cupones, Gamificación y Churn (Fase 4)
from db.repositories.growth_repo import (
    get_or_create_client_referral_code,
    get_referral_by_code,
    get_referral_by_client_id,
    credit_referral_reward,
    redeem_referral_balance,
    create_coupon,
    get_coupon,
    record_coupon_redemption,
    list_active_coupons,
    increment_member_activity,
    get_group_leaderboard,
    list_active_faqs,
    upsert_faq,
    delete_faq,
    get_client_churn_metrics
)

from core.growth import (
    ReferralManager,
    CouponManager,
    GamificationManager,
    FAQEngine,
    ChurnPredictor
)



