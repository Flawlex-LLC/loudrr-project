"""Test isolation.

DB-backed tests run against a throwaway temp SQLite — NEVER a real database. We set
``DATABASE_URL`` to the temp file BEFORE any app import so the app engine binds to it.
"""
import os
import pathlib

# MUST run before any `app.*` import so settings/engine bind to the temp DB.
_TMP = pathlib.Path(__file__).parent / "_tmp_test.db"
for p in (_TMP, _TMP.with_suffix(".db-wal"), _TMP.with_suffix(".db-shm")):
    try:
        p.unlink()
    except FileNotFoundError:
        pass
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP.as_posix()}"
