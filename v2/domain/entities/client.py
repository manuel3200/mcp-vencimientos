from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class Client:
    id: Optional[int] = None
    name: str = ""
    whatsapp: str = ""
    client_code: str = ""
    client_type: str = "consumidor_final"  # 'consumidor_final', 'revendedor', 'revendedor_vip'
    status: str = "activo"
    debt_balance: float = 0.0
    created_at: Optional[str] = None
    notes: Optional[str] = None

    @property
    def is_vip(self) -> bool:
        return "vip" in self.client_type.lower()

    @property
    def is_reseller(self) -> bool:
        return "revend" in self.client_type.lower() or self.is_vip

    @property
    def badge_icon(self) -> str:
        if self.is_vip:
            return "👑 VIP"
        elif self.is_reseller:
            return "💼 Revendedor"
        return "👤 Final"
