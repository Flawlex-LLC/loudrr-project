"""Build the crowd corpus: how regular crypto-Twitter users reply under top
creators' posts.

Top creators write fairly clean English (only ~1% of their replies use "u",
almost none "ik" or "jk"). Loudrr's users are the regular accounts replying
UNDER those creators, and they type like people on phones. This samples them:
each top creator's most-liked recent posts, and the replies X ranks top
("Relevance") under each, keeping replies from other accounts that got at
least one like (so no bots or spam).

Output: app/reply_data/crowd_replies.json. scripts/study_reply_corpus.py
measures its shortforms (u, ik, idk, ngl, dont...) and the reply generator
types like it (services/quick_replies.py).

    python -u -m scripts.build_crowd_corpus [--posts-per-kol 2]
"""
import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.integrations import x_stream
from app.integrations.x_stream import GatewayUnavailable, XStreamClient

DATA = Path(__file__).resolve().parents[1] / "app" / "reply_data"
OUT = DATA / "crowd_replies.json"

_MENTIONS = re.compile(r"^(?:@\w+[\s,]*)+")
_LINK = re.compile(r"https?://|www\.", re.I)


async def _get(gateway: XStreamClient, path: str, params: dict) -> dict | None:
    for attempt in (1, 2):
        try:
            return await gateway._request("GET", path, params=params)
        except GatewayUnavailable as e:
            if attempt == 2:
                print(f"  {path}: gateway error {str(e)[:90]}")
    return None


def _author(t: dict) -> str:
    return str((t.get("author") or {}).get("userName") or "")


def _likes(t: dict) -> int:
    try:
        return int(t.get("likeCount") or 0)
    except (TypeError, ValueError):
        return 0


def clean(tweet: dict, post_author: str) -> str | None:
    if _author(tweet).lower() in ("", post_author.lower(), "grok"):
        return None
    if tweet.get("lang") not in (None, "", "en"):
        return None
    text = " ".join(str(tweet.get("text") or "").split())
    if _LINK.search(text) or "@grok" in text.lower():
        return None
    text = _MENTIONS.sub("", text).strip()
    if not (2 <= len(text) <= 160) or not re.search(r"[A-Za-z]", text):
        return None
    return text


async def main(posts_per_kol: int) -> None:
    x_stream._TIMEOUT = httpx.Timeout(60.0)
    kols = json.loads((DATA / "reply_style_corpus.json").read_text(encoding="utf-8"))["kols"]
    gateway = XStreamClient()
    rows, seen, posts_used = [], set(), 0
    for kol in kols:
        timeline = await _get(gateway, "/twitter/user/last_tweets",
                              {"userName": kol["handle"], "includeReplies": "false"})
        body = (timeline or {}).get("data") if isinstance((timeline or {}).get("data"), dict) else (timeline or {})
        originals = [t for t in (body.get("tweets") or []) if isinstance(t, dict)
                     and not (t.get("isReply") or t.get("inReplyToId") or t.get("retweeted_tweet"))
                     and _author(t).lower() == kol["handle"].lower()]
        originals.sort(key=_likes, reverse=True)
        got = 0
        for post in originals[:posts_per_kol]:
            data = await _get(gateway, "/twitter/tweet/replies/v2", {"tweetId": str(post["id"]), "queryType": "Relevance"})
            posts_used += 1
            parent = {"text": " ".join(str(post.get("text") or "").split()), "author": kol["handle"]}
            for r in (data or {}).get("tweets") or (data or {}).get("replies") or []:
                if not isinstance(r, dict) or _likes(r) < 1:
                    continue
                text = clean(r, kol["handle"])
                if text and text.lower() not in seen:
                    seen.add(text.lower())
                    rows.append({"text": text, "author": _author(r), "likes": _likes(r), "parent": parent})
                    got += 1
        print(f"  under @{kol['handle']:<18} {got} replies")
    rows.sort(key=lambda r: r["likes"], reverse=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "top-ranked replies (1+ likes) by regular accounts under top Sorsa-scored creators' posts",
        "posts": posts_used,
        "replies": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} crowd replies under {posts_used} creator posts -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts-per-kol", type=int, default=2)
    asyncio.run(main(ap.parse_args().posts_per_kol))
