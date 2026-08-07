"""Loudrr Smart Engagement — dedicated smart-set activity tracker (see ARCHITECTURE in models.py).

Isolated from the follower-scoring crawl: own tables (eng_*), own worker (RUN_MODE=engagement),
own gateway pacing + budget ceiling. Reads smart_set; touches nothing else.
"""
