from dataclasses import dataclass
from typing import Optional

@dataclass
class Account:
    id: Optional[int] = None
    platform: str = ""
    email: str = ""
    password: str = ""
    profile_name: Optional[str] = None
    pin: Optional[str] = None
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    client_whatsapp: Optional[str] = None
    cost_price: float = 0.0
    price_sold: float = 0.0
    sale_date: Optional[str] = None
    expiry_date: Optional[str] = None
    status: str = "activo"  # 'activo', 'libre', 'vencido', 'caida'
    notes: Optional[str] = None
    is_fallen: int = 0
    master_account_id: Optional[int] = None

    @property
    def is_http_custom(self) -> bool:
        return "custom" in self.platform.lower() or "hwid" in self.platform.lower()

    @property
    def is_free(self) -> bool:
        return self.status == "libre" or self.client_id is None
