"""
Orchestrator Tools: Functions that the main agent can call.

These tools integrate sub-agents with the data-server API to provide
clean interfaces for the orchestrator.
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from agents import data_agent, plot_agent

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

FASTAPI_ENDPOINT = os.getenv("FASTAPI_ENDPOINT", "http://localhost:8001")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _strip_markdown_code_block(code: str) -> str:
    """
    Strip markdown code blocks from LLM responses.

    LLMs often wrap code in ```python ... ``` blocks, which we need to remove
    before sending to the execution server.
    """
    # Remove ```python or ``` at start and ``` at end
    code = re.sub(r'^```(?:python)?\s*\n', '', code, flags=re.MULTILINE)
    code = re.sub(r'\n```\s*$', '', code, flags=re.MULTILINE)
    return code.strip()


def ask_user(question: str) -> str:
    """
    Ask the user a clarification question.

    This is a placeholder for Chainlit integration. In the final system,
    this will use Chainlit's message handling to get user input.

    Args:
        question: Question to ask the user

    Returns:
        User's response (currently simulated with input())
    """
    # TODO: Replace with Chainlit integration in Phase 6
    print(f"\n[ASK USER] {question}")
    return input("User response: ")


def query_data(
    natural_language_query: str,
    mock_agent: bool = False,
    verbose: bool = False
) -> str:
    """
    Query project data using natural language.

    This tool:
    1. Calls data sub-agent to generate Python code
    2. Strips markdown formatting if present
    3. Sends code to data-server /execute endpoint
    4. Returns only the output (not the code)

    Args:
        natural_language_query: User's question in natural language
        mock_agent: If True, use mocked agent responses (no LLM calls)
        verbose: If True, print intermediate steps

    Returns:
        Output from code execution (what was printed to stdout)

    Raises:
        Exception: If server is unreachable, no project loaded, or code fails
    """
    logger.info(f"[QUERY_DATA] Processing query: {natural_language_query}")
    if verbose:
        print(f"[QUERY_DATA] Processing: {natural_language_query}")

    # Step 1: Generate code using data agent
    try:
        code = data_agent.generate_code(natural_language_query, mock=mock_agent)
        code = _strip_markdown_code_block(code)

        logger.info(f"[QUERY_DATA] Generated code:\n{code}")
        if verbose:
            print(f"[QUERY_DATA] Generated code:\n{code}\n")
    except Exception as e:
        logger.error(f"[QUERY_DATA] Failed to generate code: {e}")
        raise Exception(f"Failed to generate code: {e}")

    # Step 2: Execute code on data-server
    try:
        response = requests.post(
            f"{FASTAPI_ENDPOINT}/execute",
            json={"code": code},
            timeout=30
        )

        # Handle different response codes
        if response.status_code == 200:
            data = response.json()
            output = data.get("output", "").strip()

            logger.info(f"[QUERY_DATA] Execution successful. Output: {output}")
            if verbose:
                print(f"[QUERY_DATA] Execution successful")
                print(f"[QUERY_DATA] Output: {output}")

            return output

        elif response.status_code == 404:
            # No project loaded
            data = response.json()
            error_msg = data.get("message", "No project loaded")
            raise Exception(f"No project loaded: {error_msg}")

        elif response.status_code == 400:
            # Code execution error
            data = response.json()
            error_msg = data.get("error", "Unknown error")
            raise Exception(f"Code execution failed: {error_msg}")

        else:
            response.raise_for_status()

    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to data-server at {FASTAPI_ENDPOINT}")
    except requests.exceptions.Timeout:
        raise Exception("Data-server request timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Request failed: {e}")


def generate_plot(
    plot_description: str,
    mock_agent: bool = False,
    verbose: bool = False,
    save_path: Optional[Path] = None
) -> str:
    """
    Generate a plot/visualization using natural language.

    This tool:
    1. Calls plot sub-agent to generate matplotlib code
    2. Strips markdown formatting if present
    3. Sends code to data-server /plot endpoint
    4. Saves the image and returns the path

    Args:
        plot_description: Description of desired visualization
        mock_agent: If True, use mocked agent responses (no LLM calls)
        verbose: If True, print intermediate steps
        save_path: Optional path to save image (defaults to temp file)

    Returns:
        Path to saved image file

    Raises:
        Exception: If server is unreachable, no project loaded, or code fails
    """
    logger.info(f"[GENERATE_PLOT] Processing plot: {plot_description}")
    if verbose:
        print(f"[GENERATE_PLOT] Processing: {plot_description}")

    # Step 1: Generate code using plot agent
    try:
        code = plot_agent.generate_code(plot_description, mock=mock_agent)
        code = _strip_markdown_code_block(code)

        logger.info(f"[GENERATE_PLOT] Generated code:\n{code}")
        if verbose:
            print(f"[GENERATE_PLOT] Generated code:\n{code}\n")
    except Exception as e:
        logger.error(f"[GENERATE_PLOT] Failed to generate plot code: {e}")
        raise Exception(f"Failed to generate plot code: {e}")

    # Step 2: Execute code on data-server
    try:
        response = requests.post(
            f"{FASTAPI_ENDPOINT}/plot",
            json={"code": code},
            timeout=30
        )

        # Handle different response codes
        if response.status_code == 200:
            # Save image
            if save_path is None:
                # Default: save to temp directory with timestamp
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = Path(__file__).parent.parent / f"plot_{timestamp}.jpg"

            with open(save_path, "wb") as f:
                f.write(response.content)

            logger.info(f"[GENERATE_PLOT] Plot saved to: {save_path}")
            if verbose:
                print(f"[GENERATE_PLOT] Plot saved to: {save_path}")

            return str(save_path)

        elif response.status_code == 404:
            # No project loaded
            data = response.json()
            error_msg = data.get("message", "No project loaded")
            raise Exception(f"No project loaded: {error_msg}")

        elif response.status_code == 400:
            # Code execution error
            data = response.json()
            error_msg = data.get("error", "Unknown error")
            raise Exception(f"Plot generation failed: {error_msg}")

        else:
            response.raise_for_status()

    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to data-server at {FASTAPI_ENDPOINT}")
    except requests.exceptions.Timeout:
        raise Exception("Data-server request timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Request failed: {e}")
