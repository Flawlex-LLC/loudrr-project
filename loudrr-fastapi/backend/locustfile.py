"""Locust load scenario for the Loudrr FastAPI backend.

Mix mirrors real miniapp traffic (see tests/SCALING.md): feed-heavy reads with
a slice of engagement writes. Auth uses the ?telegram_id= debug bypass, so the
TARGET BACKEND MUST RUN WITH DEBUG=True — i.e. local dev or a staging box,
NEVER prod (prod-guard refuses DEBUG=True when ENVIRONMENT=prod anyway).

Quick smoke (100 users, spawn 10/s, 60s, against local dev):
    ..\\.venv\\Scripts\\python.exe -m locust -f locustfile.py --headless \
        -u 100 -r 10 -t 60s -H http://localhost:8000 --only-summary

Real confirmation run (staging, DB pool + workers sized like prod):
    locust -f locustfile.py -u 15000 -r 200 -H https://<staging> \
        --processes 8
Watch alongside: p95 latency here, Postgres active connections + lock waits
(pg_stat_activity), arq queue depth, CPU/IO on the box.

The user pool: each simulated user gets a unique telegram_id so DB rows and
locks spread realistically. Users whose telegram_id has no User row exercise
the waitlist-status path (the reality for most traffic pre-approval); the
seeded admin id exercises the authed feed path. Run scripts/seed_admins.py
first so at least one approved User exists.
"""
import itertools
import random

from locust import HttpUser, between, task

# The canonical dev admin (seed_admins.py creates it). Authed-path traffic
# rides this identity; a real staging run should seed a few hundred Users
# and widen this list.
APPROVED_IDS = [6451704338]

# Unapproved visitors — unique per simulated user, spread across a range that
# won't collide with real rows.
_visitor_ids = itertools.count(900_000_000)


class MiniappUser(HttpUser):
    """Feed-heavy read mix + waitlist status checks + a slice of writes."""

    wait_time = between(0.5, 2.5)

    def on_start(self):
        # ~1 in 5 simulated users is an approved member; the rest are
        # pre-approval visitors (matches an open-waitlist launch shape).
        if random.random() < 0.2:
            self.tg_id = random.choice(APPROVED_IDS)
            self.approved = True
        else:
            self.tg_id = next(_visitor_ids)
            self.approved = False

    def _get(self, path, name=None, ok=(200,)):
        with self.client.get(
            f"{path}?telegram_id={self.tg_id}",
            name=name or path,
            catch_response=True,
        ) as resp:
            if resp.status_code in ok:
                resp.success()
            else:
                resp.failure(f"unexpected {resp.status_code}")

    # ---- reads (the bulk) ----

    @task(30)
    def user_me(self):
        # 404/401 for unapproved visitors is the expected hot path too — the
        # frontend calls /user/ first to decide waitlist vs app.
        self._get("/user/", ok=(200, 401, 404))

    @task(20)
    def waitlist_status(self):
        self._get("/waitlist/status/")

    @task(15)
    def settings(self):
        self.client.get("/settings/", name="/settings/")

    @task(10)
    def user_stats(self):
        if not self.approved:
            return
        self._get("/user/stats/")

    @task(5)
    def waitlist_enrichment(self):
        # analytics likely unset locally -> empty-shape 200; still exercises
        # the DB lookups + endpoint plumbing.
        self._get("/user/waitlist-enrichment/")

    @task(5)
    def health(self):
        self.client.get("/health", name="/health")

    # ---- writes (the slice) ----

    @task(10)
    def session_start(self):
        if not self.approved:
            return
        with self.client.post(
            f"/session/start/?telegram_id={self.tg_id}",
            name="/session/start/",
            catch_response=True,
        ) as resp:
            # 200 with an empty feed is fine — the query work still happens.
            if resp.status_code in (200, 400):
                resp.success()

    @task(3)
    def claims_history(self):
        if not self.approved:
            return
        self._get("/claims/history/")
