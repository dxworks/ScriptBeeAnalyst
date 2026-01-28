"""
Auth Helper: Authenticate test users and retrieve JWT tokens.
"""
import os
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv
from supabase import Client, create_client

# Load environment variables
root_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(root_env_path)


def login_test_user(
    email: str,
    password: str,
    supabase_url: Optional[str] = None,
    supabase_key: Optional[str] = None,
) -> Tuple[str, str, Client]:
    """
    Login with test user credentials and return authenticated client.

    Args:
        email: User email
        password: User password
        supabase_url: Supabase URL (defaults to env)
        supabase_key: Supabase anon key (defaults to env)

    Returns:
        Tuple of (access_token, user_id, authenticated_client)

    Raises:
        Exception: If login fails
    """
    supabase_url = supabase_url or os.getenv("SUPABASE_URL")
    supabase_key = supabase_key or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")

    # Create client
    client: Client = create_client(supabase_url, supabase_key)

    # Sign in
    response = client.auth.sign_in_with_password({
        "email": email,
        "password": password,
    })

    if not response.user:
        raise Exception("Login failed: No user returned")

    access_token = response.session.access_token
    user_id = response.user.id

    return access_token, user_id, client


def get_test_user_client() -> Tuple[str, Client]:
    """
    Get authenticated client for test user.

    Uses TEST_USER_EMAIL and TEST_USER_PASSWORD from environment.

    Returns:
        Tuple of (user_id, authenticated_client)

    Raises:
        ValueError: If test user credentials not set in environment
    """
    test_email = os.getenv("TEST_USER_EMAIL")
    test_password = os.getenv("TEST_USER_PASSWORD")

    if not test_email or not test_password:
        raise ValueError("TEST_USER_EMAIL and TEST_USER_PASSWORD must be set in .env")

    access_token, user_id, client = login_test_user(
        test_email,
        test_password,
    )

    return user_id, client
