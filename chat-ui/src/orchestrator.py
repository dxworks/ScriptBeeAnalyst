"""
Orchestrator Agent: Main conversational agent using LangGraph.

This agent:
- Talks to users in natural language
- Decides when to use tools (query_data, generate_plot, ask_user)
- Never shows code to users - only presents results
- Uses StateGraph for orchestration
"""
import logging
import operator
import os
from pathlib import Path
from typing import Annotated, Literal, Sequence

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from tools import (
    ToolTrace, ask_user, generate_plot, query_data,
    query_data_traced, generate_plot_traced,
)

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# State definition
class OrchestratorState(TypedDict):
    """State for the orchestrator agent."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    mock_agents: bool  # Whether to use mocked sub-agents
    tool_traces: Annotated[list, operator.add]  # Accumulated ToolTrace objects across loops


def load_system_prompt() -> str:
    """Load orchestrator system prompt from file."""
    prompt_path = Path(__file__).parent.parent / "orchestrator_prompt.txt"
    return prompt_path.read_text()


def create_oracle_node(mock_agents: bool = False):
    """
    Create the oracle node function.

    The oracle is the LLM with tools bound that decides what to do.
    """
    # Load system prompt
    system_prompt = load_system_prompt()

    # Create LLM
    model_name = os.getenv("OPENAI_MODEL_FOR_ORACLE", "gpt-4o")
    llm = ChatOpenAI(model=model_name, temperature=0)

    # Define tool wrappers that respect mock_agents setting
    # These are used for bind_tools (LLM knows about them)
    def query_data_tool(natural_language_query: str) -> str:
        """Query project data using natural language."""
        return query_data(natural_language_query, mock_agent=mock_agents)

    def generate_plot_tool(plot_description: str) -> str:
        """Generate a plot/visualization using natural language."""
        return generate_plot(plot_description, mock_agent=mock_agents)

    # Bind tools to LLM
    tools = [query_data_tool, generate_plot_tool, ask_user]
    llm_with_tools = llm.bind_tools(tools)

    def oracle_node(state: OrchestratorState) -> dict:
        """
        Oracle node: LLM decides what to do next.

        Returns updated messages with LLM's response (possibly with tool calls).
        """
        messages = state["messages"]

        # Prepend system prompt if not already there
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)

        # Call LLM
        logger.info("[ORCHESTRATOR] Calling LLM to decide next action...")
        response = llm_with_tools.invoke(messages)

        # Log if tools were called
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_names = [tc.get("name") for tc in response.tool_calls]
            logger.info(f"[ORCHESTRATOR] LLM decided to call tools: {tool_names}")
        else:
            logger.info("[ORCHESTRATOR] LLM responded directly (no tool calls)")

        return {"messages": [response]}

    return oracle_node


def should_continue(state: OrchestratorState) -> Literal["tools", "end"]:
    """
    Conditional edge: Should we continue to tools or end?

    If the last message has tool_calls, route to tools node.
    Otherwise, we're done.
    """
    messages = state["messages"]
    last_message = messages[-1]

    # If LLM called tools, route to tools node
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    # Otherwise, end
    return "end"


def create_traced_tools_node(mock_agents: bool = False):
    """
    Create a custom tools node that captures ToolTrace for each tool execution.

    Replaces LangGraph's prebuilt ToolNode to intercept generated code and server responses.
    """
    def traced_tools_node(state: OrchestratorState) -> dict:
        last_message = state["messages"][-1]
        tool_calls = last_message.tool_calls

        tool_messages = []
        traces = []

        for tc in tool_calls:
            name = tc["name"]
            args = tc["args"]
            call_id = tc["id"]

            logger.info(f"[TOOLS_NODE] Executing tool: {name} with args: {args}")

            if name == "query_data_tool":
                result, trace = query_data_traced(
                    args["natural_language_query"],
                    mock_agent=mock_agents,
                )
                traces.append(trace)
                if trace.is_error:
                    logger.error(f"[TOOLS_NODE] query_data failed: {result}")
                    tool_messages.append(
                        ToolMessage(content=f"Error: {result}", tool_call_id=call_id)
                    )
                else:
                    tool_messages.append(
                        ToolMessage(content=result, tool_call_id=call_id)
                    )

            elif name == "generate_plot_tool":
                result, trace = generate_plot_traced(
                    args["plot_description"],
                    mock_agent=mock_agents,
                )
                traces.append(trace)
                if trace.is_error:
                    logger.error(f"[TOOLS_NODE] generate_plot failed: {result}")
                    tool_messages.append(
                        ToolMessage(content=f"Error: {result}", tool_call_id=call_id)
                    )
                else:
                    tool_messages.append(
                        ToolMessage(content=result, tool_call_id=call_id)
                    )

            elif name == "ask_user":
                result = ask_user(args["question"])
                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=call_id)
                )

            else:
                tool_messages.append(
                    ToolMessage(content=f"Unknown tool: {name}", tool_call_id=call_id)
                )

        return {"messages": tool_messages, "tool_traces": traces}

    return traced_tools_node


def create_orchestrator_graph(mock_agents: bool = False) -> StateGraph:
    """
    Create the orchestrator StateGraph.

    Args:
        mock_agents: If True, use mocked sub-agents (fast, no LLM calls)

    Returns:
        Compiled StateGraph ready to invoke
    """
    # Create graph
    workflow = StateGraph(OrchestratorState)

    # Create oracle node with mock setting
    oracle_node = create_oracle_node(mock_agents=mock_agents)

    # Add nodes
    workflow.add_node("oracle", oracle_node)

    # Custom tools node with tracing (replaces ToolNode)
    traced_tools_node = create_traced_tools_node(mock_agents=mock_agents)
    workflow.add_node("tools", traced_tools_node)

    # Add edges
    workflow.set_entry_point("oracle")
    workflow.add_conditional_edges(
        "oracle",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )
    workflow.add_edge("tools", "oracle")  # After tools, go back to oracle

    # Compile
    return workflow.compile()


def chat_cli(mock_agents: bool = False, verbose: bool = True):
    """
    Simple CLI chat interface for testing the orchestrator.

    Args:
        mock_agents: If True, use mocked sub-agents
        verbose: If True, print intermediate steps
    """
    print("=" * 60)
    print("Orchestrator Agent CLI")
    print("=" * 60)
    print(f"Mock agents: {mock_agents}")
    print("Type 'quit' to exit\n")

    # Create orchestrator
    app = create_orchestrator_graph(mock_agents=mock_agents)

    # Initialize state
    state = {
        "messages": [],
        "mock_agents": mock_agents,
        "tool_traces": [],
    }

    while True:
        # Get user input
        user_input = input("\nYou: ").strip()

        if user_input.lower() in ["quit", "exit", "q"]:
            print("Goodbye!")
            break

        if not user_input:
            continue

        # Add user message to state
        state["messages"].append(HumanMessage(content=user_input))

        # Invoke orchestrator
        try:
            if verbose:
                print("\n[Processing...]")

            result = app.invoke(state)

            # Update state with result
            state = result

            # Show tool traces if any
            traces = state.get("tool_traces", [])
            if traces and verbose:
                print(f"\n--- Tool Traces ({len(traces)}) ---")
                for i, trace in enumerate(traces, 1):
                    status = "ERROR" if trace.is_error else "OK"
                    print(f"\n[{i}] {trace.tool_name} [{status}]")
                    print(f"    Query: {trace.input_query}")
                    print(f"    Code:\n{trace.generated_code}")
                    print(f"    Response: {trace.server_response}")
                print("--- End Traces ---")

            # Reset traces for next turn
            state["tool_traces"] = []

            # Get last message (should be AI response)
            last_message = state["messages"][-1]

            if isinstance(last_message, AIMessage):
                print(f"\nAssistant: {last_message.content}")
            else:
                print(f"\n[Unexpected message type: {type(last_message)}]")

        except Exception as e:
            print(f"\n[Error: {e}]")


# For importing
def create_orchestrator(mock_agents: bool = False):
    """
    Create and return compiled orchestrator graph.

    This is the main entry point for using the orchestrator.
    """
    return create_orchestrator_graph(mock_agents=mock_agents)


if __name__ == "__main__":
    # Run CLI chat with mocked agents for fast testing
    chat_cli(mock_agents=True, verbose=True)
