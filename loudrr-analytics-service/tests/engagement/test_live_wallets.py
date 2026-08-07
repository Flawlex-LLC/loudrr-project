"""Spec — realtime KOL wallet watcher (app/engagement/live_wallets.py).

parse_swap is the load-bearing pure function: logsSubscribe hands us a signature, and this is
what turns the fetched transaction into "wallet bought/sold mint for N". The cases are built
from REAL KOL transactions captured in the scratchpad probe, not invented shapes:
  BUY  — token 0 -> 23.1M, native SOL -3.03   (5BxwExNi… on DaniWorldwide)
  SELL — token 11.4M -> 0, native SOL +2.01    (6z5EX1tf… on Kevsznx)
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engagement import models as em
from app.engagement.live_wallets import ingest_signature, parse_swap

OWNER = "AuPp4YTMTyqxYXQnHc5KUc6pUuCSsHQpBJhgnD45yqrf"
MINT = "5BxwExNiabc111111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"


def _tx(*, mint_pre, mint_post, sol_pre, sol_post, err=None, owner=OWNER,
        extra_balances=(), block_time=1784303945, fee=2039):
    pre_tb = [{"owner": owner, "mint": MINT,
               "uiTokenAmount": {"uiAmount": mint_pre}}] if mint_pre is not None else []
    post_tb = [{"owner": owner, "mint": MINT,
                "uiTokenAmount": {"uiAmount": mint_post}}] if mint_post is not None else []
    for m, a, b in extra_balances:
        pre_tb.append({"owner": owner, "mint": m, "uiTokenAmount": {"uiAmount": a}})
        post_tb.append({"owner": owner, "mint": m, "uiTokenAmount": {"uiAmount": b}})
    return {
        "blockTime": block_time,
        "meta": {"err": err, "fee": fee,
                 "preBalances": [int(sol_pre * 1e9), 0],
                 "postBalances": [int(sol_post * 1e9), 0],
                 "preTokenBalances": pre_tb, "postTokenBalances": post_tb},
        "transaction": {"message": {"accountKeys": [
            {"pubkey": owner, "signer": True}, {"pubkey": "other"}]}},
    }


# ── parse_swap ───────────────────────────────────────────────────────────────

def test_buy_detected():
    swap = parse_swap(_tx(mint_pre=0.0, mint_post=23104737.5, sol_pre=5.0, sol_post=1.965), OWNER)
    assert swap["side"] == "buy"
    assert swap["mint"] == MINT
    assert swap["token_amount"] == pytest.approx(23104737.5)
    assert swap["sol_amount"] == pytest.approx(3.035, abs=1e-3)


def test_sell_detected():
    swap = parse_swap(_tx(mint_pre=11438427.15, mint_post=0.0, sol_pre=1.0, sol_post=3.014), OWNER)
    assert swap["side"] == "sell"
    assert swap["token_amount"] == pytest.approx(11438427.15)


def test_failed_tx_is_not_a_trade():
    assert parse_swap(_tx(mint_pre=0.0, mint_post=1e6, sol_pre=5.0, sol_post=2.0,
                          err={"InstructionError": [0, "x"]}), OWNER) is None


def test_pure_sol_movement_is_not_a_swap():
    """A transfer with no token leg for the owner — SOL moved, no position change."""
    assert parse_swap(_tx(mint_pre=None, mint_post=None, sol_pre=5.0, sol_post=4.9), OWNER) is None


def test_wsol_only_is_excluded():
    """WSOL is the quote side, never the token traded — a wrap/unwrap isn't a call."""
    tx = _tx(mint_pre=None, mint_post=None, sol_pre=5.0, sol_post=4.0,
             extra_balances=[(WSOL, 0.0, 1.0)])
    assert parse_swap(tx, OWNER) is None


def test_largest_delta_mint_wins():
    """Routing touches several token accounts; the KOL's real position is the biggest move."""
    other = "9semjRcxdef2222222222222222222222222222222"
    tx = _tx(mint_pre=0.0, mint_post=1000.0, sol_pre=5.0, sol_post=2.0,
             extra_balances=[(other, 0.0, 50.0)])
    assert parse_swap(tx, OWNER)["mint"] == MINT   # 1000 > 50


def test_multiple_accounts_same_mint_are_summed():
    """An owner can hold several token accounts for one mint — a stable ATA plus a fresh temp
    account a router opens for the swap. The NET position change is what counts; keying by mint
    alone (last-wins) would compare two different accounts and flip buy<->sell (review finding)."""
    # holds 100 in an existing account (unchanged), BUYS 30 into a newly-opened account
    tx = {
        "blockTime": 1784303945,
        "meta": {"err": None, "fee": 5000,
                 "preBalances": [int(5.0 * 1e9), 0], "postBalances": [int(4.5 * 1e9), 0],
                 "preTokenBalances": [
                     {"owner": OWNER, "mint": MINT, "uiTokenAmount": {"uiAmount": 100.0}}],
                 "postTokenBalances": [
                     {"owner": OWNER, "mint": MINT, "uiTokenAmount": {"uiAmount": 100.0}},
                     {"owner": OWNER, "mint": MINT, "uiTokenAmount": {"uiAmount": 30.0}}]},
        "transaction": {"message": {"accountKeys": [{"pubkey": OWNER}, {"pubkey": "x"}]}},
    }
    swap = parse_swap(tx, OWNER)
    assert swap["side"] == "buy"                       # NOT sell (100 -> 30 last-wins would flip)
    assert swap["token_amount"] == pytest.approx(30.0)  # net +30, not |30-100|=70


def test_only_the_owners_balances_count():
    """A swap the KOL merely appears in (someone else's position moving) is not their trade."""
    tx = _tx(mint_pre=None, mint_post=None, sol_pre=5.0, sol_post=4.99)
    tx["meta"]["postTokenBalances"] = [
        {"owner": "someone-else", "mint": MINT, "uiTokenAmount": {"uiAmount": 999.0}}]
    assert parse_swap(tx, OWNER) is None


def test_sol_amount_none_when_owner_absent_from_keys():
    tx = _tx(mint_pre=0.0, mint_post=100.0, sol_pre=5.0, sol_post=2.0)
    tx["transaction"]["message"]["accountKeys"] = [{"pubkey": "x"}]  # owner not present
    swap = parse_swap(tx, OWNER)
    assert swap["side"] == "buy" and swap["sol_amount"] is None


# ── ingest_signature (DB + payload) ──────────────────────────────────────────

async def _seed(SessionLocal, *, price=0.03):
    async with SessionLocal() as s:
        s.add(em.EngToken(contract=MINT, chain="solana", symbol="PENGU", name="Pengu",
                          price_usd=price, pool_address="P1", network="solana",
                          refreshed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        s.add(em.EngWallet(address=OWNER, handle="daniworldwide", member_id="1",
                           chain="sol", source="kolscan", confidence="leaderboard",
                           score=3800.0, smart_followers=120))
        await s.commit()


async def test_ingest_writes_ride_and_returns_payload(eng_db):
    await _seed(eng_db)
    async with eng_db() as s:
        wallet = (await s.execute(select(em.EngWallet))).scalars().one()

    tx = _tx(mint_pre=0.0, mint_post=1000.0, sol_pre=5.0, sol_post=2.0)
    payload = await ingest_signature("sigAAA", wallet, tx, dex=None)

    assert payload is not None
    contract, ride = payload
    assert contract == MINT
    assert ride["side"] == "buy"
    assert ride["username"] == "daniworldwide"
    assert ride["price"] == pytest.approx(0.03)
    assert ride["volumeUsd"] == pytest.approx(30.0)   # 1000 * 0.03
    assert ride["tier"] == "Amplifier"                 # 3800 -> Amplifier band

    async with eng_db() as s:
        r = (await s.execute(select(em.EngRide))).scalars().one()
    assert r.tx_hash == "sigAAA" and r.token_contract == MINT and r.side == "buy"


async def test_ingest_is_idempotent_on_tx_hash(eng_db):
    await _seed(eng_db)
    async with eng_db() as s:
        wallet = (await s.execute(select(em.EngWallet))).scalars().one()
    tx = _tx(mint_pre=0.0, mint_post=1000.0, sol_pre=5.0, sol_post=2.0)

    first = await ingest_signature("sigDUP", wallet, tx, dex=None)
    second = await ingest_signature("sigDUP", wallet, tx, dex=None)
    assert first is not None and second is None
    async with eng_db() as s:
        assert len((await s.execute(select(em.EngRide))).scalars().all()) == 1


async def test_ingest_skips_non_swaps(eng_db):
    await _seed(eng_db)
    async with eng_db() as s:
        wallet = (await s.execute(select(em.EngWallet))).scalars().one()
    tx = _tx(mint_pre=None, mint_post=None, sol_pre=5.0, sol_post=4.9)  # SOL transfer
    assert await ingest_signature("sigX", wallet, tx, dex=None) is None
    async with eng_db() as s:
        assert (await s.execute(select(em.EngRide))).scalars().all() == []


async def test_ingest_stores_ride_even_when_token_unresolved(eng_db):
    """A KOL buying a token we haven't tracked still belongs in the global purchase feed —
    stored with the mint as contract and a null price, never dropped."""
    async with eng_db() as s:
        s.add(em.EngWallet(address=OWNER, handle="dani", member_id="1", chain="sol",
                           source="kolscan", confidence="leaderboard", score=100.0))
        await s.commit()
        wallet = (await s.execute(select(em.EngWallet))).scalars().one()

    tx = _tx(mint_pre=0.0, mint_post=1000.0, sol_pre=5.0, sol_post=2.0)
    payload = await ingest_signature("sigNEW", wallet, tx, dex=None)  # no dex -> unresolved
    assert payload is not None
    contract, ride = payload
    assert contract == MINT and ride["price"] is None and ride["volumeUsd"] is None
