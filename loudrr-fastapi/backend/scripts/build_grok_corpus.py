"""Build the @grok question corpus: what people ask @grok under posts, and
which questions performed.

It starts from Grok itself: Grok's replies in crypto conversations, from X's
"Top" search so they're the ones that drew attention. From each answer it walks
up to the question that tagged @grok, and from the question to the post it was
under. A question is scored by its own likes and views plus the attention
Grok's public answer got.

Output: app/reply_data/grok_questions.json, best first. scripts/study_reply_corpus.py
studies it, and the reply generator shows the best ones as examples when a
creator post has something worth asking about.

    python -u -m scripts.build_grok_corpus [--pages 5]
"""
import argparse
import asyncio
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.integrations import x_stream
from app.integrations.x_stream import GatewayUnavailable, XStreamClient

QUERIES = [
    "from:grok filter:replies lang:en (crypto OR bitcoin OR btc OR ethereum OR eth OR solana OR memecoin OR token)",
    "from:grok filter:replies lang:en (airdrop OR defi OR tokenomics OR \"market cap\" OR presale OR staking OR rug)",
    "from:grok filter:replies lang:en (chart OR pump OR ath OR \"all time high\" OR etf OR halving OR altcoin)",
]
OUT = Path(__file__).resolve().parents[1] / "app" / "reply_data" / "grok_questions.json"

_LEADING = re.compile(r"^(?:@\w+[\s,]*)+")
_LINK = re.compile(r"https?://|www\.", re.I)


async def _get(gateway: XStreamClient, path: str, params: dict) -> dict | None:
    for attempt in (1, 2):
        try:
            return await gateway._request("GET", path, params=params)
        except GatewayUnavailable as e:
            if attempt == 2:
                print(f"  {path}: gateway error {str(e)[:100]}")
    return None


async def tweets_by_id(gateway: XStreamClient, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for start in range(0, len(ids), 20):
        data = await _get(gateway, "/twitter/tweets", {"tweet_ids": ",".join(ids[start:start + 20])})
        for t in (data or {}).get("tweets") or []:
            if isinstance(t, dict) and t.get("id"):
                out[str(t["id"])] = t
    return out


def question_text(tweet: dict) -> str | None:
    """'@grok <question>', or None when it isn't a clean question to Grok."""
    text = " ".join(str(tweet.get("text") or "").split())
    if "@grok" not in text.lower() or _LINK.search(text):
        return None
    rest = _LEADING.sub("", text).strip()  # the other leading @handles go; @grok is re-added
    if "@grok" in rest.lower():
        rest = re.sub(r"@grok\b", "", rest, flags=re.I).strip(" ,")
    if not (4 <= len(rest) <= 150) or not re.search(r"[A-Za-z]", rest):
        return None
    return f"@grok {rest}"


def _author(t: dict) -> str:
    return str((t.get("author") or {}).get("userName") or "")


def _n(t: dict, key: str) -> int:
    try:
        return int(t.get(key) or 0)
    except (TypeError, ValueError):
        return 0


async def main(pages: int) -> None:
    x_stream._TIMEOUT = httpx.Timeout(60.0)
    gateway = XStreamClient()
    answers: dict[str, dict] = {}
    for query in QUERIES:
        cursor = ""
        for _ in range(pages):
            params = {"query": query, "queryType": "Top"}
            if cursor:
                params["cursor"] = cursor
            data = await _get(gateway, "/twitter/tweet/advanced_search", params)
            if not data:
                break
            batch = [t for t in data.get("tweets") or [] if isinstance(t, dict)]
            for t in batch:
                if _author(t).lower() == "grok" and t.get("inReplyToId"):
                    answers.setdefault(str(t["inReplyToId"]), t)
            cursor = str(data.get("next_cursor") or "")
            if not batch or not data.get("has_next_page") or not cursor:
                break
        print(f"  {query[:60]}…: {len(answers)} Grok answers so far")

    questions = await tweets_by_id(gateway, sorted(answers))
    kept = {}
    for qid, q in questions.items():
        text = question_text(q)
        if text and _author(q).lower() != "grok" and q.get("inReplyToId") and q.get("lang") in (None, "", "en"):
            kept[qid] = (q, text)
    parents = await tweets_by_id(gateway, sorted({str(q["inReplyToId"]) for q, _ in kept.values()}))

    rows = []
    for qid, (q, text) in kept.items():
        parent = parents.get(str(q["inReplyToId"]))
        if not parent:
            continue
        answer = answers[qid]
        score = (2 * math.log1p(_n(q, "likeCount")) + math.log1p(_n(q, "viewCount"))
                 + math.log1p(_n(answer, "likeCount")) + 0.5 * math.log1p(_n(answer, "viewCount")))
        media = (parent.get("extendedEntities") or {}).get("media") or []
        rows.append({
            "question": text, "author": _author(q),
            "likes": _n(q, "likeCount"), "views": _n(q, "viewCount"),
            "answer_likes": _n(answer, "likeCount"), "answer_views": _n(answer, "viewCount"),
            "score": round(score, 2),
            "parent": {"text": " ".join(str(parent.get("text") or "").split()), "author": _author(parent),
                       "has_media": bool(media)},
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "questions tagging @grok under crypto posts, found from Grok's most-engaged answers",
        "questions": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} @grok questions (from {len(answers)} answers) -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5)
    asyncio.run(main(ap.parse_args().pages))
