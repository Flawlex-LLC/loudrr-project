"""Input-validation & hostile-input hardening.

Bad input is rejected at the edge (Pydantic / parsers) before it can reach the
DB, and the service caps/normalizes what it stores.
"""
from types import SimpleNamespace

from app.schemas.waitlist import OtherPlatform, OtherPlatformKind
from app.services import waitlist as waitlist_svc

# register needs a browser-confirmed proof: ``confirmed_x_proof`` (conftest.py)


# ---- waitlist register endpoint: malformed body is a 422, never a 500 ----
async def test_register_missing_x_proof_422(client):
    r = await client.post(
        "/waitlist/register/",
        params={"telegram_id": 9_500_002},
        json={},  # x_proof is the only required field
    )
    assert r.status_code == 422


# ---- service caps / normalizes what it stores ----
async def test_other_platforms_capped_at_five(db_session, confirmed_x_proof):
    payload = SimpleNamespace(
        x_proof=await confirmed_x_proof(9_500_003, "capuser"),
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


async def test_large_telegram_id_is_accepted(db_session, confirmed_x_proof):
    """Telegram IDs are 64-bit — a value past 32-bit must store fine (BigInteger)."""
    big = 8_888_888_888  # > 2**32
    payload = SimpleNamespace(
        x_proof=await confirmed_x_proof(big, "biguser"),
        region=None,
        niche=None,
        referral_code=None,
        other_platforms=[],
    )
    result = await waitlist_svc.register_entry(
        db_session, tg_user={"id": big}, payload=payload
    )
    assert result.entry.telegram_id == big
