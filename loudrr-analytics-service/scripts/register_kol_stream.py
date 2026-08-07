"""Register tracked KOLs for realtime tweet push on the loudrr gateway.

    gateway x_user_stream --POST--> our /v1/hooks/x --> eng_call --> WS --> browser

The gateway has NO websocket (verified against gateway.loudrr.com/docs/openapi.json: 66
routes, zero WS), so a registered push is the only way to learn a KOL tweeted the moment it
happens. Without this, /v1/hooks/x is a correct endpoint nobody ever calls and "which KOL
called" stays hourly-crawl-fresh instead of realtime.

DRY RUN BY DEFAULT. Registering costs money and starts real traffic, so nothing is written
without --apply.

    python -m scripts.register_kol_stream --list                 # what's registered now
    python -m scripts.register_kol_stream --limit 200            # preview (writes nothing)
    python -m scripts.register_kol_stream --limit 200 --apply    # actually register
    python -m scripts.register_kol_stream --remove-all --apply   # tear it down

PREREQUISITE THIS SCRIPT CANNOT DO FOR YOU: the gateway's API exposes no field for the
callback URL — add_user_to_monitor_tweet takes only `x_user_name`. The destination must be
set once on the gateway side (dashboard/support) to:

    https://<our-api-host>/v1/hooks/x     with header  X-Loudrr-Secret: $LIVE_WEBHOOK_SECRET

Until that's done, registrations succeed and no push ever arrives — so --list showing
handles is NOT proof the pipe works. Confirm with a real tweet, or by watching for the
"x webhook hit" log line.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import httpx

from app.clients.twitterapi import TwitterAPIClient
from app.core.config import settings
from app.engagement.ingest import _universe

logging.basicConfig(level="INFO", format="%(message)s")
log = logging.getLogger("register")

ADD = "/oapi/x_user_stream/add_user_to_monitor_tweet"
GET = "/oapi/x_user_stream/get_user_to_monitor_tweet"
REMOVE = "/oapi/x_user_stream/remove_user_to_monitor_tweet"


def _conn() -> tuple[str, str]:
    """(root_url, api_key) — reuses the client's own gateway-preferred key selection so
    this script can never drift onto a different host/key than the crawl."""
    c = TwitterAPIClient()
    return c.root_url, c.api_key


async def _list(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(GET)
    r.raise_for_status()
    d = r.json()
    for key in ("data", "users", "results"):
        v = d.get(key) if isinstance(d, dict) else None
        if isinstance(v, list):
            return v
    return d if isinstance(d, list) else []


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=100,
                    help="how many top KOLs to register (default 100)")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--list", action="store_true", help="show what's registered, then exit")
    ap.add_argument("--remove-all", action="store_true", help="deregister everything")
    args = ap.parse_args()

    root, key = _conn()
    if not key:
        raise SystemExit("no gateway API key — set LOUDRR_GATEWAY_API")
    log.info("gateway: %s", root)

    async with httpx.AsyncClient(base_url=root, timeout=30.0,
                                 headers={"x-api-key": key}) as client:
        current = await _list(client)
        log.info("currently monitored: %d", len(current))
        if args.list:
            for u in current:
                log.info("  %s  id=%s", u.get("x_user_name") or u.get("userName"),
                         u.get("id_for_user") or u.get("id"))
            return

        if args.remove_all:
            if not args.apply:
                log.info("DRY RUN — would remove %d registrations", len(current))
                return
            for u in current:
                uid = u.get("id_for_user") or u.get("id")
                if not uid:
                    continue
                r = await client.post(REMOVE, json={"id_for_user": str(uid)})
                log.info("removed %s -> %s", uid, r.status_code)
            return

        have = {str(u.get("x_user_name") or u.get("userName") or "").lower().lstrip("@")
                for u in current}
        members = await _universe(args.limit)
        want = [uname for _, uname in members if uname]
        todo = [u for u in want if u.lower() not in have]

        log.info("universe=%s top %d -> %d handles, %d already registered, %d to add",
                 settings.engagement_universe, args.limit, len(want), len(want) - len(todo),
                 len(todo))
        if not args.apply:
            log.info("DRY RUN — would register: %s%s",
                     ", ".join(todo[:15]), " …" if len(todo) > 15 else "")
            log.info("re-run with --apply to write")
            return

        ok = 0
        for uname in todo:
            try:
                r = await client.post(ADD, json={"x_user_name": uname})
                r.raise_for_status()
                ok += 1
            except Exception as e:  # noqa: BLE001 — one bad handle must not abort the batch
                log.warning("  %s failed: %s", uname, e)
        log.info("registered %d/%d", ok, len(todo))


if __name__ == "__main__":
    asyncio.run(main())
