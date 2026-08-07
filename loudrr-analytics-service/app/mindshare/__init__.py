"""Loudrr Mindshare — a standalone share-of-voice engine (separate from the PageRank scorer).

Pipeline (medallion): roster -> ingest (gateway tweets) -> attribute -> score -> hourly buckets
-> windowed aggregate/snapshot -> movers -> serve. Owns the ``ms_*`` tables only; it *reads* the
PageRank weights (``smart_set.score``) and the scraped Kaito rosters (``kaito_mindshare``) but never
writes them. See docs/kaito_reverse_engineering.md §6 and app/mindshare/ARCHITECTURE.md.
"""
