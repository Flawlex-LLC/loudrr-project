"""TDD spec — iter_search_tweets incremental semantics (review finding #2).

advanced_search ordering is only APPROXIMATELY newest-first, so the iterator must SKIP
already-seen ids and stop only when an entire page is old — a hard stop at the first old
id would let one out-of-order item truncate the page and silently drop unseen tweets.
"""
from app.clients.twitterapi import TwitterAPIClient


def _tweet(tid):
    return {"id": tid, "text": "x"}


def _client_with_pages(pages):
    """TwitterAPIClient whose _get serves canned pages (no network)."""
    client = TwitterAPIClient(api_key="test", rate_calls=10_000, rate_window_s=1)
    state = {"i": 0, "gets": 0}

    async def fake_get(_http, _path, _params):
        state["gets"] += 1
        page = pages[min(state["i"], len(pages) - 1)]
        state["i"] += 1
        has_next = state["i"] < len(pages)
        return {"tweets": page, "has_next_page": has_next,
                "next_cursor": "c" if has_next else ""}

    client._get = fake_get  # type: ignore[method-assign]
    return client, state


async def test_out_of_order_old_id_is_skipped_not_truncating():
    client, _ = _client_with_pages([[_tweet("15"), _tweet("9"), _tweet("14")]])
    got = [t["id"] async for t in client.iter_search_tweets(
        query="from:x include:nativeretweets", since_id="10", max_pages=1)]
    assert got == ["15", "14"]          # 9 skipped; 14 NOT dropped


async def test_whole_old_page_stops_pagination():
    client, state = _client_with_pages([
        [_tweet("8"), _tweet("7")],      # all <= since -> caught up
        [_tweet("6")],                   # must never be fetched
    ])
    got = [t["id"] async for t in client.iter_search_tweets(
        query="from:x", since_id="10", max_pages=5)]
    assert got == []
    assert state["gets"] == 1            # stopped after the all-old page


async def test_no_since_id_pages_through():
    client, state = _client_with_pages([[_tweet("5")], [_tweet("4")]])
    got = [t["id"] async for t in client.iter_search_tweets(query="from:x", max_pages=5)]
    assert got == ["5", "4"]
    assert state["gets"] == 2
