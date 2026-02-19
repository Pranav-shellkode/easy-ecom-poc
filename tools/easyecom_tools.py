import re
import requests
import logging
from typing import Dict, Any, List, Optional
from strands import tool, ToolContext
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from config import REPORT_TYPES, MOCK_API_BASE_URL

logger = logging.getLogger(__name__)

@tool(
    name="order_confirmation",
    description="Confirm pending marketplace orders in EasyEcom",
    context=True
)
def order_confirmation_tool(tool_context: ToolContext, count: int, marketplace_name: List[str], 
                           order_type: Optional[str] = None, payment_mode: Optional[str] = None) -> str:
    """Execute order confirmation.
    
    Args:
        tool_context: Tool execution context
        count: Number of orders to confirm
        marketplace_name: List of marketplace names
        order_type: Optional order type filter
        payment_mode: Optional payment mode filter
        
    Returns:
        Confirmation message with order count
    """
    try:
        response = requests.post(
            f"{MOCK_API_BASE_URL}/orders/confirm",
            json={
                "count": count,
                "marketplace_name": marketplace_name,
                "order_type": order_type,
                "payment_mode": payment_mode
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            confirmed = data.get('confirmed_count', count)
            marketplaces = ', '.join(marketplace_name)
            logger.info("count=<%d>, marketplaces=<%s> | orders confirmed successfully", confirmed, marketplaces)
            return f"Successfully confirmed {confirmed} orders from {marketplaces}"
        else:
            error_detail = response.json().get('detail', 'Unknown error')
            logger.error("status_code=<%d>, error=<%s> | api error", response.status_code, error_detail)
            return f"Failed to confirm orders: {error_detail}"
    
    except requests.RequestException as e:
        logger.error("error=<%s> | failed to connect to easyecom api", str(e))
        return f"Failed to connect to EasyEcom API: {str(e)}"
    except Exception as e:
        logger.error("error=<%s> | unexpected error in order confirmation", str(e))
        return f"Error confirming orders: {str(e)}"

@tool(
    name="report_generation", 
    description="Generate business reports from EasyEcom data",
    context=True
)
def report_generation_tool(tool_context: ToolContext, report_type: str, 
                          report_params: Optional[Dict] = None, mailed: bool = False) -> str:
    """Execute report generation.
    
    Args:
        tool_context: Tool execution context
        report_type: Type of report to generate
        report_params: Optional report parameters
        mailed: Whether to email the report
        
    Returns:
        Report generation confirmation with report ID
    """
    try:
        if report_params is None:
            report_params = parse_date_range(report_type) or {}
        response = requests.post(
            f"{MOCK_API_BASE_URL}/reports/generate",
            json={
                "report_type": report_type,
                "report_params": report_params,
                "mailed": mailed
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            report_id = data.get('report_id')
            action = "emailed" if mailed else "generated"
            logger.info("report_type=<%s>, report_id=<%s>, mailed=<%s> | report %s successfully", report_type, report_id, mailed, action)
            return f"Report {action} successfully. Report ID: {report_id}"
        else:
            error_detail = response.json().get('detail', 'Unknown error')
            logger.error("status_code=<%d>, error=<%s> | api error", response.status_code, error_detail)
            return f"Failed to generate report: {error_detail}"
    
    except requests.RequestException as e:
        logger.error("error=<%s> | failed to connect to easyecom api", str(e))
        return f"Failed to connect to EasyEcom API: {str(e)}"
    except Exception as e:
        logger.error("error=<%s> | unexpected error in report generation", str(e))
        return f"Error generating report: {str(e)}"

@tool(
    name="batch_creation",
    description="Create order batches for warehouse operations", 
    context=True
)
def batch_creation_tool(tool_context: ToolContext, count: int, batch_size: int, marketplaces: List[str]) -> str:
    """Execute batch creation.
    
    Args:
        tool_context: Tool execution context
        count: Number of batches to create
        batch_size: Number of orders per batch
        marketplaces: List of marketplace names
        
    Returns:
        Batch creation confirmation message
    """
    try:
        response = requests.post(
            f"{MOCK_API_BASE_URL}/batches/create",
            json={
                "count": count,
                "batch_size": batch_size,
                "marketplaces": marketplaces
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            created = data.get('created_count', count)
            marketplace_list = ', '.join(marketplaces)
            logger.info("count=<%d>, batch_size=<%d>, marketplaces=<%s> | batches created successfully", created, batch_size, marketplace_list)
            return f"Successfully created {created} batches with {batch_size} orders each from {marketplace_list}"
        else:
            error_detail = response.json().get('detail', 'Unknown error')
            logger.error("status_code=<%d>, error=<%s> | api error", response.status_code, error_detail)
            return f"Failed to create batches: {error_detail}"
    
    except requests.RequestException as e:
        logger.error("error=<%s> | failed to connect to easyecom api", str(e))
        return f"Failed to connect to EasyEcom API: {str(e)}"
    except Exception as e:
        logger.error("error=<%s> | unexpected error in batch creation", str(e))
        return f"Error creating batches: {str(e)}"

def parse_date_range(message: str) -> Optional[Dict[str, str]]:
    """Parse date range from user message.

    Args:
        message: User message containing date references

    Returns:
        Dictionary with startDate and endDate or None
    """
    if 'last month' in message.lower():
        end_date = datetime.now().replace(day=1) - timedelta(days=1)
        start_date = end_date.replace(day=1)
        return {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d')
        }
    
    elif 'last week' in message.lower():
        end_date = datetime.now() - timedelta(days=datetime.now().weekday() + 1)
        start_date = end_date - timedelta(days=6)
        return {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d')
        }
    
    month_match = re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b', message.lower())
    if month_match:
        month_name = month_match.group(1)
        month_num = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }[month_name]
        
        year = datetime.now().year
        start_date = datetime(year, month_num, 1)
        end_date = start_date + relativedelta(months=1) - timedelta(days=1)
        
        return {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d')
        }
    
    return None