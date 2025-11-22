import asyncio
from typing import Annotated, Sequence, TypedDict, Optional
from dotenv import load_dotenv
from langchain.callbacks import AsyncIteratorCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages import ToolMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
import requests
import os
import logging
from langchain_ollama import ChatOllama
import chainlit as cl


load_dotenv()

# --- Setup logger for Markdown output ---
LOG_FILE = "agent_tools.md"

class MarkdownFileHandler(logging.FileHandler):
    def emit(self, record):
        msg = self.format(record)
        with open(self.baseFilename, "a", encoding="utf-8") as f:
            f.write(msg + "\n\n")  # extra space for readability


LOG = logging.getLogger("AgentToolsLogger")
LOG.setLevel(logging.DEBUG)

# Markdown log format
formatter = logging.Formatter(
    fmt="### %(asctime)s — **%(levelname)s**\n%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

if not LOG.handlers:
    md_handler = MarkdownFileHandler(LOG_FILE, mode="a", encoding="utf-8")
    md_handler.setFormatter(formatter)
    LOG.addHandler(md_handler)


def log_code_block(title: str, code: str):
    """Helper to log code snippets in markdown fenced blocks."""
    LOG.debug(f"#### {title}\n```python\n{code}\n```")

# --- TOOLS ---
@tool
def execute_on_server(code: str) -> dict:
    """
    Sends a Python code snippet to the FastAPI server and returns the JSON response.
    :parameters: Code (str): The Python code to execute.
    :returns: dict: The server's JSON response (contains 'output' or 'error').
    """
    server_url = os.getenv("FASTAPI_ENDPOINT")
    payload = {"code": code}
    headers = {"Content-Type": "application/json"}

    log_code_block("execute_on_server called with code", code)

    try:
        response = requests.post(server_url, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        LOG.debug(f"**Response:**\n```json\n{result}\n```")
        return result
    except requests.RequestException as e:
        LOG.error(f"**Error:** {e}")
        return {"error": f"Request failed: {e}"}

@tool
def generate_code(prompt: str) -> str:
    """
    Generates a Python code snippet that if ran on the FastAPI server answers the given question.
    The code to be generated prompt must never ask for the data to be mutated on the server,
    only query and analyze data.
    :parameters: Prompt (str): The question to answer.
    :returns: str: The generated code snippet.
    """
    LOG.debug(f"**Prompt received:** {prompt}")

    # Initialize LLM
    # code_generation_llm = ChatOpenAI(
    #     model=os.getenv("OPENAI_MODEL_FOR_CODE"),
    #     max_retries=2
    # )
    # Initialize LOCAL LLM
    code_generation_llm = ChatOllama(
        model="llama3.1:8b",
    )

    # Build absolute path to code_generation_guidelines.txt
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    guidelines_path = os.path.join(project_root, "code_generation_guidelines.txt")

    # Load guidelines file robustly
    try:
        with open(guidelines_path, "r", encoding="utf-8") as f:
            guidelines_text = f.read()
            LOG.debug(f"code_generation_guidelines.txt loaded successfully from {guidelines_path}")
    except FileNotFoundError as e:
        LOG.error(f"code_generation_guidelines.txt not found at {guidelines_path}")
        raise FileNotFoundError(f"Cannot generate code: {guidelines_path} does not exist") from e
    except Exception as e:
        LOG.error(f"Error reading code_generation_guidelines.txt: {e}")
        raise RuntimeError(f"Failed to read {guidelines_path}") from e

    # Ensure guidelines_text is a non-empty string
    if not guidelines_text.strip():
        raise ValueError(f"code_generation_guidelines.txt at {guidelines_path} is empty")

    # Build LLM messages
    messages = [
        ("system", guidelines_text),
        ("user", prompt)
    ]

    # Invoke LLM and return result
    try:
        response = code_generation_llm.invoke(messages)

        log_code_block("Generated code", response.content)


        return """git_project = graph_data["git"]
num_commits = len(git_project.git_commit_registry.all)
project_name = git_project.name

print(f"Project '{project_name}' has {num_commits} commits.")"""

    except Exception as e:
        LOG.error(f"LLM invocation failed: {e}")
        raise RuntimeError(f"Failed to generate code: {e}") from e

tools = [execute_on_server, generate_code]

# --- AGENT ---
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    stream_queue: Optional[asyncio.Queue]

async def oracle_graph_node(state: AgentState) -> AgentState:
    """Streaming Oracle node that remains LangGraph-compatible."""

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    prompt_path = os.path.join(project_root, "oracle_prompt.txt")

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            guidelines_text = f.read()
    except FileNotFoundError:
        guidelines_text = ""

    system_prompt = SystemMessage(content=guidelines_text)

    # Create streaming callback
    callback = AsyncIteratorCallbackHandler()

    # Attach callback to the model

    # oracle_with_callback = ChatOpenAI(
    #     model=os.getenv("OPENAI_MODEL_FOR_ORACLE"),
    #     streaming=True,
    #     callbacks=[callback]
    # ).bind_tools(tools)

    oracle_with_callback = ChatOllama(
        model="llama3.1:8b",
        callbacks=[callback]
    ).bind_tools(tools)

    # Run the model asynchronously
    task = asyncio.create_task(
        oracle_with_callback.ainvoke([system_prompt, *state["messages"]])
    )

    # Stream tokens outward to Chainlit via global or injected stream handler
    if hasattr(state, "stream_queue"):
        # Optionally push chunks to a queue for Chainlit
        async for token in callback.aiter():
            await state["stream_queue"].put(token)
    else:
        # If no stream consumer, just iterate to clear the stream
        async for _ in callback.aiter():
            pass

    # Wait for the model to finish
    response = await task

    # Return the updated state (required by LangGraph)
    return {"messages": [response]}

def should_continue(state :AgentState):
    messages = state["messages"]
    last_message = messages[-1]
    if not last_message.tool_calls:
        return "end"
    return "continue"

# --- GRAPH ---
graph = StateGraph(AgentState)

graph.add_node("Oracle", oracle_graph_node)

tool_node = ToolNode(tools = tools)
graph.add_node("Tools", tool_node)

graph.set_entry_point("Oracle")
graph.add_conditional_edges(
    "Oracle",
    should_continue,
    {
        "continue": "Tools",
        "end": END
    }
)
graph.add_edge("Tools", "Oracle")

app = graph.compile()

@cl.on_chat_start
async def on_start():
    await cl.Message(content="👋 Hello! I am your software analysis assistant. You can ask me any question.").send()

@cl.on_message
async def on_message(message: cl.Message):
    user_input = message.content
    msg = cl.Message(content="")
    stream_queue = asyncio.Queue()

    # Start LangGraph app in the background
    inputs = {
        "messages": [HumanMessage(content=user_input)],
        "stream_queue": stream_queue,
    }

    task = asyncio.create_task(app.ainvoke(inputs))

    # Consume stream in real time
    while True:
        try:
            token = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
            await msg.stream_token(token)
        except asyncio.TimeoutError:
            if task.done():
                break

    # Get the final result (complete message set)
    result = await task
    response_texts = [m.content for m in result["messages"] if hasattr(m, "content")]
    final_response = "\n\n".join(response_texts)
    await msg.stream_token(final_response)
    await msg.send()
