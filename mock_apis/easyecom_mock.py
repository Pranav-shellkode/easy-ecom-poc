import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # ensure project root is on path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from config import MOCK_API_PORT
from itertools import count
from mock_apis.models import OrderConfirmRequest, ReportRequest, BatchRequest

mock_app = FastAPI(title="EasyEcom Mock API")
_report_counter = count(1)


@mock_app.post("/orders/confirm")
async def confirm_orders(request: OrderConfirmRequest):
    if request.count <= 0:
        raise HTTPException(status_code=400, detail="Count must be positive")
    
    confirmed = request.count
    return {
        "status": 200,
        "message": f"Successfully confirmed {confirmed} orders",
        "confirmed_count": confirmed
    }

@mock_app.post("/reports/generate")
async def generate_report(request: ReportRequest):
    valid_reports = ["MINI_SALES_REPORT", "TAX_REPORT", "STATUS_WISE_STOCK_REPORT"]
    
    if request.report_type not in valid_reports:
        raise HTTPException(status_code=400, detail="Invalid report type")
    
    if request.report_type in ["MINI_SALES_REPORT", "TAX_REPORT"]:
        if not request.report_params.get("startDate") or not request.report_params.get("endDate"):
            raise HTTPException(status_code=400, detail="Date range is mandatory for this report")
    
    action = "emailed" if request.mailed else "generated"
    return {
        "status": 200,
        "message": f"Report {action} successfully",
        "report_id": f"RPT_{next(_report_counter):04d}"
    }

@mock_app.post("/batches/create")
async def create_batches(request: BatchRequest):
    if request.count <= 0 or request.batch_size <= 0:
        raise HTTPException(status_code=400, detail="Count and batch_size must be positive")
    
    created = request.count
    return {
        "status": 200,
        "message": f"Successfully created {created} batches",
        "created_count": created
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mock_app, host="0.0.0.0", port=MOCK_API_PORT)