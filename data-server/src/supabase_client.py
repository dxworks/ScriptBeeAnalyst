"""Supabase client — single-tenant local mode, service-role only."""

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY


def get_service_client() -> Client:
    """Return a Supabase client with the service-role key."""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
