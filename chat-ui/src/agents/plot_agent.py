"""
Plot Sub-Agent: Generates matplotlib code to create visualizations.

Uses plot_generation_guidelines.md as system prompt to generate
executable matplotlib code for data visualizations.
"""
import os
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def load_guidelines() -> str:
    """Load plot generation guidelines from file."""
    guidelines_path = Path(__file__).parent.parent.parent / "plot_generation_guidelines.md"
    return guidelines_path.read_text()


def generate_code(query: str, mock: bool = False, llm: Optional[ChatOpenAI] = None) -> str:
    """
    Generate matplotlib code to create a visualization.

    Args:
        query: Natural language description of the desired plot
        mock: If True, return hardcoded example code (for testing without LLM calls)
        llm: Optional ChatOpenAI instance (if None, creates one from env)

    Returns:
        matplotlib code as string that creates a plot
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
    Return hardcoded example matplotlib code based on query keywords.

    Used for testing without making LLM API calls.
    """
    query_lower = query.lower()

    if "commit" in query_lower and "month" in query_lower:
        return """
import matplotlib.pyplot as plt
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

    elif "author" in query_lower:
        return """
import matplotlib.pyplot as plt
from collections import Counter

# Get git project
git_project = graph_data["git"]

# Count commits per author
author_counts = Counter()
for commit in git_project.git_commit_registry.all:
    author_counts[commit.author.name] += 1

# Get top 10 authors
top_authors = author_counts.most_common(10)
authors = [a for a, _ in top_authors]
counts = [c for _, c in top_authors]

# Create bar chart
plt.figure(figsize=(12, 6))
plt.barh(authors, counts, color='#6366f1')
plt.xlabel('Number of Commits')
plt.ylabel('Author')
plt.title('Top 10 Authors by Commit Count')
plt.gca().invert_yaxis()
plt.tight_layout()
"""

    elif "issue" in query_lower and "status" in query_lower:
        return """
import matplotlib.pyplot as plt
from collections import Counter

# Get jira project
jira_project = graph_data["jira"]

# Count issues by status
status_counts = Counter()
for issue in jira_project.issue_registry.all:
    status_counts[issue.status.name] += 1

# Create pie chart
statuses = list(status_counts.keys())
counts = list(status_counts.values())

plt.figure(figsize=(10, 10))
plt.pie(counts, labels=statuses, autopct='%1.1f%%', startangle=90)
plt.title('Issues by Status')
plt.axis('equal')
plt.tight_layout()
"""

    elif "pull request" in query_lower or "pr" in query_lower:
        return """
import matplotlib.pyplot as plt
from collections import Counter

# Get github project
github_project = graph_data["github"]

# Count PRs by state
state_counts = Counter()
for pr in github_project.pull_request_registry.all:
    state_counts[pr.state] += 1

# Create bar chart
states = list(state_counts.keys())
counts = list(state_counts.values())

plt.figure(figsize=(8, 6))
plt.bar(states, counts, color='#6366f1')
plt.xlabel('State')
plt.ylabel('Number of Pull Requests')
plt.title('Pull Requests by State')
plt.tight_layout()
"""

    else:
        # Generic fallback - commits per month
        return """
import matplotlib.pyplot as plt
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
