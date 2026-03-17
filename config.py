# EasyEcom AI Assistant Configuration

# API Configuration
MOCK_API_PORT = 8001
MAIN_API_PORT = 8000
STREAMLIT_PORT = 8501
MOCK_API_BASE_URL = f"http://localhost:{MOCK_API_PORT}"

# AWS Bedrock Configuration
AWS_REGION = "us-east-1"
BEDROCK_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_THINKING_BUDGET = 1024

# Supported Marketplaces
SUPPORTED_MARKETPLACES = ["Amazon", "Flipkart", "Myntra"]

# Report Types
REPORT_TYPES = {
    "sales": "MINI_SALES_REPORT",
    "tax": "TAX_REPORT", 
    "stock": "STATUS_WISE_STOCK_REPORT"
}