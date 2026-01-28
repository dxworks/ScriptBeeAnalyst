"""
Data Sub-Agent: Generates Python code to query graph data.

Uses code_generation_guidelines.txt as system prompt to generate
executable Python code for data queries.
"""
import os
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def load_guidelines() -> str:
    """Load code generation guidelines from file."""
    guidelines_path = Path(__file__).parent.parent.parent / "code_generation_guidelines.txt"
    return guidelines_path.read_text()


def generate_code(query: str, mock: bool = False, llm: Optional[ChatOpenAI] = None) -> str:
    """
    Generate Python code to answer a data query.

    Args:
        query: Natural language question about the project data
        mock: If True, return hardcoded example code (for testing without LLM calls)
        llm: Optional ChatOpenAI instance (if None, creates one from env)

    Returns:
        Python code as string that prints the answer to stdout
    """
    if mock:
        # Return mocked code for fast testing
        return _get_mocked_code(query)

    # Load guidelines as system prompt
    guidelines = load_guidelines()

    # Create LLM if not provided
    if llm is None:
        model_name = os.getenv("OPENAI_MODEL_FOR_CODE", "gpt-4o-mini")
        llm = ChatOpenAI(model=model_name, temperature=0)

    # Generate code with LLM
    messages = [
        SystemMessage(content=guidelines),
        HumanMessage(content=query),
    ]

    response = llm.invoke(messages)
    return response.content


def _get_mocked_code(query: str) -> str:
    """
    Return hardcoded example code based on query keywords.

    Used for testing without making LLM API calls.
    """
    query_lower = query.lower()

    if ("commit" in query_lower and "count" in query_lower) or ("how many" in query_lower and "commit" in query_lower):
        return """
# Get total commit count from git project
git_project = graph_data["git"]
commit_count = len(git_project.git_commit_registry.all)
print(f"Total commits: {commit_count}")
"""

    elif "top" in query_lower and ("contributor" in query_lower or "author" in query_lower):
        # Specific handling for "top contributors/authors" question
        return """
# Get top 10 authors by commit count
from collections import Counter

git_project = graph_data["git"]
author_counts = Counter()

for commit in git_project.git_commit_registry.all:
    author_counts[commit.author.name] += 1

print("Top 10 contributors by commit count:")
for author, count in author_counts.most_common(10):
    print(f"  {author}: {count} commits")
"""

    elif "author" in query_lower:
        return """
# Get top 10 authors by commit count
from collections import Counter

git_project = graph_data["git"]
author_counts = Counter()

for commit in git_project.git_commit_registry.all:
    author_counts[commit.author.name] += 1

print("Top 10 authors by commit count:")
for author, count in author_counts.most_common(10):
    print(f"  {author}: {count} commits")
"""

    elif "issue" in query_lower:
        return """
# Get total issue count from jira project
jira_project = graph_data["jira"]
issue_count = len(jira_project.issue_registry.all)
print(f"Total issues: {issue_count}")
"""

    elif "pull request" in query_lower or "pr" in query_lower:
        return """
# Get total pull request count from github project
github_project = graph_data["github"]
pr_count = len(github_project.pull_request_registry.all)
print(f"Total pull requests: {pr_count}")
"""

    else:
        # Generic fallback
        return """
# Get project statistics
git_project = graph_data["git"]
jira_project = graph_data["jira"]
github_project = graph_data["github"]

git_commits = len(git_project.git_commit_registry.all)
jira_issues = len(jira_project.issue_registry.all)
github_prs = len(github_project.pull_request_registry.all)

print("Project Statistics:")
print(f"  Git commits: {git_commits}")
print(f"  Jira issues: {jira_issues}")
print(f"  GitHub PRs: {github_prs}")
"""
