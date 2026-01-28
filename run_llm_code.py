#!/usr/bin/env python3
"""
Script to execute LLM-generated code snippets against the data-server.

Usage:
    python run_llm_code.py <number>

Where <number> is 1-4 to select which code snippet to run.

Environment variables:
    DATA_SERVER_URL: Base URL of data server (default: http://localhost:8001)
"""

import json
import sys
import os
import re
import requests
from pathlib import Path


def load_code_snippets():
    """Load code snippets from llm_responses_data.json."""
    json_path = Path(__file__).parent / "chat-ui" / "llm_responses_data.json"

    with open(json_path, 'r') as f:
        data = json.load(f)

    # Extract code from markdown code blocks
    snippets = []
    for question, code_block in data.items():
        # Remove ```python and ``` markers
        code = re.sub(r'^```python\n', '', code_block)
        code = re.sub(r'\n```$', '', code)
        snippets.append({
            'question': question,
            'code': code
        })

    return snippets


def execute_code(code, data_server_url):
    """Execute code against the data-server."""
    url = f"{data_server_url}/execute"

    headers = {
        'Content-Type': 'application/json'
    }

    payload = {
        'code': code
    }

    print(f"\n🔄 Sending request to: {url}")
    print(f"📝 Code length: {len(code)} characters\n")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        return result.get('output', 'No output returned')

    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        print(f"Response: {e.response.text}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"❌ Request Error: {e}")
        sys.exit(1)


def main():
    # Parse command line arguments
    if len(sys.argv) != 2:
        print("Usage: python run_llm_code.py <1|2|3|4>")
        print("\nAvailable code snippets:")
        snippets = load_code_snippets()
        for i, snippet in enumerate(snippets, 1):
            print(f"  {i}. {snippet['question']}")
        sys.exit(1)

    try:
        snippet_num = int(sys.argv[1])
        if snippet_num < 1 or snippet_num > 4:
            raise ValueError()
    except ValueError:
        print("❌ Error: Please provide a number between 1 and 4")
        sys.exit(1)

    # Get data server URL (optional override)
    data_server_url = os.getenv('DATA_SERVER_URL', 'http://localhost:8001')

    # Load code snippets
    snippets = load_code_snippets()
    selected = snippets[snippet_num - 1]

    print("=" * 70)
    print(f"🚀 Running code snippet #{snippet_num}")
    print(f"❓ Question: {selected['question']}")
    print("=" * 70)
    print("\n📄 Code to execute:")
    print("-" * 70)
    print(selected['code'])
    print("-" * 70)

    # Execute the code
    output = execute_code(selected['code'], data_server_url)

    print("\n✅ Execution Result:")
    print("=" * 70)
    print(output)
    print("=" * 70)


if __name__ == '__main__':
    main()
