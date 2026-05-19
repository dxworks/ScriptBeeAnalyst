"""
Example test client for data-server API.
Demonstrates how to interact with the v2 typed-Graph endpoints.

Session model: ``/execute`` and ``/plot`` are root paths that target the
currently-loaded project. Call ``build_project`` (one-shot: build + load) or
``load_project`` first; thereafter ``execute_code`` and ``generate_plot`` no
longer take a ``project_id``.
"""

import requests
import json


class DataServerClient:
    """Client for interacting with data-server API."""

    def __init__(self, base_url: str, jwt_token: str):
        """
        Initialize client.

        Args:
            base_url: Base URL of data-server (e.g., 'http://localhost:8001')
            jwt_token: JWT token from Supabase authentication
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }

    def health_check(self):
        """Check server health and see loaded projects."""
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    def current_project(self):
        """Return ``{"loaded": False}`` or the currently-loaded project's metadata."""
        response = requests.get(f"{self.base_url}/projects/current")
        response.raise_for_status()
        return response.json()

    def build_project(self, project_id: str):
        """
        Build project graph from files in Supabase. Also loads it.

        Args:
            project_id: UUID of the project

        Returns:
            Response dict with build metadata
        """
        response = requests.post(
            f"{self.base_url}/projects/{project_id}/build",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def load_project(self, project_id: str):
        """Load a previously-built graph from the local pickle store."""
        response = requests.post(
            f"{self.base_url}/projects/{project_id}/load",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def execute_code(self, code: str):
        """
        Execute Python code against the currently-loaded project graph.

        Args:
            code: Python code to execute

        Returns:
            Dict with 'output' or 'error' key
        """
        response = requests.post(
            f"{self.base_url}/execute",
            headers=self.headers,
            json={"code": code},
        )
        response.raise_for_status()
        return response.json()

    def generate_plot(self, code: str, output_path: str):
        """
        Generate matplotlib plot from the currently-loaded project graph
        and save to file.

        Args:
            code: Python code that creates a plot
            output_path: Path to save JPEG image
        """
        response = requests.post(
            f"{self.base_url}/plot",
            headers=self.headers,
            json={"code": code},
        )
        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

    def unload_project(self, project_id: str):
        """
        Unload project graph from memory.

        Args:
            project_id: UUID of the project

        Returns:
            Response dict
        """
        response = requests.delete(
            f"{self.base_url}/projects/{project_id}/unload",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()


def example_usage():
    """Example usage of the client (v2 typed-Graph surface)."""
    # Configuration
    BASE_URL = "http://localhost:8001"
    JWT_TOKEN = "your-jwt-token-here"  # Get from Supabase auth
    PROJECT_ID = "your-project-uuid"  # Get from database

    # Create client
    client = DataServerClient(BASE_URL, JWT_TOKEN)

    # Check health
    print("=== Health Check ===")
    health = client.health_check()
    print(json.dumps(health, indent=2))

    # Build project (also loads it as the current project)
    print("\n=== Building Project ===")
    build_result = client.build_project(PROJECT_ID)
    print(json.dumps(build_result, indent=2))

    # Execute code - Count commits
    print("\n=== Executing Code: Count Commits ===")
    code = """
commits = graph_data.commits.all()
print(f"Total commits: {len(commits)}")
"""
    result = client.execute_code(code)
    print(result.get("output", result.get("error")))

    # Execute code - List issues
    print("\n=== Executing Code: List JIRA Issues ===")
    code = """
for issue in graph_data.issues.all()[:10]:
    print(f"{issue.key}: {issue.summary[:60]}")
"""
    result = client.execute_code(code)
    print(result.get("output", result.get("error")))

    # Generate plot - Top contributors
    print("\n=== Generating Plot: Top Contributors ===")
    plot_code = """
commits = graph_data.commits.all()
authors = {}
for c in commits:
    a = c.author_ref.id if c.author_ref else "<unknown>"
    authors[a] = authors.get(a, 0) + 1

top_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)[:10]
names, counts = zip(*top_authors)

plt.figure(figsize=(10, 6))
plt.barh(names, counts)
plt.xlabel('Commits')
plt.title('Top 10 Contributors')
plt.tight_layout()
"""
    client.generate_plot(plot_code, "top_contributors.jpg")
    print("Plot saved to: top_contributors.jpg")

    # Unload project
    print("\n=== Unloading Project ===")
    unload_result = client.unload_project(PROJECT_ID)
    print(json.dumps(unload_result, indent=2))


if __name__ == "__main__":
    example_usage()
