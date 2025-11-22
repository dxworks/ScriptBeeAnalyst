import asyncio
from typing import Annotated, Sequence, TypedDict, Optional
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
import os
import logging
import chainlit as cl

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
LOG = logging.getLogger(__name__)

# --- TOOLS ---
# @tool
# def query_server(query: str) -> dict:
#     """
#         Query the read-only project data server by:
#          1) generating code from the natural-language `query`
#          2) executing the generated code against the server
#          3) returning a structured JSON result with keys like 'output', 'meta', and 'error'
#
#         :param query: Natural language query describing the information you want.
#         :return: dict with the server response; see Response Schema below.
#     """
#     return {"output": "project name: ZEPPELIN number of commits: 5634"}

@tool
async def query_server(query: str) -> dict:
    """
        Query the read-only project data server by:
         1) generating code from the natural-language `query`
         2) executing the generated code against the server
         3) returning a structured JSON result with keys like 'output', 'meta', and 'error'

        :param query: Natural language query describing the information you want.
        :return: dict with the server response; see Response Schema below.
    """
    await asyncio.sleep(5)  # async sleep instead of cl.sleep
    return {"output": "project name: ZEPPELIN number of commits: 5634"}

tools = [query_server]

# --- AGENT STATE ---
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    last_answer: Optional[cl.Message]

# --- ORACLE GRAPH NODE ---
async def oracle_graph_node(state: AgentState) -> AgentState:
    oracle = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL_FOR_ORACLE", "gpt-4"),
        api_key=os.getenv("OPENAI_API_KEY"),
        streaming=False,
    ).bind_tools(tools)

    messages = list(state["messages"])

    # --- STATUS: Thinking ---
    LOG.info("🧠 current_action -> %s", "Thinking")
    state["last_answer"].content = "Thinking ..."
    await state["last_answer"].update()

    try:
        response = await oracle.ainvoke(messages)
    except Exception as e:
        LOG.exception("❌ Model invocation failed: %s", e)
        return {"messages": messages}

    if hasattr(response, "tool_calls") and response.tool_calls:
        tool_call = response.tool_calls[0]
        tool_name = tool_call.get("name", "unknown_tool")
        tool_args = tool_call.get("args", {})

        # Convert args to a nice readable string
        arg_str = ", ".join(f"{k}={v!r}" for k, v in tool_args.items())

        # Update Chainlit status message
        state["last_answer"].content = f"Using {tool_name}({arg_str})"
        await state["last_answer"].update()

        LOG.info("🔧 current_action -> %s(%s)", tool_name, arg_str)

    return {"messages": [response]}

# --- CONDITIONAL EDGE LOGIC ---
def should_continue(state: AgentState) -> str:
    """
    Decide whether to continue to the ToolNode or end.
    """
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "continue"
    return "end"

# --- GRAPH ---
graph = StateGraph(AgentState)
graph.add_node("Oracle", oracle_graph_node)
tool_node = ToolNode(tools=tools)
graph.add_node("Tools", tool_node)

graph.set_entry_point("Oracle")
graph.add_conditional_edges("Oracle", should_continue, {"continue": "Tools", "end": END})
graph.add_edge("Tools", "Oracle")

app = graph.compile()
