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

if "pending_confirmation" not in st.session_state:
    st.session_state.pending_confirmation = None

# execute_message: set to trigger streaming execution on next rerun
if "execute_message" not in st.session_state:
    st.session_state.execute_message = None

# awaiting_correction: True when the user clicked No and we're waiting for what to fix
if "awaiting_correction" not in st.session_state:
    st.session_state.awaiting_correction = False


# ── Backend helpers ────────────────────────────────────────────────────────────

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
    """Call POST /chat/stream and yield parsed event dicts."""
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


# ── Planning helpers ───────────────────────────────────────────────────────────

def _get_planning_injection() -> str:
    today = get_current_date_iso()
    return (
        "You are the planning layer of an EasyEcom AI Assistant. "
        f"Today's date (IST) is {today}. "
        "Your ONLY job is to analyse the user request and return a JSON plan. Do NOT execute anything.\n\n"

        "DATE RESOLUTION\n"
        f"Use today = {today} to resolve relative expressions into exact YYYY-MM-DD dates.\n"
        "- 'last month' → full previous calendar month (e.g. 2025-02-01 to 2025-02-28)\n"
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
        "  'ready'             → all required parameters are present and unambiguous. Show confirmation to user.\n"
        "  'needs_clarification' → one or more required parameters are missing or ambiguous. "
        "Ask a single, natural, friendly question to get the missing info. Do not list fields by name.\n"
        "  'conversational'    → no tool is needed. Answer the user directly.\n\n"

        "OUTPUT FORMAT — respond with ONLY valid JSON, no other text:\n\n"

        "When status is 'ready':\n"
        '{"tool": "<name>", "params": {<all resolved params>}, "status": "ready", '
        '"summary": "<one sentence: what will happen, e.g. \'Confirm 50 prepaid orders from Amazon\'>", '
        '"question": null}\n\n'

        "When status is 'needs_clarification':\n"
        '{"tool": "<name>", "params": {<whatever was resolved so far>}, "status": "needs_clarification", '
        '"summary": "<brief description of the intended action>", '
        '"question": "<natural, friendly clarifying question, e.g. \'How many orders would you like to confirm?\'>"}\n\n'

        "When status is 'conversational':\n"
        '{"tool": null, "params": {}, "status": "conversational", '
        '"summary": "<helpful reply>", "question": null}\n\n'

        "VALIDATION EXAMPLES\n"
        "- 'Confirm Amazon orders' → needs_clarification: question='How many orders would you like to confirm from Amazon?'\n"
        "- 'Confirm 50 Amazon orders' → ready: summary='Confirm 50 orders from Amazon'\n"
        "- 'Generate a report' → needs_clarification: question='Which report would you like — Sales, Tax, or Stock?'\n"
        "- 'Generate sales report for last month' → ready with resolved dates in report_params\n"
        "- 'Create batches for Amazon' → needs_clarification: question='How many batches would you like, and how many orders per batch?'\n"
        "- 'What can you do?' → conversational\n\n"

        "USER REQUEST: "
    )


def plan_tool_call_via_api(message: str) -> str:
    planning_message = _get_planning_injection() + message
    return backend_chat(planning_message, session_id="__planner__")


def extract_tool_plan(response_text: str) -> dict | None:
    def _sanitise(data: dict) -> dict:
        """Ensure 'params' is always a dict, not a str or other type."""
        if not isinstance(data.get("params"), dict):
            data["params"] = {}
        return data

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if "tool" in data:
                return _sanitise(data)
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{[^{}]*\"tool\"[^{}]*\}", response_text, re.DOTALL)
    if brace:
        try:
            data = json.loads(brace.group(0))
            if "tool" in data:
                return _sanitise(data)
        except json.JSONDecodeError:
            pass

    stripped = response_text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "tool" in data:
                return _sanitise(data)
        except json.JSONDecodeError:
            pass

    return None


# ── Parameter display helpers ──────────────────────────────────────────────────

_TOOL_DISPLAY_KEYS = {
    "order_confirmation": ["count", "marketplace_name", "order_type", "payment_mode"],
    "report_generation": ["report_type", "report_params", "mailed"],
    "batch_creation": ["count", "batch_size", "marketplaces"],
}

_PARAM_LABELS = {
    "count": "Count",
    "marketplace_name": "Marketplace(s)",
    "marketplaces": "Marketplace(s)",
    "order_type": "Order Type",
    "payment_mode": "Payment Mode",
    "report_type": "Report Type",
    "report_params": "Date Range",
    "mailed": "Email Report",
    "batch_size": "Batch Size",
}

TOOL_LABELS = {
    "order_confirmation": "✅ Order Confirmation",
    "report_generation": "📊 Report Generation",
    "batch_creation": "📦 Batch Creation",
}


def _user_facing_params(tool_name: str, params: dict) -> dict:
    """Return only user-relevant parameters for a given tool, formatted nicely."""
    # Guard: params must be a dict (LLM can occasionally return a string)
    if not isinstance(params, dict):
        params = {}
    allowed = _TOOL_DISPLAY_KEYS.get(tool_name, list(params.keys()))
    result = {}
    for key in allowed:
        if key not in params or params[key] is None:
            continue
        val = params[key]
        # Format report_params (startDate/endDate) as a readable range
        if key == "report_params" and isinstance(val, dict):
            start = val.get("startDate", "")
            end = val.get("endDate", "")
            if start and end:
                val = f"{start} → {end}"
            elif start:
                val = f"From {start}"
            elif end:
                val = f"Until {end}"
            else:
                continue
        # Format lists
        if isinstance(val, list):
            val = ", ".join(str(x) for x in val)
        result[_PARAM_LABELS.get(key, key)] = val
    return result


def _build_confirmation_question(
    tool_name: str, params: dict, summary: str, is_update: bool = False
) -> str:
    """Build a plain-English confirmation message as an assistant chat bubble."""
    label = TOOL_LABELS.get(tool_name, f"**{tool_name}**")
    prefix = "🔄 **Updated action —**" if is_update else "👋 **I'm about to perform:**"
    lines = [f"{prefix} {label}\n"]
    if summary:
        lines.append(f"_{summary}_\n")
    facing = _user_facing_params(tool_name, params)
    if facing:
        lines.append("**Parameters:**")
        for k, v in facing.items():
            lines.append(f"- **{k}:** {v}")
    lines.append(
        "\n> Use the buttons below to confirm or cancel, "
        "or **type a correction** to adjust the parameters (e.g. *'make it 100 orders'*, *'add Flipkart'*)."
    )
    return "\n".join(lines)


# ── Parameter-edit detection ───────────────────────────────────────────────────

_EDIT_KEYWORDS = re.compile(
    r"\b(make it|change|instead|use|add|remove|switch|update|set|"
    r"increase|decrease|more|less|only|also|with|without|include|exclude)\b",
    re.IGNORECASE,
)
_MARKETPLACE_NAMES = re.compile(
    r"\b(amazon|flipkart|meesho|myntra|jiomart|snapdeal|ajio|nykaa)\b",
    re.IGNORECASE,
)
_DATE_WORDS = re.compile(
    r"\b(today|yesterday|last|this|week|month|year|quarter|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|q1|q2|q3|q4|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)


def _is_parameter_edit(text: str) -> bool:
    t = text.strip()
    if re.search(r"\b\d+\b", t):
        return True
    if _EDIT_KEYWORDS.search(t):
        return True
    if _MARKETPLACE_NAMES.search(t):
        return True
    if _DATE_WORDS.search(t):
        return True
    return False


# ── Rendering helpers ──────────────────────────────────────────────────────────

def render_assistant_message(message: dict):
    """Render an assistant message with only user-facing parameters in the expander."""
    rich = message.get("rich_content", [])
    text = message.get("content", "")

    if rich:
        final_label = "Response Generated" if text else "Completed"
        with st.status(final_label, expanded=False, state="complete"):
            for item in rich:
                kind = item.get("kind")
                if kind == "tool_call":
                    item_tool = item.get("name", "")
                    raw_input = item.get("input", {})
                    st.write(f"🛠️ **Tool Called:** `{TOOL_LABELS.get(item_tool, item_tool)}`")
                    facing = _user_facing_params(item_tool, raw_input)
                    for k, v in facing.items():
                        st.write(f"- **{k}:** {v}")
                elif kind == "tool_response":
                    st.write("📊 **Tool Response:**")
                    st.code(item["text"])
                elif kind == "reasoning":
                    st.write("💭 **Reasoning:**")
                    st.caption(item["text"])

    if text:
        st.markdown(text)


def _run_stream_execution(original_message: str) -> tuple[str, list]:
    """Stream-execute a message, render it live, and return (full_response, rich_content)."""
    full_response = ""
    rich_content = []
    seen_tools: set = set()

    with st.chat_message("assistant"):
        with st.status("Initializing...", expanded=True) as status:
            try:
                status.update(label="Reasoning...", state="running")

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
                        if tool_name and tool_name not in seen_tools:
                            seen_tools.add(tool_name)
                            status.update(
                                label=f"Calling {TOOL_LABELS.get(tool_name, tool_name)}...",
                                state="running",
                            )
                            st.write(f"🛠️ **Tool Called:** `{TOOL_LABELS.get(tool_name, tool_name)}`")
                            facing = _user_facing_params(tool_name, tool_input)
                            for k, v in facing.items():
                                st.write(f"- **{k}:** {v}")
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

    return full_response, rich_content


# Required parameters per tool — if ALL are present the plan is complete
_REQUIRED_PARAMS: dict[str, list[str]] = {
    "order_confirmation": ["count", "marketplace_name"],
    "report_generation": ["report_type"],
    "batch_creation": ["count", "batch_size", "marketplaces"],
}


def _is_plan_complete(tool_name: str, params: dict) -> bool:
    """
    Return True when every required parameter for the tool is present
    and non-empty.  When True, we skip the confirmation card and execute
    the query directly.
    """
    if not isinstance(params, dict):
        return False
    required = _REQUIRED_PARAMS.get(tool_name, [])
    for key in required:
        val = params.get(key)
        if val is None:
            return False
        # Empty list or zero count → incomplete
        if isinstance(val, list) and len(val) == 0:
            return False
        if isinstance(val, int) and val <= 0:
            return False
        if isinstance(val, str) and not val.strip():
            return False
    return True


def _build_missing_params_question(tool_name: str, params: dict) -> str:
    """Build a plain-English question for missing required parameters."""
    required = _REQUIRED_PARAMS.get(tool_name, [])
    missing = []
    for key in required:
        val = params.get(key)
        if val is None or (isinstance(val, list) and not val) or (isinstance(val, int) and val <= 0):
            missing.append(_PARAM_LABELS.get(key, key))

    label = TOOL_LABELS.get(tool_name, tool_name)
    lines = [f"I'd like to help with **{label}**, but I need a bit more information:"]
    for m in missing:
        lines.append(f"- **{m}** is required but wasn't specified.")
    lines.append("\nCould you provide the missing details?")
    return "\n".join(lines)


def _handle_fresh_plan(prompt: str, tool_plan: dict | None):
    """
    Route the plan result:
    - status='ready'              → show Yes/No confirmation card
    - status='needs_clarification' → show AI-generated question, keep pending so the
                                     next message is treated as a correction
    - status='conversational'/None → stream response immediately
    """
    if tool_plan and tool_plan.get("tool"):
        tool_name = tool_plan["tool"]
        params = tool_plan.get("params", {})
        summary = tool_plan.get("summary", "")
        status = tool_plan.get("status", "ready")
        question = tool_plan.get("question") or ""

        if status == "needs_clarification" and question:
            # AI identified missing info — show its natural question, keep pending
            # so the user's next message is treated as a correction
            pending = {
                "tool_name": tool_name,
                "params": params,
                "original_message": prompt,
                "summary": summary,
                "is_update": False,
            }
            st.session_state.pending_confirmation = pending
            st.session_state.awaiting_correction = True   # route next message as correction
            st.session_state.messages.append({
                "role": "assistant",
                "content": question,
                "rich_content": [],
            })
        else:
            # status='ready' (or unknown) — always confirm before executing
            pending = {
                "tool_name": tool_name,
                "params": params,
                "original_message": prompt,
                "summary": summary,
                "is_update": False,
            }
            st.session_state.pending_confirmation = pending
            st.session_state.awaiting_correction = False
            confirmation_text = _build_confirmation_question(tool_name, params, summary)
            st.session_state.messages.append({
                "role": "assistant",
                "content": confirmation_text,
                "rich_content": [],
            })
    else:
        # Conversational — stream response immediately
        st.session_state.pending_confirmation = None
        st.session_state.awaiting_correction = False
        st.session_state.execute_message = prompt



# ── Replay chat history ────────────────────────────────────────────────────────
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_assistant_message(message)
        else:
            st.markdown(message["content"])


# ── Execute pending (triggered by Yes button) ──────────────────────────────────
if st.session_state.execute_message:
    original_message = st.session_state.execute_message
    st.session_state.execute_message = None
    st.session_state.pending_confirmation = None

    full_response, rich_content = _run_stream_execution(original_message)
    st.session_state.messages.append(
        {"role": "assistant", "content": full_response, "rich_content": rich_content}
    )
    st.rerun()


# ── Confirmation buttons ───────────────────────────────────────────────────────
if st.session_state.pending_confirmation and not st.session_state.awaiting_correction:
    st.markdown("---")
    col_yes, col_no, _ = st.columns([1, 1, 4])
    with col_yes:
        if st.button("Yes, proceed", key="yes_btn", type="primary", use_container_width=True):
            st.session_state.execute_message = st.session_state.pending_confirmation["original_message"]
            st.rerun()
    with col_no:
        if st.button("No", key="no_btn", use_container_width=True):
            # Ask the AI to generate a natural re-affirmation question
            pending_now = st.session_state.pending_confirmation
            ai_question = None
            if pending_now:
                rephrase_prompt = (
                    f"The user wanted to: {pending_now.get('summary', 'perform an action')}. "
                    "They said something is wrong. "
                    "Ask a single, friendly question to find out what they'd like to change. "
                    "Respond with ONLY the question, no JSON, no preamble."
                )
                try:
                    ai_question = backend_chat(rephrase_prompt, session_id="__planner__").strip()
                except Exception:
                    ai_question = None
            st.session_state.awaiting_correction = True
            st.session_state.messages.append({
                "role": "assistant",
                "content": ai_question or "No problem! What would you like to change?",
                "rich_content": [],
            })
            st.rerun()


# ── Chat input ─────────────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask me to confirm orders, generate reports, or create batches..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    pending = st.session_state.pending_confirmation

    if st.session_state.awaiting_correction and pending:
        # User clicked "No, something's wrong" and is now describing the correction
        st.session_state.awaiting_correction = False
        merged = f"{pending['original_message']}. Correction: {prompt}"
        with st.spinner("Updating parameters..."):
            try:
                plan_text = plan_tool_call_via_api(merged)
                tool_plan = extract_tool_plan(plan_text)
            except Exception:
                tool_plan = None

        if tool_plan and tool_plan.get("tool"):
            updated_pending = {
                "tool_name": tool_plan["tool"],
                "params": tool_plan.get("params", {}),
                "original_message": merged,
                "summary": tool_plan.get("summary", ""),
                "is_update": True,
            }
            st.session_state.pending_confirmation = updated_pending
            confirmation_text = _build_confirmation_question(
                tool_plan["tool"],
                tool_plan.get("params", {}),
                tool_plan.get("summary", ""),
                is_update=True,
            )
            st.session_state.messages.append({
                "role": "assistant",
                "content": confirmation_text,
                "rich_content": [],
            })
        else:
            st.session_state.pending_confirmation = None
            with st.spinner("Analyzing your request..."):
                try:
                    plan_text = plan_tool_call_via_api(prompt)
                    tool_plan = extract_tool_plan(plan_text)
                except Exception:
                    tool_plan = None
            _handle_fresh_plan(prompt, tool_plan)

    elif pending:
        # Pending confirmation exists (not in correction mode) — check message intent
        if _is_parameter_edit(prompt):
            # Inline correction while reviewing — re-plan with merged intent
            merged = f"{pending['original_message']}. Correction: {prompt}"
            with st.spinner("Updating parameters..."):
                try:
                    plan_text = plan_tool_call_via_api(merged)
                    tool_plan = extract_tool_plan(plan_text)
                except Exception:
                    tool_plan = None

            if tool_plan and tool_plan.get("tool"):
                updated_pending = {
                    "tool_name": tool_plan["tool"],
                    "params": tool_plan.get("params", {}),
                    "original_message": merged,
                    "summary": tool_plan.get("summary", ""),
                    "is_update": True,
                }
                st.session_state.pending_confirmation = updated_pending
                confirmation_text = _build_confirmation_question(
                    tool_plan["tool"],
                    tool_plan.get("params", {}),
                    tool_plan.get("summary", ""),
                    is_update=True,
                )
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": confirmation_text,
                    "rich_content": [],
                })
            else:
                st.session_state.pending_confirmation = None
                with st.spinner("Analyzing your request..."):
                    try:
                        plan_text = plan_tool_call_via_api(prompt)
                        tool_plan = extract_tool_plan(plan_text)
                    except Exception:
                        tool_plan = None
                _handle_fresh_plan(prompt, tool_plan)

        else:
            # Unrelated message — clear pending and treat as brand-new query
            st.session_state.pending_confirmation = None
            st.session_state.awaiting_correction = False
            with st.spinner("Analyzing your request..."):
                try:
                    plan_text = plan_tool_call_via_api(prompt)
                    tool_plan = extract_tool_plan(plan_text)
                except Exception:
                    tool_plan = None
            _handle_fresh_plan(prompt, tool_plan)

    else:
        # No pending — fresh planning pass
        with st.spinner("Analyzing your request..."):
            try:
                plan_text = plan_tool_call_via_api(prompt)
                tool_plan = extract_tool_plan(plan_text)
            except Exception:
                tool_plan = None
        _handle_fresh_plan(prompt, tool_plan)

    st.rerun()


# ── Sidebar ────────────────────────────────────────────────────────────────────
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
            st.markdown("**Backend:** 🟡 Unhealthy")
    except Exception:
        st.markdown("**Backend:** 🔴 Offline — start `main.py`")

    st.markdown(f"**Main API:** Port {MAIN_API_PORT}")
    st.markdown(f"**Mock API:** Port {MOCK_API_PORT}")

    if st.button("Clear Chat"):
        st.session_state.clear()
        st.rerun()