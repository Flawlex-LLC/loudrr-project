"""TDD spec — KOL wallet vault (the mapping layer that powers KOL Rides).

Sources, by confidence tier:
  * "self"        — the KOL posted the address in their OWN tweet (our bronze; highest trust),
  * "curated"     — labeled datasets (Arkham/Dune/Vybe imports),
  * "leaderboard" — kolscan/GMGN-derived community dumps (kol-quest, BULLX).

Invariants:
  * importer accepts messy external shapes (handle/twitter/name keys vary) and normalizes,
  * handles are matched to smart_set members case-insensitively; unmatched rows are KEPT
    (member_id NULL) so later-added members auto-link,
  * (address, handle) is unique — re-imports never duplicate; higher tiers upgrade confidence,
  * self-posted extraction drops CA contamination: an address that is a known called token
    or appears in many different members' tweets is a shilled contract, not a wallet.
"""
from sqlalchemy import select

from app.db.models import SmartSetMember
from app.engagement import models as em
from app.engagement.wallets import extract_self_posted, import_external, import_live_scrapes

SOL_W = "DfMxre4cKmvogbLrPigxmibVTTQDuzjdXojWzjCXXhzj"   # wallet-looking base58
SOL_CA = "9ybu4ArAY9iyGpjh99eYSmjn5tw3Jvyo9aeRFQqy6Ezh"
EVM_W = "0x28c6c06298d514db089934071355e5743bf21d60"


class FakeFetcher:
    def __init__(self, payloads):
        self.payloads = payloads  # url -> parsed JSON

    async def get_json(self, url):
        return self.payloads[url]


async def _seed_members(SessionLocal, members):
    async with SessionLocal() as s:
        s.add_all(members)
        await s.commit()


async def test_import_normalizes_shapes_and_matches_members(eng_db):
    await _seed_members(eng_db, [SmartSetMember(user_id="1", username="Ansem", score=5.0)])
    fetcher = FakeFetcher({
        "https://src/a.json": [                       # kolscan-leaderboard shape (real)
            {"twitter": "https://x.com/Ansem", "wallet_address": SOL_W, "name": "Ansem"},
            {"twitter": "unknownguy", "wallet_address": EVM_W},
        ],
        "https://src/b.json": {"wallets": [           # BULLX-ish nested shape
            {"handle": "ANSEM", "address": SOL_W},    # duplicate claim, different case
        ]},
    })
    stats = await import_external(
        sources=[("kolquest", "https://src/a.json", "leaderboard"),
                 ("bullx", "https://src/b.json", "leaderboard")],
        fetcher=fetcher)
    assert stats["imported"] == 2                     # dup (address, handle) collapsed
    async with eng_db() as s:
        rows = (await s.execute(select(em.EngWallet))).scalars().all()
        by_addr = {w.address: w for w in rows}
        assert by_addr[SOL_W].handle == "ansem"
        assert by_addr[SOL_W].member_id == "1"        # matched case-insensitively
        assert by_addr[SOL_W].chain == "sol"
        assert by_addr[EVM_W].member_id is None       # unmatched kept for later
        assert by_addr[EVM_W].chain == "evm"


async def test_live_scrapes_convert_and_store(eng_db, monkeypatch):
    """import_live_scrapes turns the live kolscan/GMGN scrape rows into vault claims, matching
    members and normalizing EVM to lowercase. Scrapers are monkeypatched — no network."""
    await _seed_members(eng_db, [SmartSetMember(user_id="7", username="Cupsey", score=9.0)])
    monkeypatch.setattr("app.engagement.wallets.scrape_kolscan_live",
                        lambda proxies=None: [{"address": SOL_W, "handle": "Cupsey", "name": "Cupsey"}])
    # realistic GMGN shape: 0x-prefixed, mixed-case hex body -> must be lowercased on store
    evm_mixed = "0x28C6C06298D514db089934071355E5743BF21D60"
    monkeypatch.setattr("app.engagement.wallets.scrape_gmgn_wallets",
                        lambda proxies=None: [{"handle": "whale", "address": evm_mixed,
                                               "chain": "evm", "source": "gmgn"}])
    # stub the token_traders scrape too, else import_live_scrapes hits the real GMGN network
    monkeypatch.setattr("app.engagement.wallets.scrape_gmgn_token_traders",
                        lambda proxies=None, extra_tokens=None: [])
    stats = await import_live_scrapes()
    assert stats == {"inserted": 2, "kolscan": 1, "gmgn": 1, "gmgn_traders": 0}
    async with eng_db() as s:
        by_addr = {w.address: w for w in (await s.execute(select(em.EngWallet))).scalars().all()}
        assert by_addr[SOL_W].handle == "cupsey" and by_addr[SOL_W].member_id == "7"
        assert by_addr[SOL_W].source == "kolscan" and by_addr[SOL_W].chain == "sol"
        # EVM address normalized to lowercase so it matches calls.py / eng_call.contract
        assert EVM_W in by_addr and by_addr[EVM_W].chain == "evm"


async def test_live_scrapes_survive_one_dead_source(eng_db, monkeypatch):
    """A scraper degrading to [] must not block the others — one dead source, still a load."""
    monkeypatch.setattr("app.engagement.wallets.scrape_kolscan_live", lambda proxies=None: [])
    monkeypatch.setattr("app.engagement.wallets.scrape_gmgn_wallets",
                        lambda proxies=None: [{"handle": "x", "address": SOL_W, "chain": "sol"}])
    monkeypatch.setattr("app.engagement.wallets.scrape_gmgn_token_traders",
                        lambda proxies=None, extra_tokens=None: [])
    stats = await import_live_scrapes()
    assert stats["inserted"] == 1 and stats["kolscan"] == 0


async def test_token_traders_wallets_are_stored(eng_db, monkeypatch):
    """The per-token top-trader scrape feeds the same vault; its wallets land with the
    gmgn_traders source and trust the address shape for chain (sol vs evm)."""
    await _seed_members(eng_db, [SmartSetMember(user_id="3", username="degenz", score=4.0)])
    monkeypatch.setattr("app.engagement.wallets.scrape_kolscan_live", lambda proxies=None: [])
    monkeypatch.setattr("app.engagement.wallets.scrape_gmgn_wallets", lambda proxies=None: [])
    monkeypatch.setattr(
        "app.engagement.wallets.scrape_gmgn_token_traders",
        lambda proxies=None, extra_tokens=None: [
            {"handle": "degenz", "address": SOL_W, "chain": "sol", "source": "gmgn_traders"},
            {"handle": "whale", "address": EVM_W, "chain": "evm", "source": "gmgn_traders"},
        ])
    stats = await import_live_scrapes()
    assert stats["gmgn_traders"] == 2 and stats["inserted"] == 2
    async with eng_db() as s:
        by = {w.address: w for w in (await s.execute(select(em.EngWallet))).scalars().all()}
        assert by[SOL_W].source == "gmgn_traders" and by[SOL_W].member_id == "3"
        assert by[EVM_W].chain == "evm"


async def test_reimport_is_idempotent(eng_db):
    fetcher = FakeFetcher({"https://src/a.json": [{"twitter": "x", "wallet": SOL_W}]})
    src = [("kolquest", "https://src/a.json", "leaderboard")]
    await import_external(sources=src, fetcher=fetcher)
    stats = await import_external(sources=src, fetcher=fetcher)
    assert stats["imported"] == 0
    async with eng_db() as s:
        assert len((await s.execute(select(em.EngWallet))).scalars().all()) == 1


async def test_self_posted_extraction_with_contamination_filter(eng_db):
    await _seed_members(eng_db, [
        SmartSetMember(user_id="10", username="kola", score=1.0),
        SmartSetMember(user_id="20", username="kolb", score=1.0),
        SmartSetMember(user_id="30", username="kolc", score=1.0),
    ])
    def tweet(tid, member, text):
        return em.EngTweetRaw(tweet_id=tid, member_id=member, created_at=None, text=text,
                              raw={"id": tid, "text": text,
                                   "author": {"id": member, "userName": f"u{member}"}})
    async with eng_db() as s:
        # genuine self-post: wallet + context words, by ONE member
        s.add(tweet("w1", "10", f"send tips to my wallet {SOL_W} ty frens"))
        # contamination: same address shilled by THREE different members = a token CA
        for i, m in enumerate(("10", "20", "30")):
            s.add(tweet(f"ca{i}", m, f"ape {SOL_CA} now"))
        # known called token: address already in eng_call -> never a wallet
        s.add(em.EngCall(tweet_id="c1", member_id="10", ticker=None, contract=SOL_CA,
                         chain="sol", confidence="contract", token_key=SOL_CA,
                         ts=None, day=__import__("datetime").date.today()))
        await s.commit()

    stats = await extract_self_posted()
    assert stats["wallets_found"] == 1
    async with eng_db() as s:
        w = (await s.execute(select(em.EngWallet))).scalars().one()
        assert (w.address, w.member_id, w.confidence, w.source) == (SOL_W, "10", "self", "tweet")


async def test_no_context_words_means_no_wallet(eng_db):
    await _seed_members(eng_db, [SmartSetMember(user_id="10", username="kola", score=1.0)])
    async with eng_db() as s:
        s.add(em.EngTweetRaw(tweet_id="x1", member_id="10", created_at=None,
                             text=f"this is the next runner {SOL_W}",
                             raw={"id": "x1", "text": f"this is the next runner {SOL_W}",
                                  "author": {"id": "10", "userName": "kola"}}))
        await s.commit()
    stats = await extract_self_posted()
    assert stats["wallets_found"] == 0                # shill without self-context = a CA
