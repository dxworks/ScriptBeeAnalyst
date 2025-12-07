"""
Supabase client initialization and management.
Provides service-role client for admin operations and user-scoped clients for RLS.
"""

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY


def get_service_client() -> Client:
    """
    Returns a Supabase client with service role key.
    Use for admin operations that bypass RLS.
    """
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_user_client(user_jwt: str) -> Client:
    """
    Returns a Supabase client scoped to a specific user's JWT.
    Use for operations that should respect RLS policies.

    Args:
        user_jwt: The user's JWT token (without 'Bearer ' prefix)

    Returns:
        Client configured with user's JWT for RLS enforcement
    """
    client = create_client(SUPABASE_URL, user_jwt)
    return client
