from dataclasses import dataclass
from typing import Optional

@dataclass
class CatalogItem:
    id: Optional[int] = None
    platform: str = ""
    screen_type: str = "Pantalla Individual"
    cost_price: float = 0.0
    price_final: float = 0.0
    price_reseller: float = 0.0
    price_reseller_vip: float = 0.0
    notes: Optional[str] = None
