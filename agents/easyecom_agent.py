import os
import logging
from collections import OrderedDict
from strands import Agent
from strands.models import BedrockModel
from strands.session.file_session_manager import FileSessionManager
from tools.easyecom_tools import order_confirmation_tool, report_generation_tool, batch_creation_tool
from agents.agent_prompts import get_easyecom_system_prompt
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
                "You are a planning assistant for EasyEcom operations. "
                "Given a user request, output ONLY a single valid JSON object describing "
                "the tool action that would be taken — do NOT execute anything.\n\n"
                "Available tools and their parameters:\n"
                "- order_confirmation: count (int), marketplace_name (list[str]), "
                "order_type (optional str), payment_mode (optional str)\n"
                "- report_generation: report_type (str), "
                "report_params (optional dict with startDate/endDate), mailed (bool)\n"
                "- batch_creation: count (int), batch_size (int), marketplaces (list[str])\n\n"
                "Response format (JSON only, no other text):\n"
                '{"tool": "<tool_name>", "params": {<key>: <value>}, '
                '"summary": "<plain English description of the action>"}\n\n'
                "If no tool is needed (conversational query), respond:\n"
                '{"tool": null, "params": {}, "summary": "<response>"}'
            ),
            tools=[],  
        )
        try:
            response = planning_agent(message)
            return str(response)
        except Exception as e:
            logger.error("error=<%s> | planning pass failed", str(e))
            return '{"tool": null, "params": {}, "summary": ""}'

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