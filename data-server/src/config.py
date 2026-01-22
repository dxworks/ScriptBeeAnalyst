"""
Configuration module for data-server.
Loads environment variables from .env file (local dev) or environment (Docker).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Try to load .env from project root (for local development)
# In Docker, environment variables are passed directly via docker-compose
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)

# Supabase configuration
# - Local dev: http://localhost:8000
# - Docker: http://kong:8000 (internal network)
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8000")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET", "")

# Graph processor configuration (Phase 3+)
# Hardcoded user_id and project_id for testing - will be dynamic in Phase 6
GRAPH_USER_ID = os.getenv("GRAPH_USER_ID", "")
GRAPH_PROJECT_ID = os.getenv("GRAPH_PROJECT_ID", "")

# Validate critical settings
if not JWT_SECRET:
    raise ValueError("JWT_SECRET must be set (via .env file or environment variable)")

if not SUPABASE_SERVICE_KEY:
    raise ValueError("SUPABASE_SERVICE_KEY must be set (via .env file or environment variable)")
