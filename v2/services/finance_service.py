from datetime import date
from typing import Dict, Any

from db.connection import get_connection
from db.repositories.accounts_repo import get_active_accounts
from db.repositories.finance_repo import get_profitability_by_platform
from core.utils import parse_money

def get_financial_balance(period: str = "mes_actual") -> Dict[str, Any]:
    """Calcula el balance completo: ingresos brutos, costos de proveedores, ganancia neta y proyecciones."""
    conn = get_connection()
    today = date.today()
    try:
        with conn:
            # 1. Pagos ya cobrados en el mes actual
            month_str = f"{today.year}-{today.month:02d}%"
            res = conn.execute("""
                SELECT 
                    COALESCE(SUM(amount), 0.0) as total_income,
                    COALESCE(SUM(cost), 0.0) as total_costs,
                    COALESCE(SUM(profit), 0.0) as net_profit,
                    COUNT(*) as total_transactions
                FROM payments
                WHERE created_at LIKE ? AND (status IS NULL OR status != 'reversed')
            """, (month_str,)).fetchone()

            total_income = float(res["total_income"])
            total_costs = float(res["total_costs"])
            net_profit = float(res["net_profit"])
            tx_count = int(res["total_transactions"])

            # 2. Dinero por cobrar esta semana (cuentas activas que vencen en los próximos 7 días)
            active_accounts = get_active_accounts()
            pending_receivables_7d = 0.0
            pending_accounts_count = 0
            pending_list = []

            for a in active_accounts:
                days = a.get("days_remaining")
                if days is not None and -5 <= days <= 7:
                    price_val = parse_money(a.get("price"))
                    pending_receivables_7d += price_val
                    pending_accounts_count += 1
                    pending_list.append({
                        "client": a.get("client_name"),
                        "platform": a.get("platform"),
                        "email": a.get("email"),
                        "days_remaining": days,
                        "price": price_val,
                        "whatsapp": a.get("whatsapp"),
                        "telegram": a.get("telegram")
                    })

            # 3. Ganancia estimada mensual total si todos pagan
            monthly_projected_gross = sum(parse_money(a.get("price")) for a in active_accounts)
            monthly_projected_costs = sum(parse_money(a.get("cost")) for a in active_accounts)
            monthly_projected_profit = monthly_projected_gross - monthly_projected_costs

            return {
                "period": f"{today.strftime('%B %Y')}",
                "collected_income": total_income,
                "collected_costs": total_costs,
                "collected_profit": net_profit,
                "transactions_count": tx_count,
                "pending_receivables_7d": pending_receivables_7d,
                "pending_accounts_count": pending_accounts_count,
                "pending_accounts": pending_list,
                "projected_monthly_gross": monthly_projected_gross,
                "projected_monthly_profit": monthly_projected_profit,
                "active_subscriptions_total": len(active_accounts)
            }
    finally:
        conn.close()
