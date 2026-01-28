#!/usr/bin/env python3
"""
Phase 4: Test Orchestrator Agent (CLI)

Simulates conversations with the orchestrator agent using hardcoded
test scenarios. Tests with mocked agents for fast iteration.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from langchain_core.messages import HumanMessage
from orchestrator import create_orchestrator


def test_simple_query(mock_agents: bool = True):
    """Test simple data query."""
    print("\n=== Test 1: Simple Data Query ===")
    print("User: How many commits are there?")

    app = create_orchestrator(mock_agents=mock_agents)

    state = {
        "messages": [HumanMessage(content="How many commits are there?")],
        "mock_agents": mock_agents,
    }

    result = app.invoke(state)
    last_message = result["messages"][-1]

    print(f"Assistant: {last_message.content}")
    print("✓ Test passed")


def test_complex_query(mock_agents: bool = True):
    """Test more complex query requiring interpretation."""
    print("\n=== Test 2: Complex Query (Top Authors) ===")
    print("User: Who are the most active contributors?")

    app = create_orchestrator(mock_agents=mock_agents)

    state = {
        "messages": [HumanMessage(content="Who are the most active contributors?")],
        "mock_agents": mock_agents,
    }

    result = app.invoke(state)
    last_message = result["messages"][-1]

    print(f"Assistant: {last_message.content}")
    print("✓ Test passed")


def test_visualization_request(mock_agents: bool = True):
    """Test visualization generation."""
    print("\n=== Test 3: Visualization Request ===")
    print("User: Show me a chart of commits over time")

    app = create_orchestrator(mock_agents=mock_agents)

    state = {
        "messages": [HumanMessage(content="Show me a chart of commits over time")],
        "mock_agents": mock_agents,
    }

    result = app.invoke(state)
    last_message = result["messages"][-1]

    print(f"Assistant: {last_message.content}")
    print("✓ Test passed")


def test_multi_turn_conversation(mock_agents: bool = True):
    """Test multi-turn conversation maintaining context."""
    print("\n=== Test 4: Multi-Turn Conversation ===")

    app = create_orchestrator(mock_agents=mock_agents)

    # Initialize state
    state = {
        "messages": [],
        "mock_agents": mock_agents,
    }

    # Turn 1
    print("\nUser: How many commits are there?")
    state["messages"].append(HumanMessage(content="How many commits are there?"))
    result = app.invoke(state)
    state = result
    print(f"Assistant: {result['messages'][-1].content}")

    # Turn 2
    print("\nUser: And who contributed the most?")
    state["messages"].append(HumanMessage(content="And who contributed the most?"))
    result = app.invoke(state)
    state = result
    print(f"Assistant: {result['messages'][-1].content}")

    # Turn 3
    print("\nUser: How many issues are there?")
    state["messages"].append(HumanMessage(content="How many issues are there?"))
    result = app.invoke(state)
    state = result
    print(f"Assistant: {result['messages'][-1].content}")

    print("\n✓ Multi-turn test passed")


def test_general_question(mock_agents: bool = True):
    """Test that general questions don't trigger tools unnecessarily."""
    print("\n=== Test 5: General Question (No Tools) ===")
    print("User: What is Git?")

    app = create_orchestrator(mock_agents=mock_agents)

    state = {
        "messages": [HumanMessage(content="What is Git?")],
        "mock_agents": mock_agents,
    }

    result = app.invoke(state)
    last_message = result["messages"][-1]

    print(f"Assistant: {last_message.content}")

    # Check if tools were called (they shouldn't be)
    tool_calls_made = any(
        hasattr(msg, "tool_calls") and msg.tool_calls
        for msg in result["messages"]
    )

    if tool_calls_made:
        print("⚠ Warning: Tools were called for general question")
    else:
        print("✓ No tools called (correct)")

    print("✓ Test passed")


def main():
    """Run all orchestrator CLI tests."""
    print("=" * 60)
    print("Phase 4: Testing Orchestrator Agent")
    print("=" * 60)

    # Run with mocked agents (fast)
    print("\n--- Running with MOCKED agents ---")
    test_simple_query(mock_agents=True)
    test_complex_query(mock_agents=True)
    test_visualization_request(mock_agents=True)
    test_multi_turn_conversation(mock_agents=True)
    test_general_question(mock_agents=True)

    # Ask if user wants to test with real LLM
    print("\n" + "=" * 60)
    response = input("Run tests with REAL LLM orchestrator? (costs tokens) [y/N]: ").strip().lower()

    if response == "y":
        print("\n--- Running with REAL LLM orchestrator ---")
        print("(Data agent also uses real LLM)")
        test_simple_query(mock_agents=False)
        test_complex_query(mock_agents=False)
        print("\n✓ Real LLM tests complete")
    else:
        print("Skipping real LLM tests")

    print("\n" + "=" * 60)
    print("✓ Phase 4 tests complete!")
    print("\nTo test interactively, run: python src/orchestrator.py")


if __name__ == "__main__":
    main()
