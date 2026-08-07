"""One-off report: every wallet-vault KOL -> CSV with X profile, Loudrr score/tier,
bio links, Telegram community detection + member counts.

    python -m scripts.wallet_kols_report
    -> data/exports/wallet_kols_report.csv

Data sources: eng_wallet (vault) + ranked_accounts (score) + gateway /user/info (live
profile: followers/bio/link — ~$0.09 for ~480 lookups) + public t.me preview pages
(member counts, no auth). Paced politely (gateway 15/min — the server crawl shares the key).
ALL t.me / t.co web traffic goes through the rotating Webshare proxies (never the home IP).
"""
from __future__ import annotations

import asyncio
import csv
import json
import random
import re
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, ".")
from app.clients.twitterapi import TwitterAPIClient  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.models import RankedAccount  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engagement.models import EngWallet  # noqa: E402


def _proxies() -> list[str]:
    """Webshare rotating proxies ('host:port:user:pass' lines) — same pool as the Kaito
    scraper. Hard-fails if none found: t.me must NEVER be hit from the home IP."""
    out: list[str] = []
    src = Path(settings.kaito_proxy_file)
    lines: list[str] = []
    if src.exists():
        lines += [ln.strip() for ln in src.read_text().splitlines() if ln.strip()]
    if settings.kaito_proxies:
        lines += [ln.strip() for ln in re.split(r"[\n,;]+", settings.kaito_proxies) if ln.strip()]
    for ln in lines:
        if ln.startswith("http"):
            out.append(ln)
        else:
            parts = ln.split(":")
            if len(parts) == 4:
                h, p, u, pw = parts
                out.append(f"http://{u}:{pw}@{h}:{p}")
    if not out:
        sys.exit("No Webshare proxies found — refusing to hit t.me from the home IP.")
    return out


PROXIES = _proxies()


def _proxied_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
                             proxy=random.choice(PROXIES), timeout=20)

OUT = "data/exports/wallet_kols_report.csv"
TIERS = [("Megaphone", 4500), ("Amplifier", 3400), ("Signal", 2300), ("Echo", 1200), ("Whisper", 0)]
_URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.I)
_TG_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(\+?[A-Za-z0-9_/-]+)", re.I)
_MEMBERS_RE = re.compile(r"([\d\s ,.]+)\s*(members|subscribers)", re.I)


def tier_of(score) -> str:
    if score is None:
        return ""
    for name, mn in TIERS:
        if score >= mn:
            return name
    return "Whisper"


def expanded_urls(info: dict) -> list[str]:
    """Real destination URLs from the profile — the gateway payload already resolves each
    t.co to its expanded_url under entities.{description,url}.urls, so NO t.co-follow needed."""
    ent = info.get("entities")
    if isinstance(ent, str):
        try:
            ent = json.loads(ent)
        except Exception:
            ent = {}
    ent = ent or {}
    out: list[str] = []
    for grp in ("description", "url"):
        for u in ((ent.get(grp) or {}).get("urls") or []):
            eu = u.get("expanded_url") or u.get("url")
            if eu:
                out.append(eu)
    return out


async def tg_members(link: str) -> str:
    """Fetch the public t.me preview via ROTATING proxies (never the home IP); retry across
    a few proxies since some are stale. Public groups/channels expose a member/subscriber count."""
    url = link if link.startswith("http") else f"https://{link}"
    url = url.replace("http://", "https://")
    for _ in range(4):
        try:
            async with _proxied_client() as c:
                r = await c.get(url, follow_redirects=True)
                m = _MEMBERS_RE.search(r.text)
                if m:
                    n = re.sub(r"[^\d]", "", m.group(1))
                    if n:
                        return n + (" subs" if "subscriber" in m.group(2).lower() else "")
                return ""  # reached page, just no public count (invite-only / private)
        except Exception:
            continue
    return ""


async def main() -> None:
    async with SessionLocal() as s:
        wallets = (await s.execute(select(EngWallet))).scalars().all()
        ranked = {a.username: a for a in (await s.execute(select(RankedAccount))).scalars().all()}

    by_handle: dict[str | None, list[EngWallet]] = {}
    for w in wallets:
        by_handle.setdefault(w.handle, []).append(w)
    handles = sorted(h for h in by_handle if h)

    # SPEED RUN (founder call): 4 req/s against our own gateway (upstream tolerates 200/s;
    # 429s auto-retry honoring Retry-After) + 30-wide parallelism for proxied web fetches.
    client = TwitterAPIClient(qps=4)
    sem = asyncio.Semaphore(30)
    done = {"n": 0}

    async def one(h: str) -> dict:
        async with sem:
            ws = by_handle[h]
            a = ranked.get(h)
            info: dict = {}
            try:
                info = await client.get_user_info(h) or {}
                info = info.get("data") or info
            except Exception as e:  # noqa: BLE001
                print(f"  {h}: profile lookup failed ({type(e).__name__})")
            bio = (info.get("description") or (a.bio if a else "") or "").replace("\n", " ")
            # real links come straight from entities (already expanded) — no t.co follow
            expanded = [u for u in expanded_urls(info) if "x.com/" not in u and "twitter.com/" not in u]
            tg_links = [u for u in expanded if _TG_RE.search(u)]
            tg_link = tg_links[0] if tg_links else ""
            members = await tg_members(tg_link) if tg_link else ""
            done["n"] += 1
            if done["n"] % 25 == 0:
                print(f"{done['n']}/{len(handles)} handles (spent ${client.usd_spent:.2f})")
            return {
                "x_handle": h,
                "name": info.get("name") or (a.name if a else ""),
                "wallets": "; ".join(w.address for w in ws),
                "chains": "; ".join(sorted({w.chain for w in ws})),
                "wallet_sources": "; ".join(sorted({w.source for w in ws})),
                "loudrr_score": a.score if a else "",
                "tier": tier_of(a.score if a else None),
                "board_rank": a.rank if a else "",
                "elite_followers": a.elite_followers if a else "",
                "followers": info.get("followers") or (a.followers if a else ""),
                "following": info.get("following") or (a.following if a else ""),
                "total_posts": info.get("statusesCount") or "",
                "media_posts": info.get("mediaCount") or "",
                "likes_given": info.get("favouritesCount") or "",
                "location": (info.get("location") or "").replace("\n", " ")[:80],
                "account_created": str(info.get("createdAt") or "")[:30],
                "verified": info.get("isBlueVerified") or info.get("verified") or (a.verified if a else ""),
                "bio": bio[:400],
                "bio_links": "; ".join(sorted(expanded))[:500],
                "has_tg_community": "yes" if tg_link else "no",
                "tg_link": tg_link,
                "tg_members": members,
                "in_top20k_crawl": "yes" if a else "no",
            }

    rows = list(await asyncio.gather(*(one(h) for h in handles)))

    # identity-less wallets at the end (no X known)
    for w in by_handle.get(None, []):
        rows.append({"x_handle": "", "name": "", "wallets": w.address, "chains": w.chain,
                     "wallet_sources": w.source, "loudrr_score": "", "tier": "", "board_rank": "",
                     "elite_followers": "", "followers": "", "following": "", "total_posts": "",
                     "media_posts": "", "likes_given": "", "location": "", "account_created": "",
                     "verified": "", "bio": "", "bio_links": "", "has_tg_community": "",
                     "tg_link": "", "tg_members": "", "in_top20k_crawl": "no"})

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    print(f"DONE -> {OUT} ({len(rows)} rows, gateway spend ${client.usd_spent:.2f})")


if __name__ == "__main__":
    asyncio.run(main())
