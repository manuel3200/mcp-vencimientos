"""
growth_repo.py - Fachada de compatibilidad para v2/db/repositories/
Re-exporta las funciones de infraestructura de persistencia.
"""

from infrastructure.persistence.repositories.growth_repo import (
    get_or_create_client_referral_code,
    get_referral_by_code,
    get_referral_by_client_id,
    credit_referral_reward,
    redeem_referral_balance,
    list_all_referral_codes,
    get_referrals_overview_stats,
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
