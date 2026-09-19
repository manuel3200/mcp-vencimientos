from dataclasses import dataclass
from typing import Optional

@dataclass
class Payment:
    id: Optional[int] = None
    client_id: Optional[int] = None
    client_name: str = ""
    sender_phone: Optional[str] = None
    amount: float = 0.0
    payment_method: str = "Transferencia"
    bank: Optional[str] = None
    reference_code: Optional[str] = None
    receipt_image_path: Optional[str] = None
    receipt_data: Optional[str] = None
    status: str = "pending"  # 'pending', 'approved', 'rejected'
    created_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    notes: Optional[str] = None
