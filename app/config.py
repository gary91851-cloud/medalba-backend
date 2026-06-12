import os
from functools import lru_cache


class Settings:
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    supabase_jwt_secret: str = os.environ.get("SUPABASE_JWT_SECRET", "")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
    resend_api_key: str = os.environ.get("RESEND_API_KEY", "")
    from_email: str = os.environ.get("FROM_EMAIL", "MedAlba <onboarding@resend.dev>")
    frontend_url: str = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    guide_base_url: str = os.environ.get("GUIDE_BASE_URL", "http://localhost:5173/guide")


@lru_cache
def get_settings() -> Settings:
    return Settings()
