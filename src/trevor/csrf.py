"""CSRF token helpers using itsdangerous signed tokens.

Applied to all state-mutating UI form POSTs. API endpoints are
CSRF-safe (JWT auth via Authorization header, not cookies).
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def generate_csrf_token(secret_key: str, salt: str = "csrf") -> str:
    """Generate a signed CSRF token."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    return s.dumps("csrf")


def validate_csrf_token(
    secret_key: str,
    token: str,
    max_age: int = 3600,
    salt: str = "csrf",
) -> bool:
    """Validate a CSRF token. Returns True if valid, False otherwise."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    try:
        s.loads(token, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False
