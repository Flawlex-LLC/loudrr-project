"""Signed short-lived tokens for OAuth proof round-trips.

Uses itsdangerous (already in requirements.txt) — the payload is a small dict
serialized to JSON, then URL-safe base64-encoded and HMAC-signed with
`settings.secret_key`. Payloads are NOT confidential (the X handle isn't
secret); the signature is what makes the token unforgeable when it's echoed
back to `/waitlist/register/`.

The salt versions the signer so we can rotate the payload shape by bumping it.
"""
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_SALT = "waitlist-x-oauth-proof-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=settings.secret_key, salt=_SALT)


def sign_x_proof(payload: dict[str, Any]) -> str:
    """Sign an arbitrary JSON-serializable dict → URL-safe token."""
    # The dev default is public knowledge — a proof signed with it is
    # forgeable by anyone. Refuse to MINT outside debug (the prod-guard in
    # config.py already blocks ENVIRONMENT=prod; this catches staging-ish
    # deployments that forgot both). Verification is deliberately unchanged.
    if (
        settings.secret_key == "dev-insecure-secret-change-me"
        and not settings.debug
    ):
        raise RuntimeError(
            "refusing to sign OAuth proofs with the dev default SECRET_KEY"
        )
    return _serializer().dumps(payload)


def verify_x_proof(
    token: str, max_age_seconds: int = 600
) -> dict[str, Any] | None:
    """Return the payload dict if signature valid and not expired, else None."""
    try:
        return _serializer().loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
