"""Shared slowapi limiter instance."""

from fastapi import Request
from slowapi import Limiter


def _rate_limit_key(request: Request) -> str | None:
    """Return None (exempt) in dev/test mode, otherwise use remote IP."""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and getattr(settings, "dev_auth_bypass", False):
        return None
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_rate_limit_key)
