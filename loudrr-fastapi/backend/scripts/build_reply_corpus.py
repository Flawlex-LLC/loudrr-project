"""Build the Quick Reply style corpus: real replies by top-scoring crypto KOLs.

Who: a broad pool of crypto-Twitter creators (traders, degens, NFT, DeFi,
memecoin culture), each looked up on Sorsa (the public profile data a
logged-out visitor sees) and ranked by Sorsa score; the top ones are kept.
Walking Sorsa's "top followers" instead drifts to mega-accounts (Elon, Sam
Altman, VCs) whose replies aren't crypto-Twitter's voice, so we rank a pool.

What: their recent replies on X, from the Loudrr gateway ("Tweets & replies"
timeline), each paired with the post it answers, so the generator sees how
top creators REACT to content, not just how they write. Cleaned: leading
@mentions dropped; replies with links, non-English ones, anything over 160
characters, and replies to their own posts (thread continuations) skipped.

Output: app/reply_data/reply_style_corpus.json, studied by scripts/study_reply_corpus.py
and imitated by the reply generator. Re-run to refresh, then commit.

    python -u -m scripts.build_reply_corpus [--kols 50] [--per-kol 40]

Scraping rules: Sorsa is read only through the proxy pool, never from this
machine's IP, only what a logged-out visitor sees, and the client backs off
when Sorsa pushes back.
"""
import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.integrations import sorsa, x_stream
from app.integrations.x_stream import GatewayUnavailable, XStreamClient

POOL = [
    "waleswoosh", "blknoiz06", "cobie", "HsakaTrades", "DegenSpartan", "CryptoKaleo", "inversebrah",
    "notthreadguy", "0xMert_", "MustStopMurad", "zachxbt", "Zeneca", "farokh", "DeeZe", "Pentosh1",
    "CryptoCred", "TheCryptoDog", "rektcapital", "AltcoinGordon", "Ashcryptoreal", "MacnBTC",
    "CryptoWizardd", "sassal0x", "cryptunez", "0xSisyphus", "gainzy222", "TraderSZ", "CL207", "loomdart",
    "Tetranode", "redphonecrypto", "frankdegods", "beaniemaxi", "0xngmi", "pranksy", "rektfencer",
    "CryptoGodJohn", "AviFelman", "KookCapitalLLC", "CryptoHayes", "Route2FI", "DefiIgnas", "milesdeutscher",
    "TheDeFinvestor", "Dynamo_Patrick", "0xRamonos", "TheWhiteWhaleV2", "Tree_of_Alpha", "Cbb0fe",
    "EmperorBTC", "CryptoDonAlt", "IncomeSharks", "CryptoTony__", "Rewkang", "ThinkingUSD", "ledgerstatus",
    "punk9059", "icebergy_", "0xfoobar", "TylerDurden", "shawmakesmagic", "mdudas", "ColdBloodShill",
]
# not crypto-Twitter's voice, however high they score
DENY = {"elonmusk", "sama", "pmarca", "garyvee", "cdixon", "naval", "jack", "saylor", "vitalikbuterin"}
OUT = Path(__file__).resolve().parents[1] / "app" / "reply_data" / "reply_style_corpus.json"

_MENTIONS = re.compile(r"^(?:@\w+[\s,]*)+")
_LINK = re.compile(r"https?://|www\.", re.I)
_LETTER = re.compile(r"[A-Za-z]")


def clean_reply(tweet: dict, handle: str) -> str | None:
    """The reply's own words, or None when it isn't a usable style example.
    The timeline also carries other people's tweets from the conversation, so
    only replies written by `handle` count."""
    if not (tweet.get("isReply") or tweet.get("inReplyToId")):
        return None
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    if str(author.get("userName") or "").lower() != handle.lower():
        return None
    if tweet.get("lang") not in (None, "", "en"):
        return None
    text = str(tweet.get("text") or "")
    if _LINK.search(text):
        return None
    text = " ".join(_MENTIONS.sub("", text.strip()).split())
    if not (2 <= len(text) <= 160) or not _LETTER.search(text):
        return None
    return text


async def rank_pool(client: sorsa.SorsaClient, *, want: int) -> list[dict]:
    """The pool's individuals with a Sorsa score, highest first."""
    found = []
    for handle in POOL:
        if handle.lower() in DENY:
            continue
        account, outcome = await client._lookup_account(handle)
        data = sorsa._score_payload(account, handle) if account else None
        if data:
            category = str(data.get("category") or "").lower()
            if not category or "influenc" in category or "person" in category:
                found.append({"handle": data["screen_name"], "score": data["score"], "category": category})
        else:
            print(f"  @{handle}: {outcome}")
        if sorsa.is_blocked():
            print("Sorsa pushed back; ranking what we have")
            break
    found.sort(key=lambda a: a["score"], reverse=True)
    print(f"scored {len(found)} of {len(POOL)} creators")
    return found[:want]


async def _get(gateway: XStreamClient, path: str, params: dict) -> dict | None:
    for attempt in (1, 2):
        try:
            return await gateway._request("GET", path, params=params)
        except GatewayUnavailable as e:
            if attempt == 2:
                print(f"  {path}: gateway error {str(e)[:100]}")
    return None


async def replies_of(gateway: XStreamClient, handle: str, want: int) -> list[dict]:
    """[{text, id, parent_id, likes}]: the handle's own replies, newest first."""
    out, cursor = [], ""
    for _ in range(3):
        params = {"userName": handle, "includeReplies": "true"}
        if cursor:
            params["cursor"] = cursor
        data = await _get(gateway, "/twitter/user/last_tweets", params)
        if data is None:
            break
        body = data.get("data") if isinstance(data.get("data"), dict) else data
        tweets = [t for t in (body.get("tweets") or []) if isinstance(t, dict)]
        for t in tweets:
            text = clean_reply(t, handle)
            if text:
                out.append({"text": text, "id": str(t.get("id") or ""),
                            "parent_id": str(t.get("inReplyToId") or ""),
                            "likes": int(t.get("likeCount") or 0)})
        cursor = str(data.get("next_cursor") or body.get("next_cursor") or "")
        if len(out) >= want or not tweets or not cursor:
            break
    return out[:want]


async def parents_of(gateway: XStreamClient, ids: list[str]) -> dict[str, dict]:
    """tweet id -> {text, author} for the posts the replies answer."""
    out: dict[str, dict] = {}
    for start in range(0, len(ids), 20):
        data = await _get(gateway, "/twitter/tweets", {"tweet_ids": ",".join(ids[start:start + 20])})
        for t in (data or {}).get("tweets") or []:
            if isinstance(t, dict) and t.get("id"):
                author = t.get("author") if isinstance(t.get("author"), dict) else {}
                out[str(t["id"])] = {"text": " ".join(str(t.get("text") or "").split()),
                                     "author": str(author.get("userName") or "")}
    return out


async def main(kols: int, per_kol: int) -> None:
    x_stream._TIMEOUT = httpx.Timeout(60.0)  # batch lookups outlast the app's 10s
    top = await rank_pool(sorsa.SorsaClient(), want=kols)
    gateway = XStreamClient()
    replies, seen = [], set()
    for kol in top:
        got = await replies_of(gateway, kol["handle"], per_kol)
        new = [r for r in got if r["text"].lower() not in seen]
        seen.update(r["text"].lower() for r in new)
        replies.extend({**r, "author": kol["handle"], "author_score": round(kol["score"])} for r in new)
        print(f"  @{kol['handle']:<18} score {kol['score']:>7.0f}  {len(new)} replies")

    parents = await parents_of(gateway, sorted({r["parent_id"] for r in replies if r["parent_id"]}))
    kept = []
    for r in replies:
        parent = parents.get(r["parent_id"])
        if parent and parent["author"].lower() == r["author"].lower():
            continue  # a thread continuation, not a reaction to someone else
        kept.append({"text": r["text"], "author": r["author"], "author_score": r["author_score"],
                     "likes": r["likes"], "parent": parent})
    print(f"parents found for {sum(1 for r in kept if r['parent'])}/{len(kept)} replies; "
          f"{len(replies) - len(kept)} self-thread replies dropped")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "replies by top Sorsa-scored crypto-Twitter creators, via the Loudrr X gateway",
        "kols": top,
        "replies": kept,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(kept)} replies from {len(top)} KOLs -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kols", type=int, default=50)
    ap.add_argument("--per-kol", type=int, default=40)
    a = ap.parse_args()
    asyncio.run(main(a.kols, a.per_kol))
