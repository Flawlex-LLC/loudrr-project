"""TwitterScore.io scraper — competitive-research collection of their public scores.

TwitterScore is a SEPARATE company from Sorsa/TweetScout (do not conflate). Their API
needs a paid key, but the public profile page server-renders everything we need inside a
schema.org ``ld+json`` ProfilePage block:

    https://twitterscore.io/twitter/<handle>/   ->  ld+json:
      mainEntity.name                              display name
      mainEntity.alternateName / identifier        @handle
      mainEntity.image  (.../profiles/<ID>.jpg)    twitter user id
      mainEntity.interactionStatistic.userInteractionCount   follower count
      mainEntity.additionalProperty.value          TwitterScore (0-1000)

The score band ("Excellent" etc.) is in the DOM count-wrapper. Parsing the ld+json is
robust (structured, no premium gate, no CSRF) — unlike the followers DataTable, which is
premium-capped.

OPSEC: every request goes through a rotating Webshare proxy (host:port:user:pass) so our
real IP is never exposed to a competitor, and a neutral browser UA is sent. Be polite —
this hits a live third-party site; keep concurrency/rate modest.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

BASE = "https://twitterscore.io/twitter/{handle}/"
FOLLOWEDBY_URL = "https://twitterscore.io/followedByList/ajax/"

# OPSEC: look like ordinary organic Chrome visitors — NOTHING identifies us (no custom UA, no
# "loudrr" in any header/param/referer). A small pool of real, current desktop UAs + matching
# client hints + varied Accept-Language, combined with rotating proxy IPs, reads as many
# different real people browsing. `_UA` kept as the default for callers that reference it.
_UA_POOL = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36", '"Chromium";v="124", "Google Chrome";v="124", "Not.A/Brand";v="99"', '"Windows"'),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/123.0.0.0 Safari/537.36", '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"', '"macOS"'),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0", '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"', '"Windows"'),
]
_LANGS = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-US,en;q=0.8", "en;q=0.9"]
_UA = _UA_POOL[0][0]


def _browser_headers(handle: str, *, ajax: bool = False) -> dict:
    """Realistic per-visitor Chrome headers. Deterministic per handle (stable across the
    request's retries) but varied across handles — organic-looking, zero identifying info."""
    seed = int(hashlib.md5(handle.encode()).hexdigest(), 16)
    ua, ch_ua, platform = _UA_POOL[seed % len(_UA_POOL)]
    h = {
        "User-Agent": ua,
        "Accept-Language": _LANGS[seed % len(_LANGS)],
        # NB: no manual Accept-Encoding — httpx sets one matching the decoders it actually has
        # (adding 'br' made servers Brotli-compress and httpx returned undecodable bytes).
        "sec-ch-ua": ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform,
    }
    if ajax:
        h.update({"Accept": "application/json, text/javascript, */*; q=0.01",
                  "X-Requested-With": "XMLHttpRequest",
                  "Referer": BASE.format(handle=handle),
                  "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
    else:
        h.update({"Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                             "image/avif,image/webp,image/apng,*/*;q=0.8"),
                  "Upgrade-Insecure-Requests": "1",
                  "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate",
                  "Sec-Fetch-Dest": "document", "Sec-Fetch-User": "?1"})
    return h

_LDJSON_RE = re.compile(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', re.S)
_ID_RE = re.compile(r"/profiles/(\d+)")
_BAND_RE = re.compile(r'count-wrapper.*?<p>\s*([^<]+?)\s*</p>', re.S)
# account-level profile fields (server-rendered in the "more-information" panel)
_DESC_RE = re.compile(r'currency-description">\s*([^<]+?)\s*<', re.S)


def _more_info(html: str, label: str) -> str | None:
    """Value of a 'more-info' row, e.g. Category/Based in/Joined/Renamed. Skips any
    opening <span> wrappers and grabs the first text token after the title."""
    m = re.search(re.escape(label) + r"</span>\s*(?:<span[^>]*>\s*)*([^<]+?)\s*<", html)
    return m.group(1).strip() if m else None


def load_proxies(path: str) -> list[str]:
    """Webshare ``host:port:user:pass`` lines -> httpx proxy URLs."""
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            h, p, u, pw = ln.split(":")
            out.append(f"http://{u}:{pw}@{h}:{p}")
    return out


def parse_profile_html(html: str) -> dict | None:
    """Extract the TwitterScore profile from page HTML via its ld+json ProfilePage block.
    Returns {user_id, username, name, twitterscore, followers, band} or None if absent."""
    for m in _LDJSON_RE.finditer(html):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        ent = obj.get("mainEntity") if isinstance(obj, dict) else None
        if not isinstance(ent, dict):
            continue
        prop = ent.get("additionalProperty") or {}
        if (prop.get("name") or "").lower() != "twitterscore":
            continue
        handle = (ent.get("identifier") or ent.get("alternateName") or "").lstrip("@")
        id_m = _ID_RE.search(ent.get("image") or "")
        stat = ent.get("interactionStatistic") or {}
        band_m = _BAND_RE.search(html)
        # account-level fields from the more-information panel
        cat_raw = _more_info(html, "Category")
        # clean comma string e.g. "Founders, Influencers" (re-normalize spacing). TwitterScore
        # renders "No category" for uncategorized accounts -> store as empty, not a fake tag.
        if cat_raw and cat_raw.strip().lower() in ("no category", "no categories"):
            cat_raw = ""
        categories = ", ".join(c.strip() for c in cat_raw.split(",") if c.strip()) if cat_raw else ""
        desc_m = _DESC_RE.search(html)
        return {
            "user_id": id_m.group(1) if id_m else None,
            "username": handle or None,
            "name": ent.get("name"),
            "twitterscore": _num(prop.get("value")),
            "followers": _int(stat.get("userInteractionCount")),
            "band": band_m.group(1) if band_m else None,
            "description": desc_m.group(1).strip() if desc_m else (ent.get("description") or None),
            "categories": categories,                 # ["Venture Capitals","Projects"] etc.
            "based_in": _more_info(html, "Based in"),
            "joined_date": _more_info(html, "Joined"),
            "renamed_count": _more_info(html, "Renamed"),
        }
    return None


def _num(v) -> float | None:
    """'1,000' / 1000 / None -> float|None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _int(v) -> int | None:
    """'20,118' / 20118 / None -> int|None (strip commas)."""
    if v is None:
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,20}$")


def clean_account(rec: dict) -> tuple[dict | None, list[str]]:
    """REPAIR-and-KEEP accuracy gate (one call/record, ~0 overhead).

    Returns (cleaned_rec, issues). Returns (None, [...]) ONLY when the record is
    fundamentally unusable — i.e. no numeric ``user_id`` (a premium-blurred / placeholder
    row that can't be a graph node anyway). For ANY other problem we KEEP the account and
    null/clean just the offending field, so we never drop legit accounts (no gaps in the
    ranking) and never store a wrong value (no garbage in the ranking)."""
    issues: list[str] = []
    uid = rec.get("user_id")
    if not uid or not str(uid).isdigit() or len(str(uid)) > 25:
        return None, ["no usable user_id (blurred/placeholder row)"]
    out = dict(rec)
    un = out.get("username")
    if un is not None and not _HANDLE_RE.match(str(un)):
        out["username"] = None; issues.append("nulled odd username")
    sc = out.get("twitterscore")
    if sc is not None:
        try:
            f = float(sc)
            if not (0.0 <= f <= 1000.0):
                out["twitterscore"] = None; issues.append("nulled out-of-range score")
            else:
                out["twitterscore"] = f
        except (TypeError, ValueError):
            out["twitterscore"] = None; issues.append("nulled non-numeric score")
    for fld in ("followers", "smart_followers"):
        v = out.get(fld)
        if v is not None and (not isinstance(v, int) or v < 0):
            out[fld] = None; issues.append(f"nulled bad {fld}")
    # tags/categories must be clean strings; clear ONLY if raw JSON leaked (parser bug).
    # description/based_in are free text (brackets in bios are fine) -> never touched.
    for fld in ("tags", "categories"):
        v = out.get(fld)
        if v is not None and not isinstance(v, str):
            out[fld] = ""; issues.append(f"cleared non-string {fld}")
        elif isinstance(v, str) and v.strip()[:1] in ("[", "{"):
            out[fld] = ""; issues.append(f"cleared json-looking {fld}")
    return out, issues


def fmt_tags(tags) -> str:
    """[{name, categories_name}] -> clean 'a16z (Tier 1 VC); Ethereum (Ecosystems)'.
    Stored as readable text (LIKE-filterable) instead of raw JSON."""
    parts = []
    for t in tags or []:
        n = (t.get("name") or "").strip()
        if not n:
            continue
        cat = (t.get("categories_name") or "").strip()
        parts.append(f"{n} ({cat})" if cat else n)
    return "; ".join(parts)


def parse_followed_rows(rows: list[dict]) -> list[dict]:
    """Map /followedByList/ajax/ rows -> normalized account dicts (with TAGS)."""
    out: list[dict] = []
    for row in rows or []:
        id_m = _ID_RE.search(row.get("profile_image") or "")
        out.append({
            "user_id": id_m.group(1) if id_m else None,
            "username": (row.get("profile_username") or "").lstrip("@") or None,
            "name": row.get("profile_name"),
            "twitterscore": _num(row.get("project_score")),
            "tags": fmt_tags(row.get("tags")),      # clean "a16z (Tier 1 VC); ..." string
            "smart_followers": _int(row.get("smart_followers_count")),  # "20,118" -> 20118
            "followers": _int(row.get("current_followers_int")),
            # following_date is {relative, absolute} -> keep the absolute string
            "following_date": (row.get("following_date") or {}).get("absolute")
            if isinstance(row.get("following_date"), dict) else row.get("following_date"),
        })
    return out


class TwitterScoreScraper:
    def __init__(self, proxies: list[str], timeout: float = 30.0):
        if not proxies:
            raise ValueError("no proxies provided")
        self._proxies = proxies
        self._cycle = itertools.cycle(proxies)
        self.timeout = timeout
        self.fetched = 0

    async def fetch_profile(self, handle: str, *, attempts: int = 3) -> dict | None:
        """Fetch + parse one profile, rotating proxy on each attempt. None if the
        handle 404s (no TwitterScore page) or all proxy attempts fail."""
        handle = handle.lstrip("@")
        url = BASE.format(handle=handle)
        last_err = None
        for _ in range(attempts):
            proxy = next(self._cycle)
            try:
                async with httpx.AsyncClient(proxy=proxy, follow_redirects=True,
                                             timeout=self.timeout) as c:
                    r = await c.get(url, headers=_browser_headers(handle))
                if r.status_code == 404:
                    return None
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                self.fetched += 1
                data = parse_profile_html(r.text)
                if data:
                    data["handle_queried"] = handle
                return data
            except Exception as e:  # noqa: BLE001 - proxy/network flake -> rotate + retry
                last_err = f"{type(e).__name__}: {e}"
                continue
        logger.warning("fetch_profile(%s) failed after %d attempts: %s", handle, attempts, last_err)
        return None

    async def fetch_followed_by(
        self, twitter_id: str, handle: str, *, category_id: str = "all",
        category_name: str = "All", length: int = 100, attempts: int = 3
    ) -> list[dict]:
        """The account's significant followers (ranked, WITH tags + scores), via the
        followedByList AJAX. GET (no CSRF), premium-capped to ~10 rows anonymously — but
        each ``category_id`` (1=Tier1 VC, 2=Tier2 VC, 3=Ecosystems, 4=Other, 'all'=mixed)
        returns its OWN top-10, so querying all 5 multiplies coverage ~5x. Returns []
        on failure; each dict carries tags + following_date."""
        handle = handle.lstrip("@")
        params = {
            "draw": "1", "start": "0", "length": str(length),
            "order[0][column]": "2", "order[0][dir]": "desc",
            "slug": handle, "username": handle, "twitter_id": str(twitter_id),
            "categoryName": category_name, "tagName": "All",
            "categoryId": category_id, "tagId": "all",
        }
        headers = _browser_headers(handle, ajax=True)
        last_err = None
        for _ in range(attempts):
            proxy = next(self._cycle)
            try:
                async with httpx.AsyncClient(proxy=proxy, follow_redirects=True,
                                             timeout=self.timeout) as c:
                    r = await c.get(FOLLOWEDBY_URL, params=params, headers=headers)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                self.fetched += 1
                return parse_followed_rows(r.json().get("data", []))
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                continue
        logger.warning("fetch_followed_by(%s) failed: %s", handle, last_err)
        return []
