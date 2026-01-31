"""
Orchestrator Agent: Main conversational agent using LangGraph.

This agent:
- Talks to users in natural language
- Decides when to use tools (query_data, generate_plot, ask_user)
- Never shows code to users - only presents results
- Uses StateGraph for orchestration
"""
import logging
import os
from pathlib import Path
from typing import Annotated, Literal, Sequence

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from tools import ask_user, generate_plot, query_data

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

    # Tools node - uses langraph's ToolNode which handles tool execution
    # Must use the SAME function names as bind_tools
    def query_data_tool(natural_language_query: str) -> str:
        """Tool function for ToolNode."""
        return query_data(natural_language_query, mock_agent=mock_agents)

    def generate_plot_tool(plot_description: str) -> str:
        """Tool function for ToolNode."""
        return generate_plot(plot_description, mock_agent=mock_agents)

    tools = [query_data_tool, generate_plot_tool, ask_user]
    workflow.add_node("tools", ToolNode(tools))

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
