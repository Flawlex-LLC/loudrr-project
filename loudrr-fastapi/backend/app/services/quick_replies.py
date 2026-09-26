"""Quick Reply: short, human reply drafts for Engage posts.

The voice comes from a study of how top Sorsa-scored crypto KOLs really reply:
scripts/build_reply_corpus.py collects their replies paired with the post
each one answers, scripts/study_reply_corpus.py measures them (length,
casing, punctuation, reaction mix). The generator shows the model real
post -> reply pairs close to the post's topic and holds it to those numbers,
then cleans what comes back.

Drafts are stored as the model wrote them; each viewer's copy is then typed
the way regular crypto-Twitter users type under top creators' posts (the
"crowd" sample, scripts/build_crowd_corpus.py): "u" for "you", "dont",
"ik", "idk", "rn", lowercase starts, each at the measured rate, stable per
viewer. So no two people's copies read alike, and none reads like AI.

One OpenRouter call per post writes several drafts, each a different kind of
reaction, so it isn't a yes-man. On creator posts one draft may ask @grok a
question, the way people do on X, but only when the post has something to
check; the examples are real @grok questions that performed
(scripts/build_grok_corpus.py). Every viewer is shown their own draft
(stable per user): a raid shouldn't paste the same line fifty times, because
X hides duplicate replies as spam and a hidden reply fails verification.

Sponsored posts, which clients pay for, use the better model list; creator
posts use the free one. Each list is tried in order until a model answers.
Drafts are written in the worker when a post is created, never on the tap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import uuid
from functools import lru_cache
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.time_utils import utcnow
from app.models.post import Post

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[1] / "reply_data"
DRAFTS_PER_POST = 6
MIN_DRAFTS = 3          # fewer usable drafts than this = try the next model
EXAMPLES = 24           # real post -> reply pairs shown to the model
TIMEOUT = httpx.Timeout(45.0, connect=10.0)

# What each draft should do. Sponsored posts never get pushback (clients pay
# for them); creator posts may get one light "yeah but".
REACTIONS = {
    "take": "react to one specific thing in the post with your own observation, in your own words",
    "question": "ask one short, genuine question about it, curious not skeptical",
    "addon": "add a related detail, fact or quick experience of your own that builds on the post",
    "opinion": "your honest opinion in a few words, good or bad, like you'd tell a friend (not praise)",
    "banter": "a light joke or playful jab about the post, friendly",
    "hype": "short, genuine excitement like a friend would say it, not a brand",
    "nuance": "a light 'yeah but' or a different angle, curious not rude",
    "grok": "only if the post has something checkable (a claim, a number, a chart, a term or news): tag @grok"
            " with one short, sharp question about it, like the @grok examples. otherwise write a take",
}
# Sponsored posts are clients' posts: every account the admin adds on the
# Sponsors page, whether a project, a founder or a creator. Positive, neutral or
# curious only, never negative (a negative reply under a client's post loses
# the client): no pushback, no blunt opinion, no @grok ("@grok is this
# legit?"), humor only on their side.
SPONSORED_REACTIONS = {
    "take": "a positive or neutral observation about one specific thing in it",
    "question": "one short, curious question about what they announced, never doubting it",
    "banter": "light humor that's on their side, never at their expense",
}
# Community posts (creators on Loudrr engaging each other): positive, curious
# or mildly skeptical, never harsh, mocking or dismissive toward the person.
COMMUNITY_REACTIONS = {
    "opinion": "your honest opinion in a few words, positive or mildly skeptical, never harsh",
    "banter": "a light, friendly joke, never mocking them",
}
# Mostly takes, as in the study: 73-80% of real top replies are takes, while
# questions, hype and plain agreement are ~3-6% each.
PLANS = {
    "sponsored": ["take", "addon", "question", "take", "hype", "banter"],
    "creator": ["take", "addon", "opinion", "nuance", "banter", "grok"],
}
# Harsh, mocking or dismissive: dropped from every draft, community ones too.
_HARSH = re.compile(
    r"\b(scam\w*|rug\w*|ponzi|trash|garbage|ngmi|cope|clown\w*|idiot\w*|stupid|dumb|delusional|"
    r"shut up|(?:nobody|no one|who) asked|l take|cringe|grifter\w*|larp\w*)\b",
    re.I,
)
# Clients' drafts with any of these are dropped too, whatever the model meant.
_NEGATIVE = re.compile(
    r"\b(lame|mid|meh|boring|trash|garbage|scam\w*|rug\w*|ponzi|overrated|overhyped|disappoint\w*|"
    r"consolation|not worth|worst|useless|pointless|cringe|sus|shady|fake|cope|ngmi|bearish|fud|"
    r"exit liquidity|dumping|dump it|red flag|(?:nobody|no one|who) asked|yikes|smh|ugh)\b",
    re.I,
)
GROK_EXAMPLES = 6

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF⭐⭕]")
_MENTIONS = re.compile(r"^(?:@\w+[\s,]*)+")
_GROK = re.compile(r"@grok\b", re.I)
_DASHES = re.compile(r"\s*[—–]\s*|\s+-\s+")
_BANNED = re.compile(r"https?://|www\.|#\w")
# phrases that read as a bot or a brand, not a person
_AI_TELLS = re.compile(
    r"\b(great (point|post|insight|thread|take)|love this|couldn'?t agree more|well said|thanks for sharing|"
    r"game[- ]?changer|exciting (times|news|stuff)|incredible|remarkable|insightful|kudos|resonates?|delve|"
    r"absolutely|truly|indeed|spot on|this is huge|fantastic|as an ai|keep up the (great|good) work|"
    r"what a (great|fantastic)|so (true|real)!|interesting perspective|nailed it|valuable)\b",
    re.I,
)
_PROFANITY = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|cunt|retard\w*|nigg\w*|fag\w*)\b", re.I)
_WORD = re.compile(r"[a-z0-9$]{3,}")
_STOP = frozenset(
    "the and for that this with you your are was have has not but just what they from will about all can its"
    " out get got been more when who how one now new like our their them than then there here some into".split()
)


# ---- data from the study ----
@lru_cache(maxsize=1)
def corpus() -> list[dict]:
    """Real replies with the post they answer ({text, author, likes, parent})."""
    path = DATA / "reply_style_corpus.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("replies") or []
    return [r for r in rows if isinstance(r, dict) and r.get("text") and r.get("parent")]


@lru_cache(maxsize=1)
def grok_questions() -> list[dict]:
    """Real '@grok ...' questions with the post they were under, best first."""
    path = DATA / "grok_questions.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("questions") or []
    return [r for r in rows if isinstance(r, dict) and r.get("question") and r.get("parent")
            and usable_grok_question(r["question"])]


# @grok asks that aren't a question a person would type under a post: image
# and meme generation, "talk like", roasts, or dragging other accounts in
_GROK_NOISE = re.compile(
    r"\b(pic|picture|image|photo|draw|generate|imagine|render|talk like|roast|poem|song|rap|meme of|make (a|an|me))\b|@(?!grok\b)\w",
    re.I,
)


def usable_grok_question(text: str) -> bool:
    return not _GROK_NOISE.search(text) and len(text) <= 110


@lru_cache(maxsize=1)
def crowd() -> list[dict]:
    """Replies by regular users under top creators' posts ({text, likes, parent})."""
    path = DATA / "crowd_replies.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("replies") or []
    return [r for r in rows if isinstance(r, dict) and r.get("text") and r.get("parent")]


@lru_cache(maxsize=1)
def study() -> dict:
    """Everything scripts/study_reply_corpus.py measured."""
    path = DATA / "reply_style_profile.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def profile() -> dict:
    """Top creators' numbers, for their most-liked replies (falls back to all)."""
    data = study()
    return data.get("most_liked") or data.get("all") or {}


def crowd_profile() -> dict:
    """How regular users type: {"most_liked": {...}, "shortforms": {"rates", "extras"}}."""
    return study().get("crowd") or {}


def max_chars() -> int:
    """Upper bound for a draft: where 75% of the most-liked replies stop."""
    p75 = (profile().get("chars") or {}).get("p75") or 80
    return max(50, min(int(p75), 110))


# ---- prompt ----
def _keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def pick_examples(post_text: str, *, k: int = EXAMPLES, sponsored: bool, rng: random.Random) -> list[dict]:
    """Half from regular users (how people type), half from top creators (what
    they say); within each, posts that share words with this one first, then
    well-liked replies, one or two per author."""
    people = _pick(crowd(), post_text, k=k // 2, sponsored=sponsored, rng=rng)
    creators = _pick(corpus(), post_text, k=k - len(people), sponsored=sponsored, rng=rng)
    picked = people + creators
    rng.shuffle(picked)
    return picked


def _pick(rows, post_text: str, *, k: int, sponsored: bool, rng: random.Random) -> list[dict]:
    rows = [r for r in rows if not (sponsored and _PROFANITY.search(r["text"]))]
    if not rows or k <= 0:
        return []
    words = _keywords(post_text)

    def weight(r):
        overlap = len(words & _keywords(r["parent"].get("text", ""))) if words else 0
        return overlap * 3 + math.log1p(r.get("likes") or 0) + rng.random() * 2

    picked, per_author = [], {}
    for r in sorted(rows, key=weight, reverse=True):
        if per_author.get(r["author"], 0) >= 2:
            continue
        picked.append(r)
        per_author[r["author"]] = per_author.get(r["author"], 0) + 1
        if len(picked) >= k:
            break
    return picked


def pick_grok_examples(post_text: str, *, k: int = GROK_EXAMPLES) -> list[dict]:
    """The best-performing @grok questions, nudged toward this post's topic."""
    words = _keywords(post_text)
    rows = grok_questions()
    ranked = sorted(
        rows, key=lambda r: (len(words & _keywords(r["parent"].get("text", ""))), r.get("score", 0)), reverse=True,
    )
    return ranked[:k]


def _style_rules() -> str:
    p = profile()
    people = crowd_profile()
    lower = (people.get("most_liked") or {}).get("starts_lowercase_pct") or p.get("starts_lowercase_pct")
    extras = (people.get("shortforms") or {}).get("extras") or {}
    common = ", ".join(f"{w} ({v}%)" for w, v in list(extras.items())[:8])
    # share of well-liked crowd replies typed casually (lowercase start) -> drafts
    phone = max(1, round(DRAFTS_PER_POST * (lower or 35) / 100))
    emoji = p.get("emoji_pct")
    median = (p.get("chars") or {}).get("median")
    lines = [
        f"- short: most real replies are about {median or 40} characters, never more than {max_chars()}",
        ("- casing: like real replies, mostly lowercase" if (lower or 30) >= 50 else
         f"- casing: like real replies, most start with a capital letter; about {round(lower or 25)}% are"
         " all lowercase"),
        "- no em dashes or en dashes, no semicolons, no hashtags, no links, no @mentions (except @grok when asked)",
        "- usually no period at the end; at most one '!' and rarely",
        f"- emoji: at most one, and only sometimes (about {round(emoji or 10)}% of real replies have one)",
        "- react to THIS post like a person scrolling X, in the moment. mention something specific from it",
        "- plain words: real replies rarely use slang. don't force crypto-speak",
        *([f"- words people drop into real replies (share of replies): {common}"] if common else []),
        f"- type about {phone} of the drafts the way people type on their phone: shortforms like u, ur, ik,"
        " idk, ngl, tbh, rn, jk, gonna, dont/im/thats without the apostrophe, 'oh' to open, 'lol' to close."
        " only where they fit, never several in one reply. the other drafts are typed normally",
        "- only the draft whose kind is a question (or @grok) asks something; the others react",
        "- never sound like a brand, a bot or an assistant: no 'great point', 'love this', 'couldn't agree more',"
        " 'thanks for sharing', 'game changer', 'exciting', 'incredible', no summarizing the post",
        "- not a yes-man and not negative: each draft is a different kind of reaction (below)",
        "- no financial advice, no promises, nothing offensive",
    ]
    return "\n".join(lines)


def build_messages(post: Post, *, rng: random.Random) -> list[dict]:
    sponsored = bool(post.is_sponsored)
    plan = PLANS["sponsored" if sponsored else "creator"][:DRAFTS_PER_POST]
    examples = pick_examples(post.tweet_text or "", sponsored=sponsored, rng=rng)
    shots = "\n\n".join(
        f"post: {e['parent']['text'][:220]}\nreply: {e['text']}" for e in examples
    )
    grok = ""
    if "grok" in plan:
        grok_shots = "\n\n".join(
            f"post: {g['parent']['text'][:220]}\nreply: {g['question']}"
            for g in pick_grok_examples(post.tweet_text or "")
        )
        if grok_shots:
            grok = f"\n\n@grok questions people asked under posts, the ones that performed best:\n\n{grok_shots}"
    rules = _style_rules()
    if sponsored:
        rules += ("\n- this post is from a client we promote (a project, founder or creator): positive, neutral"
                  " or curious only. never negative, sarcastic, doubtful or disappointed about them or what they"
                  " announced")
    else:
        rules += ("\n- this is a community member's post: positive, curious or mildly skeptical at most (a light"
                  " 'yeah but'). never harsh, mocking or dismissive toward them")
    system = (
        "You write replies for crypto X (Twitter), in the voice of top crypto creators. "
        "You only ever answer with JSON.\n\nStyle, from how top creators actually reply:\n" + rules
    )
    handle = post.tweet_author_username or "someone"
    text = (post.tweet_text or "").strip() or "(no text: an image or video post)"
    wording = {**REACTIONS, **(SPONSORED_REACTIONS if sponsored else COMMUNITY_REACTIONS)}
    kinds = "\n".join(f"{i + 1}. {k}: {wording[k]}" for i, k in enumerate(plan))
    user = (
        f"Real replies by top creators, each with the post it answered:\n\n{shots}{grok}\n\n"
        f"---\nWrite {len(plan)} different replies to this post by @{handle}:\n\n{text[:1000]}\n\n"
        f"One reply of each kind, in this order:\n{kinds}\n\n"
        'Answer with only: {"replies": ["...", "..."]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---- output ----
def clean(text, *, limit: int | None = None) -> str | None:
    """A draft ready to post, or None when it can't be made to sound right."""
    t = " ".join(str(text or "").split()).strip().strip("\"'“”‘’")
    grok = bool(_GROK.search(t))
    t = _MENTIONS.sub("", t).strip()
    if grok:
        t = _GROK.sub("", t).strip(" ,")
    if not t or "@" in t or _BANNED.search(t) or _AI_TELLS.search(t) or _PROFANITY.search(t):
        return None
    t = _DASHES.sub(", ", t).replace(";", ",")
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"(?<!\.)\.$", "", t).strip(" ,")
    if len(EMOJI.findall(t)) > 1:
        return None
    if grok:
        t = f"@grok {t}"
    limit = limit or max_chars()
    if len(t) > limit and not grok:
        # too long: keep the first sentence when it stands on its own
        first = re.sub(r"(?<!\.)\.$", "", re.split(r"(?<=[.!?])\s+", t)[0]).strip(" ,")
        if 12 <= len(first) <= limit:
            t = first
    if not (2 <= len(t) <= limit) or not re.search(r"[A-Za-z]", t):
        return None
    return t


def _lowercase_start(t: str) -> str:
    first = t.split()[0] if t.split() else ""
    if first.isupper() or first.startswith("$") or first == "I" or first[:1].isdigit():
        return t  # tickers, acronyms, "I"
    return t[:1].lower() + t[1:]


def parse(content: str) -> list[str]:
    """Drafts from a model answer: JSON, even when wrapped in prose or fences."""
    if not content:
        return []
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, content, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except ValueError:
            continue
        items = data.get("replies") if isinstance(data, dict) else data
        if isinstance(items, list):
            return [str(x) for x in items if isinstance(x, (str, int, float))]
    return []


def finalize(raw: list[str], *, rng: random.Random, allow_grok: bool = False,
             sponsored: bool = False) -> list[str]:
    """Clean, drop near-duplicates and copies of real replies, and keep at most
    one @grok question (none unless allowed). Casing and shortforms are applied
    per viewer later (humanize)."""
    # only a real reply long enough to be someone's own line counts as a copy;
    # "lfg" or "@grok is this true?" are just how people talk
    real = {re.sub(r"\W", "", r["text"].lower()) for r in (*corpus(), *crowd()) if len(r["text"]) >= 25}
    out, seen, grok_used = [], set(), False
    for item in raw:
        t = clean(item)
        key = re.sub(r"\W", "", (t or "").lower())
        if not t or key in seen or key in real:
            continue
        if _HARSH.search(t) or (sponsored and _NEGATIVE.search(t)):
            continue  # never harsh; and a client's post never gets a negative reply
        if t.startswith("@grok"):
            if grok_used or not allow_grok:
                continue
            grok_used = True
        seen.add(key)
        out.append(t)
    return out


# Casual typing, at the rates regular users type it: "when a reply needs
# 'you', how often is it 'u'". Keys match scripts/study_reply_corpus.py PAIRS.
# Longer phrases first ("I don't know" before "I know" before "I").
_APOS = "['\u2019]"
SHORTFORMS = [
    ("idk", rf"\bI don{_APOS}?t know\b", "idk"),
    ("ik", r"\bI know\b", "ik"),
    ("tbh", r"\bto be honest\b", "tbh"),
    ("tbh_honestly", r"^honestly,?\s+", "tbh "),
    ("ngl", r"\bnot (?:gonna|going to) lie\b", "ngl"),
    ("rn", r"\bright now\b", "rn"),
    ("gonna", r"\bgoing to\b", "gonna"),
    ("wanna", r"\bwant to\b", "wanna"),
    ("kinda", r"\bkind of\b", "kinda"),
    ("bc", r"\bbecause\b", "bc"),
    ("tho", r"\bthough\b", "tho"),
    ("prob", r"\bprobably\b", "prob"),
    ("ppl", r"\bpeople\b", "ppl"),
    ("ur", rf"\byou{_APOS}re\b|\byour\b", "ur"),
    ("u", r"\byou\b", "u"),
    ("im", rf"\bI{_APOS}m\b", "im"),
    ("ive", rf"\bI{_APOS}ve\b", "ive"),
    ("dont", rf"\bdon{_APOS}t\b", "dont"),
    ("cant", rf"\bcan{_APOS}t\b", "cant"),
    ("didnt", rf"\bdidn{_APOS}t\b", "didnt"),
    ("doesnt", rf"\bdoesn{_APOS}t\b", "doesnt"),
    ("isnt", rf"\bisn{_APOS}t\b", "isnt"),
    ("thats", rf"\bthat{_APOS}s\b", "thats"),
    ("ok", r"\bokay\b", "ok"),
    ("i", r"\bI\b", "i"),
]


def humanize(text: str, rng: random.Random, casualness: float = 1.0) -> str:
    """One viewer's copy of a draft, typed like regular users type. casualness
    scales the measured rates (QUICK_REPLY_CASUALNESS); no rate goes past 95%,
    so it never reads mechanical."""
    people = crowd_profile()
    rates = (people.get("shortforms") or {}).get("rates") or {}
    for key, pattern, short in SHORTFORMS:
        rate = min(0.95, (rates.get(key) or 0) * casualness)
        if rate and re.search(pattern, text, flags=re.I) and rng.random() < rate:
            text = re.sub(pattern, short, text, flags=re.I)
    lower = (people.get("most_liked") or {}).get("starts_lowercase_pct") or profile().get("starts_lowercase_pct")
    if lower and rng.random() < min(0.95, lower / 100 * casualness):
        text = _lowercase_start(text)
    return text


# ---- OpenRouter ----
def models_for(post: Post) -> list[str]:
    raw = settings.quick_reply_sponsored_models if post.is_sponsored else settings.quick_reply_creator_models
    return [m.strip() for m in raw.split(",") if m.strip()]


async def _complete(client: httpx.AsyncClient, model: str, messages: list[dict]) -> str | None:
    try:
        resp = await client.post(
            f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api}",
                "HTTP-Referer": "https://app.loudrr.com",
                "X-Title": "Loudrr",
            },
            json={"model": model, "messages": messages, "temperature": 0.9, "max_tokens": 700},
        )
    except httpx.HTTPError as e:
        logger.warning("quick replies: %s unreachable: %s", model, e)
        return None
    if resp.status_code != 200:
        logger.warning("quick replies: %s HTTP %s %s", model, resp.status_code, resp.text[:160])
        return None
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("quick replies: %s answered in an unexpected shape", model)
        return None


async def write_drafts(post: Post, *, client: httpx.AsyncClient | None = None) -> tuple[list[str], str]:
    """(drafts, model that wrote them); ([], "") when no model got there."""
    if not settings.openrouter_api:
        return [], ""
    rng = random.Random(str(post.id))
    messages = build_messages(post, rng=rng)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        for model in models_for(post):
            drafts = finalize(parse(await _complete(client, model, messages) or ""), rng=rng,
                              allow_grok=not post.is_sponsored, sponsored=bool(post.is_sponsored))
            if len(drafts) >= MIN_DRAFTS:
                return drafts[:DRAFTS_PER_POST], model
            logger.info("quick replies: %s gave %d usable drafts, trying the next model", model, len(drafts))
    finally:
        if own:
            await client.aclose()
    return [], ""


async def generate_for_post(db, post_id, *, client: httpx.AsyncClient | None = None) -> str:
    """Write and store drafts for one post: "done" | "skipped" | "failed"."""
    post = await db.get(Post, uuid.UUID(str(post_id)))
    if post is None or post.status != "active" or post.quick_replies:
        return "skipped"
    await db.commit()  # don't hold a connection across the model call
    drafts, model = await write_drafts(post, client=client)
    if not drafts:
        return "failed" if settings.openrouter_api else "skipped"
    post = await db.get(Post, post.id)
    if post is None:
        return "skipped"
    post.quick_replies, post.quick_replies_model, post.quick_replies_at = drafts, model, utcnow()
    await db.commit()
    logger.info("quick replies: %d drafts for post %s by %s", len(drafts), post.id, model)
    return "done"


async def queue(post_ids) -> None:
    """Write drafts in the worker, off the request path."""
    from app.tasks.enqueue import enqueue

    for pid in post_ids:
        await enqueue("generate_quick_replies", str(pid), job_id=f"quick_replies:{pid}")


def draft_for(post: Post, viewer_id, casualness: float = 1.0) -> str | None:
    """This viewer's draft for this post, the same one every time."""
    drafts = [d for d in (post.quick_replies or []) if isinstance(d, str) and d]
    # also guards drafts written before these filters existed
    drafts = [d for d in drafts if not _HARSH.search(d)
              and not (post.is_sponsored and _NEGATIVE.search(d))]
    if not drafts:
        return None
    digest = hashlib.sha256(f"{viewer_id}:{post.id}".encode()).digest()
    draft = drafts[int.from_bytes(digest[:4], "big") % len(drafts)]
    return humanize(draft, random.Random(digest), casualness)
