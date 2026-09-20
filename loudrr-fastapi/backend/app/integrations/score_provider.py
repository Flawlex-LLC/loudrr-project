"""Which service supplies scores — one switch, ``SCORE_PROVIDER``.

  sorsa  (default) — Sorsa's public profile pages, read through the proxy pool
                     (app/integrations/sorsa.py)
  loudrr (legacy)  — the loudrr-analytics-service graph
                     (app/integrations/loudrr_analytics.py)

Both clients expose the same two calls — ``get_user_data`` (flat
TweetScout-shaped dict or None) and ``get_top_followers`` (list of
{"username", ...} or None) — so callers never branch on the provider. Kept as
one factory so tests monkeypatch a single symbol to inject a fake provider.
"""
from app.core.config import settings


def get_score_client():
    if settings.score_provider.strip().lower() == "loudrr":
        from app.integrations.loudrr_analytics import get_loudrr_client

        return get_loudrr_client()
    from app.integrations.sorsa import SorsaClient

    return SorsaClient()
