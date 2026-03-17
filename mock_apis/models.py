from pydantic import BaseModel
from typing import List, Optional

class OrderConfirmRequest(BaseModel):
    count: int
    marketplace_name: List[str]
    order_type: Optional[str] = None
    payment_mode: Optional[str] = None

class ReportRequest(BaseModel):
    report_type: str
    report_params: dict
    mailed: bool = False

class BatchRequest(BaseModel):
    count: int
    batch_size: int
    marketplaces: List[str]