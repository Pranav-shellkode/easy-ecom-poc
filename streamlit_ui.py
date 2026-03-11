import streamlit as st
import asyncio
import json
import uuid

from agents.easyecom_agent import EasyEcomAgent
from config import MAIN_API_PORT, MOCK_API_PORT

# Configure page
st.set_page_config(
    page_title="EasyEcom AI Assistant",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 EasyEcom AI Assistant")
st.markdown("Your intelligent assistant with real-time execution visibility")

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# Initialize agent
@st.cache_resource
def get_agent():
    return EasyEcomAgent()

agent = get_agent()


def render_assistant_message(message: dict):
    """
    Render an assistant message including its rich content (tool calls,
    tool responses, reasoning) so it looks the same whether it's being
    rendered for the first time or re-rendered from history.
    """
    rich = message.get("rich_content", [])
    text = message.get("content", "")

    if rich:
        # Determine the final status label to show on the closed expander
        final_label = "Response Generated" if text else "Completed"
        with st.status(final_label, expanded=False, state="complete"):
            for item in rich:
                kind = item.get("kind")
                if kind == "tool_call":
                    st.write(f"🛠️ **Tool Call:** `{item['name']}`")
                    st.code(json.dumps(item["input"], indent=2), language="json")
                elif kind == "tool_response":
                    st.write("📊 **Tool Response:**")
                    st.code(item["text"])
                elif kind == "reasoning":
                    st.write("💭 **Reasoning:**")
                    st.caption(item["text"])

    if text:
        st.markdown(text)


# ── Replay history ──────────────────────────────────────────────────────────
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_assistant_message(message)
        else:
            st.markdown(message["content"])

# ── New user input ───────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask me to confirm orders, generate reports, or create batches..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.status("Initializing...", expanded=True) as status:
            try:
                strands_agent = agent.get_strands_agent(st.session_state.session_id)
                if not strands_agent:
                    st.error("AI assistant is currently unavailable. Please try again later.")
                    status.update(label="Unavailable", state="error")
                else:
                    invocation_state = {
                        "session_id": st.session_state.session_id,
                        "user_id": "default_user"
                    }

                    status.update(label="Reasoning...", state="running")

                    async def collect_events():
                        events = []
                        async for event in strands_agent.stream_async(prompt, **invocation_state):
                            events.append(event)
                        return events

                    events = asyncio.run(collect_events())

                    full_response = ""
                    seen_tools = set()
                    reasoning_text = ""
                    # Ordered list of rich items to persist
                    rich_content = []

                    for event in events:
                        # Capture reasoning from raw event stream
                        if "event" in event:
                            raw = event["event"]
                            if "contentBlockDelta" in raw:
                                delta = raw["contentBlockDelta"].get("delta", {})
                                if "reasoningContent" in delta:
                                    reasoning_text += delta["reasoningContent"].get("text", "")

                        # Tool call events
                        elif "current_tool_use" in event:
                            tool_data = event["current_tool_use"]
                            tool_name = tool_data.get("name", "")
                            tool_input = tool_data.get("input", {})
                            if tool_name and tool_name not in seen_tools and tool_input:
                                seen_tools.add(tool_name)
                                status.update(label=f"Calling {tool_name}...", state="running")
                                st.write(f"🛠️ **Tool Call:** `{tool_name}`")
                                st.code(json.dumps(tool_input, indent=2), language="json")
                                rich_content.append({
                                    "kind": "tool_call",
                                    "name": tool_name,
                                    "input": tool_input,
                                })

                        # Tool result in message events
                        elif "message" in event:
                            msg = event["message"]
                            if isinstance(msg, dict):
                                for block in msg.get("content", []):
                                    if isinstance(block, dict) and "toolResult" in block:
                                        result_content = block["toolResult"].get("content", [])
                                        for rc in result_content:
                                            if isinstance(rc, dict) and "text" in rc:
                                                status.update(label="Analyzing tool response...", state="running")
                                                st.write("📊 **Tool Response:**")
                                                st.code(rc["text"])
                                                rich_content.append({
                                                    "kind": "tool_response",
                                                    "text": rc["text"],
                                                })

                        # Text response tokens
                        elif "data" in event:
                            if not full_response:
                                status.update(label="Generating response...", state="running")
                            full_response += event["data"]

                    # Attach reasoning to rich content (prepend so it shows first)
                    if reasoning_text:
                        rich_content.insert(0, {"kind": "reasoning", "text": reasoning_text})
                        st.write("💭 **Reasoning:**")
                        st.caption(reasoning_text)

                    if full_response:
                        status.update(label="Response Generated", state="complete")

            except Exception as e:
                status.update(label="Error occurred", state="error")
                st.error(f"Error: {str(e)}")
                full_response = f"I encountered an error: {str(e)}"
                rich_content = []

        # Display final response outside the status block
        if full_response:
            st.markdown(full_response)

        # ── Persist the full rich message to session state ──────────────────
        assistant_message = {
            "role": "assistant",
            "content": full_response,
            "rich_content": rich_content,   # <-- this is what was missing
        }
        st.session_state.messages.append(assistant_message)


# ── Sidebar ──────────────────────────────────────────────────────────────────
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
    st.markdown("**Status:** 🟢 Real-time Event Logging")
    st.markdown(f"**Main API:** Port {MAIN_API_PORT}")
    st.markdown(f"**Mock API:** Port {MOCK_API_PORT}")

    if st.button("🗑️ Clear Chat"):
        get_agent.clear()
        st.session_state.clear()
        st.rerun()