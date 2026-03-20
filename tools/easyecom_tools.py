import re
import requests
import logging
from typing import Dict, Any, List, Optional, Tuple
from strands import tool, ToolContext
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo
from config import REPORT_TYPES, MOCK_API_BASE_URL

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")


def _today() -> date:
    """Return today's date in IST (the business timezone)."""
    return datetime.now(_IST).date()


# ── Month name helpers ─────────────────────────────────────────────────────────
_MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {name: i + 1 for i, name in enumerate(_MONTH_NAMES)}
_ALL_MONTHS = {**_MONTH_FULL, **_MONTH_ABBR}

_MONTH_PATTERN = r"("
_MONTH_PATTERN += "|".join(_MONTH_NAMES + list(_MONTH_ABBR.keys()))
_MONTH_PATTERN += r")"


def _month_range(month_num: int, year: int) -> Tuple[date, date]:
    """Return (first_day, last_day) for a given month and year."""
    start = date(year, month_num, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start, end


def _fmt(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def parse_natural_date(text: str) -> Optional[Dict[str, str]]:
    """Parse a natural-language date expression from text and return
    a ``{startDate, endDate}`` dict with ISO-8601 date strings.

    Supported patterns (case-insensitive):
    - today / yesterday
    - this week / last week
    - this month / last month
    - this quarter / last quarter
    - this year / last year
    - last N days / last N weeks / last N months
    - Named month with optional year  (e.g. "January", "feb 2024")
    - Explicit range like "Jan 1 to Mar 31" or "Jan 1 - Mar 31"

    Returns None when no recognisable date expression is found.
    """
    if not text:
        return None

    s = text.lower().strip()
    today = _today()

    # ── today ─────────────────────────────────────────────────────────────────
    if re.search(r"\btoday\b", s):
        return {"startDate": _fmt(today), "endDate": _fmt(today)}

    # ── yesterday ─────────────────────────────────────────────────────────────
    if re.search(r"\byesterday\b", s):
        yesterday = today - timedelta(days=1)
        return {"startDate": _fmt(yesterday), "endDate": _fmt(yesterday)}

    # ── last N days ───────────────────────────────────────────────────────────
    m = re.search(r"last\s+(\d+)\s+days?", s)
    if m:
        n = int(m.group(1))
        start = today - timedelta(days=n - 1)
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── last N weeks ──────────────────────────────────────────────────────────
    m = re.search(r"last\s+(\d+)\s+weeks?", s)
    if m:
        n = int(m.group(1))
        start = today - timedelta(weeks=n)
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── last N months ─────────────────────────────────────────────────────────
    m = re.search(r"last\s+(\d+)\s+months?", s)
    if m:
        n = int(m.group(1))
        start = today - relativedelta(months=n)
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── this week ─────────────────────────────────────────────────────────────
    if re.search(r"\bthis\s+week\b", s):
        start = today - timedelta(days=today.weekday())  # Monday
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── last week ─────────────────────────────────────────────────────────────
    if re.search(r"\blast\s+week\b", s):
        end = today - timedelta(days=today.weekday() + 1)   # last Sunday
        start = end - timedelta(days=6)                      # last Monday
        return {"startDate": _fmt(start), "endDate": _fmt(end)}

    # ── this month ────────────────────────────────────────────────────────────
    if re.search(r"\bthis\s+month\b", s):
        start = today.replace(day=1)
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── last month ────────────────────────────────────────────────────────────
    if re.search(r"\blast\s+month\b", s):
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        return {"startDate": _fmt(start), "endDate": _fmt(end)}

    # ── quarter helpers ───────────────────────────────────────────────────────
    def _quarter_range(year: int, q: int) -> Tuple[date, date]:
        start_month = (q - 1) * 3 + 1
        start = date(year, start_month, 1)
        end = start + relativedelta(months=3) - timedelta(days=1)
        return start, end

    # ── this quarter ──────────────────────────────────────────────────────────
    if re.search(r"\bthis\s+quarter\b", s):
        q = (today.month - 1) // 3 + 1
        start, end = _quarter_range(today.year, q)
        return {"startDate": _fmt(start), "endDate": _fmt(min(end, today))}

    # ── last quarter ──────────────────────────────────────────────────────────
    if re.search(r"\blast\s+quarter\b", s):
        q = (today.month - 1) // 3 + 1
        prev_q = q - 1 if q > 1 else 4
        prev_year = today.year if q > 1 else today.year - 1
        start, end = _quarter_range(prev_year, prev_q)
        return {"startDate": _fmt(start), "endDate": _fmt(end)}

    # ── Q1/Q2/Q3/Q4 with optional year ────────────────────────────────────────
    m = re.search(r"\bq([1-4])(?:\s+(\d{4}))?\b", s)
    if m:
        q = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        start, end = _quarter_range(year, q)
        return {"startDate": _fmt(start), "endDate": _fmt(end)}

    # ── this year ─────────────────────────────────────────────────────────────
    if re.search(r"\bthis\s+year\b", s):
        start = date(today.year, 1, 1)
        return {"startDate": _fmt(start), "endDate": _fmt(today)}

    # ── last year ─────────────────────────────────────────────────────────────
    if re.search(r"\blast\s+year\b", s):
        prev = today.year - 1
        return {"startDate": f"{prev}-01-01", "endDate": f"{prev}-12-31"}

    # ── Explicit range: "Jan 1 to/- Mar 31" (with optional years) ─────────────
    range_pat = (
        r"\b" + _MONTH_PATTERN + r"\s+(\d{1,2})(?:,?\s*(\d{4}))?\s*"
        r"(?:to|through|–|-)\s*"
        + _MONTH_PATTERN + r"\s+(\d{1,2})(?:,?\s*(\d{4}))?"
    )
    m = re.search(range_pat, s)
    if m:
        start_month_name,start_day_s,start_year_s, end_month_name, end_day_s, end_year_s = m.groups()
        start_month = _ALL_MONTHS.get(start_month_name[:3])
        end_month = _ALL_MONTHS.get(end_month_name[:3])
        if start_month and end_month:
            start_year = int(start_year_s) if start_year_s else today.year
            end_year = int(end_year_s) if end_year_s else today.year
            start = date(start_year, start_month, int(start_day_s))
            end = date(end_year, end_month, int(end_day_s))
            if start <= end:
                return {"startdate": _fmt(start), "endDate": _fmt(end)}

    # ── Named month with optional year: "January", "Jan 2024" ─────────────────
    m = re.search(r"\b" + _MONTH_PATTERN + r"(?:\s+(\d{4}))?\b", s)
    if m:
        month_name = m.group(1)
        month_num = _ALL_MONTHS.get(month_name[:3])
        if month_num:
            year = int(m.group(2)) if m.group(2) else today.year
            start, end = _month_range(month_num, year)
            return {"startDate": _fmt(start), "endDate": _fmt(end)}

    return None


# Keep the old name as an alias for backward-compatibility.
parse_date_range = parse_natural_date

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
def report_generation_tool(
    tool_context: ToolContext,
    report_type: str,
    user_message: Optional[str] = None,
    report_params: Optional[Dict] = None,
    mailed: bool = False,
) -> str:
    """Execute report generation.

    Args:
        tool_context: Tool execution context
        report_type: Type of report to generate (e.g. MINI_SALES_REPORT)
        user_message: The original user message — used to extract date range when
                      report_params is not supplied explicitly.
        report_params: Optional explicit report parameters (startDate / endDate).
        mailed: Whether to email the report

    Returns:
        Report generation confirmation with report ID
    """
    try:
        if not report_params:
            # Prefer the user's original message for date extraction so that
            # natural-language references like "last month" are parsed correctly.
            source = user_message or ""
            parsed = parse_natural_date(source)
            if parsed:
                report_params = parsed
                logger.info(
                    "report_type=<%s> | parsed date range from user_message: %s",
                    report_type, parsed,
                )
            else:
                report_params = {}
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

