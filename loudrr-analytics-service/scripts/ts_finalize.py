"""Finalize the TwitterScore data so EVERYTHING is clean + complete (run after the snowball).

Idempotent / re-runnable. DB-side backfill only (no scraping):
  1. fix any stale smart_followers stored as text "20,118" -> int 20118 (pre-int-fix rows)
  2. derive seen_count = # distinct accounts listing it as a significant follower (edge in-degree)
  3. VERIFY scan: prove 0 raw-JSON, 0 text-typed numerics, 0 out-of-range left (certainty)

(Category/description completeness is a separate SCRAPE step:
   python -m scripts.harvest_twitterscore   # profile-enriches all known + discovered)

    python -m scripts.ts_finalize
"""
import sqlite3
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    con = sqlite3.connect("data/harvest.db")
    cur = con.cursor()
    q = lambda sql: cur.execute(sql).fetchone()[0]

    stale = q("SELECT count(*) FROM twitterscore_accounts WHERE typeof(smart_followers)='text'")
    cur.execute("""UPDATE twitterscore_accounts
        SET smart_followers = CAST(REPLACE(REPLACE(smart_followers, ',', ''), ' ', '') AS INTEGER)
        WHERE typeof(smart_followers)='text'""")
    cur.execute("""UPDATE twitterscore_accounts
        SET seen_count = COALESCE((SELECT COUNT(DISTINCT followee_id) FROM twitterscore_follows
            WHERE follower_id = twitterscore_accounts.user_id), seen_count)
        WHERE user_id IN (SELECT DISTINCT follower_id FROM twitterscore_follows)""")
    con.commit()

    print(f"fixed stale smart_followers: {stale}")
    print("VERIFY (all should be 0):")
    print("  raw-JSON tags        :", q("SELECT count(*) FROM twitterscore_accounts WHERE tags LIKE '%[%'"))
    print("  raw-JSON categories  :", q("SELECT count(*) FROM twitterscore_accounts WHERE categories LIKE '%[%'"))
    print("  text smart_followers :", q("SELECT count(*) FROM twitterscore_accounts WHERE typeof(smart_followers)='text'"))
    print("  text twitterscore    :", q("SELECT count(*) FROM twitterscore_accounts WHERE typeof(twitterscore)='text'"))
    print("  out-of-range score   :", q("SELECT count(*) FROM twitterscore_accounts WHERE twitterscore<0 OR twitterscore>1000"))
    print("coverage:")
    print("  seen_count > 1       :", q("SELECT count(*) FROM twitterscore_accounts WHERE seen_count>1"))
    print("  missing category     :", q("SELECT count(*) FROM twitterscore_accounts WHERE coalesce(categories,'')=''"),
          "(run harvest_twitterscore to profile-enrich)")
    con.close()


if __name__ == "__main__":
    main()
