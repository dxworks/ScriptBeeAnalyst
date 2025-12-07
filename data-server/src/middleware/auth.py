"""
Authentication middleware for FastAPI.
Validates JWT tokens and extracts user information.
"""

from typing import Optional
from fastapi import Header, HTTPException, status
from jose import jwt, JWTError
from pydantic import BaseModel
from src.config import JWT_SECRET


class UserContext(BaseModel):
    """User context extracted from JWT token."""
    user_id: str
    email: Optional[str] = None
    role: Optional[str] = None
    token: str  # Raw JWT token for creating user-scoped Supabase client


def extract_token_from_header(authorization: str) -> str:
    """
    Extracts JWT token from Authorization header.

    Args:
        authorization: Authorization header value (e.g., "Bearer <token>")

    Returns:
        JWT token string

    Raises:
        HTTPException: If header format is invalid
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'"
        )

    return parts[1]


async def verify_jwt_token(authorization: str = Header(...)) -> UserContext:
    """
    FastAPI dependency that validates JWT token and returns user context.

    Args:
        authorization: Authorization header (injected by FastAPI)

    Returns:
        UserContext with user_id, optional email/role, and raw token

    Raises:
        HTTPException: If token is invalid, expired, or missing
    """
    token = extract_token_from_header(authorization)

    try:
        # Decode and verify JWT
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False}  # Supabase doesn't use aud claim
        )

        # Extract user_id from 'sub' claim (standard JWT claim)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing 'sub' claim"
            )

        # Extract optional fields
        email = payload.get("email")
        role = payload.get("role")

        return UserContext(user_id=user_id, email=email, role=role, token=token)

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token validation failed: {str(e)}"
        )
