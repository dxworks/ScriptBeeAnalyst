#!/usr/bin/env python3
"""
Phase 1: CLI Testing - Direct Data Server Interaction

Tests data-server endpoints with manually crafted code to verify connectivity
and understand API response format before building agents.
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

FASTAPI_ENDPOINT = os.getenv("FASTAPI_ENDPOINT", "http://localhost:8001")


def test_health():
    """Test /health endpoint to verify server is running."""
    print("\n=== Test 1: Health Check ===")
    try:
        response = requests.get(f"{FASTAPI_ENDPOINT}/health", timeout=5)
        response.raise_for_status()
        data = response.json()
        print(f"✓ Server is healthy: {json.dumps(data, indent=2)}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"✗ Health check failed: {e}")
        return False


def test_current_project():
    """Test /projects/current to detect if project is loaded."""
    print("\n=== Test 2: Current Project Status ===")
    try:
        response = requests.get(f"{FASTAPI_ENDPOINT}/projects/current", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print(f"✓ Project loaded: {json.dumps(data, indent=2)}")
            return True
        elif response.status_code == 404:
            data = response.json()
            print(f"⚠ No project loaded: {data.get('message', 'Unknown')}")
            return False
        else:
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"✗ Current project check failed: {e}")
        return False


def test_execute_endpoint():
    """Test /execute endpoint with hardcoded query code."""
    print("\n=== Test 3: Execute Code (Count Commits) ===")

    # Hardcoded query: count total commits
    query_code = """
# Get total commit count from git project
git_project = graph_data["git"]
commit_count = len(git_project.git_commit_registry.all)
print(f"Total commits: {commit_count}")
"""

    try:
        response = requests.post(
            f"{FASTAPI_ENDPOINT}/execute",
            json={"code": query_code},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            print(f"✓ Execution successful:")
            print(f"  Output: {data.get('output', 'No output')}")
            print(f"  Logs: {data.get('logs', 'No logs')}")
            return True
        elif response.status_code == 404:
            data = response.json()
            print(f"⚠ No project loaded: {data.get('message', 'Unknown')}")
            return False
        elif response.status_code == 400:
            data = response.json()
            print(f"✗ Code execution error: {data.get('error', 'Unknown')}")
            return False
        else:
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"✗ Execute endpoint failed: {e}")
        return False


def test_plot_endpoint():
    """Test /plot endpoint with hardcoded matplotlib code."""
    print("\n=== Test 4: Generate Plot (Commits Per Month) ===")

    # Hardcoded plot: bar chart of commits per month
    plot_code = """
import matplotlib.pyplot as plt
from datetime import datetime
from collections import defaultdict

# Get git project
git_project = graph_data["git"]

# Group commits by month
commits_by_month = defaultdict(int)
for commit in git_project.git_commit_registry.all:
    month = commit.author_date.strftime('%Y-%m')
    commits_by_month[month] += 1

# Sort by month
sorted_months = sorted(commits_by_month.items())
months = [m for m, _ in sorted_months]
counts = [c for _, c in sorted_months]

# Create bar chart
plt.figure(figsize=(12, 6))
plt.bar(months, counts, color='#6366f1')
plt.xlabel('Month')
plt.ylabel('Number of Commits')
plt.title('Commits Per Month')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
"""

    try:
        response = requests.post(
            f"{FASTAPI_ENDPOINT}/plot",
            json={"code": plot_code},
            timeout=10
        )

        if response.status_code == 200:
            # Save image to file
            output_path = Path(__file__).parent / "test_plot.jpg"
            with open(output_path, "wb") as f:
                f.write(response.content)
            print(f"✓ Plot generated successfully: {output_path}")
            return True
        elif response.status_code == 404:
            data = response.json()
            print(f"⚠ No project loaded: {data.get('message', 'Unknown')}")
            return False
        elif response.status_code == 400:
            data = response.json()
            print(f"✗ Plot generation error: {data.get('error', 'Unknown')}")
            return False
        else:
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"✗ Plot endpoint failed: {e}")
        return False


def main():
    """Run all tests in sequence."""
    print(f"Testing data-server at: {FASTAPI_ENDPOINT}")
    print("=" * 60)

    # Test 1: Health check (must pass)
    if not test_health():
        print("\n❌ Server is not running. Aborting tests.")
        sys.exit(1)

    # Test 2: Current project status
    project_loaded = test_current_project()

    if not project_loaded:
        print("\n⚠ No project is loaded in memory.")
        print("To test /execute and /plot endpoints, load a project first:")
        print("  1. Go to web-ui (http://localhost:4200)")
        print("  2. Create a project and upload data files")
        print("  3. Click 'Process Data' to load into memory")
        print("\nSkipping execute and plot tests.")
        return

    # Test 3: Execute endpoint
    test_execute_endpoint()

    # Test 4: Plot endpoint
    test_plot_endpoint()

    print("\n" + "=" * 60)
    print("✓ Phase 1 tests complete!")


if __name__ == "__main__":
    main()
