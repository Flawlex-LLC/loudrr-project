"""Study how top crypto KOLs reply, from app/reply_data/reply_style_corpus.json.

Measures what "sounds human" there — length, casing, punctuation, emoji,
vocabulary, the mix of reaction types — overall and for the most-liked
replies, and writes:

  * app/reply_data/reply_style_profile.json — the numbers the reply generator
    follows (length cap, casing, punctuation, reaction mix, vocabulary)
  * a readable report (path printed at the end)

    python -m scripts.study_reply_corpus [--report ../docs/reply-style-study.md]

Reaction types here are keyword heuristics, good enough to show the mix; they
are labels for a study, not a classifier the app relies on.
"""
import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "app" / "reply_data"
CORPUS = DATA / "reply_style_corpus.json"
GROK = DATA / "grok_questions.json"
PROFILE = DATA / "reply_style_profile.json"

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF⭐⭕‼⁉]")
WORD = re.compile(r"[a-z0-9$][a-z0-9$'’]*")
STOP = set("""a an the and or but if so of to in on at for with from by as is are was were be been being it its
this that these those i you he she we they me my your our their them him her us do does did have has had not no
yes just can will would could should than then there here what who how why when which all any some more most very
up out about into over also too only get got im i'm it's don't dont can't cant""".split())
CT_TERMS = [
    "gm", "gn", "ser", "fren", "frens", "lfg", "wagmi", "ngmi", "based", "bullish", "bearish", "degen", "ape",
    "lol", "lmao", "lmfao", "fr", "ngl", "tbh", "imo", "idk", "bro", "sir", "king", "legend", "cooked", "bags",
    "pump", "send", "sending", "goat", "real", "facts", "huge", "insane", "wild", "crazy", "banger", "alpha",
    "anon", "chad", "cope", "mid", "rekt", "moon", "vibes", "hodl", "wen", "fud", "jeet", "early", "congrats",
]
REACTIONS = [  # first match wins, so the specific ones come first
    ("question", lambda t: "?" in t),
    ("humor", lambda t: re.search(r"\b(lol|lmao|lmfao|haha|kek)\b|😂|🤣|💀", t)),
    ("pushback", lambda t: re.search(r"^(no|nah|nope|wrong)\b|\b(disagree|cope|not really|doubt|overrated|won't|isn't|doesn't)\b", t)),
    ("hype", lambda t: re.search(r"\b(lfg|congrats|legend|king|goat|huge|banger|bullish|insane|fire|love|massive|incredible)\b|🔥|🚀|🫡|❤", t)),
    ("agree", lambda t: re.search(r"^(yes|yep|yeah|yea|true|facts|this|agreed|exactly|real|100%|same|based)\b", t)),
]


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def reaction(text: str) -> str:
    low = text.lower()
    for name, test in REACTIONS:
        if test(low):
            return name
    return "take"  # an observation / opinion / add-on


def describe(replies: list[dict]) -> dict:
    texts = [r["text"] for r in replies]
    n = len(texts)
    chars = [len(t) for t in texts]
    words = [len(t.split()) for t in texts]
    qs = statistics.quantiles(chars, n=10) if n >= 10 else [0] * 9
    letters_start = [t for t in texts if t[:1].isalpha()]
    ends = Counter(
        "period" if t.endswith(".") and not t.endswith("..") else
        "ellipsis" if t.endswith(("...", "…")) else
        "exclamation" if t.endswith("!") else
        "question" if t.endswith("?") else
        "emoji" if EMOJI.search(t[-1:]) else "none"
        for t in texts
    )
    vocab = Counter(w for t in texts for w in WORD.findall(t.lower()) if w not in STOP and len(w) > 1)
    ct = Counter({term: sum(1 for t in texts if re.search(rf"\b{re.escape(term)}\b", t.lower())) for term in CT_TERMS})
    emojis = Counter(e for t in texts for e in EMOJI.findall(t))
    return {
        "replies": n,
        "chars": {"p10": round(qs[0]), "p25": round(statistics.quantiles(chars, n=4)[0]) if n >= 4 else 0,
                  "median": round(statistics.median(chars)) if n else 0, "p75": round(statistics.quantiles(chars, n=4)[2]) if n >= 4 else 0,
                  "p90": round(qs[8])},
        "words_median": round(statistics.median(words)) if n else 0,
        "under_60_chars_pct": pct(sum(1 for c in chars if c <= 60), n),
        "under_100_chars_pct": pct(sum(1 for c in chars if c <= 100), n),
        "starts_lowercase_pct": pct(sum(1 for t in letters_start if t[0].islower()), len(letters_start)),
        "all_lowercase_pct": pct(sum(1 for t in texts if re.search("[a-z]", t) and t == t.lower()), n),
        "ending": {k: pct(v, n) for k, v in ends.most_common()},
        "em_dash_pct": pct(sum(1 for t in texts if "—" in t), n),
        "en_dash_pct": pct(sum(1 for t in texts if "–" in t), n),
        "semicolon_pct": pct(sum(1 for t in texts if ";" in t), n),
        "exclamation_pct": pct(sum(1 for t in texts if "!" in t), n),
        "question_pct": pct(sum(1 for t in texts if "?" in t), n),
        "emoji_pct": pct(sum(1 for t in texts if EMOJI.search(t)), n),
        "hashtag_pct": pct(sum(1 for t in texts if re.search(r"#\w", t)), n),
        "top_emojis": [e for e, _ in emojis.most_common(12)],
        "ct_terms": {k: pct(v, n) for k, v in ct.most_common(25) if v},
        "top_words": [w for w, _ in vocab.most_common(60)],
        "reactions": {k: pct(v, n) for k, v in Counter(reaction(t) for t in texts).most_common()},
    }


def study_grok(rows: list[dict]) -> dict:
    """What the @grok questions that performed look like, and what they sit under."""
    if not rows:
        return {}
    n = len(rows)
    openers: dict[str, list[dict]] = {}
    for r in rows:
        words = r["question"].lower().replace("@grok", "").split()
        openers.setdefault(" ".join(words[:2]) or "?", []).append(r)
    parents = [r["parent"] for r in rows]
    news = re.compile(r"\b(breaking|just in|announce\w*|launch\w*|report\w*|says|according|confirmed|leaked)\b", re.I)
    return {
        "questions": n,
        "chars_median": round(statistics.median(len(r["question"]) for r in rows)),
        "ends_with_question_pct": pct(sum(1 for r in rows if r["question"].rstrip().endswith("?")), n),
        "openers": sorted(
            ({"opener": k, "count": len(v),
              "median_likes": round(statistics.median(x["likes"] for x in v)),
              "median_views": round(statistics.median(x["views"] for x in v)),
              "median_answer_views": round(statistics.median(x["answer_views"] for x in v))}
             for k, v in openers.items() if len(v) >= 2),
            key=lambda o: (o["count"], o["median_likes"]), reverse=True,
        )[:15],
        "parent_has_number_pct": pct(sum(1 for p in parents if re.search(r"\d", p.get("text", ""))), n),
        "parent_has_media_pct": pct(sum(1 for p in parents if p.get("has_media")), n),
        "parent_has_ticker_pct": pct(sum(1 for p in parents if re.search(r"\$[A-Za-z]{2,}", p.get("text", ""))), n),
        "parent_news_words_pct": pct(sum(1 for p in parents if news.search(p.get("text", ""))), n),
        "top": [{"question": r["question"], "likes": r["likes"], "views": r["views"],
                 "answer_views": r["answer_views"], "parent": r["parent"]["text"][:140]} for r in rows[:12]],
    }


def grok_report(g: dict) -> list[str]:
    if not g:
        return []
    return [
        "",
        "## Asking @grok",
        "",
        f"{g['questions']} real questions that tagged @grok under crypto posts, found from Grok's most-engaged "
        "answers and ranked by the question's likes and views plus the attention Grok's answer got "
        f"({g.get('excluded_as_noise', 0)} image/meme requests, roasts and questions dragging in other accounts left out).",
        "",
        f"- Median length {g['chars_median']} characters; {g['ends_with_question_pct']}% end with a question mark.",
        f"- What they're asked under: {g['parent_has_number_pct']}% of those posts have a number, "
        f"{g['parent_has_media_pct']}% an image or video, {g['parent_has_ticker_pct']}% a $ticker, "
        f"{g['parent_news_words_pct']}% news words (breaking, announced, reports...).",
        "",
        "| Opens with | Count | Median likes | Median views | Median views of Grok's answer |",
        "|---|---|---|---|---|",
        *[f"| @grok {o['opener']} … | {o['count']} | {o['median_likes']} | {o['median_views']} | "
          f"{o['median_answer_views']} |" for o in g["openers"]],
        "",
        "Best-performing questions:",
        "",
        *[f"- **{t['question']}** ({t['likes']} likes, {t['views']} views; Grok's answer {t['answer_views']} views)"
          f" under: \"{t['parent']}\"" for t in g["top"]],
    ]


def report(overall: dict, top: dict, meta: dict) -> str:
    def row(label, key, fmt="{}"):
        return f"| {label} | {fmt.format(overall[key])} | {fmt.format(top[key])} |"
    lines = [
        "# How top crypto KOLs reply — study",
        "",
        f"{overall['replies']} real replies by {meta['kols']} top Sorsa-scored KOLs "
        f"(scores {meta['score_min']}–{meta['score_max']}), {meta['with_parent']} of them with the post they answered. "
        f"Corpus collected {meta['generated_at']}.",
        "",
        "\"Most-liked\" = the top 20% of replies by likes.",
        "",
        "## Shape",
        "",
        "| | All replies | Most-liked |",
        "|---|---|---|",
        f"| Median length | {overall['chars']['median']} chars / {overall['words_median']} words | {top['chars']['median']} chars / {top['words_median']} words |",
        f"| 75% are shorter than | {overall['chars']['p75']} chars | {top['chars']['p75']} chars |",
        row("≤ 60 chars", "under_60_chars_pct", "{}%"),
        row("≤ 100 chars", "under_100_chars_pct", "{}%"),
        row("Starts lowercase", "starts_lowercase_pct", "{}%"),
        row("Entirely lowercase", "all_lowercase_pct", "{}%"),
        row("Has an em dash (—)", "em_dash_pct", "{}%"),
        row("Has a semicolon", "semicolon_pct", "{}%"),
        row("Has an exclamation mark", "exclamation_pct", "{}%"),
        row("Asks a question", "question_pct", "{}%"),
        row("Has an emoji", "emoji_pct", "{}%"),
        row("Has a hashtag", "hashtag_pct", "{}%"),
        "",
        "## How replies end",
        "",
        "| Ending | All | Most-liked |",
        "|---|---|---|",
        *[f"| {k} | {v}% | {top['ending'].get(k, 0)}% |" for k, v in overall["ending"].items()],
        "",
        "## Reaction mix (keyword heuristic)",
        "",
        "| Type | All | Most-liked |",
        "|---|---|---|",
        *[f"| {k} | {v}% | {top['reactions'].get(k, 0)}% |" for k, v in overall["reactions"].items()],
        "",
        "\"take\" = an observation, opinion or add-on that isn't a plain yes, hype, joke, question or pushback.",
        "",
        "## Vocabulary",
        "",
        "Crypto-Twitter terms (share of replies using them): "
        + ", ".join(f"{k} {v}%" for k, v in overall["ct_terms"].items()),
        "",
        "Most common words: " + ", ".join(overall["top_words"][:40]),
        "",
        "Most used emojis: " + " ".join(overall["top_emojis"]) if overall["top_emojis"] else "Emojis: rare.",
    ]
    return "\n".join(lines) + "\n"


def main(report_path: Path) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    replies = corpus["replies"]
    likes = sorted(r.get("likes", 0) for r in replies)
    cut = likes[int(len(likes) * 0.8)] if likes else 0
    top_replies = [r for r in replies if r.get("likes", 0) >= cut] or replies
    overall, top = describe(replies), describe(top_replies)
    scores = [k["score"] for k in corpus["kols"]]
    meta = {
        "kols": len(corpus["kols"]), "score_min": round(min(scores)) if scores else 0,
        "score_max": round(max(scores)) if scores else 0,
        "with_parent": sum(1 for r in replies if r.get("parent")), "generated_at": corpus["generated_at"],
    }
    from app.services.quick_replies import usable_grok_question
    grok_rows = json.loads(GROK.read_text(encoding="utf-8"))["questions"] if GROK.is_file() else []
    grok = study_grok([r for r in grok_rows if usable_grok_question(r["question"])])
    if grok:
        grok["excluded_as_noise"] = len(grok_rows) - grok["questions"]
    PROFILE.write_text(json.dumps({"meta": meta, "all": overall, "most_liked": top, "grok": grok},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report(overall, top, meta) + "\n".join(grok_report(grok)) + "\n", encoding="utf-8")
    print(f"profile -> {PROFILE}\nreport  -> {report_path.resolve()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "reply-style-study.md")
    main(ap.parse_args().report)
