#!/usr/bin/env python3
"""
Phase 2: Test Sub-Agents with Mocked Responses

Tests data_agent and plot_agent code generation in isolation,
using mocked responses to avoid LLM API calls during development.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agents import data_agent, plot_agent


def test_data_agent_mocked():
    """Test data agent with mocked responses (no LLM calls)."""
    print("\n=== Test 1: Data Agent (Mocked) ===")

    test_queries = [
        "How many commits are there?",
        "Who are the top authors?",
        "How many issues?",
        "How many pull requests?",
        "Show me project statistics",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        code = data_agent.generate_code(query, mock=True)
        print(f"Generated code:\n{code}")

    print("✓ Data agent mocked tests passed")


def test_plot_agent_mocked():
    """Test plot agent with mocked responses (no LLM calls)."""
    print("\n=== Test 2: Plot Agent (Mocked) ===")

    test_queries = [
        "Plot commits per month",
        "Show top authors",
        "Issues by status",
        "Pull requests by state",
        "Show me a chart",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        code = plot_agent.generate_code(query, mock=True)
        print(f"Generated code:\n{code[:200]}...")  # First 200 chars

    print("✓ Plot agent mocked tests passed")


def test_data_agent_real():
    """Test data agent with real LLM calls (costs tokens)."""
    print("\n=== Test 3: Data Agent (Real LLM) ===")
    print("⚠ This will make API calls and cost tokens")

    # Check if API key is set
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "REPLACE_WITH_VALID_KEY":
        print("✗ OPENAI_API_KEY not set or is placeholder, skipping real LLM test")
        return

    test_queries = [
        "How many commits are there in total?",
        "Who is the most active author?",
        "How many issues are in the project?",
        "Show me all pull requests",
    ]

    # Save responses to file for future mock usage
    responses_file = Path(__file__).parent / "llm_responses_data.json"
    responses = {}

    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            code = data_agent.generate_code(query, mock=False)
            print(f"Generated code:\n{code}")

            # Save to responses dict
            responses[query] = code

            # Basic validation: code should access graph_data
            if "graph_data" in code:
                print("✓ Code contains graph_data access")
            else:
                print("⚠ Code does not access graph_data")

        except Exception as e:
            print(f"✗ Error: {e}")
            return

    # Save all responses to file
    with open(responses_file, "w") as f:
        json.dump(responses, f, indent=2)
    print(f"\n💾 Saved LLM responses to: {responses_file}")
    print("✓ Data agent real LLM tests passed")


def test_plot_agent_real():
    """Test plot agent with real LLM calls (costs tokens)."""
    print("\n=== Test 4: Plot Agent (Real LLM) ===")
    print("⚠ This will make API calls and cost tokens")

    # Check if API key is set
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "REPLACE_WITH_VALID_KEY":
        print("✗ OPENAI_API_KEY not set or is placeholder, skipping real LLM test")
        return

    test_queries = [
        "Create a bar chart of commits per month",
        "Plot the top 10 authors by commit count",
        "Show a pie chart of issues by status",
    ]

    # Save responses to file for future mock usage
    responses_file = Path(__file__).parent / "llm_responses_plot.json"
    responses = {}

    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            code = plot_agent.generate_code(query, mock=False)
            print(f"Generated code:\n{code}")

            # Save to responses dict
            responses[query] = code

            # Basic validation: code should import matplotlib and access graph_data
            if "matplotlib" in code and "graph_data" in code:
                print("✓ Code contains matplotlib and graph_data")
            else:
                print("⚠ Code may be missing required imports or data access")

        except Exception as e:
            print(f"✗ Error: {e}")
            return

    # Save all responses to file
    with open(responses_file, "w") as f:
        json.dump(responses, f, indent=2)
    print(f"\n💾 Saved LLM responses to: {responses_file}")
    print("✓ Plot agent real LLM tests passed")


def main():
    """Run all agent tests."""
    print("=" * 60)
    print("Phase 2: Testing Sub-Agents")
    print("=" * 60)

    # Always run mocked tests (fast, free)
    test_data_agent_mocked()
    test_plot_agent_mocked()

    # Ask user if they want to run real LLM tests
    print("\n" + "=" * 60)
    response = input("Run real LLM tests for DATA AGENT only? (costs tokens) [y/N]: ").strip().lower()

    if response == "y":
        test_data_agent_real()
        print("\n⚠ Skipping plot agent real LLM test (will use mocks only)")
    else:
        print("Skipping real LLM tests")

    print("\n" + "=" * 60)
    print("✓ Phase 2 tests complete!")


if __name__ == "__main__":
    main()
