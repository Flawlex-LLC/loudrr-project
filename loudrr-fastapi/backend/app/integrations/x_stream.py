"""Loudrr gateway: realtime tweet stream for sponsored accounts.

The gateway (same key/base as integrations/twitter.py) pushes new tweets of
the accounts on our "monitor" list over one websocket:

    wss://<gateway>/twitter/tweet/websocket     header x-api-key
    frames: {"event_type": "connected"} | {"event_type": "ping"} |
            {"event_type": "tweet", "tweets": [<tweet>, ...], ...}

Monitor config (add / list / remove a user) is free; delivered tweets bill.

Search (`search_latest`) is the poller's source: the gateway bills reads only
for tweets actually returned, so polling a short time window that is usually
empty costs nothing. `user_info` resolves a handle when an admin adds it.

Tweet objects use the twitterapi.io shape: id, text, createdAt
("Tue Jul 28 14:30:39 +0000 2026"), isReply, inReplyToId, isRetweet,
retweeted_tweet, quoted_tweet, author{id,userName,name,profilePicture},
extendedEntities.media[].
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# short: an admin action chains two calls behind Next's proxy timeout
_TIMEOUT = httpx.Timeout(10.0)


class GatewayUnavailable(Exception):
    """The gateway couldn't answer (network, 5xx, bad key, no credits)."""


class XStreamClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = settings.loudrr_gateway_api if api_key is None else api_key
        self.base_url = (base_url or settings.gateway_base_url).rstrip("/")
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def websocket_url(self) -> str:
        base = self.base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return f"{base}/twitter/tweet/websocket"

    def headers(self) -> dict:
        return {"x-api-key": self.api_key}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.api_key:
            raise GatewayUnavailable("gateway key not configured")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, headers=self.headers(), timeout=_TIMEOUT,
                transport=self._transport,
            ) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise GatewayUnavailable(f"{method} {path}: {e}") from e
        if resp.status_code >= 400:
            raise GatewayUnavailable(f"{method} {path}: HTTP {resp.status_code} {resp.text[:120]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise GatewayUnavailable(f"{method} {path}: non-JSON response") from e
        return data if isinstance(data, dict) else {}

    # ---- reads ----
    async def user_info(self, username: str) -> dict | None:
        """{id, userName, name, profilePicture, ...} or None if X has no such user."""
        data = await self._request("GET", "/twitter/user/info", params={"userName": username})
        user = data.get("data")
        if isinstance(user, dict) and user.get("id"):
            return user
        msg = str(data.get("msg") or "").lower()
        if data.get("status") == "error" and "not found" not in msg and "suspend" not in msg:
            raise GatewayUnavailable(f"user info @{username}: {msg or data}")
        return None

    async def search_latest(self, query: str, cursor: str = "") -> tuple[list[dict], str, bool]:
        """One page of X "Latest" search: (tweets, next_cursor, has_next_page)."""
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/twitter/tweet/advanced_search", params=params)
        if data.get("status") == "error":
            raise GatewayUnavailable(f"search: {data.get('msg') or data}")
        tweets = [t for t in (data.get("tweets") or []) if isinstance(t, dict)]
        return tweets, str(data.get("next_cursor") or ""), bool(data.get("has_next_page"))

    async def users_by_ids(self, user_ids) -> list[dict]:
        """Profiles for permanent X ids (suspended/deleted ones are skipped)."""
        ids = [str(i) for i in user_ids if str(i).isdigit()]
        users: list[dict] = []
        for start in range(0, len(ids), 100):
            data = await self._request(
                "GET", "/twitter/user/batch_info_by_ids",
                params={"userIds": ",".join(ids[start:start + 100])},
            )
            if data.get("status") == "error":
                raise GatewayUnavailable(f"users by ids: {data.get('msg') or data}")
            users.extend(u for u in (data.get("users") or []) if isinstance(u, dict))
        return users

    async def account_info(self) -> dict:
        """Our own key's balance and usage (free call): {recharge_credits, ...}."""
        data = await self._request("GET", "/oapi/my/info")
        if data.get("status") == "error":
            raise GatewayUnavailable(f"account info: {data.get('msg') or data}")
        return data

    # ---- monitor list (free) ----
    async def list_monitors(self) -> list[dict]:
        data = await self._request("GET", "/oapi/x_user_stream/get_user_to_monitor_tweet")
        return [row for row in (data.get("data") or []) if isinstance(row, dict)]

    async def add_monitor(self, username: str) -> None:
        data = await self._request(
            "POST", "/oapi/x_user_stream/add_user_to_monitor_tweet",
            json={"x_user_name": username},
        )
        if data.get("status") != "success":
            msg = str(data.get("msg") or "")
            # adding an account that is already monitored is not a failure
            # (but "does not exist" is)
            lowered = msg.lower()
            if "already" in lowered or ("exist" in lowered and "not" not in lowered and "n't" not in lowered):
                return
            raise GatewayUnavailable(f"add monitor @{username}: {msg or data}")

    async def remove_monitor(self, username: str) -> bool:
        """Remove every monitor entry for this handle. False if there was none."""
        removed = False
        for row in await self.list_monitors():
            name = str(row.get("x_user_screen_name") or row.get("x_user_name") or "")
            if name.lower() != username.lower():
                continue
            data = await self._request(
                "POST", "/oapi/x_user_stream/remove_user_to_monitor_tweet",
                json={"id_for_user": row.get("id_for_user")},
            )
            if data.get("status") != "success":
                raise GatewayUnavailable(f"remove monitor @{username}: {data.get('msg') or data}")
            removed = True
        return removed


def get_x_stream_client() -> XStreamClient:
    return XStreamClient()
