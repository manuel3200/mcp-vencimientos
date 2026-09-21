"""
services/community_service.py - Fachada de servicio para engagement comunitario, encuestas y moderación.
"""
from application.community.polls_service import (
    create_and_dispatch_poll
)
from application.community.scheduled_broadcast_service import (
    run_monday_rules_broadcast,
    run_friday_weekend_promo_broadcast,
    get_active_community_groups,
    MONDAY_RULES_MESSAGE,
    FRIDAY_PROMO_MESSAGE
)
from application.community.word_filter_service import (
    inspect_community_message
)

__all__ = [
    "create_and_dispatch_poll",
    "run_monday_rules_broadcast",
    "run_friday_weekend_promo_broadcast",
    "get_active_community_groups",
    "inspect_community_message",
    "MONDAY_RULES_MESSAGE",
    "FRIDAY_PROMO_MESSAGE"
]
