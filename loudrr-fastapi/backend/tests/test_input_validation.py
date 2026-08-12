"""Input-validation & hostile-input hardening.

Bad input is rejected at the edge (Pydantic / parsers) before it can reach the
DB, and the service caps/normalizes what it stores.
"""
from types import SimpleNamespace

from app.core.crypto import sign_x_proof
from app.core.time_utils import utcnow
from app.schemas.waitlist import OtherPlatform, OtherPlatformKind
from app.services import waitlist as waitlist_svc


def _proof(tg_id: int, username: str, x_user_id: str = "1"):
    return sign_x_proof({
        "tg_id": tg_id,
        "x_username": username,
        "x_user_id": x_user_id,
        "iat": int(utcnow().timestamp()),
    })


# ---- waitlist register endpoint: malformed body is a 422, never a 500 ----
async def test_register_missing_x_proof_422(client):
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": 9_500_002},
        json={},  # x_proof is the only required field
    )
    assert r.status_code == 422


# ---- service caps / normalizes what it stores ----
async def test_other_platforms_capped_at_five(db_session):
    payload = SimpleNamespace(
        x_proof=_proof(tg_id=9_500_003, username="capuser"),
        region=None,
        niche=None,
        referral_code=None,
        other_platforms=[
            OtherPlatform(platform=OtherPlatformKind.OTHER, username=f"u{i}")
            for i in range(7)
        ],
    )
    result = await waitlist_svc.register_entry(
        db_session, tg_user={"id": 9_500_003}, payload=payload
    )
    assert len(result.entry.other_platforms) == 5  # 7 submitted, stored 5


async def test_large_telegram_id_is_accepted(db_session):
    """Telegram IDs are 64-bit — a value past 32-bit must store fine (BigInteger)."""
    big = 8_888_888_888  # > 2**32
    payload = SimpleNamespace(
        x_proof=_proof(tg_id=big, username="biguser"),
        region=None,
        niche=None,
        referral_code=None,
        other_platforms=[],
    )
    result = await waitlist_svc.register_entry(
        db_session, tg_user={"id": big}, payload=payload
    )
    assert result.entry.telegram_id == big
