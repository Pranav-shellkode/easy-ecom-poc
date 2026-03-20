from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

import streamlit as st
import requests
import json
import re
import uuid

from config import MAIN_API_PORT, MOCK_API_PORT
from agents.agent_prompts import get_current_date_iso

BACKEND_URL = f"http://localhost:{MAIN_API_PORT}"

st.set_page_config(
    page_title="EasyEcom AI Assistant",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 EasyEcom AI Assistant")
st.markdown("Your intelligent assistant with real-time execution visibility")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

#   "idle"      → show chat input, accept new messages
#   "awaiting"  → planning done, show approval card, block input
#   "executing" → user approved, run real agent, then return to idle
if "approval_state" not in st.session_state:
    st.session_state.approval_state = "idle"

# pending_approval holds:
#   {tool_name, tool_input, original_message, summary}
if "pending_approval" not in st.session_state:
    st.session_state.pending_approval = None


def backend_chat(message: str, session_id: str) -> str:
    """Call POST /chat and return the response text."""
    try:
        resp = requests.post(
            f"{BACKEND_URL}/chat",
            json={"message": message, "session_id": session_id},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        return f"Error contacting backend: {e}"


def backend_chat_stream(message: str, session_id: str):
    """
    Call POST /chat/stream and yield parsed event dicts.
    Each yielded dict has one of these shapes:
      {"token": "<text>"}
      {"tool_use": {<tool data>}}
      {"result": "<text>"}
      {"error": "<text>"}
    """
    try:
        with requests.post(
            f"{BACKEND_URL}/chat/stream",
            json={"message": message, "session_id": session_id},
            stream=True,
            timeout=300,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data:"):
                    payload = text[len("data:"):].strip()
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        yield {"error": str(e)}



def extract_tool_plan(response_text: str) -> dict | None:
    """Parse tool plan JSON from an LLM planning response."""
    # 1. JSON inside a code fence
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if "tool" in data:
                return data
        except json.JSONDecodeError:
            pass

    # 2. First {...} block containing the word "tool"
    brace = re.search(r"\{[^{}]*\"tool\"[^{}]*\}", response_text, re.DOTALL)
    if brace:
        try:
            data = json.loads(brace.group(0))
            if "tool" in data:
                return data
        except json.JSONDecodeError:
            pass

    # 3. Whole response as JSON
    stripped = response_text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "tool" in data:
                return data
        except json.JSONDecodeError:
            pass

    return None


TOOL_LABELS = {
    "order_confirmation": " Order Confirmation",
    "report_generation": " Report Generation",
    "batch_creation": " Batch Creation",
}

def _get_planning_injection() -> str:
    """Build the planning prompt fresh on each call so today's date is always accurate."""
    return (
        "You are a planning assistant for EasyEcom operations. "
        f"Today's date (IST) is {get_current_date_iso()}. "
        "Use this date when resolving relative date references like 'last month', "
        "'last week', 'last 7 days', 'January', etc. into concrete startDate/endDate values.\n\n"
        "Given a user request, output ONLY a single valid JSON object describing "
        "the tool action that would be taken \u2014 do NOT execute anything.\n\n"
        "Available tools and their parameters:\n"
        "- order_confirmation: count (int), marketplace_name (list[str]), "
        "order_type (optional str), payment_mode (optional str)\n"
        "- report_generation: report_type (str), user_message (str \u2014 always include "
        "the original user message verbatim), "
        "report_params (optional dict with startDate/endDate as YYYY-MM-DD strings), mailed (bool)\n"
        "- batch_creation: count (int), batch_size (int), marketplaces (list[str])\n\n"
        "Response format (JSON only, no other text):\n"
        '{"tool": "<tool_name>", "params": {<key>: <value>}, '
        '"summary": "<plain English description including any resolved date range>"}\n\n'
        "If no tool is needed (conversational query), respond:\n"
        '{"tool": null, "params": {}, "summary": "<response>"}\n\n'
        "USER REQUEST: "
    )


def plan_tool_call_via_api(message: str) -> str:
    """
    Ask the backend to plan (not execute) the given message.
    We embed the planning instructions inside the message itself so the
    existing /chat endpoint acts as our planning pass.
    """
    planning_message = _get_planning_injection() + message
    return backend_chat(planning_message, session_id="__planner__")


def render_approval_card(pending: dict):
    """Render the HITL approval card with Approve / Cancel buttons."""
    tool_name = pending.get("tool_name", "Unknown Tool")
    tool_input = pending.get("tool_input", {})
    summary = pending.get("summary", "")

    label = TOOL_LABELS.get(tool_name, f"️ {tool_name}")

    st.markdown("---")
    st.warning(f"###️ Approval Required Before Execution")
    st.markdown(f"**Action:** {label}")

    if summary:
        st.info(f"**What will happen:** {summary}")

    # Parameters table
    st.markdown("**Parameters to be used:**")
    rows = [
        {"Parameter": k, "Value": str(v)}
        for k, v in tool_input.items()
        if v is not None
    ]
    if rows:
        st.table(rows)
    else:
        st.caption("No parameters.")

    col1, col2, _ = st.columns([1, 1, 2])
    with col1:
        if st.button(
            "Approve & Execute",
            key="approve_btn",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.approval_state = "executing"
            st.rerun()
    with col2:
        if st.button("Cancel", key="cancel_btn", use_container_width=True):
            st.session_state.pending_approval = None
            st.session_state.approval_state = "idle"
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": " Action cancelled. No changes were made.",
                    "rich_content": [],
                }
            )
            st.rerun()


def render_assistant_message(message: dict):
    """
    Render an assistant message including its rich content (tool calls,
    tool responses, reasoning) so it looks the same whether being
    rendered for the first time or re-rendered from history.
    """
    rich = message.get("rich_content", [])
    text = message.get("content", "")

    if rich:
        final_label = "Response Generated" if text else "Completed"
        with st.status(final_label, expanded=False, state="complete"):
            for item in rich:
                kind = item.get("kind")
                if kind == "tool_call":
                    st.write(f"🛠️ **Tool Call:** `{item['name']}`")
                elif kind == "tool_response":
                    st.write("📊 **Tool Response:**")
                    st.code(item["text"])
                elif kind == "reasoning":
                    st.write("💭 **Reasoning:**")
                    st.caption(item["text"])

    if text:
        st.markdown(text)


# ── Replay chat history ────────────────────────────────────────────────────────
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_assistant_message(message)
        else:
            st.markdown(message["content"])


approval_state = st.session_state.approval_state


# ─────────────────────── AWAITING APPROVAL ────────────────────────────────────
if approval_state == "awaiting":
    pending = st.session_state.pending_approval
    if pending:
        render_approval_card(pending)
    else:
        st.session_state.approval_state = "idle"
        st.rerun()


# state after the user presses the approve and execute button
elif approval_state == "executing":
    pending = st.session_state.pending_approval or {}
    original_message = pending.get("original_message", "")

    full_response = ""
    rich_content = []

    with st.chat_message("assistant"):
        with st.status("Initializing...", expanded=True) as status:
            try:
                status.update(label="Reasoning...", state="running")

                # stream from the /chat/stream endpoint 
                seen_tools: set = set()

                for event in backend_chat_stream(original_message, st.session_state.session_id):
                    if "error" in event:
                        status.update(label="Error occurred", state="error")
                        st.error(f"Error: {event['error']}")
                        full_response = f"I encountered an error: {event['error']}"
                        break

                    elif "token" in event:
                        if not full_response:
                            status.update(label="Generating response...", state="running")
                        full_response += event["token"]

                    elif "tool_use" in event:
                        tool_data = event["tool_use"]
                        tool_name = tool_data.get("name", "")
                        tool_input = tool_data.get("input", {})
                        if tool_name and tool_name not in seen_tools and tool_input:
                            seen_tools.add(tool_name)
                            status.update(label=f"Calling {tool_name}...", state="running")
                            st.write(f"🛠️ **Tool Call:** `{tool_name}`")
                            st.code(json.dumps(tool_input, indent=2), language="json")
                            rich_content.append(
                                {"kind": "tool_call", "name": tool_name, "input": tool_input}
                            )

                    elif "result" in event:
                        result_text = event["result"]
                        if result_text:
                            status.update(label="Analyzing tool response...", state="running")
                            st.write("📊 **Tool Response:**")
                            st.code(result_text)
                            rich_content.append({"kind": "tool_response", "text": result_text})

                if full_response:
                    status.update(label="Response Generated", state="complete")

            except Exception as e:
                status.update(label="Error occurred", state="error")
                st.error(f"Error: {str(e)}")
                full_response = f"I encountered an error: {str(e)}"
                rich_content = []

        if full_response:
            st.markdown(full_response)

    # Persist message and reset state
    st.session_state.messages.append(
        {"role": "assistant", "content": full_response, "rich_content": rich_content}
    )
    st.session_state.approval_state = "idle"
    st.session_state.pending_approval = None
    st.rerun()


elif approval_state == "idle":
    if prompt := st.chat_input(
        "Ask me to confirm orders, generate reports, or create batches..."
    ):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 2. Planning pass — POST /chat with a planning prompt (no side effects)
        with st.spinner("Analyzing your request..."):
            try:
                plan_text = plan_tool_call_via_api(prompt)
                tool_plan = extract_tool_plan(plan_text)
            except Exception:
                tool_plan = None

        if tool_plan and tool_plan.get("tool"):
            st.session_state.pending_approval = {
                "tool_name": tool_plan["tool"],
                "tool_input": tool_plan.get("params", {}),
                "original_message": prompt,
                "summary": tool_plan.get("summary", ""),
            }
            st.session_state.approval_state = "awaiting"
        else:
            st.session_state.pending_approval = {
                "tool_name": None,
                "tool_input": {},
                "original_message": prompt,
                "summary": "",
            }
            st.session_state.approval_state = "executing"

        st.rerun()


with st.sidebar:
    st.header("💡 Example Commands")

    st.subheader("Order Confirmation")
    st.code("Confirm 50 prepaid Amazon orders")
    st.code("Confirm 25 COD orders from Flipkart")

    st.subheader("Report Generation")
    st.code("Generate sales report for last month")
    st.code("Generate tax report for June and email it")
    st.code("Generate stock report")

    st.subheader("Batch Creation")
    st.code("Create 3 batches with 100 orders each for Amazon")
    st.code("Create 5 batches of 50 orders from Flipkart")

    st.markdown("---")

    try:
        health = requests.get(f"{BACKEND_URL}/health", timeout=3)
        if health.ok:
            st.markdown("**Backend:** 🟢 Connected")
        else:
            st.markdown("**Backend:** � Unhealthy")
    except Exception:
        st.markdown("**Backend:** 🔴 Offline — start `main.py`")

    st.markdown(f"**Main API:** Port {MAIN_API_PORT}")
    st.markdown(f"**Mock API:** Port {MOCK_API_PORT}")

    if st.button("Clear Chat"):
        st.session_state.clear()
        st.rerun()