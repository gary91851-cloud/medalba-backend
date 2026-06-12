"""Verify Supabase JWTs and resolve the calling provider.

Supports both Supabase signing schemes:
- New projects: asymmetric keys (ES256/RS256), verified against the project's
  public JWKS endpoint
- Legacy projects: shared-secret HS256
"""
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Header

from .config import get_settings
from .db import get_db


@lru_cache
def _jwk_client() -> PyJWKClient:
    s = get_settings()
    return PyJWKClient(f"{s.supabase_url}/auth/v1/.well-known/jwks.json")


def _decode_token(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
        if alg == "HS256":
            return jwt.decode(
                token,
                s.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")


def get_current_provider(authorization: str | None = Header(None)) -> dict:
    claims = _decode_token(authorization)
    auth_user_id = claims["sub"]
    db = get_db()

    res = db.table("providers").select("*").eq("auth_user_id", auth_user_id).execute()
    if res.data:
        return res.data[0]

    # First login: create practice + provider from auth metadata
    meta = claims.get("user_metadata", {}) or {}
    email = claims.get("email", "")
    full_name = meta.get("full_name") or email.split("@")[0]
    practice_name = meta.get("practice_name") or f"{full_name}'s Practice"

    practice = (
        db.table("practices")
        .insert({"name": practice_name, "subscription_tier": "pilot"})
        .execute()
        .data[0]
    )
    provider = (
        db.table("providers")
        .insert(
            {
                "auth_user_id": auth_user_id,
                "practice_id": practice["id"],
                "full_name": full_name,
                "email": email,
            }
        )
        .execute()
        .data[0]
    )
    try:
        from .email_service import send_welcome
        send_welcome(email, full_name, practice_name)
    except Exception:
        pass
    return provider
