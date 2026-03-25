import os
from dotenv import load_dotenv
load_dotenv()
import logging
from collections import OrderedDict
from strands import Agent
from strands.models import BedrockModel
from strands.session.file_session_manager import FileSessionManager
from tools.easyecom_tools import order_confirmation_tool, report_generation_tool, batch_creation_tool
from agents.agent_prompts import get_easyecom_system_prompt, get_current_date_iso
from config import AWS_REGION, BEDROCK_MODEL_ID, BEDROCK_THINKING_BUDGET
from typing import Dict, Any, Optional, AsyncGenerator

logger = logging.getLogger(__name__)


MAX_SESSIONS = 10

class EasyEcomAgent:
    """EasyEcom AI Assistant using Strands SDK"""
    
    def __init__(self):
        import boto3
        self._boto_session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )

        self._agents: OrderedDict[str, Agent] = OrderedDict()
        self._bedrock_model = BedrockModel(
            model_id=BEDROCK_MODEL_ID,
            boto_session=self._boto_session,
            additional_request_fields={"thinking": {"type": "enabled", "budget_tokens": BEDROCK_THINKING_BUDGET}}
        )
    
    def get_strands_agent(self, session_id: str) -> Optional[Agent]:
        """Get configured Strands agent instance"""
        try:
            if session_id not in self._agents:
                if len(self._agents) >= MAX_SESSIONS:
                    self._agents.popitem(last=False)
                session_manager = FileSessionManager(session_id=session_id)
                self._agents[session_id] = Agent(
                    name="EasyEcom AI Assistant",
                    model=self._bedrock_model,
                    system_prompt=get_easyecom_system_prompt(),
                    tools=[
                        order_confirmation_tool,
                        report_generation_tool,
                        batch_creation_tool
                    ],
                    session_manager=session_manager,
                )
                logger.info("session_id=<%s> | created new agent instance", session_id)
            return self._agents[session_id]
            
        except Exception as e:
            logger.error("session_id=<%s>, error=<%s> | failed to initialize strands agent", session_id, str(e))
            return None

    # this part of the tool call is purely used by the streamlit implementation for confirmation before tool execution only
    def plan_tool_call(self, message: str) -> str:
        """Run a lightweight planning pass (no tools, no thinking) to extract
        the intended tool call as a JSON plan before asking the user to approve.

        Returns a string that should contain a JSON object like:
            {"tool": "<name>", "params": {...}, "summary": "<human description>"}
        or:
            {"tool": null, "params": {}, "summary": "<conversational response>"}
        """
        planning_model = BedrockModel(
            model_id=BEDROCK_MODEL_ID,
            boto_session=self._boto_session,
        )
        planning_agent = Agent(
            name="EasyEcom Planner",
            model=planning_model,
            system_prompt=(
                "You are the planning layer of an EasyEcom AI Assistant. "
                f"Today's date (IST) is {get_current_date_iso()}. "
                "Your ONLY job is to analyse the user request and return a JSON plan. Do NOT execute anything.\n\n"

                "DATE RESOLUTION\n"
                f"Use today = {get_current_date_iso()} to resolve relative expressions into exact YYYY-MM-DD dates.\n"
                "- 'last month' → full previous calendar month\n"
                "- 'last week' → last Monday to last Sunday\n"
                "- 'last N days' → today minus N days to today\n"
                "- Named month (e.g. 'January') → full month of current year unless year is specified\n"
                "- Q1/Q2/Q3/Q4 → corresponding calendar quarter\n"
                "Sales and tax reports ALWAYS require both startDate and endDate. "
                "Stock reports NEVER need a date range.\n\n"

                "TOOLS & REQUIRED PARAMETERS\n"
                "- order_confirmation:\n"
                "    count (int > 0) REQUIRED\n"
                "    marketplace_name (list — Amazon | Flipkart | Myntra) REQUIRED\n"
                "    order_type (optional: 'forward' | 'reverse')\n"
                "    payment_mode (optional: 'prepaid' | 'COD')\n"
                "- report_generation:\n"
                "    report_type (MINI_SALES_REPORT | TAX_REPORT | STATUS_WISE_STOCK_REPORT) REQUIRED\n"
                "    user_message (copy user's original message verbatim) REQUIRED\n"
                "    report_params ({startDate, endDate} YYYY-MM-DD) REQUIRED for sales/tax, omit for stock\n"
                "    mailed (bool) — true ONLY if user explicitly asks to email/send the report\n"
                "- batch_creation:\n"
                "    count (int > 0) REQUIRED\n"
                "    batch_size (int > 0) REQUIRED\n"
                "    marketplaces (list — Amazon | Flipkart | Myntra) REQUIRED\n\n"

                "STATUS RULES — choose exactly one:\n"
                "  'ready'               → all required params present → show confirmation card\n"
                "  'needs_clarification' → one or more required params missing → write a natural question\n"
                "  'conversational'      → no tool needed → answer the user directly\n\n"

                "OUTPUT FORMAT — respond with ONLY valid JSON, no other text:\n\n"

                "When status is 'ready':\n"
                '{"tool": "<name>", "params": {<all resolved params>}, "status": "ready", '
                '"summary": "<one sentence: what will happen>", "question": null}\n\n'

                "When status is 'needs_clarification':\n"
                '{"tool": "<name>", "params": {<whatever resolved so far>}, "status": "needs_clarification", '
                '"summary": "<brief description>", '
                '"question": "<natural friendly clarifying question>"}\n\n'

                "When status is 'conversational':\n"
                '{"tool": null, "params": {}, "status": "conversational", '
                '"summary": "<helpful reply>", "question": null}\n\n'

                "EXAMPLES\n"
                "- 'Confirm Amazon orders' → needs_clarification: question='How many orders would you like to confirm from Amazon?'\n"
                "- 'Confirm 50 Amazon orders' → ready\n"
                "- 'Generate a report' → needs_clarification: question='Which report — Sales, Tax, or Stock?'\n"
                "- 'Generate sales report for last month' → ready with resolved dates\n"
                "- 'Create batches for Amazon' → needs_clarification: question='How many batches, and how many orders per batch?'\n"
                "- 'What can you do?' → conversational\n\n"

                "USER REQUEST: "
            ),
            tools=[],
        )
        try:
            response = planning_agent(message)
            return str(response)
        except Exception as e:
            logger.error("error=<%s> | planning pass failed", str(e))
            return '{"tool": null, "params": {}, "summary": ""}'
# =============================================================================================================================

    async def process_message_streaming(self, message: str, session_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Process user message with streaming response for API"""
        try:
            agent = self.get_strands_agent(session_id)
            if not agent:
                yield {"error": "AI assistant is currently unavailable. Please try again later."}
                return
            
            invocation_state = {
                "session_id": session_id,
                "user_id": "default_user"
            }
            
            async for event in agent.stream_async(message, **invocation_state):
                if "data" in event:
                    yield {"type": "token", "data": event["data"]}
                elif "current_tool_use" in event:
                    yield {"type": "tool_use", "data": event["current_tool_use"]}
                elif "result" in event:
                    yield {"type": "result", "data": str(event["result"].content) if hasattr(event["result"], "content") else str(event["result"])}
                elif "error" in event:
                    yield {"type": "error", "data": event["error"]}
                    
        except Exception as e:
            logger.error("session_id=<%s>, error=<%s> | error in streaming", session_id, str(e))
            yield {"type": "error", "data": f"I encountered an error: {str(e)}"}
    
    async def process_message(self, message: str, session_id: str) -> Dict[str, Any]:
        """Process user message using Strands agent for API"""
        try:
            agent = self.get_strands_agent(session_id)
            if not agent:
                return {"response": "AI assistant is currently unavailable. Please try again later."}
            
            invocation_state = {
                "session_id": session_id,
                "user_id": "default_user"
            }
            
            response = agent(message, **invocation_state)
            response_text = response.content if hasattr(response, 'content') else str(response)
            
            return {"response": response_text}
            
        except Exception as e:
            logger.error("session_id=<%s>, error=<%s> | error processing message", session_id, str(e))
            return {"response": f"I encountered an error: {str(e)}"}