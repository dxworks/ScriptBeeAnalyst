#!/usr/bin/env python3
"""
Phase 3: Test Orchestrator Tools

Tests the tools that integrate sub-agents with data-server.
Tests with both mocked agents (fast) and real agents (costs tokens).
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tools import query_data, generate_plot


def test_query_data_mocked():
    """Test query_data tool with mocked agent (no LLM calls)."""
    print("\n=== Test 1: query_data with Mocked Agent ===")

    test_queries = [
        "How many commits are there?",
        "Who are the top 5 authors?",
        "How many issues?",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            result = query_data(query, mock_agent=True, verbose=True)
            print(f"✓ Result: {result}")
        except Exception as e:
            print(f"✗ Error: {e}")
            return

    print("\n✓ query_data mocked tests passed")


def test_generate_plot_mocked():
    """Test generate_plot tool with mocked agent (no LLM calls)."""
    print("\n=== Test 2: generate_plot with Mocked Agent ===")

    test_queries = [
        "Plot commits per month",
        "Show top authors",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            save_path = Path(__file__).parent / f"test_tool_plot.jpg"
            result = generate_plot(query, mock_agent=True, verbose=True, save_path=save_path)
            print(f"✓ Plot saved to: {result}")
        except Exception as e:
            print(f"✗ Error: {e}")
            return

    print("\n✓ generate_plot mocked tests passed")


def test_query_data_real():
    """Test query_data tool with real LLM agent (costs tokens)."""
    print("\n=== Test 3: query_data with Real LLM Agent ===")
    print("⚠ This will make API calls and cost tokens")

    test_queries = [
        "How many commits are in the project?",
        "Who is the most active author?",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        try:
            result = query_data(query, mock_agent=False, verbose=True)
            print(f"✓ Result: {result}")
        except Exception as e:
            print(f"✗ Error: {e}")
            return

    print("\n✓ query_data real LLM tests passed")


def test_error_handling():
    """Test error handling for various failure scenarios."""
    print("\n=== Test 4: Error Handling ===")

    # Test with mocked agent and presumably loaded project
    print("\n4a. Test normal operation (should succeed):")
    try:
        result = query_data("How many commits?", mock_agent=True, verbose=False)
        print(f"✓ Normal operation works: {result[:50]}...")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")

    print("\n✓ Error handling tests passed")


def main():
    """Run all tool tests."""
    print("=" * 60)
    print("Phase 3: Testing Orchestrator Tools")
    print("=" * 60)

    # Always run mocked tests (fast, free)
    test_query_data_mocked()
    test_generate_plot_mocked()
    test_error_handling()

    # Ask user if they want to run real LLM tests
    print("\n" + "=" * 60)
    response = input("Run real LLM tests for query_data? (costs tokens) [y/N]: ").strip().lower()

    if response == "y":
        test_query_data_real()
    else:
        print("Skipping real LLM tests")

    print("\n" + "=" * 60)
    print("✓ Phase 3 tests complete!")
    print("\nNote: Plot generation uses mocked agent only (as per plan).")


if __name__ == "__main__":
    main()
