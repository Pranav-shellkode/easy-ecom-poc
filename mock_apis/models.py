from pydantic import BaseModel, model_validator
from typing import Dict, List, Optional

class OrderConfirmRequest(BaseModel):
    count: int
    marketplace_name: List[str]
    order_type: Optional[str] = None
    payment_mode: Optional[str] = None

_DATE_SENSITIVE_REPORTS = {"MINI_SALES_REPORT", "TAX_REPORT"}

class ReportRequest(BaseModel):
    report_type: str
    report_params: Optional[Dict[str, str]] = None
    mailed: bool = False

    @model_validator(mode="after")
    def _require_dates_for_date_sensitive_reports(self) -> "ReportRequest":
        if self.report_type in _DATE_SENSITIVE_REPORTS:
            params = self.report_params or {}
            if not params.get("startDate") or not params.get("endDate"):
                raise ValueError(
                    f"{self.report_type} requires both 'startDate' and 'endDate' "
                    "in report_params (format: YYYY-MM-DD)."
                )
        return self

class BatchRequest(BaseModel):
    count: int
    batch_size: int
    marketplaces: List[str]